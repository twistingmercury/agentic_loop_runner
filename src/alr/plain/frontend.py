"""The plain front end: what a run without the full-screen view does with each process."""

import asyncio
import sys
from asyncio.subprocess import PIPE
from collections.abc import Callable, Sequence

from alr.engine import proc
from alr.engine.frontend import SessionResult
from alr.engine.gates import first_line
from alr.record.events import (
    Activity,
    Committed,
    Event,
    GateFinished,
    RunDone,
    SessionFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import Gate, Task

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
            # stderr is inherited, so it reaches the terminal untouched.
            process = await proc.start(argv, stdin=PIPE, stdout=PIPE)
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

    async def run_gate(self, task: Task, gate: Gate) -> str | None:
        # Flushed, so the line stays ahead of the gate's own output.
        print(f"gate: {gate.cmd}", flush=True)
        self._report(Activity(task, "→ gate " + first_line(gate.cmd)))
        # No stdin, and stdout and stderr inherited, so the output passes straight through.
        try:
            process = await proc.start(["sh", "-c", gate.cmd])
        except OSError as exc:
            return proc.start_failed_text("sh", exc)

        return await proc.finish(process)

    def gate_finished(
        self,
        task: Task,
        gate: Gate,
        limit: str,
        error: str | None,
        timed_out: bool,
        seconds: float,
    ) -> None:
        self._report(GateFinished(task, gate, limit, error, timed_out, seconds))

    def commit_started(self, task: Task) -> None:
        # Only the first line, so a multi-line name stays one line. Flushed,
        # so the line stays ahead of git's own output.
        print(f"commit: {first_line(task.name)}", flush=True)
        self._report(Activity(task, "→ commit " + first_line(task.name)))

    async def run_git(self, task: Task, argv: Sequence[str], cwd: str) -> str | None:
        # stdout and stderr inherited, so git's output passes straight through.
        try:
            process = await proc.start(argv, cwd=cwd)
        except OSError as exc:
            return proc.start_failed_text(argv[0], exc)

        return await proc.finish(process, gentle=True)

    def committed(self, task: Task, commit_hash: str) -> None:
        self._report(Committed(task, commit_hash))

    def task_finished(self, task: Task, error: str | None, seconds: float) -> None:
        self._report(TaskFinished(task, error, seconds))

    def run_done(self, error: str | None, seconds: float) -> None:
        self._report(RunDone(error, seconds))

    def _raw_line(self, task: Task) -> Callable[[bytes], None]:
        def report_line(line: bytes) -> None:
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            self._report(Activity(task, text))

        return report_line


async def _pump(
    process: asyncio.subprocess.Process,
    stdin: bytes,
    chunks: list[bytes],
    on_line: Callable[[bytes], None],
) -> None:
    """Feed stdin and tee stdout at the same time, so a full pipe never blocks the session."""
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("the session was started without stdin and stdout pipes")

    await asyncio.gather(
        proc.feed_stdin(process.stdin, stdin), _tee(process.stdout, chunks, on_line)
    )


async def _tee(
    reader: asyncio.StreamReader,
    chunks: list[bytes],
    on_line: Callable[[bytes], None],
) -> None:
    """Copy the session's stdout to the terminal as raw bytes, keeping it for the result line.

    Each line also goes to on_line as it completes, and a last line with no
    newline goes when the stream ends.
    """
    # Pieces of the partial line, joined only when it completes, so a very
    # long line is not copied again for every chunk.
    pieces: list[bytes] = []
    while chunk := await reader.read(_READ_SIZE):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        chunks.append(chunk)
        *ends, rest = chunk.split(b"\n")
        for end in ends:
            on_line(b"".join([*pieces, end]))
            pieces.clear()

        if rest:
            pieces.append(rest)

    if pieces:
        on_line(b"".join(pieces))
