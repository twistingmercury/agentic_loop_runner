"""Process groups: a cancel must stop a child and everything it started."""

import asyncio
import os
import signal
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from alr.engine import proc

# Stands in for Go's TestProcessTreeHelper: the parent starts a descendant,
# writes its PID to the ready file named in argv, and blocks.
_DESCENDANT = "import time\nwhile True:\n    time.sleep(3600)\n"
_PARENT = f"""
import subprocess, sys, time
from pathlib import Path

descendant = subprocess.Popen([sys.executable, "-c", {_DESCENDANT!r}])
Path(sys.argv[1]).write_text(str(descendant.pid))
while True:
    time.sleep(3600)
"""


@pytest.fixture
def descendant_ready(tmp_path: Path) -> Iterator[Path]:
    """The ready file path; whatever PID it ends up holding is killed afterwards."""
    path = tmp_path / "descendant-ready"
    yield path
    if not path.exists():
        return

    try:
        os.kill(int(path.read_text()), signal.SIGKILL)
    except (ProcessLookupError, ValueError):
        return


async def wait_for_process_tree_pid(path: Path, waiter: asyncio.Future) -> int:
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        text = path.read_text() if path.exists() else ""
        if text:
            return int(text)

        if waiter.done():
            pytest.fail("process exited before descendant was ready")

        await asyncio.sleep(0.01)

    pytest.fail("descendant did not become ready")


def unix_process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass

    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        # No /proc (macOS), or the process was reaped between the signal probe
        # and this read; probe again to tell the two apart.
        return _probe(pid)

    fields = stat.rpartition(")")[2].split()
    return not fields or fields[0] not in ("Z", "X")


def _probe(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


async def test_terminate_process_tree(descendant_ready: Path) -> None:
    process = await proc.start([sys.executable, "-c", _PARENT, str(descendant_ready)])
    waiter = asyncio.ensure_future(proc.finish(process))
    descendant_pid = await wait_for_process_tree_pid(descendant_ready, waiter)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, 5)

    assert process.returncode == -signal.SIGKILL

    deadline = asyncio.get_running_loop().time() + 2
    while (
        unix_process_is_running(descendant_pid)
        and asyncio.get_running_loop().time() < deadline
    ):
        await asyncio.sleep(0.01)

    assert not unix_process_is_running(descendant_pid), (
        f"descendant process {descendant_pid} remained running after cancellation"
    )


async def test_gentle_cancel_lets_the_child_handle_sigterm(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    process = await proc.start(
        ["sh", "-c", f"trap 'exit 7' TERM; echo ready > {ready}; sleep 60 & wait"]
    )
    waiter = asyncio.ensure_future(proc.finish(process, gentle=True))
    while not ready.exists():
        await asyncio.sleep(0.01)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert process.returncode == 7


async def test_finish_returns_go_style_exit_text() -> None:
    process = await proc.start(["sh", "-c", "exit 3"])
    assert await proc.finish(process) == "exit status 3"


async def test_finish_returns_none_on_zero_exit() -> None:
    process = await proc.start(["true"])
    assert await proc.finish(process) is None


async def test_cancel_kills_the_whole_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    process = await proc.start(["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"])
    waiter = asyncio.ensure_future(proc.finish(process))
    while not pid_file.exists() or not pid_file.read_text().strip():
        await asyncio.sleep(0.01)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    child = int(pid_file.read_text())
    for _ in range(200):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return

        await asyncio.sleep(0.01)

    pytest.fail("the grandchild outlived the cancel")


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
