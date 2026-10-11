"""Process groups: a cancel must stop a child and everything it started."""

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    kill_holder_and_let_pipes_close,
    wait_for_file,
    wait_for_text,
    wait_process_gone,
    wait_until_gone,
)

from alr.engine import proc


async def test_gentle_cancel_lets_the_child_handle_sigterm(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    process = await proc.start(
        ["sh", "-c", f"trap 'exit 7' TERM; echo ready > {ready}; sleep 60 & wait"]
    )
    waiter = asyncio.ensure_future(proc.finish(process, gentle=True))
    await wait_for_text(ready)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert process.returncode == 7


async def test_a_new_session_child_leads_its_own_session() -> None:
    process = await proc.start(
        ["sleep", "5"], stdout=asyncio.subprocess.DEVNULL, new_session=True
    )
    try:
        assert os.getsid(process.pid) == process.pid
        assert os.getsid(process.pid) != os.getsid(0)
        assert os.getpgid(process.pid) == process.pid
    finally:
        process.kill()
        await process.wait()


async def test_cancel_kills_the_whole_group_of_a_new_session_child(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "child.pid"
    process = await proc.start(
        ["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"], new_session=True
    )
    waiter = asyncio.ensure_future(proc.finish(process))
    await wait_for_text(pid_file)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert process.returncode == -signal.SIGKILL
    await wait_until_gone(int(pid_file.read_text()))


# Starts a child tied to this process and says where it is, then idles until killed.
_PARENT = """
import asyncio, pathlib
from alr.engine import proc


async def main():
    process = await proc.start(["sleep", "60"], new_session=True, die_with_parent=True)
    pathlib.Path("{pid_file}").write_text(str(process.pid))
    await asyncio.sleep(60)


asyncio.run(main())
"""


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux-only")
def test_a_child_that_dies_with_its_parent_stops_when_the_parent_is_killed(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "child.pid"
    parent = subprocess.Popen([sys.executable, "-c", _PARENT.format(pid_file=pid_file)])
    try:
        child_pid = int(wait_for_file(pid_file))
    finally:
        # SIGKILL: the parent gets no chance to clean up after itself.
        parent.kill()
        parent.wait()

    wait_process_gone(child_pid, 5)


# Starts a child that traps SIGTERM and reports it, tied to this process.
_TERM_PARENT = """
import asyncio, pathlib
from alr.engine import proc

SCRIPT = "trap 'echo term > {term_file}; exit 0' TERM; echo $$ > {pid_file}; while :; do sleep 0.05; done"


async def main():
    await proc.start(["sh", "-c", SCRIPT], new_session=True, die_with_parent=True)
    await asyncio.sleep(60)


asyncio.run(main())
"""


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux-only")
def test_a_child_that_dies_with_its_parent_gets_sigterm_not_sigkill(
    tmp_path: Path,
) -> None:
    pid_file, term_file = tmp_path / "child.pid", tmp_path / "term"
    script = _TERM_PARENT.format(pid_file=pid_file, term_file=term_file)
    parent = subprocess.Popen([sys.executable, "-c", script])
    try:
        # The child writes its pid only after the kernel was asked to signal it.
        wait_for_file(pid_file)
    finally:
        parent.kill()
        parent.wait()

    # Only SIGTERM lets the trap run; SIGKILL would leave no file.
    assert wait_for_file(term_file).strip() == "term"


async def test_a_child_whose_parent_is_already_gone_exits_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fork copies the patch into the child, which then sees a parent that is
    # not the one alr had when it started the child.
    monkeypatch.setattr(proc.os, "getppid", lambda: -1)
    process = await proc.start(["sleep", "60"], die_with_parent=True)

    assert await asyncio.wait_for(process.wait(), 5) == 1


async def test_cancel_kills_the_whole_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    process = await proc.start(["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"])
    waiter = asyncio.ensure_future(proc.finish(process))
    await wait_for_text(pid_file)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert process.returncode == -signal.SIGKILL
    await wait_until_gone(int(pid_file.read_text()))


def _hold_spawn(monkeypatch: pytest.MonkeyPatch, release: asyncio.Event) -> None:
    """Make proc.start wait on release after the child exists but before it returns."""
    real_spawn = asyncio.create_subprocess_exec

    async def held_spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await real_spawn(*args, **kwargs)
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", held_spawn)


async def _cancel_start_during_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, gentle: bool
) -> tuple[Path, Path]:
    """Cancel proc.start after the child is running but before the spawn returns.

    Returns the file the child writes on SIGTERM and the file holding the pid of
    the child's own background sleep.
    """
    ready, stopped = tmp_path / "ready", tmp_path / "stopped"
    script = (
        f"trap 'echo bye > {stopped}; exit 0' TERM; sleep 60 & echo $! > {ready}; wait"
    )
    release = asyncio.Event()
    _hold_spawn(monkeypatch, release)
    starting = asyncio.ensure_future(proc.start(["sh", "-c", script], gentle=gentle))
    await wait_for_text(ready)

    starting.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(starting, 5)

    return stopped, ready


async def test_a_gentle_cancel_during_the_spawn_sends_sigterm_to_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped, ready = await _cancel_start_during_spawn(
        tmp_path, monkeypatch, gentle=True
    )

    assert stopped.read_text(encoding="utf-8") == "bye\n", (
        "SIGTERM must reach the child before any SIGKILL"
    )
    await wait_until_gone(int(ready.read_text()))


async def test_a_cancel_during_the_spawn_kills_the_whole_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped, ready = await _cancel_start_during_spawn(
        tmp_path, monkeypatch, gentle=False
    )

    assert not stopped.exists(), "only gentle sends SIGTERM first"
    await wait_until_gone(int(ready.read_text()))


async def test_a_missing_executable_still_fails_the_start(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await proc.start([str(tmp_path / "missing")])


async def test_read_lines_has_no_length_limit() -> None:
    process = await proc.start(
        ["sh", "-c", "head -c 200000 /dev/zero | tr '\\0' 'x'; echo; printf tail"],
        stdout=asyncio.subprocess.PIPE,
    )
    lines: list[bytes] = []
    assert process.stdout is not None
    await proc.finish(process, proc.read_lines(process.stdout, lines.append))

    assert len(lines[0]) == 200001
    assert lines[1] == b"tail"


async def test_read_lines_splits_only_on_newlines() -> None:
    stream = asyncio.StreamReader()
    stream.feed_data(b"one\r\ntwo\rstill two\n\nlast")
    stream.feed_eof()
    lines: list[bytes] = []
    await proc.read_lines(stream, lines.append)

    assert lines == [b"one\r\n", b"two\rstill two\n", b"\n", b"last"]


async def test_capture_returns_output_and_code() -> None:
    assert await proc.capture(["sh", "-c", "echo out; echo err >&2; exit 2"]) == (
        2,
        "out\n",
        "err\n",
    )


@pytest.mark.parametrize(
    ("returncode", "text"),
    [
        (0, None),
        (1, "exit status 1"),
        (-signal.SIGKILL, "signal: SIGKILL"),
        (-250, "signal: 250"),
    ],
)
def test_exit_text(returncode: int, text: str | None) -> None:
    assert proc.exit_text(returncode) == text


def test_start_failed_text() -> None:
    missing = proc.start_failed_text("claude", FileNotFoundError(2, "No such file"))
    assert "claude" in missing
    assert "not found" in missing

    refused = proc.start_failed_text("claude", PermissionError(13, "Permission denied"))
    assert "claude" in refused
    assert "cannot be started" in refused
    assert "not found" not in refused


async def test_finish_stops_the_group_when_the_drain_fails(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    process = await proc.start(
        ["sh", "-c", f"sleep 60 & echo $! > {pid_file}; while :; do echo line; done"],
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None

    def fail(line: bytes) -> None:
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        await asyncio.wait_for(
            proc.finish(process, proc.read_lines(process.stdout, fail)), 5
        )

    assert process.returncode == -signal.SIGKILL
    await wait_until_gone(int(pid_file.read_text()))


async def test_gentle_finish_cuts_off_a_drain_held_open_by_a_background_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)
    pid_file = tmp_path / "holder.pid"
    process = await proc.start(
        ["sh", "-c", f"sleep 30 & echo $! > {pid_file}; echo line"],
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    lines: list[bytes] = []
    started = time.monotonic()

    try:
        result = await proc.finish(
            process, proc.read_lines(process.stdout, lines.append), gentle=True
        )
        elapsed = time.monotonic() - started
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    assert result is None
    assert lines == [b"line\n"]
    assert elapsed < 2


async def test_bounded_finish_cuts_off_a_drain_held_open_by_a_background_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)
    pid_file = tmp_path / "holder.pid"
    process = await proc.start(
        ["sh", "-c", f"sleep 30 & echo $! > {pid_file}; echo line; exit 3"],
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    lines: list[bytes] = []
    started = time.monotonic()

    try:
        result = await proc.finish(
            process, proc.read_lines(process.stdout, lines.append), bounded=True
        )
        elapsed = time.monotonic() - started
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    assert result == "exit status 3"
    assert lines == [b"line\n"]
    assert 0.2 <= elapsed < 2


async def test_bounded_finish_cuts_off_a_drain_held_open_by_an_escaped_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)
    pid_file = tmp_path / "escaped.pid"
    # setsid takes the sleeper out of the group, so nothing but the grace
    # period can end the drain.
    process = await proc.start(
        [
            "sh",
            "-c",
            f"setsid sh -c 'echo $$ > {pid_file}; exec sleep 30' & echo line",
        ],
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    lines: list[bytes] = []

    try:
        result = await asyncio.wait_for(
            proc.finish(
                process, proc.read_lines(process.stdout, lines.append), bounded=True
            ),
            5,
        )
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    assert result is None
    assert lines == [b"line\n"]


async def test_unbounded_finish_waits_for_a_drain_held_open_by_a_background_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)
    pid_file = tmp_path / "holder.pid"
    process = await proc.start(
        ["sh", "-c", f"sleep 30 & echo $! > {pid_file}; echo line"],
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    lines: list[bytes] = []
    waiter = asyncio.ensure_future(
        proc.finish(process, proc.read_lines(process.stdout, lines.append))
    )

    try:
        # Well past the grace period a bounded finish would have given up after.
        done, _ = await asyncio.wait({waiter}, timeout=0.6)
    finally:
        await kill_holder_and_let_pipes_close(pid_file)
        await asyncio.gather(waiter, return_exceptions=True)

    assert not done, "a session's output must be read to the end"
    assert lines == [b"line\n"]


async def test_bounded_cancel_stops_the_group_without_sigterm(
    tmp_path: Path,
) -> None:
    ready, stopped = tmp_path / "ready", tmp_path / "stopped"
    script = f"trap 'echo bye > {stopped}; exit 0' TERM; echo ready > {ready}; sleep 60 & wait"
    process = await proc.start(["sh", "-c", script])
    waiter = asyncio.ensure_future(proc.finish(process, bounded=True))
    await wait_for_text(ready)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert process.returncode == -signal.SIGKILL
    assert not stopped.exists(), "only gentle sends SIGTERM first"


# Runs a command whose escaped descendant holds the pipes past the grace period,
# then lets the loop close and the garbage go, as the end of alr does.
_CUT_OFF_SCRIPT = """
import asyncio, gc, os, signal, sys
from pathlib import Path
from alr.engine import proc

proc._GENTLE_GRACE = 0.2
pid_file = Path(sys.argv[2])
argv = ["sh", "-c", f"setsid sh -c 'echo $$ > {pid_file}; exec sleep 30' & echo hi"]


async def run_bounded() -> None:
    process = await proc.start(argv, stdout=asyncio.subprocess.PIPE)
    await proc.finish(process, process.stdout.read(), bounded=True)


async def run_captured() -> None:
    await proc.capture(argv, gentle=True)


try:
    asyncio.run({"bounded": run_bounded, "capture": run_captured}[sys.argv[1]]())
    gc.collect()
finally:
    os.kill(int(pid_file.read_text()), signal.SIGKILL)
"""


@pytest.mark.parametrize("mode", ["bounded", "capture"])
def test_a_drain_cut_off_at_the_deadline_leaves_no_transport_open(
    tmp_path: Path, mode: str
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "dev",
            "-W",
            "error::ResourceWarning",
            "-c",
            _CUT_OFF_SCRIPT,
            mode,
            str(tmp_path / "escaped.pid"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.stderr == ""
    assert result.returncode == 0


async def test_cancel_returns_when_an_escaped_descendant_holds_the_pipe(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "escaped.pid"
    # setsid takes the sleeper out of the group the stop kills, and it keeps
    # the stdout pipe; the test kills it, and it outlasts a loaded machine.
    # The sleeper writes its own pid, so the file only appears once it has left
    # the group: with `$!` it appears at the fork, and a stop that came before
    # setsid ran would kill the sleeper too and leave nothing to clean up.
    process = await proc.start(
        [
            "sh",
            "-c",
            f"setsid sh -c 'echo $$ > {pid_file}; exec sleep 30' & sleep 60",
        ],
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    lines: list[bytes] = []
    waiter = asyncio.ensure_future(
        proc.finish(process, proc.read_lines(process.stdout, lines.append))
    )

    try:
        await wait_for_text(pid_file)
        waiter.cancel()
        # Not awaiting the waiter directly: a stop that hangs must fail here,
        # not hold up the suite.
        done, _ = await asyncio.wait({waiter}, timeout=1)
    finally:
        await kill_holder_and_let_pipes_close(pid_file)
        await asyncio.gather(waiter, return_exceptions=True)

    assert done, "the stop waited for the escaped descendant to close the pipe"
    assert process.returncode == -signal.SIGKILL


async def test_finish_does_not_spin_when_the_drain_ends_before_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits = 0
    real_wait = asyncio.wait

    async def counting_wait(*args: Any, **kwargs: Any) -> Any:
        nonlocal waits
        waits += 1
        return await real_wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", counting_wait)
    # The child closes its output at once, so the drain is done while it runs on.
    process = await proc.start(
        ["sh", "-c", "exec >/dev/null 2>&1; sleep 0.3"],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    lines: list[bytes] = []

    result = await asyncio.wait_for(
        proc.finish(process, proc.read_lines(process.stdout, lines.append)), 5
    )

    assert result is None
    # A busy loop would count thousands; a sleeping one a handful.
    assert waits < 10


async def test_gentle_cancel_kills_a_child_that_ignores_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.3)
    ready = tmp_path / "ready"
    process = await proc.start(
        ["sh", "-c", f"trap '' TERM; echo ready > {ready}; while :; do sleep 0.1; done"]
    )
    waiter = asyncio.ensure_future(proc.finish(process, gentle=True))
    await wait_for_text(ready)

    started = time.monotonic()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert process.returncode == -signal.SIGKILL
    assert time.monotonic() - started >= 0.3


async def test_gentle_cancel_kills_a_grandchild_that_ignores_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.3)
    pid_file = tmp_path / "grandchild.pid"
    grandchild = f"trap '' TERM; echo $$ > {pid_file}; while :; do sleep 0.1; done"
    process = await proc.start(
        ["sh", "-c", f"trap 'exit 0' TERM; sh -c \"{grandchild}\" & wait"]
    )
    waiter = asyncio.ensure_future(proc.finish(process, gentle=True))
    await wait_for_text(pid_file)

    started = time.monotonic()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert process.returncode == 0
    assert time.monotonic() - started >= 0.3
    await wait_until_gone(int(pid_file.read_text()))


def test_a_group_that_refuses_the_signal_counts_as_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(pgid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "killpg", refuse)

    assert proc._signal_group(1, 0) is False
