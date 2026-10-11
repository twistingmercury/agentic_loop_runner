"""Every exit code alr can return, with the result its run log records.

0 completed, 1 failed, 2 usage, 3 refused, 4 commit failed, 130 and 143 stopped.
"""

import json
import signal
from pathlib import Path
from typing import Any

import pytest
from e2e_helpers import (
    ONE_TASK,
    TWO_TASKS,
    assert_no_tmp_file,
    read_tasks_yaml,
    run_alr,
    run_args,
    shared_block,
    start_alr,
    task_states,
    wait_for_file,
    write_tasks_yaml,
)
from test_signal import stopped_at_exit

ONE_TASK_YAML = shared_block() + ONE_TASK

# The fake claude changes no files, so an action does the "work" a commit picks up.
WORK_ACTION = [{"action": "echo one > one.txt"}]
ONE_WORK_TASK_YAML = shared_block("Body.", WORK_ACTION) + ONE_TASK

# The action lets the first task through and fails the second, so a run-mode
# commit has a task that completed and a task that failed.
SECOND_TASK_FAILS = [
    {"action": "if [ -e one.txt ]; then exit 1; else echo one > one.txt; fi"}
]
SECOND_TASK_FAILS_YAML = shared_block("Body.", SECOND_TASK_FAILS) + TWO_TASKS

TWO_WORK_TASKS_YAML = (
    shared_block(
        "Body.",
        [
            {
                "action": "if [ -e one.txt ]; then echo two > two.txt; else echo one > one.txt; fi"
            }
        ],
    )
    + TWO_TASKS
)

BLOCKED_TASK_YAML = (
    shared_block()
    + """tasks:
  - id: 1
    name: First task
    prompt: Block forever.
"""
)


