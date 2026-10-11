"""What Start does before a run: the startup checks again, then the saves."""

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path

from alr.engine.commit import (
    CommitMode,
    Repo,
    check_clean,
    check_log_dir,
    find_repo,
    held_lock,
)
from alr.engine.loop import (
    RunOptions,
    read_tasks,
    refuse_failed,
    reset_in_memory,
    save_for_start,
)
from alr.engine.stop import Stop
from alr.errors import LOG_FOLDER_VISIBLE, RefusedError
from alr.record.events import Event
from alr.record.runlog import Info, RunLog, run_dir
from alr.tasks import TaskList
from alr.tui.settings import Permissions, RunSettings


@dataclass
class Prepared:
    """A run that passed every check and is ready to hand to the engine."""

    task_list: TaskList
    options: RunOptions
    log: RunLog | None = None
    locks: ExitStack = field(default_factory=ExitStack)
    """What the run holds until close: the repository's commit lock, for a run that commits."""

    @property
    def record(self) -> Callable[[Event], None] | None:
        return None if self.log is None else self.log.record

    def close(self) -> None:
        if self.log is not None:
            self.log.close()

        self.locks.close()

    def discard(self) -> None:
        """Close the log and remove its folder, for a run refused after the log opened."""
        if self.log is not None:
            self.log.discard()


async def prepare(settings: RunSettings, stop: Stop | None = None) -> Prepared:
    """Check everything a run starts with; nothing is written before every check passes.

    The dialogs checked their own answers as they were given, but the files
    and the repository can change meanwhile, so the checks run again here on
    the values the run will use. The run's `RunOptions` carry `stop`, the
    app's own object, so the engine reports the signal the app was given; the
    default is for callers that handle no signals. A run that commits holds the
    repository's commit lock from here until `Prepared.close`; a refusal, or
    any other way out of this function, releases it.
    """
    locks = ExitStack()
    try:
        return await _prepare(settings, stop, locks)
    except BaseException:
        locks.close()
        raise


async def _prepare(
    settings: RunSettings, stop: Stop | None, locks: ExitStack
) -> Prepared:
    task_list = read_tasks(settings.tasks_file)
    repo = await _find_repo(settings, locks)
    session_args = settings.session_args()
    log_path = await _check_log_folder(settings, repo)
    # After the log folder, so a tree dirty only with it is told to ignore it.
    if repo is not None:
        await check_clean(repo)

    # In memory only: the view's own copy was reset for display, and this one
    # is read again from disk.
    reset_count = reset_in_memory(task_list) if settings.reset_failed else 0
    refuse_failed(task_list)
    log = None if log_path is None else _open_log(settings, log_path)
    options = RunOptions(
        tasks_file=settings.tasks_file,
        action_timeout=settings.action_timeout,
        session_args=session_args,
        repo=repo,
        commit_mode=settings.commit_mode,
        stop=Stop() if stop is None else stop,
        # The folder the log really opened, which has a suffix when the name was taken.
        run_dir=None if log is None else str(log.path),
    )
    prepared = Prepared(task_list, options, log, locks)
    # After the log opens, so a check or the log failing never leaves the task file rewritten.
    try:
        _save_changes(settings, task_list, reset_count)
    except RefusedError:
        prepared.discard()
        raise

    return prepared


async def _find_repo(settings: RunSettings, locks: ExitStack) -> Repo | None:
    """Look at the repository only when the run commits; with none git is never touched.

    The commit lock is taken before any check that reads the tree: a run that
    holds it leaves the tree dirty, and the second run is to be told it is
    locked out.
    """
    if settings.commit_mode is CommitMode.NONE:
        return None

    repo = await find_repo(settings.tasks_file)
    locks.enter_context(held_lock(repo))
    return repo


async def _check_log_folder(settings: RunSettings, repo: Repo | None) -> Path | None:
    """Return the new run's log folder, or None with logging off; refuse one git can see."""
    if not settings.logging:
        return None

    path = run_dir(settings.log_folder(), datetime.now().astimezone())
    try:
        await check_log_dir(repo, str(path))
    except RefusedError as exc:
        # Any other refusal (a git failure) keeps its own reason.
        if exc.reason != LOG_FOLDER_VISIBLE:
            raise

        raise RefusedError(
            LOG_FOLDER_VISIBLE,
            f"the log folder {settings.log_folder()} is not ignored by git; "
            "add it to .gitignore or turn logging off",
        ) from exc

    return path


def _save_changes(settings: RunSettings, task_list: TaskList, reset_count: int) -> None:
    """Save the edited actions and the reset together, once; nothing to save leaves the file alone."""
    if settings.after_task is not None:
        task_list.shared.after_task = settings.after_task

    if settings.after_task is None and not reset_count:
        return

    save_for_start(settings.tasks_file, task_list)


def _open_log(settings: RunSettings, path: Path) -> RunLog:
    skip = settings.permissions is Permissions.SKIP
    info = Info(
        version=metadata.version("alr"),
        tasks_file=settings.tasks_file,
        permissions="skip" if skip else "sandbox",
        sandbox_settings="" if skip else settings.sandbox_file,
        action_timeout=settings.action_timeout,
        commit=settings.commit_mode.value,
    )
    return RunLog.open(path, info)
