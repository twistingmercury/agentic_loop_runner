"""The full-screen view, driven through a pseudo-terminal like a user would."""

import os
import signal
import time
from pathlib import Path

import pexpect
from e2e_helpers import (
    assert_no_tmp_file,
    find_alr,
    read_tasks_yaml,
    shared_block,
    task_states,
    wait_for_file,
    wait_process_gone,
    write_tasks_yaml,
)

TASKS = (
    shared_block(after_task=[])
    + """tasks:
  - id: 1
    name: First task
    prompt: Do it.
"""
)

CTRL_C = "\x03"


def _spawn(folder: Path, extra_env: dict[str, str]) -> pexpect.spawn:
    """Start alr on the folder and wait until the view is drawn and ready for keys.

    TERM lets Textual draw, --yolo answers that step. Keys sent before the first
    draw can be lost, so nothing is returned before "(s)tart", which the Start
    button shows, is on the screen.
    """
    env = {**os.environ, "TERM": "xterm-256color", **extra_env}
    child = pexpect.spawn(
        find_alr(),
        ["-d", str(folder), "--yolo"],
        env=env,
        encoding="utf-8",
        timeout=30,
        dimensions=(40, 140),
    )
    child.expect(r"\(s\)tart")
    return child


def test_view_runs_a_task_to_done(tmp_path: Path, fake_claude: Path) -> None:
    folder = tmp_path / "run"
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, TASKS)
    child = _spawn(folder, {})
    try:
        child.send("s")
        child.expect("Run done")
        # The summary's Exit button has focus, so Enter ends alr.
        child.send("\r")
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)

    assert child.exitstatus == 0
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}


def test_view_stop_ends_a_blocked_run_as_stopped(
    tmp_path: Path, fake_claude: Path
) -> None:
    folder = tmp_path / "run"
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, TASKS)
    before = tasks_path.read_text(encoding="utf-8")
    ready_file = tmp_path / "ready"
    child = _spawn(
        folder, {"FAKECLAUDE_BLOCK": "1", "FAKECLAUDE_READY_FILE": str(ready_file)}
    )
    try:
        child.send("s")
        wait_for_file(ready_file, 20)
        # The running mark, so the screen is drained and the app has seen the task start.
        child.expect("▶")
        child.send(CTRL_C)
        child.expect("Run stopped")
        child.send(CTRL_C)
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)

    assert child.exitstatus == 130
    # A stopped run saves nothing, so the next run picks the task up again.
    assert tasks_path.read_text(encoding="utf-8") == before
    assert_no_tmp_file(tasks_path)


def test_sighup_stops_the_view_and_its_session(
    tmp_path: Path, fake_claude: Path
) -> None:
    folder = tmp_path / "run"
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, TASKS)
    before = tasks_path.read_text(encoding="utf-8")
    ready_file = tmp_path / "ready"
    descendant_pid_file = tmp_path / "descendant.pid"
    child = _spawn(
        folder,
        {
            "FAKECLAUDE_BLOCK": "1",
            "FAKECLAUDE_READY_FILE": str(ready_file),
            "FAKECLAUDE_DESCENDANT_PID_FILE": str(descendant_pid_file),
        },
    )
    try:
        child.send("s")
        claude_pid = int(wait_for_file(ready_file, 20))
        descendant_pid = int(wait_for_file(descendant_pid_file, 5))
        # The running mark, so the screen is drained and the app has seen the task start.
        child.expect("▶")
        child.kill(signal.SIGHUP)
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)

    assert child.exitstatus == 129
    # The descendant proves the whole group was stopped, not just the direct child.
    wait_process_gone(claude_pid, 10)
    wait_process_gone(descendant_pid, 10)
    # A stopped run saves nothing, so the next run picks the task up again.
    assert tasks_path.read_text(encoding="utf-8") == before
    assert_no_tmp_file(tasks_path)


def _ignore_hangup() -> None:
    """Start the child with SIGHUP ignored, as a script that traps it would."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)


def test_a_view_that_ignored_sighup_still_stops_when_its_terminal_closes(
    tmp_path: Path, fake_claude: Path
) -> None:
    """A view cannot go on without its terminal, so it stops like any other hangup."""
    folder = tmp_path / "run"
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, TASKS)
    before = tasks_path.read_text(encoding="utf-8")
    ready_file = tmp_path / "ready"
    descendant_pid_file = tmp_path / "descendant.pid"
    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "FAKECLAUDE_BLOCK": "1",
        "FAKECLAUDE_READY_FILE": str(ready_file),
        "FAKECLAUDE_DESCENDANT_PID_FILE": str(descendant_pid_file),
    }
    child = pexpect.spawn(
        find_alr(),
        ["-d", str(folder), "--yolo"],
        env=env,
        encoding="utf-8",
        timeout=30,
        dimensions=(40, 140),
        preexec_fn=_ignore_hangup,
    )
    try:
        child.expect(r"\(s\)tart")
        child.send("s")
        claude_pid = int(wait_for_file(ready_file, 20))
        descendant_pid = int(wait_for_file(descendant_pid_file, 5))
        child.expect("▶")

        # Closing the master end hangs the terminal up for real: the kernel sends
        # SIGHUP, which alr catches although it was ignored at start.
        os.close(child.child_fd)
        deadline = time.monotonic() + 15
        while child.isalive() and time.monotonic() < deadline:
            time.sleep(0.05)

        assert not child.isalive(), "alr did not stop on the hangup"
    finally:
        if child.isalive():
            os.kill(child.pid, signal.SIGKILL)

    assert child.exitstatus == 129
    wait_process_gone(claude_pid, 10)
    wait_process_gone(descendant_pid, 10)
    assert tasks_path.read_text(encoding="utf-8") == before
    assert_no_tmp_file(tasks_path)
