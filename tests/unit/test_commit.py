"""--commit: the repository checks before a run and the commit after each completed task."""

import asyncio
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import (
    commit_subjects,
    fake_claude_records,
    git,
    run_loop,
    until,
    wait_for_text,
    write_nested_file,
)

from alr import tasks
from alr.engine import loop, proc
from alr.engine.commit import (
    CommitMode,
    Repo,
    check_log_dir,
    open_repo,
    work_tree_root,
)
from alr.engine.stop import Stop
from alr.errors import CommitFailedError, RefusedError, RunCancelledError, RunError
from alr.plain.frontend import PlainFrontEnd
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

    assert await open_repo(str(_task_file(git_repo))) == Repo(root=str(git_repo))


async def test_a_broken_repository_is_refused_with_git_error(git_repo: Path) -> None:
    _break_config(git_repo)

    with pytest.raises(RefusedError) as caught:
        await open_repo(str(_task_file(git_repo)))

    assert caught.value.reason == "git-error"
    assert str(caught.value).startswith("refused: git-error: --commit: git rev-parse: ")


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
    front_end: PlainFrontEnd | None = None,
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
    assert git(git_repo, "rev-parse", "HEAD").strip() in message
    assert "tasks.yaml was not updated" in message
    assert "failed to save tasks to" in message


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
    assert git(git_repo, "rev-parse", "HEAD").strip() in message
    assert "tasks.yaml was not updated" in message


async def test_work_tree_root_names_the_repository(git_repo: Path) -> None:
    assert await work_tree_root() == str(git_repo)


async def test_work_tree_root_outside_a_repository_is_none(
    outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)

    assert await work_tree_root() is None