def run_log(folder: Path) -> list[dict[str, Any]]:
    """The run's ledger: the one run.jsonl under <folder>/logs, one entry per line."""
    (run_folder,) = (folder / "logs").iterdir()
    lines = (run_folder / "run.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def assert_run_finished(folder: Path, result: str, exit_code: int) -> None:
    last = run_log(folder)[-1]

    assert last["event"] == "run_finished"
    assert last["result"] == result
    assert last["exit"] == exit_code


def test_completed_exits_0(tmp_path: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(tmp_path, ONE_TASK_YAML)

    res = run_alr(*run_args(tasks_path))

    assert res.code == 0, res
    assert_run_finished(tmp_path, "completed", 0)


def test_failed_task_exits_1(tmp_path: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(tmp_path, ONE_TASK_YAML)

    res = run_alr(
        *run_args(tasks_path),
        env={"FAKECLAUDE_OUTPUT": '{"state":"failed","error":"boom"}\n'},
    )

    assert res.code == 1, res
    assert_run_finished(tmp_path, "failed", 1)


def test_bad_flag_exits_2(tmp_path: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(tmp_path, ONE_TASK_YAML)

    res = run_alr(*run_args(tasks_path), "--action-timeout", "soon")

    assert res.code == 2, res
    assert not (tmp_path / "logs").exists()


def test_refusal_exits_3_and_writes_no_log(git_repo: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")

    res = run_alr(*run_args(tasks_path), "--commit", "run")

    assert res.code == 3, res
    assert res.err.splitlines()[0].startswith("error: refused: dirty-tree: ")
    assert not (tasks_path.parent / "logs").exists()


def test_reset_failed_with_an_unwritable_log_folder_leaves_the_file_alone(
    tmp_path: Path, fake_claude: Path
) -> None:
    failed_yaml = (
        shared_block()
        + "tasks:\n  - {id: 1, name: First, prompt: p1, state: failed, error: boom}\n"
    )
    tasks_path = write_tasks_yaml(tmp_path, failed_yaml)
    # A file where the log folder belongs fails for every user, root included.
    (tmp_path / "logs").write_text("a file, not a folder", encoding="utf-8")

    res = run_alr(*run_args(tasks_path), "--reset-failed")

    assert res.code == 3, res
    assert res.err.splitlines()[0].startswith("error: refused: log-unwritable: ")
    assert tasks_path.read_text(encoding="utf-8") == failed_yaml


def test_reset_failed_keeps_a_users_own_tmp_file(
    tmp_path: Path, fake_claude: Path
) -> None:
    failed_yaml = (
        shared_block()
        + "tasks:\n  - {id: 1, name: First, prompt: p1, state: failed, error: boom}\n"
    )
    tasks_path = write_tasks_yaml(tmp_path, failed_yaml)
    users = tmp_path / "tasks.yaml.tmp"
    users.write_text("precious", encoding="utf-8")

    res = run_alr(*run_args(tasks_path), "--reset-failed")

    assert res.code == 0, res
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}
    assert users.read_bytes() == b"precious"
    assert_no_tmp_file(tasks_path)


def install_pre_commit_hook(git_repo: Path, script: str) -> None:
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    hook.chmod(0o700)


def test_commit_failure_exits_4(git_repo: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)
    install_pre_commit_hook(git_repo, "echo no\nexit 1")

    res = run_alr(*run_args(tasks_path), "--commit", "task")

    assert res.code == 4, res
    assert "commit failed: no" in res.err
    assert_run_finished(tasks_path.parent, "commit_failed", 4)
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "failed"}


@pytest.mark.parametrize(
    "sig", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"]
)
def test_a_signal_exits_128_plus_its_number(
    tmp_path: Path, fake_claude: Path, sig: signal.Signals
) -> None:
    """130 for SIGINT and 143 for SIGTERM, with the stop on the run log."""
    tasks_path = write_tasks_yaml(tmp_path, BLOCKED_TASK_YAML)
    ready_file = tmp_path / "ready"
    env = {"FAKECLAUDE_BLOCK": "1", "FAKECLAUDE_READY_FILE": str(ready_file)}

    with stopped_at_exit(
        start_alr(*run_args(tasks_path), env=env), ready_file
    ) as process:
        # The fake is blocking inside the session once this exists.
        wait_for_file(ready_file, 10)

        process.send_signal(sig)
        out, err = process.communicate(timeout=10)

        assert process.returncode == 128 + sig, (out, err)
        assert_run_finished(tmp_path, "stopped", 128 + sig)


def test_run_mode_failed_run_leaves_nothing_completed(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", SECOND_TASK_FAILS_YAML)

    res = run_alr(*run_args(tasks_path), "--commit", "run")

    assert res.code == 1, res
    assert list(task_states(read_tasks_yaml(tasks_path)).values()) == [
        "pending",
        "failed",
    ]


def test_run_mode_failed_commit_exits_4_and_leaves_the_last_task_failed(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", TWO_WORK_TASKS_YAML)
    install_pre_commit_hook(git_repo, "echo no\nexit 1")

    res = run_alr(*run_args(tasks_path), "--commit", "run")

    assert res.code == 4, res
    assert list(task_states(read_tasks_yaml(tasks_path)).values()) == [
        "pending",
        "failed",
    ]
    assert_run_finished(tasks_path.parent, "commit_failed", 4)


def test_task_mode_commit_failure_names_the_task_in_the_run_finished_error(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)
    install_pre_commit_hook(git_repo, "echo no\nexit 1")

    run_alr(*run_args(tasks_path), "--commit", "task")

    assert run_log(tasks_path.parent)[-1]["error"] == (
        "task 1: First task failed: commit failed: no"
    )


def test_run_mode_commit_failure_has_no_task_in_the_run_finished_error(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)
    install_pre_commit_hook(git_repo, "echo no\nexit 1")

    run_alr(*run_args(tasks_path), "--commit", "run")

    assert run_log(tasks_path.parent)[-1]["error"] == "commit failed: no"


def test_run_mode_ledger_says_passed_until_the_commit_lands(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)

    res = run_alr(*run_args(tasks_path), "--commit", "run")

    assert res.code == 0, res
    finished = [e for e in run_log(tasks_path.parent) if e["event"] == "task_finished"]
    assert [e["state"] for e in finished] == ["passed"]
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}


def test_task_mode_ledger_says_completed(git_repo: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)

    res = run_alr(*run_args(tasks_path), "--commit", "task")

    assert res.code == 0, res
    finished = [e for e in run_log(tasks_path.parent) if e["event"] == "task_finished"]
    assert [e["state"] for e in finished] == ["completed"]


@pytest.mark.parametrize("mode", ["task", "run"])
def test_a_signal_during_a_commit_exits_143(
    git_repo: Path, fake_claude: Path, mode: str
) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)
    ready_file = tasks_path.parent / "ready"
    # The pre-commit hook runs before the commit exists, so the stop arrives mid-commit.
    install_pre_commit_hook(git_repo, f"echo $$ > {ready_file}\nsleep 60")

    with stopped_at_exit(
        start_alr(*run_args(tasks_path), "--commit", mode), ready_file
    ) as process:
        wait_for_file(ready_file, 10)

        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=15)

        assert process.returncode == 143, (out, err)
        assert_run_finished(tasks_path.parent, "stopped", 143)
        assert tasks_path.read_text(encoding="utf-8") == ONE_WORK_TASK_YAML
