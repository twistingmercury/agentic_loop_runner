"""Child processes: each in its own process group, stopped as a group when the run is cancelled."""

import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from subprocess import DEVNULL

# How long a gentle stop waits for the group to exit on SIGTERM before SIGKILL,
# and how long a gentle capture keeps reading after its process has exited.
# Tests patch it to stay fast.
_GENTLE_GRACE = 2.0
_GENTLE_POLL = 0.01
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
    """Wait for process after drain is done and return its exit text.

    If the drain or the wait raises anything, cancellation included, the whole
    group is stopped before the error carries on.
    gentle is for git, which cleans up its index lock on SIGTERM but not on SIGKILL.
    """
    try:
        if drain is not None:
            await drain

        returncode = await process.wait()
    except BaseException:
        await asyncio.shield(_stop_group(process, gentle))
        raise

    return exit_text(returncode)


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
    await finish(process, _read_pipes(process, stdout, stderr, gentle), gentle=gentle)
    if process.returncode is None:
        raise RuntimeError(f"{argv[0]} finished without an exit code")

    return (
        process.returncode,
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
    process: asyncio.subprocess.Process,
    stdout: bytearray,
    stderr: bytearray,
    gentle: bool,
) -> None:
    """Read both pipes at the same time, so a full one never blocks the child."""
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("the process was started without output pipes")

    reads = asyncio.gather(
        _read_all(process.stdout, stdout), _read_all(process.stderr, stderr)
    )
    try:
        if not gentle:
            await reads
            return

        await process.wait()
        # wait_for cancels the reads at the deadline; what they read is kept.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(reads, _GENTLE_GRACE)
    finally:
        reads.cancel()
        await asyncio.gather(reads, return_exceptions=True)


async def _read_all(stream: asyncio.StreamReader, buffer: bytearray) -> None:
    """Read stream to its end into buffer, so a cut-off read still leaves the data so far."""
    while chunk := await stream.read(_READ_SIZE):
        buffer.extend(chunk)


async def _stop_group(process: asyncio.subprocess.Process, gentle: bool) -> None:
    if gentle and _signal_group(process.pid, signal.SIGTERM):
        await _wait_for_group_exit(process.pid)

    _signal_group(process.pid, signal.SIGKILL)
    await process.wait()


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
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS reports EPERM, not ESRCH, for a group that holds only a zombie.
        return False

    return True
