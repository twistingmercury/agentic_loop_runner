"""The plain front end: what a run without the full-screen view does with each process."""

import asyncio
import sys
from asyncio.subprocess import PIPE
from collections.abc import Callable, Sequence
from typing import BinaryIO

from alr.engine import proc
from alr.engine.frontend import FirstLine, SessionResult
from alr.engine.actions import first_line
from alr.record.events import (
    Activity,
    CommitStarted,
    Committed,
    Event,
    ActionFinished,
    ActionStarted,
    RunDone,
    SessionFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import Action, RunResult, Task

_READ_SIZE = 64 * 1024


class PlainFrontEnd:
    """Shows everything on the terminal as it happens; reports to a recorder, if given."""

    session_flags: Sequence[str] = ()

    def __init__(self, record: Callable[[Event], None] | None = None) -> None:
        self._record = record

    def _report(self, event: Event) -> None:
        if self._record is not None:
            self._record(event)

    def task_skipped(self, task: Task) -> None:
        print(f"task {task.id}: {task.name} already completed, skipping", flush=True)

    def task_started(self, task: Task) -> None:
        self._report(TaskStarted(task))

    async def run_session(
        self, task: Task, argv: Sequence[str], stdin_text: str
    ) -> SessionResult:
        # gralph prints the prompt with Println, so its trailing newline is followed by one more.
        print(stdin_text, flush=True)
        try:
            # Both outputs are piped so each line can reach the run log.
            process = await proc.start(argv, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        except OSError as exc:
            return SessionResult(text="", error=proc.start_failed_text(argv[0], exc))

        chunks: list[bytes] = []
        on_line = self._raw_line(task)
        error = await proc.finish(
            process, _pump(process, stdin_text.encode(), chunks, on_line)
        )
        return SessionResult(
            text=b"".join(chunks).decode("utf-8", errors="replace"), error=error
        )

    def session_finished(self, task: Task, seconds: float) -> None:
        self._report(SessionFinished(task, seconds))

    async def run_action(self, task: Task, action: Action) -> str | None:
        # Flushed, so the line stays ahead of the action's own output.
        print(f"action: {action.command}", flush=True)
        self._report(Activity(task, "→ action " + first_line(action.command)))
        self._report(ActionStarted(task, action))
        return await self._run_command(task, ["sh", "-c", action.command])

    def action_finished(
        self,
        task: Task,
        action: Action,
        limit: str,
        error: str | None,
        timed_out: bool,
        seconds: float,
    ) -> None:
        self._report(ActionFinished(task, action, limit, error, timed_out, seconds))

    def commit_started(self, task: Task) -> None:
        # Only the first line, so a multi-line name stays one line. Flushed,
        # so the line stays ahead of git's own output.
        print(f"commit: {first_line(task.name)}", flush=True)
        self._report(Activity(task, "→ commit " + first_line(task.name)))
        self._report(CommitStarted(task))

    async def run_git(self, task: Task, argv: Sequence[str], cwd: str) -> str | None:
        first = FirstLine()
        error = await self._run_command(
            task, argv, cwd=cwd, gentle=True, tap=first.take
        )
        return first.reason(error)

    def committed(self, task: Task, commit_hash: str) -> None:
        self._report(Committed(task, commit_hash))

    def task_finished(self, task: Task, error: str | None, seconds: float) -> None:
        self._report(TaskFinished(task, error, seconds))

    def run_done(
        self, error: str | None, seconds: float, result: RunResult, exit_code: int
    ) -> None:
        self._report(RunDone(error, seconds, result, exit_code))

    async def _run_command(
        self,
        task: Task,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        gentle: bool = False,
        tap: Callable[[str], None] | None = None,
    ) -> str | None:
        """Run an action or a git command: show its output as it comes and report each line."""
        # No stdin. Both outputs are piped so each line can reach the run log.
        try:
            # gentle is true only for git, which gets no controlling terminal
            # and is stopped by the kernel if alr dies.
            process = await proc.start(
                argv,
                stdout=PIPE,
                stderr=PIPE,
                cwd=cwd,
                gentle=gentle,
                new_session=gentle,
                die_with_parent=gentle,
            )
        except OSError as exc:
            return proc.start_failed_text(argv[0], exc)

        # Bounded: an action's or git's own exit is its result, so a descendant
        # that outlives it and holds the pipes must not hold up the run.
        return await proc.finish(
            process,
            _pump_outputs(process, self._raw_line(task, tap)),
            gentle=gentle,
            bounded=True,
        )

    def _raw_line(
        self, task: Task, tap: Callable[[str], None] | None = None
    ) -> Callable[[bytes], None]:
        def report_line(line: bytes) -> None:
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            self._report(Activity(task, text))
            if tap is not None:
                tap(text)

        return report_line


async def _pump(
    process: asyncio.subprocess.Process,
    stdin: bytes,
    chunks: list[bytes],
    on_line: Callable[[bytes], None],
) -> None:
    """Feed stdin and tee both outputs at the same time, so a full pipe never blocks the session.

    Only stdout is kept in chunks: the engine parses the result line from it,
    and stderr must never end up there.
    """
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("the session was started without stdin and output pipes")

    await asyncio.gather(
        proc.feed_stdin(process.stdin, stdin),
        _tee(process.stdout, sys.stdout.buffer, on_line, chunks),
        _tee(process.stderr, sys.stderr.buffer, on_line),
    )


async def _pump_outputs(
    process: asyncio.subprocess.Process, on_line: Callable[[bytes], None]
) -> None:
    """Tee stdout and stderr at the same time, so a full pipe never blocks the command."""
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("the command was started without output pipes")

    await asyncio.gather(
        _tee(process.stdout, sys.stdout.buffer, on_line),
        _tee(process.stderr, sys.stderr.buffer, on_line),
    )


async def _tee(
    reader: asyncio.StreamReader,
    out: BinaryIO,
    on_line: Callable[[bytes], None],
    chunks: list[bytes] | None = None,
) -> None:
    """Copy a stream to out as raw bytes, keeping the chunks if asked (the result line needs them).

    Each line also goes to on_line as it completes, and a last line with no
    newline goes when the stream ends.
    """
    # Pieces of the partial line, joined only when it completes, so a very
    # long line is not copied again for every chunk.
    pieces: list[bytes] = []
    while chunk := await reader.read(_READ_SIZE):
        out.write(chunk)
        out.flush()
        if chunks is not None:
            chunks.append(chunk)

        *ends, rest = chunk.split(b"\n")
        for end in ends:
            on_line(b"".join([*pieces, end]))
            pieces.clear()

        if rest:
            pieces.append(rest)

    if pieces:
        on_line(b"".join(pieces))
