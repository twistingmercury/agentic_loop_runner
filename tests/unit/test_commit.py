"""--commit: the repository checks before a run and the commit after each completed task."""

import asyncio
import errno
import fcntl
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    commit_subjects,
    fake_claude_records,
    git,
    run_loop,
    until,
    wait_for_file,
    wait_for_text,
    write_nested_file,
)

from textual.app import App, ComposeResult

from alr import tasks
from alr.engine import commit, loop, proc
from alr.engine.commit import (
    CommitMode,
    Repo,
    check_log_dir,
    check_unlocked,
    find_repo,
    head,
    held_lock,
    open_repo,
    recheck_ignored,
    work_tree_root,
)
from alr.engine.stop import Stop
from alr.errors import (
    CommitFailedError,
    RefusedError,
    RunCancelledError,
    RunError,
    TaskFileError,
)
from alr.engine.frontend import FrontEnd
from alr.plain.frontend import PlainFrontEnd
from alr.record.events import Event, RunDone
from alr.stream.frontend import StreamFrontEnd
from alr.tasks import (
    COMPLETED,
    FAILED,
    PENDING,
    Gate,
    RunResult,
    Shared,
    Task,
    TaskList,
)
from alr.tui.panes import TasksPane

RUN_DIR = "20261001T140211"


def _ignore_and_commit(repo: Path, *patterns: str) -> None:
    """Replace .gitignore and commit it, so the tree stays clean."""
    write_nested_file(repo / ".gitignore", "\n".join(patterns) + "\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore")


def _task_file(repo: Path) -> Path:
    """Where tests keep the task file: inside the ignored run/."""
    return repo / "run" / "tasks.yaml"


def _repo_of(path: Path) -> Repo:
    """The Repo a run finds in path, whose git directory is its own .git."""
    return Repo(root=str(path), git_dir=str(path / ".git"))


def _break_config(repo: Path) -> None:
    with (repo / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write("[broken\n")


async def test_open_repo_outside_a_repository_is_nil(
    outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)

    assert await open_repo(str(_task_file(outside))) is None


async def test_open_repo_git_not_installed_is_nil(
    git_repo: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(outside))

    assert await open_repo(str(_task_file(git_repo))) is None


def _dirty_modified(repo: Path) -> None:
    write_nested_file(repo / "seed.txt", "changed\n")


def _dirty_staged(repo: Path) -> None:
    write_nested_file(repo / "new.txt", "new\n")
    git(repo, "add", "new.txt")


def _dirty_untracked(repo: Path) -> None:
    write_nested_file(repo / "stray.txt", "stray\n")


def _dirty_new_directory(repo: Path) -> None:
    write_nested_file(repo / "docs" / "notes.md", "notes\n")


def _dirty_non_ascii(repo: Path) -> None:
    write_nested_file(repo / "café.txt", "latte\n")


@pytest.mark.parametrize(
    ("dirty", "want_line"),
    [
        pytest.param(_dirty_modified, " M seed.txt", id="modified"),
        pytest.param(_dirty_staged, "A  new.txt", id="staged"),
        pytest.param(_dirty_untracked, "?? stray.txt", id="untracked"),
        pytest.param(
            _dirty_new_directory,
            "?? docs/notes.md",
            id="untracked file in a new directory is named",
        ),
        pytest.param(_dirty_non_ascii, "?? café.txt", id="non-ASCII path is readable"),
    ],
)
async def test_open_repo_dirty_tree_is_refused(
    git_repo: Path, dirty: Callable[[Path], None], want_line: str
) -> None:
    tasks_file = write_nested_file(_task_file(git_repo), "tasks: []\n")
    dirty(git_repo)

    with pytest.raises(RefusedError) as caught:
        await open_repo(str(tasks_file))

    assert caught.value.reason == "dirty-tree"
    assert str(caught.value) == (
        "refused: dirty-tree: --commit needs a clean work tree; "
        f"commit, stash, or remove:\n{want_line}"
    )


async def test_open_repo_ignored_files_are_clean(git_repo: Path) -> None:
    write_nested_file(git_repo / "run" / "notes.md", "notes\n")
    tasks_file = write_nested_file(_task_file(git_repo), "tasks: []\n")

    assert await open_repo(str(tasks_file)) is not None


def _seen_untracked(repo: Path, outside: Path) -> str:
    return str(write_nested_file(repo / "tasks.yaml", "tasks: []\n"))


def _seen_tracked(repo: Path, outside: Path) -> str:
    path = write_nested_file(repo / "tasks.yaml", "tasks: []\n")
    git(repo, "add", "tasks.yaml")
    git(repo, "commit", "-q", "-m", "track tasks")
    return str(path)


def _seen_staged(repo: Path, outside: Path) -> str:
    path = write_nested_file(repo / "tasks.yaml", "tasks: []\n")
    git(repo, "add", "tasks.yaml")
    return str(path)


def _seen_tracked_matching_a_pattern(repo: Path, outside: Path) -> str:
    path = _seen_tracked(repo, outside)
    _ignore_and_commit(repo, "run/", "tasks.yaml")
    return path


def _seen_relative(repo: Path, outside: Path) -> str:
    write_nested_file(repo / "docs" / "tasks.yaml", "tasks: []\n")
    return "docs/tasks.yaml"


def _seen_symlink(repo: Path, outside: Path) -> str:
    target = write_nested_file(outside / "tasks.yaml", "tasks: []\n")
    link = repo / "link.yaml"
    link.symlink_to(target)
    return str(link)


@pytest.mark.parametrize(
    ("setup", "want_rel"),
    [
        pytest.param(_seen_untracked, "tasks.yaml", id="untracked and not ignored"),
        pytest.param(_seen_tracked, "tasks.yaml", id="tracked"),
        pytest.param(_seen_staged, "tasks.yaml", id="staged"),
        pytest.param(
            _seen_tracked_matching_a_pattern,
            "tasks.yaml",
            id="tracked but matching an ignore pattern",
        ),
        pytest.param(_seen_relative, "docs/tasks.yaml", id="relative path"),
        pytest.param(
            _seen_symlink,
            "link.yaml",
            id="untracked symlink to a file outside the tree",
        ),
    ],
)
async def test_open_repo_task_file_must_be_ignored_or_outside(
    git_repo: Path, outside: Path, setup: Callable[[Path, Path], str], want_rel: str
) -> None:
    tasks_file = setup(git_repo, outside)

    with pytest.raises(RefusedError) as caught:
        await open_repo(tasks_file)

    assert caught.value.reason == "task-file-visible"
    assert str(caught.value) == (
        "refused: task-file-visible: --commit needs the task file ignored by git "
        f"or outside the repository: {want_rel}"
    )


def _unseen_directory_pattern(repo: Path, outside: Path) -> str:
    return str(write_nested_file(_task_file(repo), "tasks: []\n"))


def _unseen_file_pattern(repo: Path, outside: Path) -> str:
    _ignore_and_commit(repo, "tasks.yaml")
    return str(write_nested_file(repo / "tasks.yaml", "tasks: []\n"))


def _unseen_outside(repo: Path, outside: Path) -> str:
    return str(write_nested_file(outside / "tasks.yaml", "tasks: []\n"))


def _unseen_not_yet_created(repo: Path, outside: Path) -> str:
    return str(_task_file(repo))


def _unseen_directory_missing(repo: Path, outside: Path) -> str:
    return str(repo / "run" / "later" / "tasks.yaml")


def _unseen_relative(repo: Path, outside: Path) -> str:
    write_nested_file(_task_file(repo), "tasks: []\n")
    return "run/tasks.yaml"


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(_unseen_directory_pattern, id="ignored by a directory pattern"),
        pytest.param(_unseen_file_pattern, id="ignored by a pattern naming the file"),
        pytest.param(_unseen_outside, id="outside the tree"),
        pytest.param(
            _unseen_not_yet_created, id="not yet created in an ignored directory"
        ),
        pytest.param(
            _unseen_directory_missing,
            id="not yet created in a directory that does not exist",
        ),
        pytest.param(_unseen_relative, id="relative and ignored"),
    ],
)
async def test_open_repo_task_file_ignored_or_outside_is_accepted(
    git_repo: Path, outside: Path, setup: Callable[[Path, Path], str]
) -> None:
    assert await open_repo(setup(git_repo, outside)) == _repo_of(git_repo)


