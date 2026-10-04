"""--commit: check the repository before a run, and commit each completed task's work."""

import os
from dataclasses import dataclass
from pathlib import Path

from alr.engine import proc
from alr.engine.frontend import FrontEnd
from alr.engine.gates import first_line
from alr.errors import RepoError
from alr.tasks import COMPLETED, FAILED, Task

# What git says when the working directory has no work tree: no repository at
# all, or a bare repository or .git directory.
_NOT_A_REPO = ("not a git repository", "must be run in a work tree")


@dataclass(frozen=True)
class Repo:
    """The git work tree a --commit run commits into."""

    root: str
    """The work tree's top-level directory."""


async def open_repo(tasks_file: str) -> Repo | None:
    """Find the work tree around the working directory for a --commit run.

    Outside a work tree, or without git installed, there is nothing to commit
    and the result is None. Any other git failure is an error, so a repository
    alr cannot read never turns into a run that silently commits nothing.
    Inside a work tree, git must not see tasks_file and the tree must be
    clean, so that each commit holds exactly one task's work.
    """
    root = await _find_root()
    if not root:
        return None

    repo = Repo(root=root)
    await _check_task_file(repo, tasks_file)
    await _check_clean(repo)
    return repo


async def check_log_dir(repo: Repo | None, run_dir: str) -> None:
    """Refuse a log directory git can see.

    `git add -A` takes no exclusions, so an ignore rule is all that keeps a
    run's record out of a task's commit. run_dir, the run's own folder inside
    the log directory, is the path checked because neither exists yet, and git
    matches a directory pattern such as `logs/` only for a path it can tell
    lies inside that directory.
    """
    if repo is None:
        return

    rel = _relative(repo, run_dir)
    if rel is None or await _ignored(repo, rel):
        return

    raise RepoError(
        f"--commit needs the log directory ignored by git or outside the repository: {Path(rel).parent}"
    )


async def commit(repo: Repo, task: Task, front_end: FrontEnd) -> tuple[str, str]:
    """Commit what the task left in the work tree, named after the task; return its final outcome."""
    error = await _commit_changes(repo, task, front_end)
    if error is None:
        return COMPLETED, ""

    return FAILED, f"commit failed: {error}"


async def _commit_changes(repo: Repo, task: Task, front_end: FrontEnd) -> str | None:
    """Stage everything and commit it; return why git refused, or None.

    A task that changed nothing is not an error and gets no empty commit; that
    also covers a session that committed by itself.
    """
    error = await front_end.run_git(task, ["git", "add", "-A"], repo.root)
    if error is not None:
        return error

    # Exit 1 is git's answer "something is staged", not a failure.
    returncode, _, _ = await _git(repo, "diff", "--cached", "--quiet")
    if returncode != 1:
        return proc.exit_text(returncode)

    front_end.commit_started(task)
    error = await front_end.run_git(task, ["git", "commit", "-m", task.name], repo.root)
    if error is not None:
        return error

    # The commit is already made, so failing to read its hash must not change
    # the task's outcome: the front end is then simply not told.
    returncode, commit_hash, _ = await _git(repo, "rev-parse", "HEAD")
    if returncode == 0:
        front_end.committed(task, commit_hash.strip())

    return None


async def _find_root() -> str | None:
    """Return the work tree's root, or None when there is none."""
    try:
        returncode, stdout, stderr = await proc.capture(
            ["git", "rev-parse", "--show-toplevel"],
            # git's message is matched below, so it must not be localized.
            env={**os.environ, "LC_ALL": "C"},
            gentle=True,
        )
    except FileNotFoundError:
        return None

    if returncode == 0:
        return stdout.strip()

    if any(message in stderr for message in _NOT_A_REPO):
        return None

    raise RepoError(f"--commit: git rev-parse: {_git_message(returncode, stderr)}")


def _git_message(returncode: int, stderr: str) -> str:
    """Say why a git command failed: its first stderr line, or its exit when it said nothing."""
    message = first_line(stderr.strip()).removeprefix("fatal: ")
    return message or f"{proc.exit_text(returncode)}"


async def _check_task_file(repo: Repo, tasks_file: str) -> None:
    """Refuse a task file git can see.

    alr rewrites it after every task and never names it to git, so an ignore
    rule is all that keeps it out of the commits; a tracked file is not
    ignored, however its name matches a pattern.
    """
    rel = _relative(repo, tasks_file)
    if rel is None or await _ignored(repo, rel):
        return

    raise RepoError(
        f"--commit needs the task file ignored by git or outside the repository: {rel}"
    )


async def _check_clean(repo: Repo) -> None:
    """Refuse a work tree with uncommitted changes."""
    # Every untracked file is listed by name, and quotePath keeps non-ASCII
    # names readable in the error.
    returncode, dirty, stderr = await _git(
        repo,
        "-c",
        "core.quotePath=false",
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    if returncode != 0:
        raise RepoError(f"--commit: git status: {_git_message(returncode, stderr)}")

    if dirty.strip():
        files = dirty.rstrip("\n")
        raise RepoError(
            f"--commit needs a clean work tree; commit, stash, or remove:\n{files}"
        )


def _relative(repo: Repo, path: str) -> str | None:
    """Return path relative to the work tree's root, or None when it lies outside the tree.

    Only the parent directory is resolved: git sees a symlink as the link
    itself, so a link to a file elsewhere is still inside. git reports the
    root with symlinks resolved, so the parent must be too; resolve() keeps
    the part that does not exist yet as written.
    """
    # abspath, not Path.absolute, because it also folds away "..".
    target = Path(os.path.abspath(path))
    resolved = target.parent.resolve() / target.name
    try:
        return str(resolved.relative_to(repo.root))
    except ValueError:
        return None


async def _ignored(repo: Repo, rel: str) -> bool:
    """Report whether git ignores rel, a path relative to the root."""
    returncode, _, stderr = await _git(repo, "check-ignore", "-q", "--", rel)
    # Exit 0 is ignored and 1 is not ignored; anything else is a failure, not an answer.
    if returncode not in (0, 1):
        raise RepoError(
            f"--commit: git check-ignore: {_git_message(returncode, stderr)}"
        )

    return returncode == 0


async def _git(repo: Repo, *args: str) -> tuple[int, str, str]:
    """Run a quiet git command from the root, so it means the same wherever alr was started."""
    return await proc.capture(["git", *args], cwd=repo.root, gentle=True)
