"""Child processes: each in its own process group, stopped as a group when the run is cancelled."""

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from subprocess import DEVNULL

from alr.gocompat import exit_error

_GIT_STOP_GRACE = 2.0
_GIT_STOP_POLL = 0.01
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
    """Wait for process after drain is done and return its Go-style exit text.

    On cancellation the whole group is stopped before the cancellation carries on.
    gentle is for git, which cleans up its index lock on SIGTERM but not on SIGKILL.
    """
    try:
        if drain is not None:
            await drain

        returncode = await process.wait()
    except asyncio.CancelledError:
        await asyncio.shield(_stop_group(process, gentle))
        raise

    return exit_error(returncode)


async def capture(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    gentle: bool = False,
) -> tuple[int, str, str]:
    """Run argv to the end and return its exit code, stdout, and stderr."""
    process = await start(
        argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    output: list[bytes] = []
    await finish(process, _read_pipes(process, output), gentle=gentle)
    assert process.returncode is not None
    stdout, stderr = (data.decode("utf-8", errors="replace") for data in output)
    return process.returncode, stdout, stderr


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


async def _read_pipes(process: asyncio.subprocess.Process, output: list[bytes]) -> None:
    """Read both pipes at the same time, so a full one never blocks the child."""
    assert process.stdout is not None
    assert process.stderr is not None
    output.extend(await asyncio.gather(process.stdout.read(), process.stderr.read()))


async def _stop_group(process: asyncio.subprocess.Process, gentle: bool) -> None:
    if gentle and _signal_group(process.pid, signal.SIGTERM):
        await _wait_for_group_exit(process.pid)

    _signal_group(process.pid, signal.SIGKILL)
    await process.wait()


async def _wait_for_group_exit(pgid: int) -> None:
    deadline = asyncio.get_running_loop().time() + _GIT_STOP_GRACE
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(_GIT_STOP_POLL)
        if not _signal_group(pgid, 0):
            return


def _signal_group(pgid: int, sig: int) -> bool:
    """Send sig to the group; False when the group is already gone."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False

    return True
