"""Child processes: each in its own process group, stopped as a group when the run is cancelled."""

import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from subprocess import DEVNULL

# How long a gentle stop waits for the group to exit on SIGTERM before SIGKILL,
# and how long a gentle finish keeps draining after its process has exited.
# Tests patch it to stay fast.
_GENTLE_GRACE = 2.0
_GENTLE_POLL = 0.01
# How often to look at the exit code when waiting on the process does not wake
# up; see _wait_for_exit.
_EXIT_POLL = 0.1
_READ_SIZE = 64 * 1024


async def start(
    argv: Sequence[str],
    *,
    stdin: int | None = DEVNULL,
    stdout: int | None = None,
    stderr: int | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> asyncio.subprocess.Process:
    """Start argv in its own process group, so a stop reaches everything it spawns.

    Because the child leaves alr's foreground process group, a terminal Ctrl-C
    reaches only alr. Cancelling finish is what stops the child; nothing may
    rely on the child receiving SIGINT itself.
    """
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        env=env,
        process_group=0,
    )


async def finish(
    process: asyncio.subprocess.Process,
    drain: Awaitable[None] | None = None,
    *,
    gentle: bool = False,
) -> str | None:
    """Run drain alongside process until both are done and return the exit text.

    If the drain or the wait raises anything, cancellation included, the whole
    group is stopped before the error carries on.
    gentle is for git, which cleans up its index lock on SIGTERM but not on SIGKILL.
    It also bounds the drain: once the process has exited, a drain still held
    open by a background process is cut off after a grace period, keeping what
    it had read.
    """
    return exit_text(await _run_to_exit(process, drain, gentle))


async def _run_to_exit(
    process: asyncio.subprocess.Process,
    drain: Awaitable[None] | None,
    gentle: bool,
) -> int:
    """Wait for the child to exit, then for the drain; stop the group if either fails."""
    task = None if drain is None else asyncio.ensure_future(drain)
    try:
        await _wait_for_exit(process, task)
        if task is not None:
            await _finish_drain(task, gentle)
    except BaseException:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await asyncio.shield(_stop_group(process, gentle))
        raise

    if process.returncode is None:
        raise RuntimeError(f"process {process.pid} finished without an exit code")

    return process.returncode


async def _wait_for_exit(
    process: asyncio.subprocess.Process, drain: asyncio.Future[None] | None
) -> None:
    """Wait until the child itself has exited, whoever still holds its pipes.

    Waiting on the process wakes at once in the normal case. On Python 3.12 it
    also waits for the pipes to close, so a descendant that keeps them open
    would block it forever; the exit code is set the moment the child exits, so
    looking at it every _EXIT_POLL seconds covers that case. A drain that fails
    ends the wait early. A drain that finishes cleanly is left out of the next
    round, or the wait would return at once every time.
    """
    exited = asyncio.ensure_future(process.wait())
    try:
        while process.returncode is None:
            waiting: set[asyncio.Future[object]] = {exited}
            if drain is not None and not drain.done():
                waiting.add(drain)

            await asyncio.wait(
                waiting, timeout=_EXIT_POLL, return_when=asyncio.FIRST_COMPLETED
            )
            if drain is not None and drain.done():
                drain.result()
    finally:
        exited.cancel()
        await asyncio.gather(exited, return_exceptions=True)


async def _finish_drain(drain: asyncio.Future[None], gentle: bool) -> None:
    """Let the drain finish; when gentle, only for the grace period."""
    if not gentle:
        await drain
        return

    # wait_for cancels the drain at the deadline; what it read is kept.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(drain, _GENTLE_GRACE)


def exit_text(returncode: int) -> str | None:
    """Say how a process ended: None for a zero exit, else `exit status 3` or `signal: SIGKILL`."""
    if returncode == 0:
        return None

    if returncode > 0:
        return f"exit status {returncode}"

    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = str(-returncode)

    return f"signal: {name}"


def not_found_text(name: str) -> str:
    """Say that an executable is not on PATH."""
    return f"{name}: not found on PATH"


def start_failed_text(name: str, exc: OSError) -> str:
    """Say why an executable could not be started."""
    if isinstance(exc, FileNotFoundError):
        return not_found_text(name)

    return f"{name}: cannot be started: {exc.strerror}"


async def capture(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    gentle: bool = False,
) -> tuple[int, str, str]:
    """Run argv to the end and return its exit code, stdout, and stderr.

    With gentle, a daemon that still holds the pipes after the command has
    exited does not hang the call: reading stops after the grace period and
    returns what was read.
    """
    process = await start(
        argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    stdout, stderr = bytearray(), bytearray()
    returncode = await _run_to_exit(
        process, _read_pipes(process, stdout, stderr), gentle
    )
    return (
        returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def read_lines(
    stream: asyncio.StreamReader, on_line: Callable[[bytes], None]
) -> None:
    """Pass each line of stream, newline included, to on_line.

    StreamReader.readline fails on a line longer than its buffer, and one
    stream-json event (a tool result holding a whole file, say) can be larger.
    Reading in chunks has no line limit, and a final line with no trailing
    newline still reaches on_line.
    """
    pieces: list[bytes] = []
    while chunk := await stream.read(_READ_SIZE):
        *ends, rest = chunk.split(b"\n")
        for end in ends:
            on_line(b"".join([*pieces, end, b"\n"]))
            pieces.clear()

        if rest:
            pieces.append(rest)

    if pieces:
        on_line(b"".join(pieces))


async def _read_pipes(
    process: asyncio.subprocess.Process, stdout: bytearray, stderr: bytearray
) -> None:
    """Read both pipes at the same time, so a full one never blocks the child."""
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("the process was started without output pipes")

    await asyncio.gather(
        _read_all(process.stdout, stdout), _read_all(process.stderr, stderr)
    )


async def _read_all(stream: asyncio.StreamReader, buffer: bytearray) -> None:
    """Read stream to its end into buffer, so a cut-off read still leaves the data so far."""
    while chunk := await stream.read(_READ_SIZE):
        buffer.extend(chunk)


async def _stop_group(process: asyncio.subprocess.Process, gentle: bool) -> None:
    if gentle and _signal_group(process.pid, signal.SIGTERM):
        await _wait_for_group_exit(process.pid)

    _signal_group(process.pid, signal.SIGKILL)
    # Not process.wait(): on 3.12 it also waits for the pipes to close, and a
    # descendant that left the group still holds them.
    await _wait_for_exit(process, None)
    # Whatever still holds a pipe left the group, so the pipes will not close
    # by themselves. Closing our ends here keeps asyncio from doing it at
    # garbage collection, after the loop is gone, and printing a traceback.
    process._transport.close()


async def _wait_for_group_exit(pgid: int) -> None:
    deadline = asyncio.get_running_loop().time() + _GENTLE_GRACE
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(_GENTLE_POLL)
        if not _signal_group(pgid, 0):
            return


def _signal_group(pgid: int, sig: int) -> bool:
    """Send sig to the group; False when the group is already gone."""
    try:
        os.killpg(pgid, sig)
    # macOS reports EPERM, not ESRCH, for a group that holds only a zombie.
    except (ProcessLookupError, PermissionError):
        return False

    return True
