"""The run loop: each pending task in a fresh Claude session, saved as soon as it ends."""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple

from alr.engine.frontend import FrontEnd
from alr.engine.actions import action_env, run_actions
from alr.engine.result import last_result_line, outcome
from alr.engine.stop import Stop
from alr.errors import (
    FAILED_TASKS_MESSAGE,
    TASK_FILE_UNWRITABLE,
    AfterRunFailedError,
    AlrError,
    FailedTasksError,
    RefusedError,
    RunCancelledError,
    RunError,
    RunLogError,
    TaskFileError,
    quote,
)
from alr.tasks import (
    COMPLETED,
    FAILED,
    PENDING,
    RUN_SCOPE,
    RunResult,
    Task,
    TaskList,
    parse_tasks,
    save_tasks,
)

__all__ = ["FAILED_TASKS_MESSAGE", "RunOptions", "read_tasks", "run"]

# What a stop that carries no task of its own is recorded as; a stop inside a
# task says "task N: name: stopped" through RunCancelledError.
_STOPPED = "stopped"


class _Outcome(NamedTuple):
    """What a task ended as: the state and error to save."""

    state: str
    error: str


@dataclass(frozen=True)
class RunOptions:
    """What a run needs besides the tasks."""

    tasks_file: str

    action_timeout: str
    """The --action-timeout value; empty when the flag was not passed."""

    session_args: Sequence[str]
    """The Claude flags that set what a session may do."""

    log_dir: str
    """The folder the run folders go in, beside the task file; given to every action, logging or not."""

    stop: Stop = field(default_factory=Stop)
    """Which signal stopped the run; the caller that handles signals fills it in."""


def read_tasks(path: str) -> TaskList:
    """Read and validate the task file, failed tasks and all."""
    try:
        # Strict: the file is saved back, and a replacement character would
        # overwrite the user's byte on the first save.
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise TaskFileError(
            f"tasks file {quote(path)} is not accessible: {exc.strerror}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise TaskFileError(f"tasks file {quote(path)} is not valid UTF-8") from exc

    try:
        task_list = parse_tasks(text)
    except TaskFileError as exc:
        raise TaskFileError(f"failed to parse tasks yaml: {exc}") from exc

    return task_list


def refuse_failed(task_list: TaskList) -> None:
    """Refuse a list that holds a failed task."""
    if any(task.state == FAILED for task in task_list.tasks):
        raise FailedTasksError(task_list)


def reset_in_memory(task_list: TaskList) -> int:
    """Set every failed task back to pending, in the list only, and return how many there were."""
    failed = [task for task in task_list.tasks if task.state == FAILED]
    for task in failed:
        task.state, task.error = PENDING, ""

    return len(failed)


def reset_failed(tasks_file: str) -> int:
    """Set every failed task back to pending and return how many there were.

    The file is rewritten only when something was reset, so a file with
    nothing failed stays exactly as the user left it.
    """
    task_list = read_tasks(tasks_file)
    count = reset_in_memory(task_list)
    if count == 0:
        return 0

    save_tasks(tasks_file, task_list)
    return count


def save_for_start(tasks_file: str, task_list: TaskList) -> None:
    """Save the changes a start made to the list; a file that cannot be written refuses the run."""
    try:
        save_tasks(tasks_file, task_list)
    except TaskFileError as exc:
        raise RefusedError(TASK_FILE_UNWRITABLE, str(exc)) from exc


async def run(task_list: TaskList, options: RunOptions, front_end: FrontEnd) -> None:
    """Run every pending task in file order, saving the file after each one."""
    started = time.monotonic()
    error: str | None = None
    ended: BaseException | None = None
    result, exit_code = RunResult.COMPLETED, 0
    try:
        await _run_loop(task_list, options, front_end)
    except BaseException as exc:
        # Whatever ends the run is recorded, so a crash never reads as a clean finish.
        ended = exc
        error = _error_text(exc)
        result, exit_code = _ending(exc, options.stop)
        raise
    finally:
        _close_record(
            front_end, ended, error, time.monotonic() - started, result, exit_code
        )


def _close_record(
    front_end: FrontEnd,
    ended: BaseException | None,
    error: str | None,
    seconds: float,
    result: RunResult,
    exit_code: int,
) -> None:
    """Write the run's end; a log that cannot take it must not hide the error that ended the run."""
    try:
        front_end.run_done(error, seconds, result, exit_code)
    except RunLogError as exc:
        if ended is None:
            raise

        ended.add_note(f"the run log could not record the end of the run: {exc}")


def _ending(exc: BaseException, stop: Stop) -> tuple[RunResult, int]:
    """Name how the run ended and the code alr will exit with."""
    if isinstance(exc, RunCancelledError | asyncio.CancelledError | KeyboardInterrupt):
        return RunResult.STOPPED, stop.exit_code()

    if isinstance(exc, AfterRunFailedError):
        return RunResult.RUN_FAILED, exc.exit_code

    if isinstance(exc, AlrError):
        return RunResult.FAILED, exc.exit_code

    return RunResult.FAILED, 1


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, RunError):
        return str(exc)

    # A stop is not a crash, so it must not read like one in the record.
    if isinstance(exc, asyncio.CancelledError | KeyboardInterrupt):
        return _STOPPED

    name = type(exc).__name__
    return f"{name}: {exc}" if str(exc) else name


