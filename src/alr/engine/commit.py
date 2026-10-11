"""--commit: check the repository before a run, and commit each completed task's work."""

import errno
import fcntl
import os
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from alr.engine import proc
from alr.engine.frontend import FrontEnd
from alr.engine.actions import first_line
from alr.errors import (
    DIRTY_TREE,
    GIT_ERROR,
    LOCKED,
    LOG_FOLDER_VISIBLE,
    TASK_FILE_VISIBLE,
    RefusedError,
)
from alr.tasks import Task

# What git says when the working directory has no work tree: no repository at
# all, or a bare repository or .git directory.
_NOT_A_REPO = ("not a git repository", "must be run in a work tree")

_LOCK_NAME = "alr.lock"
_LOCKED_MESSAGE = "another alr run is committing in this repository"

# Runs right before staging; a reason it returns is the commit's failure.
type Guard = Callable[[], Awaitable[str | None]]


class CommitMode(StrEnum):
    """When a run commits: never, after each completed task, or once after the last one."""

    NONE = "none"
    TASK = "task"
    RUN = "run"


@dataclass(frozen=True)
class Repo:
    """The git work tree a --commit run commits into."""

    root: str
    """The work tree's top-level directory."""

    git_dir: str
    """The absolute git directory; a linked worktree has its own, and so its own commit lock."""


async def open_repo(tasks_file: str) -> Repo | None:
    """Find the work tree around the working directory for a --commit run.

    Outside a work tree, or without git installed, there is nothing to commit
    and the result is None. Any other git failure is an error, so a repository
    alr cannot read never turns into a run that silently commits nothing.
    Inside a work tree, git must not see tasks_file and the tree must be
    clean, so that each commit holds exactly one task's work.
    """
    repo = await find_repo(tasks_file)
    if repo is not None:
        await check_clean(repo)

    return repo


async def find_repo(tasks_file: str) -> Repo | None:
    """Find the work tree like open_repo, but leave the clean-tree check to the caller.

    A caller that also checks the log directory does that first: a tree dirty
    only with the run's own log folder needs that folder ignored, not committed.
    """
    root = await _find_root()
    if not root:
        return None

    repo = Repo(root=root, git_dir=await _find_git_dir(root))
    await _check_task_file(repo, tasks_file)
    return repo


@contextmanager
def held_lock(repo: Repo | None) -> Iterator[None]:
    """Hold the repository's commit lock for the whole run, or refuse when another run has it.

    flock, not a file that exists or not: the kernel drops the lock when alr
    dies, however it dies, so a killed run never leaves a stuck lock behind,
    and the file a finished run leaves is no lock at all. The descriptor is
    not inherited, so git and the sessions never keep the lock alive.
    """
    if repo is None:
        yield
        return

    fd = _lock(_lock_path(repo), os.O_CREAT, fcntl.LOCK_EX)
    try:
        yield
    finally:
        os.close(fd)


def check_unlocked(repo: Repo | None) -> None:
    """Refuse like held_lock when another run holds the lock, without keeping it.

    Read-only, as a dry run writes nothing: no lock file is made, and the
    lock taken is shared, so two dry runs never refuse each other. Without a
    lock file yet, the git directory must still allow making one, as a real
    run's would be refused otherwise.
    """
    if repo is None:
        return

    path = _lock_path(repo)
    if not path.exists():
        _check_creatable(path)
        return

    os.close(_lock(path, 0, fcntl.LOCK_SH))


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

    raise RefusedError(
        LOG_FOLDER_VISIBLE,
        f"--commit needs the log directory ignored by git or outside the repository: {Path(rel).parent}",
    )


async def recheck_ignored(
    repo: Repo, tasks_file: str, run_dir: str | None
) -> str | None:
    """Say which bookkeeping path git can see now, or None when all are still hidden.

    A task's own commands can edit .gitignore, and `git add -A` takes no
    exclusions: an ignore rule is all that keeps the task file and the log
    folders out of the commit, so it is checked again right before staging.
    Every earlier run's folder in the log directory counts, not only this
    run's: a rule that unhides one of those is taken by `git add -A` too.
    A git that cannot start, or a `git check-ignore` that fails, is a reason
    too, as it is for the `git add` that would follow: tasks may already be
    committed by now, so it fails this commit and does not refuse the run.
    """
    task_rel = _relative(repo, tasks_file)
    log_rel = None if run_dir is None else _relative(repo, run_dir)
    logs_rel = _relative(repo, _logs_dir(tasks_file, run_dir))
    try:
        return await _first_visible(repo, task_rel, log_rel, logs_rel)
    except OSError as exc:
        return proc.start_failed_text("git", exc)
    except RefusedError as exc:
        # The reason follows "commit failed: ", which already says what it was.
        return exc.message.removeprefix("--commit: ")


def _logs_dir(tasks_file: str, run_dir: str | None) -> str:
    """Return the log directory: the run folder's parent, else the default beside the task file."""
    if run_dir is None:
        return str(Path(tasks_file).parent / "logs")

    return str(Path(run_dir).parent)


async def _first_visible(
    repo: Repo, task_rel: str | None, log_rel: str | None, logs_rel: str | None
) -> str | None:
    """Name the first of the task file and the log folders that git does not ignore, or None."""
    if task_rel is not None and not await _ignored(repo, task_rel):
        return f"task file visible: {task_rel}"

    if log_rel is not None and not await _ignored(repo, log_rel):
        return f"log folder visible: {Path(log_rel).parent}"

    if logs_rel is not None and await _has_visible_files(repo, logs_rel):
        return f"log folder visible: {logs_rel}"

    return None


