"""What Start does before a run: the startup checks again, then the saves."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

from alr.engine.commit import CommitMode, Repo, check_clean, check_log_dir, find_repo
from alr.engine.loop import RunOptions, load_tasks
from alr.errors import LOG_FOLDER_VISIBLE, RefusedError, TaskFileError
from alr.record.events import Event
from alr.record.runlog import Info, RunLog, run_dir
from alr.tasks import TaskList, save_tasks
from alr.tui.settings import Permissions, RunSettings


@dataclass
class Prepared:
    """A run that passed every check and is ready to hand to the engine."""

    task_list: TaskList
    options: RunOptions
    log: RunLog | None = None

    @property
    def record(self) -> Callable[[Event], None] | None:
        return None if self.log is None else self.log.record

    def close(self) -> None:
        if self.log is not None:
            self.log.close()


async def prepare(settings: RunSettings) -> Prepared:
    """Check everything a run starts with; nothing is written before every check passes.

    The dialogs checked their own answers as they were given, but the files
    and the repository can change meanwhile, so the checks run again here on
    the values the run will use.
    """
    task_list = load_tasks(settings.tasks_file)
    repo = await _find_repo(settings)
    session_args = settings.session_args()
    log_path = await _check_log_folder(settings, repo)
    # After the log folder, so a tree dirty only with it is told to ignore it.
    if repo is not None:
        await check_clean(repo)

    options = RunOptions(
        tasks_file=settings.tasks_file,
        gate_timeout=settings.gate_timeout,
        session_args=session_args,
        repo=repo,
        commit_mode=settings.commit_mode,
    )
    log = None if log_path is None else _open_log(settings, log_path)
    prepared = Prepared(task_list, options, log)
    # Last, so a check or the log failing never leaves the task file rewritten.
    try:
        _save_gates(settings, task_list)
    except TaskFileError:
        prepared.close()
        raise

    return prepared


async def _find_repo(settings: RunSettings) -> Repo | None:
    """Look at the repository only when the run commits; with none git is never touched."""
    if settings.commit_mode is CommitMode.NONE:
        return None

    return await find_repo(settings.tasks_file)


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


def _save_gates(settings: RunSettings, task_list: TaskList) -> None:
    if settings.gates is None:
        return

    task_list.shared.gates = settings.gates
    save_tasks(settings.tasks_file, task_list)


def _open_log(settings: RunSettings, path: Path) -> RunLog:
    skip = settings.permissions is Permissions.SKIP
    info = Info(
        version=metadata.version("alr"),
        tasks_file=settings.tasks_file,
        permissions="skip" if skip else "sandbox",
        sandbox_settings="" if skip else settings.sandbox_file,
        gate_timeout=settings.gate_timeout,
        commit=settings.commit_mode.value,
    )
    return RunLog.open(path, info)
