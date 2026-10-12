"""The plain real run: load the inputs, then run every pending task."""

import sys
from collections.abc import Sequence
from datetime import datetime
from importlib import metadata
from pathlib import Path

from alr.engine.loop import (
    RunOptions,
    read_tasks,
    reset_in_memory,
    run,
    save_for_start,
)
from alr.engine.stop import Stop
from alr.errors import (
    INVALID_TASK_FILE,
    AfterRunFailedError,
    RefusedError,
    RunCancelledError,
    RunError,
    TaskFileError,
)
from alr.plain.frontend import PlainFrontEnd
from alr.plain.report import refuse_failed_report
from alr.record.runlog import Info, RunLog, run_dir
from alr.tasks import TaskList


async def start(
    tasks_file: str,
    action_timeout: str,
    session_args: Sequence[str],
    reset: bool = False,
    sandbox_settings: str = "",
    stop: Stop | None = None,
) -> None:
    """Run the loop in plain mode: load, then run every pending task.

    With reset, failed tasks go back to pending, without a word. The reset
    reaches the disk only after the run log has opened, so a refused run
    changes nothing on disk.
    Every run is logged in a new folder under logs/ beside the task file.
    """
    task_list = _read_for_start(tasks_file)
    path = run_dir(Path(tasks_file).parent / "logs", datetime.now().astimezone())
    reset_count = reset_in_memory(task_list) if reset else 0
    refuse_failed_report(sys.stdout, task_list)
    info = _info(tasks_file, action_timeout, sandbox_settings)
    log = RunLog.open(path, info)
    try:
        if reset_count:
            _save_reset(tasks_file, task_list, log)

        await _run_logged(
            task_list,
            tasks_file,
            action_timeout,
            session_args,
            log,
            stop or Stop(),
        )
    finally:
        log.close()


def _read_for_start(tasks_file: str) -> TaskList:
    """Read the task file, failed tasks and all, so a bad file is refused before anything else is looked at."""
    try:
        return read_tasks(tasks_file)
    except TaskFileError as exc:
        raise RefusedError(
            INVALID_TASK_FILE, f"failed to start loop runner: {exc}"
        ) from exc


def _save_reset(tasks_file: str, task_list: TaskList, log: RunLog) -> None:
    """Save the reset; a refused save takes the run folder it just made with it."""
    try:
        save_for_start(tasks_file, task_list)
    except RefusedError:
        log.discard()
        raise


def _info(tasks_file: str, action_timeout: str, sandbox_settings: str) -> Info:
    return Info(
        version=metadata.version("alr"),
        tasks_file=tasks_file,
        permissions="sandbox" if sandbox_settings else "skip",
        sandbox_settings=sandbox_settings,
        action_timeout=action_timeout,
    )


async def _run_logged(
    task_list: TaskList,
    tasks_file: str,
    action_timeout: str,
    session_args: Sequence[str],
    log: RunLog,
    stop: Stop,
) -> None:
    options = RunOptions(
        tasks_file=tasks_file,
        action_timeout=action_timeout,
        session_args=session_args,
        stop=stop,
        log_dir=str(log.path.parent),
    )
    try:
        await run(task_list, options, PlainFrontEnd(record=log.record))
    except RunCancelledError as exc:
        raise RunCancelledError(f"loop error: {exc}") from exc
    except AfterRunFailedError as exc:
        # Its own class, so the exit code 4 survives the prefix.
        raise AfterRunFailedError(f"loop error: {exc}") from exc
    except RunError as exc:
        raise RunError(f"loop error: {exc}") from exc
