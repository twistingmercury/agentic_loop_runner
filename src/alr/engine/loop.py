"""The run loop: each pending task in a fresh Claude session, saved as soon as it ends."""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from alr.engine.commit import Repo, commit
from alr.engine.frontend import FrontEnd
from alr.engine.gates import run_gates
from alr.engine.result import last_result_line, outcome
from alr.errors import (
    FAILED_TASKS_MESSAGE,
    FailedTasksError,
    RunCancelledError,
    RunError,
    TaskFileError,
    quote,
)
from alr.tasks import COMPLETED, FAILED, Gate, Task, TaskList, parse_tasks, save_tasks

__all__ = ["FAILED_TASKS_MESSAGE", "RunOptions", "load_tasks", "run"]

# What a stop that carries no task of its own is recorded as; a stop inside a
# task says "task N: name: stopped" through RunCancelledError.
_STOPPED = "stopped"


@dataclass(frozen=True)
class RunOptions:
    """What a run needs besides the tasks."""

    tasks_file: str

    gate_timeout: str
    """The --gate-timeout value; empty when the flag was not passed."""

    session_args: Sequence[str]
    """The Claude flags that set what a session may do."""

    repo: Repo | None = None
    """Where each completed task is committed; None for a run without --commit."""


def load_tasks(path: str) -> TaskList:
    """Read and validate the task file; a file holding a failed task is refused."""
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

    if any(task.state == FAILED for task in task_list.tasks):
        raise FailedTasksError(task_list)

    return task_list


async def run(task_list: TaskList, options: RunOptions, front_end: FrontEnd) -> None:
    """Run every pending task in file order, saving the file after each one."""
    started = time.monotonic()
    error: str | None = None
    try:
        await _run_loop(task_list, options, front_end)
    except BaseException as exc:
        # Whatever ends the run is recorded, so a crash never reads as a clean finish.
        error = _error_text(exc)
        raise
    finally:
        front_end.run_done(error, time.monotonic() - started)


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


async def _run_and_save(
    task: Task,
    task_list: TaskList,
    options: RunOptions,
    front_end: FrontEnd,
) -> None:
    # The front end gets copies, so nothing it keeps changes under it.
    front_end.task_started(replace(task))
    started = time.monotonic()
    state, error = await _run_task(
        task, task_list.shared.prompt, task_list.gate_list(), options, front_end
    )
    task.state, task.error = state, error
    failure = (
        None if state == COMPLETED else f"task {task.id}: {task.name} failed: {error}"
    )
    _save(task, task_list, options.tasks_file, failure)
    front_end.task_finished(replace(task), failure, time.monotonic() - started)
    if failure is not None:
        raise RunError(failure)


async def _run_task(
    task: Task,
    prompt: str,
    gates: Sequence[Gate],
    options: RunOptions,
    front_end: FrontEnd,
) -> tuple[str, str]:
    """Run the task's session, the gates, and the commit; return the state and error to save."""
    stdin_text = f"{prompt}\n\n{task.text()}\n"
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
        return state, error

    state, error = await run_gates(
        replace(task), gates, options.gate_timeout, front_end
    )
    if state != COMPLETED or options.repo is None:
        return state, error

    try:
        return await commit(options.repo, replace(task), front_end)
    except asyncio.CancelledError:
        # Nothing is saved, so the next run picks the task up again.
        raise RunCancelledError(
            f"task {task.id}: {task.name}: stopped during commit"
        ) from None


def _save(
    task: Task, task_list: TaskList, tasks_file: str, failure: str | None
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
