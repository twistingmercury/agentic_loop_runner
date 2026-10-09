"""--commit run: alr makes one commit for the whole run, after the last task completes.

The fake claude changes no files, so a gate does the "work" a commit picks up.
"""

import signal
import subprocess
from pathlib import Path

import pytest

from e2e_helpers import (
    TWO_TASKS,
    Result,
    read_tasks_yaml,
    run_alr,
    run_args,
    shared_block,
    start_alr,
    task_states,
    wait_for_file,
    write_tasks_yaml,
)
from test_outcomes import assert_run_finished
from test_signal import stopped_at_exit

# The one gate picks its file by what the first task left behind, so the two
# tasks leave two files for the single commit to pick up.
TWO_WORK_TASKS = (
    shared_block(
        "Body.",
        [
            {
                "cmd": "if [ -e one.txt ]; then echo two > two.txt; else echo one > one.txt; fi"
            }
        ],
    )
    + TWO_TASKS
)


DIRTY_TREE_ERROR = (
    "error: refused: dirty-tree: --commit needs a clean work tree; "
    "commit, stash, or remove:"
)


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return done.stdout


@pytest.fixture
def tasks_path(git_repo: Path, fake_claude: Path) -> Path:
    return write_tasks_yaml(git_repo / "run", TWO_WORK_TASKS)


def run_with(tasks_path: Path, *flags: str) -> Result:
    return run_alr(*run_args(tasks_path), *flags)


def test_commit_run_makes_one_commit_for_the_run(
    git_repo: Path, tasks_path: Path
) -> None:
    res = run_with(tasks_path, "--commit", "run")

    assert res.code == 0, res
    assert git(git_repo, "log", "--format=%s").splitlines() == [
        "alr: 2 tasks completed",
        "init",
    ]
    assert git(git_repo, "status", "--porcelain") == "", "nothing may be left behind"
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed", 2: "completed"}


def test_commit_run_dirty_tree_refuses_to_start(
    git_repo: Path, tasks_path: Path
) -> None:
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")

    res = run_with(tasks_path, "--commit", "run")

    assert res.code == 3, res
    assert f"{DIRTY_TREE_ERROR}\n?? stray.txt\n" in res.err
    assert git(git_repo, "log", "--format=%s").splitlines() == ["init"]
    assert tasks_path.read_text(encoding="utf-8") == TWO_WORK_TASKS


def test_commit_after_all_is_an_unknown_flag(git_repo: Path, tasks_path: Path) -> None:
    """--commit run replaced it; exit 2 and the unknown-flag line keep the old name from coming back as an alias."""
    res = run_with(tasks_path, "--commit-after-all")

    assert res.code == 2, res
    assert "unrecognized arguments: --commit-after-all\n" in res.err
    assert git(git_repo, "log", "--format=%s").splitlines() == ["init"]


def test_bare_commit_is_a_usage_error(git_repo: Path, tasks_path: Path) -> None:
    res = run_with(tasks_path, "--commit")

    assert res.code == 2, res
    assert "argument --commit: expected one argument\n" in res.err
    assert git(git_repo, "log", "--format=%s").splitlines() == ["init"]


def test_commit_with_an_unknown_mode_is_a_usage_error(
    git_repo: Path, tasks_path: Path
) -> None:
    res = run_with(tasks_path, "--commit", "sometimes")

    assert res.code == 2, res
    assert "argument --commit: invalid choice: 'sometimes'" in res.err
    assert "(choose from none, task, run)" in res.err
    assert git(git_repo, "log", "--format=%s").splitlines() == ["init"]


def test_commit_followed_by_a_flag_takes_no_value(
    git_repo: Path, tasks_path: Path
) -> None:
    res = run_with(tasks_path, "--commit", "--plain")

    assert res.code == 2, res
    assert "argument --commit: expected one argument\n" in res.err


def test_commit_run_sigterm_after_the_commit_landed_keeps_the_tasks_completed(
    git_repo: Path, tasks_path: Path
) -> None:
    hook = git_repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(exist_ok=True)
    ready = tasks_path.parent / "ready"
    # Git has made the commit by the time a post-commit hook runs, and it waits
    # for the hook, so the stop arrives with the commit already in the history.
    hook.write_text(f"#!/bin/sh\necho $$ > {ready}\nsleep 60\n", encoding="utf-8")
    hook.chmod(0o700)
    process = start_alr(*run_args(tasks_path), "--commit", "run")

    with stopped_at_exit(process, ready):
        wait_for_file(ready, 10)
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=15)

    assert process.returncode == 143, (out, err)
    assert git(git_repo, "log", "--format=%s").splitlines() == [
        "alr: 2 tasks completed",
        "init",
    ]
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed", 2: "completed"}
    assert_run_finished(tasks_path.parent, "stopped", 143)
