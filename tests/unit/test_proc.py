"""Process groups: a cancel must stop a child and everything it started."""

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest
from helpers import wait_for_text, wait_until_gone

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


async def _kill_holder_and_let_pipes_close(pid_file: Path) -> None:
    os.kill(int(pid_file.read_text()), signal.SIGKILL)
    # Closing the pipes lets asyncio finish with the transport; one left open
    # would only be cleaned up after the loop is gone, with a warning.
    await asyncio.sleep(0.05)


async def test_gentle_finish_cuts_off_a_drain_held_open_by_a_background_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)
    pid_file = tmp_path / "holder.pid"
    process = await proc.start(
        ["sh", "-c", f"sleep 3 & echo $! > {pid_file}; echo line"],
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
        await _kill_holder_and_let_pipes_close(pid_file)

    assert result is None
    assert lines == [b"line\n"]
    assert elapsed < 2


async def test_cancel_returns_when_an_escaped_descendant_holds_the_pipe(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "escaped.pid"
    # setsid takes the sleeper out of the group the stop kills, and it keeps
    # the stdout pipe; short, so a failing run cleans up on its own.
    process = await proc.start(
        ["sh", "-c", f"setsid sleep 3 & echo $! > {pid_file}; sleep 60"],
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
        await _kill_holder_and_let_pipes_close(pid_file)
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
