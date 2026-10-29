"""What Start does before a run: the startup checks again, then the saves."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

from alr.engine.loop import (
    RunOptions,
    read_tasks,
    refuse_failed,
    reset_in_memory,
    save_for_start,
)
from alr.engine.presets import restore_previous, write_preset
from alr.engine.stop import Stop
from alr.errors import RefusedError
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

    @property
    def record(self) -> Callable[[Event], None] | None:
        return None if self.log is None else self.log.record

    def close(self) -> None:
        if self.log is not None:
            self.log.close()

    def discard(self) -> None:
        """Close the log and remove its folder, for a run refused after the log opened."""
        if self.log is not None:
            self.log.discard()


async def prepare(settings: RunSettings, stop: Stop | None = None) -> Prepared:
    """Check everything a run starts with; nothing is written before every check passes.

    The dialogs checked their own answers as they were given, but the files
    can change meanwhile, so the checks run again here on the values the run
    will use. The run's `RunOptions` carry `stop`, the app's own object, so the
    engine reports the signal the app was given; the default is for callers
    that handle no signals.
    """
    task_list = read_tasks(settings.tasks_file)
    session_args = settings.session_args()
    log_path = _log_folder(settings)
    # In memory only: the view's own copy was reset for display, and this one
    # is read again from disk.
    reset_count = reset_in_memory(task_list) if settings.reset_failed else 0
    refuse_failed(task_list)
    log = None if log_path is None else _open_log(settings, log_path)
    options = RunOptions(
        tasks_file=settings.tasks_file,
        action_timeout=settings.action_timeout,
        session_args=session_args,
        stop=Stop() if stop is None else stop,
        log_dir=str(settings.log_folder()),
    )
    prepared = Prepared(task_list, options, log)
    # After the log opens, so a check or the log failing never leaves the task file rewritten.
    try:
        _save_changes(settings, task_list, reset_count)
    except RefusedError:
        prepared.discard()
        raise

    return prepared


def _log_folder(settings: RunSettings) -> Path | None:
    """Return the new run's log folder, or None with logging off."""
    if not settings.logging:
        return None

    return run_dir(settings.log_folder(), datetime.now().astimezone())


def _save_changes(settings: RunSettings, task_list: TaskList, reset_count: int) -> None:
    """Write the preset, then save the actions and the reset; a refused save puts the old preset back."""
    if settings.switches is None:
        _save_tasks(settings, task_list, reset_count)
        return

    previous = write_preset(settings.sandbox_file, settings.switches)
    try:
        _save_tasks(settings, task_list, reset_count)
    except RefusedError:
        restore_previous(settings.sandbox_file, previous)
        raise


def _save_tasks(settings: RunSettings, task_list: TaskList, reset_count: int) -> None:
    """Save the edited actions and the reset together, once; nothing to save leaves the file alone."""
    if settings.after_task is not None:
        task_list.shared.after_task = settings.after_task

    if settings.after_run is not None:
        task_list.shared.after_run = settings.after_run

    edited = settings.after_task is not None or settings.after_run is not None
    if not edited and not reset_count:
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
    )
    return RunLog.open(path, info)