async def test_open_repo_task_file_reached_through_a_symlinked_directory(
    git_repo: Path, outside: Path
) -> None:
    write_nested_file(_task_file(git_repo), "tasks: []\n")
    alias = outside / "alias"
    alias.symlink_to(git_repo / "run")

    assert await open_repo(str(alias / "tasks.yaml")) is not None, (
        "the parent directory resolves into the tree, where run/ is ignored"
    )


async def test_open_repo_from_subdirectory_covers_whole_tree(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = git_repo / "sub"
    sub.mkdir()
    write_nested_file(git_repo / "stray.txt", "stray\n")
    monkeypatch.chdir(sub)

    with pytest.raises(RefusedError, match=r"\?\? stray\.txt"):
        await open_repo(str(_task_file(git_repo)))


async def test_open_repo_from_subdirectory_checks_the_task_file_from_the_root(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Anchored to the root: the fixture's bare "run/" also matches sub/run/, which
    # is where a git started in sub would look for the root-relative path.
    _ignore_and_commit(git_repo, "/run/")
    sub = git_repo / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)

    assert await open_repo(str(_task_file(git_repo))) == _repo_of(git_repo)


async def test_a_broken_repository_is_refused_with_git_error(git_repo: Path) -> None:
    _break_config(git_repo)

    with pytest.raises(RefusedError) as caught:
        await open_repo(str(_task_file(git_repo)))

    assert caught.value.reason == "git-error"
    assert str(caught.value).startswith("refused: git-error: --commit: git rev-parse: ")


async def test_a_git_dir_git_cannot_name_is_refused_with_git_error(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_capture = proc.capture

    async def failing_for_the_git_dir(
        argv: Sequence[str], **kwargs: Any
    ) -> tuple[int, str, str]:
        if "--absolute-git-dir" in argv:
            return 128, "", "fatal: no git dir for you\n"

        return await real_capture(argv, **kwargs)

    monkeypatch.setattr(proc, "capture", failing_for_the_git_dir)

    with pytest.raises(RefusedError) as caught:
        await find_repo(str(_task_file(git_repo)))

    assert caught.value.reason == "git-error"
    assert str(caught.value) == (
        "refused: git-error: --commit: git rev-parse: no git dir for you"
    )
    assert list(git_repo.rglob("alr.lock")) == []


async def test_ignored_refuses_with_git_error_when_check_ignore_fails(
    git_repo: Path,
) -> None:
    repo = await _open(_task_file(git_repo))
    _break_config(git_repo)

    with pytest.raises(RefusedError) as caught:
        await commit._ignored(repo, "logs")

    assert caught.value.reason == "git-error"
    assert str(caught.value).startswith(
        "refused: git-error: --commit: git check-ignore: "
    )


async def test_open_repo_status_failure_carries_gits_message(
    git_repo: Path, outside: Path
) -> None:
    (git_repo / ".git" / "index").write_bytes(b"garbage")

    # Outside the tree, so the task file check does not touch the index first.
    with pytest.raises(RefusedError) as caught:
        await open_repo(str(outside / "tasks.yaml"))

    assert caught.value.reason == "git-error"
    message = str(caught.value)
    assert "git status" in message
    # git's own complaint about the bad index, not just its exit status.
    assert "index" in message


def _work_gate(name: str) -> Gate:
    """Stand in for a session's work: the fake claude changes no files, so a gate writes one."""
    return Gate(cmd=f"echo work > {name}")


def _gated_task(*task_gates: Gate) -> TaskList:
    return TaskList(
        shared=Shared(prompt="shared", gates=list(task_gates)),
        tasks=[Task(id=1, name="First", prompt="p1")],
    )


def _two_tasks(*task_gates: Gate) -> TaskList:
    return TaskList(
        shared=Shared(prompt="shared", gates=list(task_gates)),
        tasks=[
            Task(id=1, name="First", prompt="p1"),
            Task(id=2, name="Second", prompt="p2"),
        ],
    )


async def _open(tasks_file: Path) -> Repo:
    """Open the test's repository the way a --commit run does."""
    repo = await open_repo(str(tasks_file))
    assert repo is not None
    return repo


def _head_files(repo: Path) -> list[str]:
    """The files the newest commit changed."""
    return sorted(git(repo, "show", "--name-only", "--format=", "HEAD").split())


def _head_staged(repo: Path) -> list[str]:
    """The files staged for the next commit."""
    return sorted(git(repo, "diff", "--cached", "--name-only").split())


def _install_hook(repo: Path, script: str, name: str = "pre-commit") -> None:
    hook = write_nested_file(repo / ".git" / "hooks" / name, f"#!/bin/sh\n{script}\n")
    hook.chmod(0o700)


def _saved_states(path: Path) -> list[tuple[str, str]]:
    saved = tasks.parse_tasks(path.read_text(encoding="utf-8"))
    return [(task.state, task.error) for task in saved.tasks]


# The stream half of gralph's table belongs with the stream front end.
@pytest.mark.usefixtures("fake_claude")
async def test_run_commits_one_commit_per_completed_task(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)
    # Each run writes the next free work-<n>.txt, so each task's commit holds
    # a file of its own.
    numbered = Gate(
        cmd="n=1; while [ -e work-$n.txt ]; do n=$((n+1)); done; echo work > work-$n.txt"
    )

    await run_loop(_two_tasks(numbered), tasks_file, repo=await _open(tasks_file))

    assert commit_subjects(git_repo) == ["Second", "First", "init"]
    assert _head_files(git_repo) == ["work-2.txt"]
    assert git(git_repo, "status", "--porcelain") == "", (
        "nothing may be left uncommitted"
    )
    assert _saved_states(tasks_file) == [(COMPLETED, ""), (COMPLETED, "")]


@pytest.mark.usefixtures("fake_claude")
async def test_run_nil_repo_never_commits(git_repo: Path) -> None:
    await run_loop(_gated_task(_work_gate("one.txt")), _task_file(git_repo))

    assert commit_subjects(git_repo) == ["init"]
    assert (git_repo / "one.txt").exists()


@pytest.mark.usefixtures("fake_claude")
async def test_run_nothing_to_commit_still_completes(
    git_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    tasks_file = _task_file(git_repo)

    await run_loop(_gated_task(), tasks_file, repo=await _open(tasks_file))

    # A commit line with no commit behind it would tell the user about a
    # commit git never made.
    assert "commit:" not in capfd.readouterr().out, (
        "nothing staged must announce nothing"
    )
    assert commit_subjects(git_repo) == ["init"], "no empty commit"
    assert _saved_states(tasks_file) == [(COMPLETED, "")]


@pytest.mark.usefixtures("fake_claude")
async def test_run_failed_gate_leaves_changes_uncommitted(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task(Gate(cmd="echo work > one.txt; exit 1"))

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file, repo=await _open(tasks_file))

    assert 'gate "echo work > one.txt; exit 1" failed' in str(caught.value)
    assert commit_subjects(git_repo) == ["init"]
    assert (git_repo / "one.txt").exists(), (
        "a failed task's changes stay in the work tree"
    )


@pytest.mark.usefixtures("fake_claude")
async def test_run_session_failure_skips_commit(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", '{"state":"failed","error":"nope"}')
    tasks_file = _task_file(git_repo)
    repo = await _open(tasks_file)
    write_nested_file(git_repo / "left.txt", "left by the session\n")

    with pytest.raises(RunError, match="nope"):
        await run_loop(_gated_task(), tasks_file, repo=repo)

    assert commit_subjects(git_repo) == ["init"]


@pytest.mark.usefixtures("fake_claude")
async def test_run_commit_failure_fails_task_and_stops_run(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_hook(git_repo, "echo hook-says-no >&2\nexit 1")
    record = tmp_path / "record.log"
    monkeypatch.setenv("FAKECLAUDE_RECORD_FILE", str(record))
    tasks_file = _task_file(git_repo)
    want_error = "commit failed: hook-says-no"

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(
            _two_tasks(_work_gate("one.txt")), tasks_file, repo=await _open(tasks_file)
        )

    assert caught.value.exit_code == 4
    assert str(caught.value) == f"task 1: First failed: {want_error}"
    assert commit_subjects(git_repo) == ["init"]
    assert len(record.read_text().splitlines()) == 1, "the second task must never run"
    assert _saved_states(tasks_file) == [(FAILED, want_error), (PENDING, "")]
    assert _head_staged(git_repo) == ["one.txt"], "the work stays staged"


@pytest.mark.usefixtures("fake_claude")
async def test_run_commit_failure_reason_is_trimmed_to_200(git_repo: Path) -> None:
    _install_hook(git_repo, "printf 'x%.0s' $(seq 300) >&2\nexit 1")
    tasks_file = _task_file(git_repo)

    with pytest.raises(CommitFailedError):
        await run_loop(
            _two_tasks(_work_gate("one.txt")), tasks_file, repo=await _open(tasks_file)
        )

    error = _saved_states(tasks_file)[0][1]
    assert error == "commit failed: " + "x" * 200


@pytest.mark.usefixtures("fake_claude")
async def test_run_commit_failure_without_output_says_the_exit(
    git_repo: Path,
) -> None:
    _install_hook(git_repo, "exit 1")
    tasks_file = _task_file(git_repo)

    with pytest.raises(CommitFailedError):
        await run_loop(
            _two_tasks(_work_gate("one.txt")), tasks_file, repo=await _open(tasks_file)
        )

    assert _saved_states(tasks_file)[0] == (FAILED, "commit failed: exit status 1")


# The task file is rewritten after every task and git must never see it, so
# a gate that appends its own ignore pattern or edits .gitignore must not
# disturb the commit: the edit is simply part of the task's work.
@pytest.mark.usefixtures("fake_claude")
async def test_run_commit_survives_a_gate_editing_gitignore(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task(
        Gate(cmd="echo 'run/' >> .gitignore"),
        Gate(cmd="echo '*.log' >> .gitignore"),
        _work_gate("one.txt"),
    )

    await run_loop(task_list, tasks_file, repo=await _open(tasks_file))

    assert commit_subjects(git_repo) == ["First", "init"]
    assert _head_files(git_repo) == [".gitignore", "one.txt"]
    assert git(git_repo, "status", "--porcelain") == ""
    assert _saved_states(tasks_file) == [(COMPLETED, "")]


# An ignore rule is all that keeps the task file and the log folder out of
# `git add -A`, and a task's own commands can edit it: every commit checks the
# rules again right before it stages anything.
_UNIGNORE_TASK_FILE = "commit failed: task file visible: run/tasks.yaml"
# Empties the .gitignore that hides run/, then does the task's work.
_UNIGNORE_ALL = Gate(cmd=": > .gitignore; echo work > one.txt")


@pytest.mark.usefixtures("fake_claude")
async def test_a_task_that_unignores_the_task_file_fails_its_commit(
    git_repo: Path,
) -> None:
    tasks_file = _task_file(git_repo)

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(
            _gated_task(_UNIGNORE_ALL), tasks_file, repo=await _open(tasks_file)
        )

    assert caught.value.exit_code == 4
    assert str(caught.value) == f"task 1: First failed: {_UNIGNORE_TASK_FILE}"
    assert commit_subjects(git_repo) == ["init"], "nothing may be committed"
    assert _head_staged(git_repo) == [], "nothing may be staged"
    assert _saved_states(tasks_file) == [(FAILED, _UNIGNORE_TASK_FILE)]


@pytest.mark.usefixtures("fake_claude")
async def test_a_task_that_unignores_the_log_folder_fails_its_commit(
    git_repo: Path,
) -> None:
    _ignore_and_commit(git_repo, "run/", "logs/")
    subjects = commit_subjects(git_repo)
    tasks_file = _task_file(git_repo)
    # run/ stays hidden, so only the log folder becomes visible.
    unignore = Gate(cmd="echo 'run/' > .gitignore; echo work > one.txt")
    want_error = "commit failed: log folder visible: logs"

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(
            _gated_task(unignore),
            tasks_file,
            repo=await _open(tasks_file),
            run_dir=str(git_repo / "logs" / RUN_DIR),
        )

    assert caught.value.exit_code == 4
    assert str(caught.value) == f"task 1: First failed: {want_error}"
    assert commit_subjects(git_repo) == subjects, "nothing may be committed"
    assert _head_staged(git_repo) == [], "nothing may be staged"
    assert _saved_states(tasks_file) == [(FAILED, want_error)]


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_unignored_file_fails_the_final_commit(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(
            _two_tasks(_UNIGNORE_ALL),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
        )

    assert caught.value.exit_code == 4
    assert str(caught.value) == _UNIGNORE_TASK_FILE
    assert commit_subjects(git_repo) == ["init"], "nothing may be committed"
    assert _head_staged(git_repo) == [], "nothing may be staged"
    assert _saved_states(tasks_file) == [(PENDING, ""), (FAILED, _UNIGNORE_TASK_FILE)]


_RECHECK_FAILURE = "commit failed: git check-ignore: "


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("commit_mode", "want_states"),
    [
        pytest.param(CommitMode.TASK, [FAILED, PENDING], id="task mode"),
        pytest.param(CommitMode.RUN, [PENDING, FAILED], id="run mode"),
    ],
)
async def test_a_git_failure_in_the_recheck_fails_the_commit(
    git_repo: Path, commit_mode: CommitMode, want_states: list[str]
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _two_tasks(Gate(cmd="echo work > one.txt; echo garbage > .git/index"))

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(
            task_list,
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=commit_mode,
        )

    assert caught.value.exit_code == 4
    assert _RECHECK_FAILURE in str(caught.value)
    assert "--commit: " not in str(caught.value)
    assert commit_subjects(git_repo) == ["init"], "nothing may be committed"
    states = _saved_states(tasks_file)
    assert [state for state, _ in states] == want_states
    assert all(
        error.startswith(_RECHECK_FAILURE) for state, error in states if state == FAILED
    )


@pytest.mark.parametrize(
    ("ignore", "log_folder", "want"),
    [
        pytest.param("run/\nlogs/\n", True, None, id="both hidden"),
        pytest.param("run/\n", False, None, id="no log folder to check"),
        pytest.param(
            "logs/\n", True, "task file visible: run/tasks.yaml", id="task file"
        ),
        pytest.param("run/\n", True, "log folder visible: logs", id="log folder"),
        pytest.param(
            "", True, "task file visible: run/tasks.yaml", id="the task file is first"
        ),
    ],
)
async def test_recheck_ignored_names_the_path_git_can_see(
    git_repo: Path, ignore: str, log_folder: bool, want: str | None
) -> None:
    repo = await _open(_task_file(git_repo))
    # After the repository opened, as a task's own commands would.
    (git_repo / ".gitignore").write_text(ignore, encoding="utf-8")
    run_dir = str(git_repo / "logs" / RUN_DIR) if log_folder else None

    reason = await recheck_ignored(repo, str(_task_file(git_repo)), run_dir)

    assert reason == want


async def test_recheck_ignored_needs_no_rule_for_paths_outside_the_tree(
    git_repo: Path, outside: Path
) -> None:
    repo = await _open(_task_file(git_repo))
    (git_repo / ".gitignore").write_text("", encoding="utf-8")

    reason = await recheck_ignored(
        repo, str(outside / "tasks.yaml"), str(outside / "logs" / RUN_DIR)
    )

    assert reason is None


async def test_recheck_ignored_gives_a_failing_check_ignore_as_the_reason(
    git_repo: Path,
) -> None:
    repo = await _open(_task_file(git_repo))
    _break_config(git_repo)

    reason = await recheck_ignored(repo, str(_task_file(git_repo)), None)

    assert reason is not None
    assert reason.startswith("git check-ignore: ")


async def test_recheck_ignored_gives_a_git_that_cannot_start_as_the_reason(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _open(_task_file(git_repo))
    monkeypatch.setenv("PATH", "")

    reason = await recheck_ignored(repo, str(_task_file(git_repo)), None)

    assert reason == "git: not found on PATH"


async def test_recheck_ignored_does_not_blame_git_for_a_path_it_cannot_resolve(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _open(_task_file(git_repo))

    def unresolvable(repo: Repo, path: str) -> str:
        raise OSError(errno.EIO, "disk on fire")

    monkeypatch.setattr(commit, "_relative", unresolvable)

    with pytest.raises(OSError, match="disk on fire"):
        await recheck_ignored(repo, str(_task_file(git_repo)), None)


@pytest.mark.usefixtures("fake_claude")
async def test_run_commit_after_the_session_committed_itself(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task(
        Gate(cmd="echo work > one.txt && git add -A && git commit -q -m own")
    )

    await run_loop(task_list, tasks_file, repo=await _open(tasks_file))

    assert commit_subjects(git_repo) == ["own", "init"], (
        "alr must add no commit of its own"
    )
    assert _saved_states(tasks_file) == [(COMPLETED, "")]


# An index.lock left in the repository makes git add fail before anything is
# staged, which is the other way the commit step can fail.
@pytest.mark.usefixtures("fake_claude")
async def test_run_failed_add_fails_the_task(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task(_work_gate("one.txt"), Gate(cmd="touch .git/index.lock"))
    lock = (git_repo / ".git" / "index.lock").resolve()
    want_error = f"commit failed: fatal: Unable to create '{lock}': File exists."

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(task_list, tasks_file, repo=await _open(tasks_file))

    assert str(caught.value) == f"task 1: First failed: {want_error}"
    assert _saved_states(tasks_file) == [(FAILED, want_error)]
    assert commit_subjects(git_repo) == ["init"]


@pytest.mark.usefixtures("fake_claude")
async def test_run_git_that_cannot_be_executed_fails_the_task(
    git_repo: Path, fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = _task_file(git_repo)
    repo = await _open(tasks_file)
    # No execute bit at all, which exec refuses even for root. Only this and
    # the fake claude are on PATH, because exec moves on to the next folder
    # that holds an executable git.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text("#!/bin/true\n", encoding="utf-8")
    (bin_dir / "git").chmod(0o644)
    monkeypatch.setenv("PATH", f"{fake_claude}:{bin_dir}")

    with pytest.raises(RunError) as caught:
        await run_loop(_gated_task(), tasks_file, repo=repo)

    assert "git" in str(caught.value)
    [(state, error)] = _saved_states(tasks_file)
    assert state == FAILED
    assert "git" in error


@pytest.mark.usefixtures("fake_claude")
async def test_run_commit_with_task_file_outside_the_tree(
    git_repo: Path, outside: Path
) -> None:
    tasks_file = outside / "tasks.yaml"

    await run_loop(
        _gated_task(_work_gate("one.txt")), tasks_file, repo=await _open(tasks_file)
    )

    assert commit_subjects(git_repo) == ["First", "init"]
    assert git(git_repo, "status", "--porcelain") == ""


@pytest.mark.usefixtures("fake_claude")
async def test_run_commit_from_subdirectory_commits_whole_tree(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = git_repo / "sub"
    write_nested_file(sub / "keep.txt", "keep\n")
    git(git_repo, "add", "sub/keep.txt")
    git(git_repo, "commit", "-q", "-m", "add sub")
    monkeypatch.chdir(sub)
    tasks_file = _task_file(git_repo)
    # The gate runs in sub and writes one file there and one in the root.
    task_list = _gated_task(Gate(cmd="echo a > here.txt; echo b > ../top.txt"))

    await run_loop(task_list, tasks_file, repo=await _open(tasks_file))

    assert _head_files(git_repo) == ["sub/here.txt", "top.txt"]


# gralph asserts the announced line on its stream path; here it is the plain
# front end's commit line.
@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("task_name", "want_subject", "want_line"),
    [
        pytest.param(
            "Subject line\n\nBody text",
            "Subject line",
            "commit: Subject line\n",
            id="multi-line",
        ),
        pytest.param(
            "--amend the widget",
            "--amend the widget",
            "commit: --amend the widget\n",
            id="leading dash",
        ),
    ],
)
async def test_run_commit_odd_task_names(
    git_repo: Path,
    capfd: pytest.CaptureFixture[str],
    task_name: str,
    want_subject: str,
    want_line: str,
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = TaskList(
        shared=Shared(prompt="shared", gates=[_work_gate("one.txt")]),
        tasks=[Task(id=1, name=task_name, prompt="p")],
    )

    await run_loop(task_list, tasks_file, repo=await _open(tasks_file))

    assert commit_subjects(git_repo) == [want_subject, "init"]
    # The subject alone cannot tell a whole name from its first line: only
    # the announced line is cut, the message keeps the body.
    assert git(git_repo, "log", "-1", "--format=%B").rstrip("\n") == task_name
    output = capfd.readouterr().out
    assert want_line in output
    assert "1 file changed" in output, "git's own output passes through"


async def _cancel_during_commit(
    repo_dir: Path,
    tasks_file: Path,
    ready: Path,
    script: str,
    commit_mode: CommitMode = CommitMode.TASK,
    *,
    hook: str = "pre-commit",
    task_list: TaskList | None = None,
    front_end: FrontEnd | None = None,
    stop: Stop | None = None,
    expect: type[RunError] = RunCancelledError,
) -> tuple[float, RunError]:
    """Run tasks whose commit hook is script and cancel once the hook has started.

    The pre-commit hook runs before the commit exists, the post-commit hook after
    it. Returns how long the run took to end after the cancel, and its error.
    """
    _install_hook(repo_dir, script.replace("READY", f"'{ready}'"), hook)
    repo = await _open(tasks_file)
    run = asyncio.ensure_future(
        run_loop(
            task_list or _gated_task(_work_gate("one.txt")),
            tasks_file,
            repo=repo,
            commit_mode=commit_mode,
            front_end=front_end,
            stop=stop,
        )
    )
    await wait_for_text(ready)

    started = time.monotonic()
    run.cancel()
    with pytest.raises(expect) as caught:
        await asyncio.wait_for(run, 10)

    return time.monotonic() - started, caught.value


@pytest.mark.usefixtures("fake_claude")
async def test_run_cancel_during_commit_leaves_file_untouched(
    git_repo: Path, tmp_path: Path
) -> None:
    tasks_yaml = "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p1, state: pending}\n"
    tasks_file = write_nested_file(_task_file(git_repo), tasks_yaml)

    _, error = await _cancel_during_commit(
        git_repo, tasks_file, tmp_path / "ready", "echo ready > READY\nsleep 60"
    )

    assert "task 1: First: stopped during commit" in str(error)
    assert tasks_file.read_text(encoding="utf-8") == tasks_yaml, (
        "the tasks file must be byte-for-byte unchanged after a cancel during the commit"
    )
    assert commit_subjects(git_repo) == ["init"]
    assert list((git_repo / ".git").glob("*.lock")) == [], (
        "git must be stopped politely so it removes its own lock"
    )


# A hook that ignores SIGTERM must not hang alr: after the grace period the
# whole group is killed.
@pytest.mark.usefixtures("fake_claude")
async def test_run_cancel_during_commit_kills_a_hook_that_ignores_sigterm(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Short, so the test waits for the kill instead of the real two seconds.
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.3)
    tasks_file = write_nested_file(
        _task_file(git_repo),
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p1, state: pending}\n",
    )

    took, error = await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "trap '' TERM\necho ready > READY\nsleep 60",
    )

    assert "task 1: First: stopped during commit" in str(error)
    assert took < 6, "SIGKILL must follow the grace period"
    assert took >= proc._GENTLE_GRACE / 2, "SIGTERM must come first and be given time"
    assert commit_subjects(git_repo) == ["init"]


async def test_check_log_dir_nil_repo_checks_nothing() -> None:
    await check_log_dir(None, str(Path("logs") / RUN_DIR))


async def test_check_log_dir_inside_the_tree_must_be_ignored(git_repo: Path) -> None:
    repo = await _open(_task_file(git_repo))

    with pytest.raises(RefusedError) as caught:
        await check_log_dir(repo, str(git_repo / "logs" / RUN_DIR))

    assert caught.value.reason == "log-folder-visible"
    assert str(caught.value) == (
        "refused: log-folder-visible: --commit needs the log directory ignored "
        "by git or outside the repository: logs"
    )


# The log directory does not exist yet and the rule is a directory pattern,
# which git only matches for a path it can tell is inside that directory.
async def test_check_log_dir_ignored_by_a_directory_pattern_before_it_exists(
    git_repo: Path,
) -> None:
    _ignore_and_commit(git_repo, "run/", "logs/")
    repo = await _open(_task_file(git_repo))

    await check_log_dir(repo, str(git_repo / "logs" / RUN_DIR))

    assert not (git_repo / "logs").exists(), "the check creates nothing"


async def test_check_log_dir_relative_path_from_a_subdirectory(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ignore_and_commit(git_repo, "run/", "logs/")
    repo = await _open(_task_file(git_repo))
    sub = git_repo / "run" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    await check_log_dir(repo, str(Path("..", "..", "logs", RUN_DIR)))

    with pytest.raises(RefusedError) as caught:
        await check_log_dir(repo, str(Path("..", "..", "elsewhere", RUN_DIR)))

    assert str(caught.value) == (
        "refused: log-folder-visible: --commit needs the log directory ignored "
        "by git or outside the repository: elsewhere"
    )


async def test_check_log_dir_outside_the_tree_needs_no_rule(
    git_repo: Path, outside: Path
) -> None:
    repo = await _open(_task_file(git_repo))

    await check_log_dir(repo, str(outside / "logs" / RUN_DIR))


_NUMBERED = Gate(
    cmd="n=1; while [ -e work-$n.txt ]; do n=$((n+1)); done; echo work > work-$n.txt"
)


@pytest.mark.usefixtures("fake_claude")
async def test_run_after_all_makes_one_commit_listing_every_task(
    git_repo: Path,
) -> None:
    tasks_file = _task_file(git_repo)

    await run_loop(
        _two_tasks(_NUMBERED),
        tasks_file,
        repo=await _open(tasks_file),
        commit_mode=CommitMode.RUN,
    )

    assert commit_subjects(git_repo) == ["alr: 2 tasks completed", "init"]
    assert git(git_repo, "log", "-1", "--format=%b").split("\n")[:2] == [
        "1: First",
        "2: Second",
    ]
    assert _head_files(git_repo) == ["work-1.txt", "work-2.txt"]


@pytest.mark.usefixtures("fake_claude")
async def test_run_with_mode_none_makes_no_commit_even_with_a_repo(
    git_repo: Path,
) -> None:
    tasks_file = _task_file(git_repo)

    await run_loop(
        _two_tasks(_NUMBERED),
        tasks_file,
        repo=await _open(tasks_file),
        commit_mode=CommitMode.NONE,
    )

    assert commit_subjects(git_repo) == ["init"]


@pytest.mark.usefixtures("fake_claude")
async def test_run_after_all_failed_task_makes_no_commit(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", '{"state":"failed","error":"nope"}')
    tasks_file = _task_file(git_repo)
    repo = await _open(tasks_file)

    with pytest.raises(RunError, match="nope"):
        await run_loop(_two_tasks(), tasks_file, repo=repo, commit_mode=CommitMode.RUN)

    assert commit_subjects(git_repo) == ["init"]


# Passes while one.txt is absent and writes it, so task 1 completes with work
# in the tree and any later task fails its gate.
_WORK_ONCE = Gate(cmd="if [ -e one.txt ]; then exit 1; fi; echo work > one.txt")


def _assert_work_left_uncommitted(repo: Path) -> None:
    assert commit_subjects(repo) == ["init"]
    assert git(repo, "status", "--porcelain").strip() == "?? one.txt"


@pytest.mark.usefixtures("fake_claude")
async def test_run_after_all_later_failed_task_leaves_earlier_work_uncommitted(
    git_repo: Path,
) -> None:
    tasks_file = _task_file(git_repo)

    with pytest.raises(RunError, match="task 2: Second failed"):
        await run_loop(
            _two_tasks(_WORK_ONCE),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
        )

    _assert_work_left_uncommitted(git_repo)
    assert _saved_states(tasks_file)[0] == (PENDING, "")


async def test_run_after_all_later_cancelled_task_leaves_earlier_work_uncommitted(
    git_repo: Path, record: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK_ON", "block-second")
    tasks_file = _task_file(git_repo)
    task_list = TaskList(
        shared=Shared(prompt="shared", gates=[_WORK_ONCE]),
        tasks=[
            Task(id=1, name="First", prompt="p1"),
            Task(id=2, name="Second", prompt="block-second"),
        ],
    )
    run = asyncio.ensure_future(
        run_loop(
            task_list,
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
        )
    )
    await until(lambda: len(fake_claude_records(record)) == 2)

    run.cancel()
    with pytest.raises(RunCancelledError, match="task 2"):
        await asyncio.wait_for(run, 10)

    _assert_work_left_uncommitted(git_repo)


@pytest.mark.usefixtures("fake_claude")
async def test_run_after_all_cancel_during_the_final_commit_stops_the_run(
    git_repo: Path, tmp_path: Path
) -> None:
    tasks_file = write_nested_file(
        _task_file(git_repo),
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p1, state: pending}\n",
    )

    _, error = await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "echo ready > READY\nsleep 60",
        CommitMode.RUN,
    )

    assert str(error) == "stopped during the final commit"
    assert commit_subjects(git_repo) == ["init"]
    assert list((git_repo / ".git").glob("*.lock")) == [], (
        "git must be stopped politely so it removes its own lock"
    )


@pytest.mark.usefixtures("fake_claude")
async def test_run_after_all_outside_a_repository_never_commits(
    git_repo: Path,
) -> None:
    await run_loop(
        _gated_task(_work_gate("one.txt")),
        _task_file(git_repo),
        repo=None,
        commit_mode=CommitMode.RUN,
    )

    assert commit_subjects(git_repo) == ["init"]
    assert (git_repo / "one.txt").exists()


@pytest.mark.usefixtures("fake_claude")
async def test_run_after_all_resumed_run_commits_only_the_tasks_that_ran(
    git_repo: Path,
) -> None:
    resumed = _two_tasks(_NUMBERED)
    resumed.tasks[0].state = COMPLETED
    tasks_file = _task_file(git_repo)

    await run_loop(
        resumed,
        tasks_file,
        repo=await _open(tasks_file),
        commit_mode=CommitMode.RUN,
    )

    assert commit_subjects(git_repo) == ["alr: 1 task completed", "init"]
    assert git(git_repo, "log", "-1", "--format=%b").strip() == "2: Second"


@pytest.mark.usefixtures("fake_claude")
async def test_run_after_all_with_every_task_completed_makes_no_commit(
    git_repo: Path,
) -> None:
    done = _two_tasks()
    for task in done.tasks:
        task.state = COMPLETED
    tasks_file = _task_file(git_repo)

    await run_loop(
        done, tasks_file, repo=await _open(tasks_file), commit_mode=CommitMode.RUN
    )

    assert commit_subjects(git_repo) == ["init"]


class _SnapshotFrontEnd(PlainFrontEnd):
    """Notes what tasks.yaml holds each time a task finishes."""

    def __init__(self, tasks_file: Path) -> None:
        super().__init__()
        self.seen: list[list[tuple[str, str]]] = []
        self._tasks_file = tasks_file

    def task_finished(self, task: Task, error: str | None, seconds: float) -> None:
        self.seen.append(_saved_states(self._tasks_file))
        super().task_finished(task, error, seconds)


def _save_first(task_list: TaskList, tasks_file: Path) -> None:
    """Write the task file as the user left it, so a test can read it mid-run."""
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    tasks.save_tasks(str(tasks_file), task_list)


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_saves_nothing_until_the_commit_lands(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _two_tasks(_NUMBERED)
    _save_first(task_list, tasks_file)
    front_end = _SnapshotFrontEnd(tasks_file)

    await run_loop(
        task_list,
        tasks_file,
        repo=await _open(tasks_file),
        commit_mode=CommitMode.RUN,
        front_end=front_end,
    )

    assert front_end.seen == [[(PENDING, ""), (PENDING, "")]] * 2, (
        "the file must stay untouched until the commit lands"
    )
    assert _saved_states(tasks_file) == [(COMPLETED, ""), (COMPLETED, "")]


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_failed_task_leaves_the_others_pending(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)

    with pytest.raises(RunError, match="task 2: Second failed"):
        await run_loop(
            _two_tasks(_WORK_ONCE),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
        )

    states = _saved_states(tasks_file)
    assert [state for state, _ in states] == [PENDING, FAILED]
    assert states[1][1], "the failed task carries its reason"


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_failed_commit_marks_only_the_last_task_failed(
    git_repo: Path,
) -> None:
    _install_hook(git_repo, "echo hook-says-no >&2\nexit 1")
    tasks_file = _task_file(git_repo)

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(
            _two_tasks(_NUMBERED),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
        )

    assert caught.value.exit_code == 4
    assert commit_subjects(git_repo) == ["init"]
    assert _saved_states(tasks_file) == [
        (PENDING, ""),
        (FAILED, "commit failed: hook-says-no"),
    ]


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_resumed_run_keeps_earlier_completed_tasks(
    git_repo: Path,
) -> None:
    resumed = _two_tasks(Gate(cmd="exit 1"))
    resumed.tasks[0].state = COMPLETED
    tasks_file = _task_file(git_repo)

    with pytest.raises(RunError, match="task 2: Second failed"):
        await run_loop(
            resumed,
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
        )

    states = _saved_states(tasks_file)
    assert states[0] == (COMPLETED, ""), (
        "work committed by an earlier run stays completed"
    )
    assert states[1][0] == FAILED


# A comment and a flow-style task: a rewrite of the file would lose both.
_HAND_WRITTEN = (
    "# keep me\nshared:\n  prompt: shared\ntasks:\n"
    "  - {id: 1, name: First, prompt: p1, state: pending}\n"
)
_HAND_WRITTEN_TWO = (
    _HAND_WRITTEN + "  - {id: 2, name: Second, prompt: block-second, state: pending}\n"
)


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_stop_during_the_final_commit_leaves_the_file_as_it_was(
    git_repo: Path, tmp_path: Path
) -> None:
    tasks_file = write_nested_file(_task_file(git_repo), _HAND_WRITTEN)

    await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "echo ready > READY\nsleep 60",
        CommitMode.RUN,
    )

    # The commit never landed, so the file is as the run found it, byte for byte.
    assert tasks_file.read_text(encoding="utf-8") == _HAND_WRITTEN
    assert commit_subjects(git_repo) == ["init"]


async def test_run_mode_stop_during_a_session_leaves_the_file_as_it_was(
    git_repo: Path, record: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK_ON", "block-second")
    tasks_file = write_nested_file(_task_file(git_repo), _HAND_WRITTEN_TWO)
    task_list = tasks.parse_tasks(_HAND_WRITTEN_TWO)
    task_list.shared.gates = [_WORK_ONCE]
    run = asyncio.ensure_future(
        run_loop(
            task_list,
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
        )
    )
    await until(lambda: len(fake_claude_records(record)) == 2)

    run.cancel()
    with pytest.raises(RunCancelledError, match="task 2"):
        await asyncio.wait_for(run, 10)

    assert tasks_file.read_text(encoding="utf-8") == _HAND_WRITTEN_TWO


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("commit_mode", "want_error", "want_states", "want_subject"),
    [
        pytest.param(
            CommitMode.TASK,
            "task 1: First: stopped during commit",
            [(COMPLETED, ""), (PENDING, "")],
            "First",
            id="task mode completes only the committed task",
        ),
        pytest.param(
            CommitMode.RUN,
            "stopped during the final commit",
            [(COMPLETED, ""), (COMPLETED, "")],
            "alr: 2 tasks completed",
            id="run mode completes every task the commit covers",
        ),
    ],
)
async def test_a_stop_after_the_commit_landed_saves_the_covered_tasks_completed(
    git_repo: Path,
    tmp_path: Path,
    commit_mode: CommitMode,
    want_error: str,
    want_states: list[tuple[str, str]],
    want_subject: str,
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _two_tasks(_NUMBERED)
    _save_first(task_list, tasks_file)

    # A post-commit hook runs once git has made the commit, so the stop arrives
    # with the commit already in the history.
    _, error = await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "echo ready > READY\nsleep 60",
        commit_mode,
        hook="post-commit",
        task_list=task_list,
    )

    assert str(error) == want_error
    assert commit_subjects(git_repo)[0] == want_subject
    assert _saved_states(tasks_file) == want_states, (
        "committed work must never be left pending for a rerun to redo"
    )


class _PaneHost(App[None]):
    def __init__(self, pane: TasksPane) -> None:
        super().__init__()
        self._pane = pane

    def compose(self) -> ComposeResult:
        yield self._pane


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("commit_mode", "want_states"),
    [
        pytest.param(CommitMode.TASK, [(COMPLETED, ""), (PENDING, "")], id="task mode"),
        pytest.param(CommitMode.RUN, [(COMPLETED, "")] * 2, id="run mode"),
    ],
)
async def test_the_tasks_pane_follows_the_saved_file_after_a_stop_past_the_commit(
    git_repo: Path,
    tmp_path: Path,
    commit_mode: CommitMode,
    want_states: list[tuple[str, str]],
) -> None:
    tasks_file = _task_file(git_repo)
    _save_first(_two_tasks(_NUMBERED), tasks_file)
    events: list[Event] = []

    # The post-commit hook is still running when the stop arrives, so the engine
    # never saw the commit finish; the rows must still match the file.
    await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "echo ready > READY\nsleep 60",
        commit_mode,
        hook="post-commit",
        task_list=_two_tasks(_NUMBERED),
        front_end=StreamFrontEnd(events.append),
    )

    assert _saved_states(tasks_file) == want_states
    pane = TasksPane()
    async with _PaneHost(pane).run_test():
        pane.load(_two_tasks(_NUMBERED))
        for event in events:
            pane.apply(event)

        symbols = {COMPLETED: "✔", PENDING: "·"}
        assert [pane.get_row_at(i)[0] for i in range(2)] == [
            symbols[state] for state, _ in want_states
        ]


@pytest.mark.usefixtures("fake_claude")
async def test_a_stop_after_the_run_commit_landed_keeps_earlier_completed_tasks(
    git_repo: Path, tmp_path: Path
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = TaskList(
        shared=Shared(prompt="shared", gates=[_NUMBERED]),
        tasks=[
            Task(id=1, name="First", prompt="p1", state=COMPLETED),
            Task(id=2, name="Second", prompt="p2"),
            Task(id=3, name="Third", prompt="p3"),
        ],
    )
    _save_first(task_list, tasks_file)

    _, error = await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "echo ready > READY\nsleep 60",
        CommitMode.RUN,
        hook="post-commit",
        task_list=task_list,
    )

    assert str(error) == "stopped during the final commit"
    assert git(git_repo, "log", "-1", "--format=%b").split("\n")[:2] == [
        "2: Second",
        "3: Third",
    ]
    assert _saved_states(tasks_file) == [(COMPLETED, "")] * 3


@pytest.mark.usefixtures("fake_claude")
async def test_a_second_stop_while_checking_head_still_saves_the_landed_commit(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task(_work_gate("one.txt"))
    _save_first(task_list, tasks_file)
    _install_hook(
        git_repo, f"echo ready > '{tmp_path / 'ready'}'\nsleep 60", "post-commit"
    )
    real_head = loop.head
    looking = asyncio.Event()
    lookups: list[str | None] = []

    async def slow_head_after_the_stop(repo: Repo) -> str | None:
        # The first call is the one before the commit; the second is the look
        # after the stop, held open so a second stop can arrive during it.
        lookups.append(None)
        if len(lookups) > 1:
            looking.set()
            await asyncio.sleep(0.2)

        return await real_head(repo)

    monkeypatch.setattr(loop, "head", slow_head_after_the_stop)
    run = asyncio.ensure_future(
        run_loop(task_list, tasks_file, repo=await _open(tasks_file))
    )
    await wait_for_text(tmp_path / "ready")

    run.cancel()
    await asyncio.wait_for(looking.wait(), 10)
    run.cancel()
    with pytest.raises(RunCancelledError, match="stopped during commit"):
        await asyncio.wait_for(run, 10)

    assert _saved_states(tasks_file) == [(COMPLETED, "")]


@pytest.mark.usefixtures("fake_claude")
async def test_a_stop_while_reading_head_after_the_commit_still_saves_the_task(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task(_work_gate("one.txt"))
    _save_first(task_list, tasks_file)
    real_head = loop.head
    reading = asyncio.Event()
    lookups: list[None] = []

    async def hold_the_read_after_the_commit(repo: Repo) -> str | None:
        # The first call is the one before the commit, the second the read after
        # it, held open so the stop arrives once the commit has landed.
        lookups.append(None)
        if len(lookups) == 2:
            reading.set()
            await asyncio.sleep(3600)

        return await real_head(repo)

    monkeypatch.setattr(loop, "head", hold_the_read_after_the_commit)
    run = asyncio.ensure_future(
        run_loop(task_list, tasks_file, repo=await _open(tasks_file))
    )
    await asyncio.wait_for(reading.wait(), 10)

    run.cancel()
    with pytest.raises(RunCancelledError, match="stopped during commit"):
        await asyncio.wait_for(run, 10)

    assert _saved_states(tasks_file) == [(COMPLETED, "")]


@pytest.mark.usefixtures("fake_claude")
async def test_a_task_that_changed_nothing_and_cannot_be_saved_names_no_commit(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task()
    _save_first(task_list, tasks_file)

    def refuse_to_save(path: str, task_list: TaskList) -> None:
        raise TaskFileError(f"failed to save tasks to {path}: disk full")

    monkeypatch.setattr(loop, "save_tasks", refuse_to_save)

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file, repo=await _open(tasks_file))

    # No commit was made, so HEAD is not a commit this run landed.
    assert not isinstance(caught.value, CommitFailedError)
    assert caught.value.exit_code == 1
    assert "tasks.yaml was not updated" not in str(caught.value)
    assert "failed to save task state" in str(caught.value)


class _EndingFrontEnd(PlainFrontEnd):
    """Notes how the run ended: its result and the exit code alr will return."""

    def __init__(self) -> None:
        super().__init__()
        self.endings: list[tuple[RunResult, int]] = []

    def run_done(
        self, error: str | None, seconds: float, result: RunResult, exit_code: int
    ) -> None:
        self.endings.append((result, exit_code))


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize("hook", ["pre-commit", "post-commit"])
@pytest.mark.parametrize("commit_mode", [CommitMode.TASK, CommitMode.RUN])
async def test_a_sigterm_during_a_commit_ends_stopped_with_exit_143(
    git_repo: Path, tmp_path: Path, commit_mode: CommitMode, hook: str
) -> None:
    tasks_file = _task_file(git_repo)
    front_end = _EndingFrontEnd()

    # Whether or not the commit landed, a stop stays a stop: 143, never 1.
    await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "echo ready > READY\nsleep 60",
        commit_mode,
        hook=hook,
        front_end=front_end,
        stop=Stop(signal.SIGTERM),
    )

    assert front_end.endings == [(RunResult.STOPPED, 143)]


def _unsavable_file(repo: Path) -> Path:
    """A task file whose folder does not exist, so saving it fails.

    A read-only folder would not do: the Docker build runs the tests as root,
    which ignores folder permissions. The folder is inside the ignored run/.
    """
    return repo / "run" / "gone" / "tasks.yaml"


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("commit_mode", "want_failure"),
    [
        pytest.param(
            CommitMode.TASK,
            "task 1: First failed: commit failed: hook-says-no",
            id="task mode",
        ),
        pytest.param(CommitMode.RUN, "commit failed: hook-says-no", id="run mode"),
    ],
)
async def test_a_commit_failure_that_cannot_be_saved_still_exits_4(
    git_repo: Path, commit_mode: CommitMode, want_failure: str
) -> None:
    _install_hook(git_repo, "echo hook-says-no >&2\nexit 1")
    tasks_file = _unsavable_file(git_repo)

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(
            _gated_task(_work_gate("one.txt")),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=commit_mode,
        )

    assert caught.value.exit_code == 4
    assert str(caught.value).startswith(f"{want_failure}\n")
    assert "failed to save tasks to" in str(caught.value)


@pytest.mark.usefixtures("fake_claude")
async def test_a_landed_run_commit_that_cannot_be_saved_names_the_commit(
    git_repo: Path,
) -> None:
    tasks_file = _unsavable_file(git_repo)

    with pytest.raises(RunError) as caught:
        await run_loop(
            _two_tasks(_NUMBERED),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
        )

    message = str(caught.value)
    assert caught.value.exit_code == 4
    assert git(git_repo, "rev-parse", "HEAD").strip() in message
    assert "tasks.yaml was not updated" in message
    assert "failed to save tasks to" in message


@pytest.mark.usefixtures("fake_claude")
async def test_the_tasks_pane_marks_no_row_failed_when_the_save_after_the_run_commit_fails(
    git_repo: Path,
) -> None:
    tasks_file = _unsavable_file(git_repo)
    events: list[Event] = []

    with pytest.raises(CommitFailedError):
        await run_loop(
            _two_tasks(_NUMBERED),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.RUN,
            front_end=StreamFrontEnd(events.append),
        )

    pane = TasksPane()
    async with _PaneHost(pane).run_test():
        pane.load(_two_tasks(_NUMBERED))
        for event in events:
            pane.apply(event)

        # The commit landed and the file says pending: nothing failed.
        assert [pane.get_row_at(i)[0] for i in range(2)] == ["·", "·"]


async def _row_symbols(events: list[Event], task_list: TaskList) -> list[str]:
    """What the tasks pane shows once it has seen the run's events, one symbol per row."""
    pane = TasksPane()
    async with _PaneHost(pane).run_test():
        pane.load(task_list)
        for event in events:
            pane.apply(event)

        return [pane.get_row_at(i)[0] for i in range(len(task_list.tasks))]


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("commit_mode", "want_states"),
    [
        pytest.param(
            CommitMode.TASK,
            [(FAILED, _UNIGNORE_TASK_FILE), (PENDING, "")],
            id="task mode",
        ),
        pytest.param(
            CommitMode.RUN,
            [(PENDING, ""), (FAILED, _UNIGNORE_TASK_FILE)],
            id="run mode",
        ),
    ],
)
async def test_the_tasks_pane_follows_the_saved_file_after_an_unignore_failure(
    git_repo: Path, commit_mode: CommitMode, want_states: list[tuple[str, str]]
) -> None:
    tasks_file = _task_file(git_repo)
    events: list[Event] = []

    with pytest.raises(CommitFailedError):
        await run_loop(
            _two_tasks(_UNIGNORE_ALL),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=commit_mode,
            front_end=StreamFrontEnd(events.append),
        )

    done = events[-1]
    assert isinstance(done, RunDone)
    assert (done.result, done.exit_code) == (RunResult.COMMIT_FAILED, 4)
    assert _saved_states(tasks_file) == want_states
    symbols = {FAILED: "✖", PENDING: "·"}
    assert await _row_symbols(events, _two_tasks(_UNIGNORE_ALL)) == [
        symbols[state] for state, _ in want_states
    ]


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("commit_mode", "want_symbols"),
    [
        pytest.param(CommitMode.TASK, ["✖", "·"], id="task mode"),
        pytest.param(CommitMode.RUN, ["·", "✖"], id="run mode"),
    ],
)
async def test_the_tasks_pane_follows_the_saved_file_after_a_failed_recheck(
    git_repo: Path, commit_mode: CommitMode, want_symbols: list[str]
) -> None:
    tasks_file = _task_file(git_repo)
    gate = Gate(cmd="echo work > one.txt; echo garbage > .git/index")
    events: list[Event] = []

    with pytest.raises(CommitFailedError):
        await run_loop(
            _two_tasks(gate),
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=commit_mode,
            front_end=StreamFrontEnd(events.append),
        )

    done = events[-1]
    assert isinstance(done, RunDone)
    assert (done.result, done.exit_code) == (RunResult.COMMIT_FAILED, 4)
    saved = [state for state, _ in _saved_states(tasks_file)]
    assert saved == [FAILED if symbol == "✖" else PENDING for symbol in want_symbols]
    assert await _row_symbols(events, _two_tasks(gate)) == want_symbols


@pytest.mark.usefixtures("fake_claude")
async def test_a_stop_after_the_commit_landed_that_cannot_be_saved_names_the_commit(
    git_repo: Path, tmp_path: Path
) -> None:
    tasks_file = _unsavable_file(git_repo)

    _, error = await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "echo ready > READY\nsleep 60",
        CommitMode.RUN,
        hook="post-commit",
        expect=RunError,
    )

    message = str(error)
    assert error.exit_code == 4
    assert git(git_repo, "rev-parse", "HEAD").strip() in message
    assert "tasks.yaml was not updated" in message


@pytest.mark.usefixtures("fake_claude")
async def test_a_landed_task_commit_that_cannot_be_saved_names_the_commit(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task(_work_gate("one.txt"))
    tasks.save_tasks(str(tasks_file), task_list)
    before = tasks_file.read_text(encoding="utf-8")

    # The file exists, so what the save leaves behind can be compared.
    def refuse_to_save(path: str, task_list: TaskList) -> None:
        raise TaskFileError(f"failed to save tasks to {path}: disk full")

    monkeypatch.setattr(loop, "save_tasks", refuse_to_save)

    with pytest.raises(RunError) as caught:
        await run_loop(
            task_list,
            tasks_file,
            repo=await _open(tasks_file),
            commit_mode=CommitMode.TASK,
        )

    message = str(caught.value)
    assert caught.value.exit_code == 4
    assert git(git_repo, "rev-parse", "HEAD").strip() in message
    assert "tasks.yaml was not updated" in message
    assert "disk full" in message
    assert tasks_file.read_text(encoding="utf-8") == before


@pytest.mark.usefixtures("fake_claude")
async def test_a_stop_in_the_commit_step_with_head_unreadable_saves_nothing(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = _task_file(git_repo)
    task_list = _gated_task(_work_gate("one.txt"))
    _save_first(task_list, tasks_file)
    before = tasks_file.read_text(encoding="utf-8")
    real_head = loop.head
    lookups: list[None] = []

    async def unreadable_after_the_stop(repo: Repo) -> str | None:
        # The first call is the one before the commit; later ones are the look
        # after the stop, where git cannot say.
        lookups.append(None)
        return await real_head(repo) if len(lookups) == 1 else None

    monkeypatch.setattr(loop, "head", unreadable_after_the_stop)

    _, error = await _cancel_during_commit(
        git_repo,
        tasks_file,
        tmp_path / "ready",
        "echo ready > READY\nsleep 60",
        hook="post-commit",
        task_list=task_list,
    )

    assert str(error) == "task 1: First: stopped during commit"
    assert len(lookups) > 1, "the stop must have looked at HEAD again"
    assert tasks_file.read_text(encoding="utf-8") == before, (
        "with git unable to say, nothing is saved"
    )


async def test_head_names_the_commit_head_points_at(git_repo: Path) -> None:
    repo = _repo_of(git_repo)

    assert await head(repo) == git(git_repo, "rev-parse", "HEAD").strip()


async def test_head_is_none_in_a_repository_with_no_commits(
    git_repo: Path, outside: Path
) -> None:
    git(outside, "init", "--quiet", "--initial-branch=main")

    assert await head(_repo_of(outside)) is None


async def test_head_is_none_when_git_fails(git_repo: Path) -> None:
    _break_config(git_repo)

    assert await head(_repo_of(git_repo)) is None


async def test_head_is_none_when_git_is_not_installed(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")

    assert await head(_repo_of(git_repo)) is None


async def test_work_tree_root_names_the_repository(git_repo: Path) -> None:
    assert await work_tree_root() == str(git_repo)


async def test_work_tree_root_outside_a_repository_is_none(
    outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)

    assert await work_tree_root() is None


async def test_find_repo_names_the_absolute_git_directory(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    assert repo == _repo_of(git_repo)


async def test_a_second_lock_is_refused(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    with held_lock(repo):
        with pytest.raises(RefusedError) as caught:
            with held_lock(repo):
                pass

    assert caught.value.reason == "locked"
    assert str(caught.value) == (
        "refused: locked: another alr run is committing in this repository"
    )


async def test_the_lock_is_released_on_exit(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    with held_lock(repo):
        pass

    with held_lock(repo):
        pass


async def test_the_lock_is_released_when_the_run_raises(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    with pytest.raises(RunError):
        with held_lock(repo):
            raise RunError("the run failed")

    with held_lock(repo):
        pass


async def test_no_repository_needs_no_lock() -> None:
    with held_lock(None):
        with held_lock(None):
            pass


async def test_a_stale_lock_file_does_not_block(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))
    Path(repo.git_dir, "alr.lock").write_text("left by a killed alr", encoding="utf-8")

    with held_lock(repo):
        pass


_LOCK_HOLDER = """
import asyncio, pathlib, sys
from alr.engine.commit import find_repo, held_lock

async def main():
    repo = await find_repo(sys.argv[1])
    with held_lock(repo):
        pathlib.Path(sys.argv[2]).write_text("held")
        await asyncio.sleep(60)

asyncio.run(main())
"""


async def test_a_killed_run_leaves_no_stuck_lock(
    git_repo: Path, tmp_path: Path
) -> None:
    tasks_file = str(_task_file(git_repo))
    ready = tmp_path / "ready"
    holder = subprocess.Popen(
        [sys.executable, "-c", _LOCK_HOLDER, tasks_file, str(ready)]
    )
    try:
        wait_for_file(ready)
        repo = await find_repo(tasks_file)
        with pytest.raises(RefusedError) as caught:
            check_unlocked(repo)

        assert caught.value.reason == "locked", "the other process holds it"
    finally:
        holder.kill()
        holder.wait()

    with held_lock(repo):
        pass


async def test_a_linked_worktree_finds_its_own_lock_location(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path.resolve() / "wt"
    git(git_repo, "worktree", "add", "-q", str(worktree))
    (worktree / "run").mkdir()
    monkeypatch.chdir(worktree)
    repo = await find_repo(str(worktree / "run" / "tasks.yaml"))
    assert repo is not None

    with held_lock(repo):
        pass

    assert (worktree / ".git").is_file(), "a linked worktree's .git is a file"
    assert Path(repo.git_dir) == git_repo / ".git" / "worktrees" / "wt"
    assert (Path(repo.git_dir) / "alr.lock").exists()


async def test_a_lock_that_cannot_be_opened_is_refused_with_git_error(
    git_repo: Path,
) -> None:
    repo = Repo(root=str(git_repo), git_dir=str(git_repo / "no" / "such" / "dir"))

    with pytest.raises(RefusedError) as caught:
        with held_lock(repo):
            pass

    assert caught.value.reason == "git-error"
    assert "alr.lock" in caught.value.message


async def test_check_unlocked_passes_when_nothing_holds_the_lock(
    git_repo: Path,
) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    check_unlocked(repo)
    check_unlocked(None)


async def test_check_unlocked_passes_over_a_stale_lock_file(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))
    Path(repo.git_dir, "alr.lock").write_text("left by a killed alr", encoding="utf-8")

    check_unlocked(repo)


async def test_check_unlocked_refuses_while_another_run_holds_the_lock(
    git_repo: Path,
) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    with held_lock(repo):
        with pytest.raises(RefusedError) as caught:
            check_unlocked(repo)

    assert caught.value.reason == "locked"


async def test_check_unlocked_does_not_keep_the_lock(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))
    # The file must exist, or there is nothing for the check to lock.
    Path(repo.git_dir, "alr.lock").write_text("", encoding="utf-8")

    check_unlocked(repo)

    with held_lock(repo):
        pass


async def test_check_unlocked_is_not_refused_by_another_check(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))
    Path(repo.git_dir, "alr.lock").write_text("", encoding="utf-8")
    holder = os.open(Path(repo.git_dir, "alr.lock"), os.O_RDONLY)
    fcntl.flock(holder, fcntl.LOCK_SH)

    try:
        check_unlocked(repo)
    finally:
        os.close(holder)


async def test_check_unlocked_writes_nothing(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    check_unlocked(repo)

    assert not (Path(repo.git_dir) / "alr.lock").exists()


async def test_check_unlocked_refuses_a_git_dir_the_lock_cannot_be_made_in(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await find_repo(str(_task_file(git_repo)))
    access = os.access

    def deny_the_git_dir(path: str | Path, mode: int, **kwargs: bool) -> bool:
        # chmod 555 would not stop root, so the refusal is made here instead.
        denied = Path(path) == Path(repo.git_dir) and mode & os.W_OK
        return not denied and access(path, mode, **kwargs)

    monkeypatch.setattr(os, "access", deny_the_git_dir)

    with pytest.raises(RefusedError) as caught:
        check_unlocked(repo)

    assert caught.value.reason == "git-error"
    assert str(caught.value) == (
        f"refused: git-error: --commit: cannot lock {repo.git_dir}/alr.lock: "
        "Permission denied"
    )
    assert not (Path(repo.git_dir) / "alr.lock").exists()