async def _run_loop(
    task_list: TaskList, options: RunOptions, front_end: FrontEnd
) -> None:
    for task in task_list.tasks:
        if task.state == COMPLETED:
            front_end.task_skipped(task)
            continue

        await _run_and_save(task, task_list, options, front_end)

    await _run_after_run(task_list, options, front_end)


async def _run_after_run(
    task_list: TaskList, options: RunOptions, front_end: FrontEnd
) -> None:
    """Run the file's after_run actions once; a failure ends the run with its own exit code.

    The tasks are already saved completed, so nothing here changes them. It
    runs even when every task was already completed, so a rerun after a
    failure here tries again.
    """
    actions = task_list.after_run_list()
    if not actions:
        return

    env = action_env(None, options.tasks_file, options.log_dir)
    state, error = await run_actions(
        RUN_SCOPE, actions, options.action_timeout, front_end, env
    )
    if state == COMPLETED:
        return

    raise AfterRunFailedError(error)


async def _run_and_save(
    task: Task,
    task_list: TaskList,
    options: RunOptions,
    front_end: FrontEnd,
) -> None:
    # The front end gets copies, so nothing it keeps changes under it.
    front_end.task_started(replace(task))
    started = time.monotonic()
    ended = await _run_task(task, task_list, options, front_end)
    task.state, task.error = ended.state, ended.error
    failure = (
        None
        if ended.state == COMPLETED
        else f"task {task.id}: {task.name} failed: {ended.error}"
    )
    _save(task, task_list, options.tasks_file, failure)
    front_end.task_finished(replace(task), failure, time.monotonic() - started)
    if failure is None:
        return

    raise RunError(failure)


async def _run_task(
    task: Task,
    task_list: TaskList,
    options: RunOptions,
    front_end: FrontEnd,
) -> _Outcome:
    """Run the task's session and its after_task actions; return the state and error to save."""
    stdin_text = f"{task_list.shared.prompt}\n\n{task.text()}\n"
    argv = ["claude", "--print", *front_end.session_flags, *options.session_args]
    started = time.monotonic()
    try:
        result = await front_end.run_session(replace(task), argv, stdin_text)
    except asyncio.CancelledError:
        # Nothing is saved, so the next run picks the task up again.
        raise RunCancelledError(f"task {task.id}: {task.name}: stopped") from None

    state, error = outcome(result.error, last_result_line(result.text))
    front_end.session_finished(
        replace(task, state=state, error=error), time.monotonic() - started
    )
    if state != COMPLETED:
        return _Outcome(state, error)

    env = action_env(task, options.tasks_file, options.log_dir)
    state, error = await run_actions(
        replace(task),
        task_list.after_task_list(),
        options.action_timeout,
        front_end,
        env,
    )
    return _Outcome(state, error)


def _save(
    task: Task,
    task_list: TaskList,
    tasks_file: str,
    failure: str | None,
) -> None:
    try:
        save_tasks(tasks_file, task_list)
    except TaskFileError as exc:
        if failure is None:
            raise RunError(
                f"task {task.id}: {task.name}: failed to save task state: {exc}"
            ) from exc

        # Both, each on its own line: the failure alone would hide that the
        # file still says pending, the save error alone why the run stopped.
        raise RunError(f"{failure}\n{exc}") from exc
