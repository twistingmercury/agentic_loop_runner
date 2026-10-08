"""--commit task: alr commits each task itself once the session and every gate pass.

The fake claude changes no files, so a gate does the "work" a commit picks up.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from e2e_helpers import (
    TWO_TASKS,
    Result,
    fake_claude_records,
    read_task_log,
    read_tasks_yaml,
    run_alr,
    run_args,
    task_errors,
    shared_block,
    task_states,
    write_tasks_yaml,
)

DIRTY_TREE_ERROR = "error: --commit needs a clean work tree; commit, stash, or remove:"
BROKEN_REPO_ERROR = "error: --commit: git rev-parse:"

# The gates are shared by both tasks, so the one gate picks its file by what
# the first task left behind: each commit then holds exactly one new file.
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


@dataclass(frozen=True)
class CommitRepo:
    """A git repository whose ignored run/ folder holds the task file."""

    dir: Path
    tasks_path: Path
    record_file: Path

    def run(self, *flags: str) -> Result:
        return run_alr(
            *run_args(self.tasks_path),
            *flags,
            env={"FAKECLAUDE_RECORD_FILE": str(self.record_file)},
        )

    def dry_run(self) -> Result:
        return run_alr(
            "-d", str(self.tasks_path.parent), "--dry-run", "--commit", "task"
        )

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=self.dir, check=True, capture_output=True, text=True
        )
        return done.stdout

    def subjects(self) -> list[str]:
        return self.git("log", "--format=%s").splitlines()

    def calls(self) -> int:
        return len(fake_claude_records(self.record_file))


def new_commit_repo(git_repo: Path, tasks_yaml: str = TWO_WORK_TASKS) -> CommitRepo:
    run = git_repo / "run"
    return CommitRepo(
        dir=git_repo,
        tasks_path=write_tasks_yaml(run, tasks_yaml),
        record_file=run / "record.ndjson",
    )


@pytest.fixture
def repo(git_repo: Path, fake_claude: Path) -> CommitRepo:
    return new_commit_repo(git_repo)


def break_repo(repo: CommitRepo) -> None:
    """Make git refuse to read the repository without making it look like "not a repository"."""
    with (repo.dir / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write("[broken\n")


def test_commit_one_commit_per_completed_task(repo: CommitRepo) -> None:
    res = repo.run("--commit", "task")

    assert res.code == 0, res
    assert repo.subjects() == ["Second task", "First task", "init"]
    assert repo.git("show", "--name-only", "--format=", "HEAD") == "two.txt\n"
    assert repo.git("status", "--porcelain") == "", "nothing may be left uncommitted"
    assert "commit: First task\n" in res.out
    assert "commit: Second task\n" in res.out
    assert task_states(read_tasks_yaml(repo.tasks_path)) == {
        1: "completed",
        2: "completed",
    }


def test_commit_without_the_flag_git_is_never_touched(repo: CommitRepo) -> None:
    (repo.dir / "stray.txt").write_text("stray\n", encoding="utf-8")

    res = repo.run()

    assert res.code == 0, res
    assert repo.subjects() == ["init"]
    assert "commit:" not in res.out


def test_commit_none_git_is_never_touched(repo: CommitRepo) -> None:
    (repo.dir / "stray.txt").write_text("stray\n", encoding="utf-8")

    res = repo.run("--commit", "none")

    assert res.code == 0, res
    assert repo.subjects() == ["init"]
    assert "commit:" not in res.out


def test_commit_dirty_tree_refuses_to_start(repo: CommitRepo) -> None:
    (repo.dir / "stray.txt").write_text("stray\n", encoding="utf-8")

    res = repo.run("--commit", "task")

    assert res.code == 1, res
    assert f"{DIRTY_TREE_ERROR}\n?? stray.txt\n" in res.err
    assert repo.calls() == 0, "claude must not run"
    assert repo.tasks_path.read_text(encoding="utf-8") == TWO_WORK_TASKS


def test_commit_task_file_git_can_see_is_refused(repo: CommitRepo) -> None:
    visible = write_tasks_yaml(repo.dir, TWO_WORK_TASKS)

    res = run_alr(
        *run_args(visible),
        "--commit",
        "task",
        env={"FAKECLAUDE_RECORD_FILE": str(repo.record_file)},
    )

    assert res.code == 1, res
    assert (
        "error: --commit needs the task file ignored by git or outside the repository: tasks.yaml\n"
        in res.err
    )
    assert repo.calls() == 0, "claude must not run"
    assert repo.subjects() == ["init"]


def test_commit_broken_repository_is_an_error_run(repo: CommitRepo) -> None:
    break_repo(repo)

    res = repo.run("--commit", "task")

    assert res.code == 1, res
    assert BROKEN_REPO_ERROR in res.err
    assert repo.calls() == 0, "claude must not run"


def test_commit_broken_repository_is_an_error_dry_run(repo: CommitRepo) -> None:
    break_repo(repo)

    res = repo.dry_run()

    assert res.code == 1, res
    assert BROKEN_REPO_ERROR in res.err
    assert "is valid" not in res.out


def test_commit_failed_gate_leaves_no_commit_and_blocks_the_rerun(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_yaml = (
        shared_block("Body.", [{"cmd": "echo one > one.txt; exit 1"}])
        + """tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
