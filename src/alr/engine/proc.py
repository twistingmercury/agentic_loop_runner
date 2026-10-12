"""Child processes: each in its own process group, stopped as a group when the run is cancelled."""

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from subprocess import DEVNULL

# How long a bounded finish keeps draining after its process has exited. Tests
# patch it to stay fast.
_DRAIN_GRACE = 2.0
# What a stop may take beyond the grace period: the SIGKILL and the wait for the exit.
_KILL_SLACK = 1.0
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
    A cancel that lands while the child is still being created is handled like
    a cancel of finish: the spawn is let to complete, the whole group is
    stopped, and the cancellation carries on. An OSError from the spawn
    propagates unchanged.
    """
    spawn = asyncio.ensure_future(
        asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
            env=env,
            process_group=0,
        )
    )
    try:
        # Shielded, because asyncio would otherwise cancel the spawn by killing
        # only the leader with SIGKILL: no SIGTERM, and its children left alone.
        return await asyncio.shield(spawn)
    except asyncio.CancelledError:
        await asyncio.shield(_stop_spawned(spawn))
        raise


async def _stop_spawned(
    spawn: asyncio.Future[asyncio.subprocess.Process],
) -> None:
    """Wait for a spawn whose caller was cancelled, then stop the group it made."""
    [outcome] = await asyncio.gather(spawn, return_exceptions=True)
    if isinstance(outcome, BaseException):
        # No process exists; the caller's cancellation is what carries on.
        return

    await _stop_group(outcome)


async def finish(
    process: asyncio.subprocess.Process,
    drain: Awaitable[None] | None = None,
    *,
    bounded: bool = False,
) -> str | None:
    """Run drain alongside process until both are done and return the exit text.

    If the drain or the wait raises anything, cancellation included, the whole
    group is stopped before the error carries on.
    bounded: once the process has exited, a drain still held open by a
    background process is cut off after a grace period, keeping what it had
    read, and the process's pipes are closed. For an action, whose own exit is its
    result. Leave it off for a session, whose output must be read to the end.
    """
    return exit_text(await _run_to_exit(process, drain, bounded))


async def _run_to_exit(
    process: asyncio.subprocess.Process,
    drain: Awaitable[None] | None,
    bounded: bool = False,
) -> int:
    """Wait for the child to exit, then for the drain; stop the group if either fails."""
    task = None if drain is None else asyncio.ensure_future(drain)
    try:
        await _wait_for_exit(process, task)
        if task is not None:
            await _finish_drain(process, task, bounded)
    except BaseException:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await asyncio.shield(_stop_group(process))
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


async def _finish_drain(
    process: asyncio.subprocess.Process, drain: asyncio.Future[None], bounded: bool
) -> None:
    """Let the drain finish; when bounded, only for the grace period."""
    if not bounded:
        await drain
        return

    # wait_for cancels the drain at the deadline; what it read is kept.
    try:
        await asyncio.wait_for(drain, _DRAIN_GRACE)
    except TimeoutError:
        _close_transport(process)


def stop_deadline() -> float:
    """Return how long a stop of the group may take: the grace period, then the kill."""
    return _DRAIN_GRACE + _KILL_SLACK


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


async def feed_stdin(writer: asyncio.StreamWriter, data: bytes) -> None:
    """Write data to a child's stdin, wait for it to drain, and close the pipe."""
    writer.write(data)
    try:
        await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        # A session may exit without reading its stdin.
        pass
    finally:
        writer.close()


async def _stop_group(process: asyncio.subprocess.Process) -> None:
    _signal_group(process.pid, signal.SIGKILL)
    # Not process.wait(): on 3.12 it also waits for the pipes to close, and a
    # descendant that left the group still holds them.
    await _wait_for_exit(process, None)
    _close_transport(process)


def _close_transport(process: asyncio.subprocess.Process) -> None:
    """Close our ends of the process's pipes, which will not close by themselves.

    Whatever still holds a pipe left the group or outlived the process. Closing
    them here keeps asyncio from doing it at garbage collection, after the loop
    is gone, and printing a traceback.
    """
    process._transport.close()


def _signal_group(pgid: int, sig: int) -> bool:
    """Send sig to the group; False when the group is already gone."""
    try:
        os.killpg(pgid, sig)
    # macOS reports EPERM, not ESRCH, for a group that holds only a zombie.
    except (ProcessLookupError, PermissionError):
        return False

    return True
