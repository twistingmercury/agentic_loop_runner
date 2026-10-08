"""The full-screen view, driven through a pseudo-terminal like a user would."""

import os
from pathlib import Path

import pexpect
from e2e_helpers import (
    assert_no_tmp_file,
    find_alr,
    read_tasks_yaml,
    shared_block,
    task_states,
    wait_for_file,
    write_tasks_yaml,
)

TASKS = (
    shared_block(gates=[])
    + """tasks:
  - id: 1
    name: First task
    prompt: Do it.
"""
)

CTRL_C = "\x03"


def _spawn(
    folder: Path, extra_env: dict[str, str], cwd: Path | None = None
) -> pexpect.spawn:
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
        cwd=None if cwd is None else str(cwd),
    )
    child.expect(r"\(s\)tart")
    return child


def test_view_runs_a_task_to_done(git_repo: Path, fake_claude: Path) -> None:
    folder = git_repo / "run"
    tasks_path = write_tasks_yaml(folder, TASKS)
    child = _spawn(folder, {})
    try:
        child.send("s")
        child.expect("Run done")
        # Ctrl-C quits when nothing is running; the summary is a modal, which hides plain keys.
        child.send(CTRL_C)
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)

    assert child.exitstatus == 0
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}


def test_view_stop_ends_a_blocked_run_as_stopped(
    git_repo: Path, fake_claude: Path, tmp_path: Path
) -> None:
    folder = git_repo / "run"
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

    assert child.exitstatus == 1
    # A stopped run saves nothing, so the next run picks the task up again.
    assert tasks_path.read_text(encoding="utf-8") == before
    assert_no_tmp_file(tasks_path)


def test_view_runs_to_done_outside_a_git_repository(
    outside: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(outside, TASKS)
    child = _spawn(
        outside,
        {
            # Stops git from finding a repository above the folder.
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(outside.parent),
        },
        cwd=outside,
    )
    try:
        child.send("s")
        child.expect("Run done")
        child.send(CTRL_C)
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)

    assert child.exitstatus == 0
    assert not (outside / ".git").exists()
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}
