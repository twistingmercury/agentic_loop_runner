"""The plain front end: what a run without the full-screen view does with each process."""

import asyncio
import sys
from asyncio.subprocess import PIPE
from collections.abc import Sequence

from alr.engine import proc
from alr.engine.frontend import SessionResult
from alr.tasks import Gate, Task

_READ_SIZE = 64 * 1024


class PlainFrontEnd:
    """Shows everything on the terminal as it happens; reports nothing else."""

    session_flags: Sequence[str] = ()

    def task_skipped(self, task: Task) -> None:
        print(f"task {task.id}: {task.name} already completed, skipping", flush=True)

    def task_started(self, task: Task) -> None:
        return

    async def run_session(
        self, task: Task, argv: Sequence[str], stdin_text: str
    ) -> SessionResult:
        # gralph prints the prompt with Println, so its trailing newline is followed by one more.
        print(stdin_text, flush=True)
        try:
            # stderr is inherited, so it reaches the terminal untouched.
            process = await proc.start(argv, stdin=PIPE, stdout=PIPE)
        except FileNotFoundError:
            return SessionResult(text="", error=proc.not_found_text(argv[0]))

        chunks: list[bytes] = []
        error = await proc.finish(process, _pump(process, stdin_text.encode(), chunks))
        return SessionResult(
            text=b"".join(chunks).decode("utf-8", errors="replace"), error=error
        )

    def session_finished(self, task: Task, seconds: float) -> None:
        return

    async def run_gate(self, task: Task, gate: Gate) -> str | None:
        # Flushed, so the line stays ahead of the gate's own output.
        print(f"gate: {gate.cmd}", flush=True)
        # No stdin, and stdout and stderr inherited, so the output passes straight through.
        process = await proc.start(["sh", "-c", gate.cmd])
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
        return

    def commit_started(self, task: Task) -> None:
        raise NotImplementedError

    async def run_git(self, task: Task, argv: Sequence[str], cwd: str) -> str | None:
        raise NotImplementedError

    def committed(self, task: Task, commit_hash: str) -> None:
        raise NotImplementedError

    def task_finished(self, task: Task, error: str | None, seconds: float) -> None:
        return

    def run_done(self, error: str | None, seconds: float) -> None:
        return


async def _pump(
    process: asyncio.subprocess.Process, stdin: bytes, chunks: list[bytes]
) -> None:
    """Feed stdin and tee stdout at the same time, so a full pipe never blocks the session."""
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("the session was started without stdin and stdout pipes")

    await asyncio.gather(_feed(process.stdin, stdin), _tee(process.stdout, chunks))


async def _feed(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(data)
    try:
        await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        # A session may exit without reading its stdin.
        pass
    finally:
        writer.close()


async def _tee(reader: asyncio.StreamReader, chunks: list[bytes]) -> None:
    """Copy the session's stdout to the terminal as raw bytes, keeping it for the result line."""
    while chunk := await reader.read(_READ_SIZE):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        chunks.append(chunk)
