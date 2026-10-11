"""Child processes: each in its own process group, stopped as a group when the run is cancelled."""

import asyncio
import ctypes
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from subprocess import DEVNULL

# How long a gentle stop waits for the group to exit on SIGTERM before SIGKILL,
# and how long a bounded or gentle finish keeps draining after its process has
# exited. Tests patch it to stay fast.
_GENTLE_GRACE = 2.0
_GENTLE_POLL = 0.01
# What a stop may take beyond the grace period: the SIGKILL and the wait for the exit.
_KILL_SLACK = 1.0
# How often to look at the exit code when waiting on the process does not wake
# up; see _wait_for_exit.
_EXIT_POLL = 0.1
_READ_SIZE = 64 * 1024
_PR_SET_PDEATHSIG = 1
# Looked up now, not in the child: the child runs between fork and exec, where
# loading a library could deadlock on a lock another thread held at the fork.
_prctl = ctypes.CDLL(None).prctl if sys.platform == "linux" else None


async def start(
    argv: Sequence[str],
    *,
    stdin: int | None = DEVNULL,
    stdout: int | None = None,
    stderr: int | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    gentle: bool = False,
    new_session: bool = False,
    die_with_parent: bool = False,
) -> asyncio.subprocess.Process:
    """Start argv in its own process group, so a stop reaches everything it spawns.

    Because the child leaves alr's foreground process group, a terminal Ctrl-C
    reaches only alr. Cancelling finish is what stops the child; nothing may
    rely on the child receiving SIGINT itself.
    A cancel that lands while the child is still being created is handled like
    a cancel of finish: the spawn is let to complete, the whole group is
    stopped (with SIGTERM first when gentle, as for finish), and the
    cancellation carries on. An OSError from the spawn propagates unchanged.
    new_session starts the child as a session leader instead, which is also a
    group leader, so a stop still reaches it. With no controlling terminal, a
    program that prompts on /dev/tty (git running a signing program, say) fails
    instead of waiting on a person. A program that opens a terminal by path
    (GPG_TTY) or a GUI pinentry through gpg-agent is not stopped by this and can
    still wait; a stop still gets it out.
    die_with_parent has the kernel send the child SIGTERM when alr dies, even by
    SIGKILL, which runs no cleanup of alr's own. SIGTERM, not SIGKILL, so git
    still removes its index lock. Only Linux has this; elsewhere the flag does
    nothing. It must be called from the thread that runs alr's event loop,
    because the kernel ties the signal to the thread that spawned the child.
    """
    # The two ways of leaving alr's group conflict: a session leader cannot be
    # moved into another group, so exactly one of them is passed.
    group = {"start_new_session": True} if new_session else {"process_group": 0}
    spawn = asyncio.ensure_future(
        asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
            env=env,
            preexec_fn=_die_with_parent_of(os.getpid()) if die_with_parent else None,
            **group,
        )
    )
    try:
        # Shielded, because asyncio would otherwise cancel the spawn by killing
        # only the leader with SIGKILL: no SIGTERM, and its children left alone.
        return await asyncio.shield(spawn)
    except asyncio.CancelledError:
        await asyncio.shield(_stop_spawned(spawn, gentle))
        raise


def _die_with_parent_of(parent: int) -> Callable[[], None] | None:
    """Make the child-side hook that has the kernel stop the child when parent dies."""
    if _prctl is None:
        return None

    def ask_kernel() -> None:
        _prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
        # alr may have died before the call above, and the signal would never
        # come. Compared with the pid, not 1: a container's init may not be 1.
        if os.getppid() != parent:
            os._exit(1)

    return ask_kernel


async def _stop_spawned(
    spawn: asyncio.Future[asyncio.subprocess.Process], gentle: bool
) -> None:
    """Wait for a spawn whose caller was cancelled, then stop the group it made."""
    [outcome] = await asyncio.gather(spawn, return_exceptions=True)
    if isinstance(outcome, BaseException):
        # No process exists; the caller's cancellation is what carries on.
        return

    await _stop_group(outcome, gentle)


async def finish(
    process: asyncio.subprocess.Process,
    drain: Awaitable[None] | None = None,
    *,
    gentle: bool = False,
    bounded: bool = False,
) -> str | None:
    """Run drain alongside process until both are done and return the exit text.

    If the drain or the wait raises anything, cancellation included, the whole
    group is stopped before the error carries on.
    bounded: once the process has exited, a drain still held open by a
    background process is cut off after a grace period, keeping what it had
    read, and the process's pipes are closed. For an action, whose own exit is its
    result. Leave it off for a session, whose output must be read to the end.
    gentle is for git, which cleans up its index lock on SIGTERM but not on
    SIGKILL: a stop sends SIGTERM first and waits the grace period before
    SIGKILL. It implies bounded.
    """
    return exit_text(await _run_to_exit(process, drain, gentle, bounded))


async def _run_to_exit(
    process: asyncio.subprocess.Process,
    drain: Awaitable[None] | None,
    gentle: bool,
    bounded: bool = False,
) -> int:
    """Wait for the child to exit, then for the drain; stop the group if either fails."""
    task = None if drain is None else asyncio.ensure_future(drain)
    try:
        await _wait_for_exit(process, task)
        if task is not None:
            await _finish_drain(process, task, gentle or bounded)
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


async def _finish_drain(
    process: asyncio.subprocess.Process, drain: asyncio.Future[None], bounded: bool
) -> None:
    """Let the drain finish; when bounded, only for the grace period."""
    if not bounded:
        await drain
        return

    # wait_for cancels the drain at the deadline; what it read is kept.
    try:
        await asyncio.wait_for(drain, _GENTLE_GRACE)
    except TimeoutError:
        _close_transport(process)


def stop_deadline() -> float:
    """Return how long a stop of the group may take: the grace period, then the kill."""
    return _GENTLE_GRACE + _KILL_SLACK


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
        gentle=gentle,
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
    _close_transport(process)


def _close_transport(process: asyncio.subprocess.Process) -> None:
    """Close our ends of the process's pipes, which will not close by themselves.

    Whatever still holds a pipe left the group or outlived the process. Closing
    them here keeps asyncio from doing it at garbage collection, after the loop
    is gone, and printing a traceback.
    """
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
