"""--commit: the repository checks before a run and the commit after each completed task."""

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import (
    commit_subjects,
    git,
    run_loop,
    wait_for_text,
    write_nested_file,
)

from alr import tasks
from alr.engine import proc
from alr.engine.commit import Repo, check_log_dir, open_repo
from alr.errors import RepoError, RunCancelledError, RunError
from alr.tasks import COMPLETED, FAILED, PENDING, Gate, Shared, Task, TaskList

RUN_DIR = "20261001T140211"


def _ignore_and_commit(repo: Path, *patterns: str) -> None:
    """Replace .gitignore and commit it, so the tree stays clean."""
    write_nested_file(repo / ".gitignore", "\n".join(patterns) + "\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore")


def _task_file(repo: Path) -> Path:
    """Where tests keep the task file: inside the ignored run/."""
    return repo / "run" / "tasks.yaml"


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

    with pytest.raises(RepoError) as caught:
        await open_repo(str(tasks_file))

    assert str(caught.value) == (
        f"--commit needs a clean work tree; commit, stash, or remove:\n{want_line}"
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

    with pytest.raises(RepoError) as caught:
        await open_repo(tasks_file)

    assert str(caught.value) == (
        f"--commit needs the task file ignored by git or outside the repository: {want_rel}"
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
    assert await open_repo(setup(git_repo, outside)) == Repo(root=str(git_repo))


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

    with pytest.raises(RepoError, match=r"\?\? stray\.txt"):
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

    assert await open_repo(str(_task_file(git_repo))) == Repo(root=str(git_repo))


async def test_open_repo_broken_repository_is_an_error(git_repo: Path) -> None:
    _break_config(git_repo)

    with pytest.raises(RepoError) as caught:
        await open_repo(str(_task_file(git_repo)))

    assert "git rev-parse" in str(caught.value)


async def test_open_repo_status_failure_carries_gits_message(
    git_repo: Path, outside: Path
) -> None:
    (git_repo / ".git" / "index").write_bytes(b"garbage")

    # Outside the tree, so the task file check does not touch the index first.
    with pytest.raises(RepoError) as caught:
        await open_repo(str(outside / "tasks.yaml"))

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


def _install_hook(repo: Path, script: str) -> None:
    hook = write_nested_file(
        repo / ".git" / "hooks" / "pre-commit", f"#!/bin/sh\n{script}\n"
    )
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
    want_error = "commit failed: exit status 1"

    with pytest.raises(RunError) as caught:
        await run_loop(
            _two_tasks(_work_gate("one.txt")), tasks_file, repo=await _open(tasks_file)
        )

    assert str(caught.value) == f"task 1: First failed: {want_error}"
    assert commit_subjects(git_repo) == ["init"]
    assert len(record.read_text().splitlines()) == 1, "the second task must never run"
    assert _saved_states(tasks_file) == [(FAILED, want_error), (PENDING, "")]


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
    want_error = "commit failed: exit status 128"

    with pytest.raises(RunError) as caught:
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
    repo_dir: Path, tasks_file: Path, ready: Path, script: str
) -> tuple[float, RunCancelledError]:
    """Run one task whose pre-commit hook is script and cancel once the hook has started.

    Returns how long the run took to end after the cancel, and its error.
    """
    _install_hook(repo_dir, script.replace("READY", f"'{ready}'"))
    repo = await _open(tasks_file)
    run = asyncio.ensure_future(
        run_loop(_gated_task(_work_gate("one.txt")), tasks_file, repo=repo)
    )
    await wait_for_text(ready)

    started = time.monotonic()
    run.cancel()
    with pytest.raises(RunCancelledError) as caught:
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

    with pytest.raises(RepoError) as caught:
        await check_log_dir(repo, str(git_repo / "logs" / RUN_DIR))

    assert str(caught.value) == (
        "--commit needs the log directory ignored by git or outside the repository: logs"
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

    with pytest.raises(RepoError) as caught:
        await check_log_dir(repo, str(Path("..", "..", "elsewhere", RUN_DIR)))

    assert str(caught.value) == (
        "--commit needs the log directory ignored by git or outside the repository: elsewhere"
    )


async def test_check_log_dir_outside_the_tree_needs_no_rule(
    git_repo: Path, outside: Path
) -> None:
    repo = await _open(_task_file(git_repo))

    await check_log_dir(repo, str(outside / "logs" / RUN_DIR))