"""
    )
    repo = new_commit_repo(git_repo, tasks_yaml)

    res = repo.run("--commit", "task")

    assert res.code != 0, res
    assert repo.subjects() == ["init"]
    assert (repo.dir / "one.txt").exists(), "the failed task's work stays in the tree"
    assert task_states(read_tasks_yaml(repo.tasks_path))[1] == "failed"

    # A person resets the task to pending without cleaning up: the next
    # --commit run must refuse the leftovers.
    repo.tasks_path.write_text(tasks_yaml, encoding="utf-8")
    again = repo.run("--commit", "task")

    assert again.code == 1, again
    assert f"{DIRTY_TREE_ERROR}\n?? one.txt\n" in again.err
    assert repo.calls() == 1, "the refused run must not start claude"


def test_commit_rejected_commit_fails_the_task(repo: CommitRepo) -> None:
    hook = repo.dir / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\necho hook-says-no >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o700)

    res = repo.run("--commit", "task")

    assert res.code != 0, res
    assert "hook-says-no" in res.err
    assert "task 1: First task failed: commit failed: exit status 1" in res.err
    assert repo.subjects() == ["init"]

    after = read_tasks_yaml(repo.tasks_path)
    assert task_errors(after)[1] == "commit failed: exit status 1"
    assert task_states(after)[2] == "pending"
    assert repo.calls() == 1, "the run must stop before the second task"


def test_commit_hook_output_is_in_the_task_log(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_yaml = (
        shared_block("Body.", [{"cmd": "echo one > one.txt"}])
        + """tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
"""
    )
    repo = new_commit_repo(git_repo, tasks_yaml)
    hook = repo.dir / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\necho hook-said-this\n", encoding="utf-8")
    hook.chmod(0o700)

    res = repo.run("--commit", "task")

    assert res.code == 0, res
    assert repo.subjects() == ["First task", "init"]
    # Git sends a hook's stdout to its own stderr.
    assert "hook-said-this" in res.err
    assert "hook-said-this" in read_task_log(repo.tasks_path, 1)


def test_commit_outside_a_repository_runs_without_committing(
    tmp_path: Path, fake_claude: Path
) -> None:
    folder = tmp_path.resolve() / "plain"
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, TWO_WORK_TASKS)

    res = run_alr(
        *run_args(tasks_path),
        "--commit",
        "task",
        cwd=folder,
        # Stops git from finding a repository above the folder.
        env={
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(folder.parent),
        },
    )

    assert res.code == 0, res
    assert "commit: not a git repository, nothing will be committed\n" in res.out
    assert not (folder / ".git").exists()
    assert task_states(read_tasks_yaml(tasks_path))[2] == "completed"


def test_commit_dry_run_clean(repo: CommitRepo) -> None:
    res = repo.dry_run()

    assert res.code == 0, res
    assert res.out.endswith(f"commit: {repo.dir}\n{repo.tasks_path} is valid\n")
    assert repo.subjects() == ["init"]


def test_commit_dry_run_dirty(repo: CommitRepo) -> None:
    (repo.dir / "stray.txt").write_text("stray\n", encoding="utf-8")

    res = repo.dry_run()

    assert res.code == 1, res
    assert f"{DIRTY_TREE_ERROR}\n?? stray.txt\n" in res.err
    assert "is valid" not in res.out
