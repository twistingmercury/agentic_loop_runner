"""The full-screen view, driven through a pseudo-terminal like a user would."""

import os
from pathlib import Path

import pexpect
from e2e_helpers import (
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


def _spawn(folder: Path, extra_env: dict[str, str]) -> pexpect.spawn:
    """Start alr on the folder; TERM lets Textual draw, --skip-permissions answers that step."""
    env = {**os.environ, "TERM": "xterm-256color", **extra_env}
    return pexpect.spawn(
        find_alr(),
        ["-d", str(folder), "--skip-permissions"],
        env=env,
        encoding="utf-8",
        timeout=30,
        dimensions=(40, 140),
    )


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
    ready_file = tmp_path / "ready"
    child = _spawn(
        folder, {"FAKECLAUDE_BLOCK": "1", "FAKECLAUDE_READY_FILE": str(ready_file)}
    )
    try:
        child.send("s")
        wait_for_file(ready_file, 20)
        child.send(CTRL_C)
        child.expect("Run stopped")
        child.send(CTRL_C)
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)

    assert child.exitstatus == 1
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "pending"}