async def _has_visible_files(repo: Repo, rel: str) -> bool:
    """Report whether rel holds untracked files that no ignore rule hides; a missing rel holds none."""
    returncode, stdout, stderr = await _git(
        repo, "ls-files", "--others", "--exclude-standard", "--", rel
    )
    if returncode != 0:
        raise RefusedError(
            GIT_ERROR, f"--commit: git ls-files: {_git_message(returncode, stderr)}"
        )

    return bool(stdout.strip())


async def commit(
    repo: Repo, task: Task, front_end: FrontEnd, guard: Guard
) -> str | None:
    """Commit what the task left in the work tree, named after the task; return why it was not made, or None."""
    return await _commit_changes(repo, task, front_end, [task.name], guard)


async def commit_all(
    repo: Repo, tasks: Sequence[Task], front_end: FrontEnd, guard: Guard
) -> str | None:
    """Commit everything the run left in the work tree, once; return why it was not made, or None.

    The front end's calls each name a task, so the last one carries the git steps.
    """
    noun = "task" if len(tasks) == 1 else "tasks"
    title = f"alr: {len(tasks)} {noun} completed"
    body = "\n".join(f"{task.id}: {task.name}" for task in tasks)
    return await _commit_changes(repo, tasks[-1], front_end, [title, body], guard)


async def head(repo: Repo) -> str | None:
    """Return the hash HEAD points at, or None when there is no commit yet or git cannot say."""
    try:
        returncode, stdout, _ = await _git(repo, "rev-parse", "--verify", "-q", "HEAD")
    except OSError:
        return None

    if returncode != 0:
        return None

    return stdout.strip()


async def work_tree_root() -> str | None:
    """Return the root of the work tree around the working directory, or None when there is none."""
    return await _find_root()


async def _commit_changes(
    repo: Repo,
    task: Task,
    front_end: FrontEnd,
    messages: Sequence[str],
    guard: Guard,
) -> str | None:
    """Stage everything and commit it; return why it was not made, or None.

    The guard runs first, so a reason it gives leaves nothing staged. A task
    that changed nothing is not an error and gets no empty commit; that also
    covers a session that committed by itself.
    """
    reason = await guard()
    if reason is not None:
        return reason

    error = await front_end.run_git(task, ["git", "add", "-A"], repo.root)
    if error is not None:
        return error

    # Exit 1 is git's answer "something is staged", not a failure.
    returncode, _, _ = await _git(repo, "diff", "--cached", "--quiet")
    if returncode != 1:
        return proc.exit_text(returncode)

    front_end.commit_started(task)
    argv = ["git", "commit"]
    for message in messages:
        argv += ["-m", message]

    error = await front_end.run_git(task, argv, repo.root)
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

    raise RefusedError(
        GIT_ERROR, f"--commit: git rev-parse: {_git_message(returncode, stderr)}"
    )


async def _find_git_dir(root: str) -> str:
    """Return the absolute git directory of the work tree at root."""
    returncode, stdout, stderr = await proc.capture(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=root, gentle=True
    )
    if returncode != 0:
        raise RefusedError(
            GIT_ERROR, f"--commit: git rev-parse: {_git_message(returncode, stderr)}"
        )

    return stdout.strip()


def _lock_path(repo: Repo) -> Path:
    return Path(repo.git_dir) / _LOCK_NAME


def _check_creatable(path: Path) -> None:
    """Refuse, as making the lock file would, a git directory the user cannot write, creating nothing."""
    if not os.access(path.parent, os.W_OK | os.X_OK):
        raise _unusable(path, OSError(errno.EACCES, os.strerror(errno.EACCES)))


def _lock(path: Path, flags: int, operation: int) -> int:
    """Open the lock file and flock it without waiting; return the descriptor, which holds the lock.

    Opened read-only: flock does not need write access. The file is created
    with 0o666 masked by the umask, and core.sharedRepository does not change
    that, so a second user of a shared repository can open the file the first
    one made only when the first user's umask left it readable by others.
    Under a stricter umask their open fails with EACCES, and the run is refused
    as git-error.
    """
    try:
        fd = os.open(path, flags | os.O_RDONLY, 0o666)
    except OSError as exc:
        raise _unusable(path, exc) from exc

    try:
        fcntl.flock(fd, operation | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RefusedError(LOCKED, _LOCKED_MESSAGE) from None
    except OSError as exc:
        os.close(fd)
        raise _unusable(path, exc) from exc

    return fd


def _unusable(path: Path, exc: OSError) -> RefusedError:
    """Say that alr cannot use the lock file, which it cannot tell from a git failure."""
    return RefusedError(GIT_ERROR, f"--commit: cannot lock {path}: {exc.strerror}")


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

    raise RefusedError(
        TASK_FILE_VISIBLE,
        f"--commit needs the task file ignored by git or outside the repository: {rel}",
    )


async def check_clean(repo: Repo) -> None:
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
        raise RefusedError(
            GIT_ERROR, f"--commit: git status: {_git_message(returncode, stderr)}"
        )

    if dirty.strip():
        files = dirty.rstrip("\n")
        raise RefusedError(
            DIRTY_TREE,
            f"--commit needs a clean work tree; commit, stash, or remove:\n{files}",
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
        raise RefusedError(
            GIT_ERROR,
            f"--commit: git check-ignore: {_git_message(returncode, stderr)}",
        )

    return returncode == 0


async def _git(repo: Repo, *args: str) -> tuple[int, str, str]:
    """Run a quiet git command from the root, so it means the same wherever alr was started."""
    return await proc.capture(["git", *args], cwd=repo.root, gentle=True)
