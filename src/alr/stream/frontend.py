"""The stream front end: what a full-screen run does with each process."""

import asyncio
from asyncio.subprocess import PIPE
from collections.abc import Callable, Sequence

from alr.engine import proc
from alr.engine.frontend import SessionResult
from alr.engine.gates import first_line
from alr.stream.events import (
    Activity,
    Committed,
    Event,
    GateFinished,
    RunDone,
    SessionFinished,
    TaskFinished,
    TaskStarted,
)
from alr.stream.parser import parse_stream_line
from alr.tasks import Gate, Task


class StreamFrontEnd:
    """Captures everything a run's processes print and reports it as events.

    Nothing reaches the terminal: the full-screen view owns it.
    """

    session_flags: Sequence[str] = ("--output-format", "stream-json", "--verbose")

    def __init__(self, report: Callable[[Event], None]) -> None:
        self._report = report

    def task_skipped(self, task: Task) -> None:
        return

    def task_started(self, task: Task) -> None:
        self._report(TaskStarted(task))

    async def run_session(
        self, task: Task, argv: Sequence[str], stdin_text: str
    ) -> SessionResult:
        try:
            process = await proc.start(argv, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        except OSError as exc:
            return SessionResult(text="", error=proc.start_failed_text(argv[0], exc))

        result_text = ""

        def on_stdout(line: bytes) -> None:
            nonlocal result_text
            activity, text, is_result = parse_stream_line(line)
            for entry in activity:
                self._report(Activity(task, entry))

            if is_result:
                result_text = text

        drain = _drain(process, on_stdout, self._raw_line(task), stdin_text.encode())
        error = await proc.finish(process, drain)
        return SessionResult(text=result_text, error=error)

    def session_finished(self, task: Task, seconds: float) -> None:
        self._report(SessionFinished(task, seconds))

    async def run_gate(self, task: Task, gate: Gate) -> str | None:
        # Only the first line, so a multi-line command stays one activity line.
        self._report(Activity(task, "→ gate " + first_line(gate.cmd)))
        return await self._run_command(task, ["sh", "-c", gate.cmd])

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
        self._report(Activity(task, "→ commit " + first_line(task.name)))

    async def run_git(self, task: Task, argv: Sequence[str], cwd: str) -> str | None:
        return await self._run_command(task, argv, cwd=cwd, gentle=True)

    def committed(self, task: Task, commit_hash: str) -> None:
        self._report(Committed(task, commit_hash))

    def task_finished(self, task: Task, error: str | None, seconds: float) -> None:
        self._report(TaskFinished(task, error, seconds))

    def run_done(self, error: str | None, seconds: float) -> None:
        self._report(RunDone(error, seconds))

    async def _run_command(
        self,
        task: Task,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        gentle: bool = False,
    ) -> str | None:
        """Run argv with each output line reported as activity, so nothing reaches the terminal."""
        try:
            process = await proc.start(argv, stdout=PIPE, stderr=PIPE, cwd=cwd)
        except OSError as exc:
            return proc.start_failed_text(argv[0], exc)

        on_line = self._raw_line(task)
        return await proc.finish(
            process, _drain(process, on_line, on_line), gentle=gentle
        )

    def _raw_line(self, task: Task) -> Callable[[bytes], None]:
        def report_line(line: bytes) -> None:
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            self._report(Activity(task, text))

        return report_line


async def _drain(
    process: asyncio.subprocess.Process,
    on_stdout: Callable[[bytes], None],
    on_stderr: Callable[[bytes], None],
    stdin: bytes | None = None,
) -> None:
    """Read both pipes, and feed stdin when given, at the same time so a full pipe never blocks the child."""
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("the process was started without output pipes")

    readers = [
        proc.read_lines(process.stdout, on_stdout),
        proc.read_lines(process.stderr, on_stderr),
    ]
    if stdin is not None:
        readers.append(_feed(process, stdin))

    await asyncio.gather(*readers)


async def _feed(process: asyncio.subprocess.Process, data: bytes) -> None:
    if process.stdin is None:
        raise RuntimeError("the session was started without a stdin pipe")

    process.stdin.write(data)
    try:
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        # A session may exit without reading its stdin.
        pass
    finally:
        process.stdin.close()
