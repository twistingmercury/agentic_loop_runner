"""--commit-after-all: alr makes one commit for the whole run, after the last task completes.

The fake claude changes no files, so a gate does the "work" a commit picks up.
"""

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
    task_states,
    write_tasks_yaml,
)

BOTH_FLAGS_ERROR = "--commit and --commit-after-all cannot be used together"

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


def test_commit_after_all_makes_one_commit_for_the_run(
    git_repo: Path, tasks_path: Path
) -> None:
    res = run_with(tasks_path, "--commit-after-all")

    assert res.code == 0, res
    assert git(git_repo, "log", "--format=%s").splitlines() == [
        "alr: 2 tasks completed",
        "init",
    ]
    assert git(git_repo, "status", "--porcelain") == "", "nothing may be left behind"
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed", 2: "completed"}


def test_commit_and_commit_after_all_together_is_a_usage_error(
    git_repo: Path, tasks_path: Path
) -> None:
    res = run_with(tasks_path, "--commit", "--commit-after-all")

    assert res.code == 1, res
    assert f"error: {BOTH_FLAGS_ERROR}\n" in res.err
    assert git(git_repo, "log", "--format=%s").splitlines() == ["init"]
