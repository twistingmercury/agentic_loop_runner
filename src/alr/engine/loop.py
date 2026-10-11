"""The run loop: each pending task in a fresh Claude session, saved as soon as it ends."""

import asyncio
import contextlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple

from alr.engine.commit import CommitMode, Repo, commit, commit_all, head
from alr.engine.frontend import FrontEnd
from alr.engine.gates import run_gates
from alr.engine.result import last_result_line, outcome
from alr.engine.stop import Stop
from alr.errors import (
    FAILED_TASKS_MESSAGE,
    TASK_FILE_UNWRITABLE,
    AlrError,
    CommitFailedError,
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
    PASSED,
    PENDING,
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
    commit_failed: bool = False
    """Whether the commit step is what failed, which ends the run with its own exit code."""

    landed: str | None = None
    """The hash of the commit this task's work landed in, when alr made one."""


@dataclass(frozen=True)
class RunOptions:
    """What a run needs besides the tasks."""

    tasks_file: str

    gate_timeout: str
    """The --gate-timeout value; empty when the flag was not passed."""

    session_args: Sequence[str]
    """The Claude flags that set what a session may do."""

    repo: Repo | None = None
    """Where completed work is committed; None for a run without a repository."""

    commit_mode: CommitMode = CommitMode.NONE
    """When the repo is committed into: never, after each task, or once after the last."""

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
    """Run every pending task in file order, saving the file after each one.

    In `run` commit mode the file is saved once, after the single commit.
    """
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

    if isinstance(exc, CommitFailedError):
        return RunResult.COMMIT_FAILED, exc.exit_code

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


def _defers_saving(options: RunOptions) -> bool:
    """Report whether the run saves tasks.yaml once, at the end.

    Only `run` mode with a repository: the one commit decides whether any task
    is complete, so no task may be saved as completed before it lands.
    """
    return options.repo is not None and options.commit_mode is CommitMode.RUN


async def _run_loop(
    task_list: TaskList, options: RunOptions, front_end: FrontEnd
) -> None:
    ran: list[Task] = []
    for task in task_list.tasks:
        if task.state == COMPLETED:
            front_end.task_skipped(task)
            continue

        await _run_and_save(task, task_list, options, front_end, ran)
        ran.append(replace(task))

    await _commit_after_all(task_list, ran, options, front_end)


async def _commit_after_all(
    task_list: TaskList,
    ran: Sequence[Task],
    options: RunOptions,
    front_end: FrontEnd,
) -> None:
    """Commit the whole run once, then save it; a failed run never gets here, so its work stays in the tree."""
    if options.repo is None or not _defers_saving(options):
        return

    if not ran:
        return

    before = await head(options.repo)
    try:
        error = await commit_all(options.repo, ran, front_end)
    except asyncio.CancelledError:
        # A stop before the commit lands saves nothing: the file is still as the run found it.
        await _save_if_landed(
            options.repo, before, ran, task_list, options.tasks_file, front_end
        )
        raise RunCancelledError("stopped during the final commit") from None

    if error is not None:
        raise _commit_failed(task_list, ran, options, error)

    await _save_committed(options.repo, ran, task_list, options.tasks_file)


async def _save_if_landed(
    repo: Repo,
    before: str | None,
    covered: Sequence[Task],
    task_list: TaskList,
    tasks_file: str,
    front_end: FrontEnd,
) -> None:
    """After a stop in the commit step, save the covered tasks completed if git had already made the commit.

    A stop that lands after the commit exists must not undo it, or a rerun
    would redo committed work. When HEAD did not move, or git cannot say,
    nothing is saved. The stop cut the commit step short before it could tell
    the front end, so the front end is told here, and its rows follow the file.
    """
    after = await _head_after_stop(repo)
    if after is None or after == before:
        return

    front_end.committed(covered[-1], after)
    await _save_committed(repo, covered, task_list, tasks_file, after)


async def _head_after_stop(repo: Repo) -> str | None:
    """Read HEAD once more after a stop, shielded so another stop cannot cut the read short."""
    lookup = asyncio.ensure_future(head(repo))
    while not lookup.done():
        # The view lets a user press Stop twice; the answer is still needed.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(lookup)

    return lookup.result()


async def _save_committed(
    repo: Repo,
    covered: Sequence[Task],
    task_list: TaskList,
    tasks_file: str,
    commit_hash: str | None = None,
) -> None:
    """Save the tasks a landed commit covers as completed.

    If the save fails, the error names the commit: it exists while the file
    still says otherwise, and a driver must not blindly rerun. It exits with
    the commit step's code, whichever mode or stop led here.
    """
    for task in covered:
        listed = _listed(task_list, task)
        listed.state, listed.error = COMPLETED, ""

    try:
        save_tasks(tasks_file, task_list)
    except TaskFileError as exc:
        landed = commit_hash or await head(repo) or "(hash unreadable)"
        raise CommitFailedError(
            f"commit {landed} was made but tasks.yaml was not updated; "
            f"mark the tasks it covers completed before rerunning: {exc}"
        ) from exc


def _commit_failed(
    task_list: TaskList, ran: Sequence[Task], options: RunOptions, error: str
) -> CommitFailedError:
    """Save the run as a failed commit and return the error to raise.

    The commit covered every task that ran and cannot say which one it choked
    on, so the last one carries the failure and the others go back to pending.
    """
    failure = f"commit failed: {error}"
    last = _listed(task_list, ran[-1])
    last.state, last.error = FAILED, failure
    _finish_deferred(
        task_list,
        ran[:-1],
        options,
        failed=last,
        failure=failure,
        error_type=CommitFailedError,
    )
    return CommitFailedError(failure)


def _listed(task_list: TaskList, task: Task) -> Task:
    """Return the task list's own entry for a task, whichever copy was passed."""
    return next(listed for listed in task_list.tasks if listed.id == task.id)


def _finish_deferred(
    task_list: TaskList,
    ran: Sequence[Task],
    options: RunOptions,
    failed: Task | None = None,
    failure: str | None = None,
    error_type: type[RunError] = RunError,
) -> None:
    """Save a deferred run that did not land: every task in ran goes back to pending.

    Tasks completed before the run are not in ran, so they are never touched.
    """
    for task in ran:
        listed = _listed(task_list, task)
        listed.state, listed.error = PENDING, ""

    anchor = failed or (ran[-1] if ran else None)
    if anchor is None:
        return

    _save(anchor, task_list, options.tasks_file, failure, error_type)


async def _run_and_save(
    task: Task,
    task_list: TaskList,
    options: RunOptions,
    front_end: FrontEnd,
    ran: Sequence[Task],
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
    # The commit step has its own exit code, which a save failure must not hide.
    error_type = CommitFailedError if ended.commit_failed else RunError
    await _save_ended(task, ended, task_list, options, failure, ran, error_type)
    front_end.task_finished(_shown(task, options), failure, time.monotonic() - started)
    if failure is None:
        return

    raise error_type(failure)


async def _save_ended(
    task: Task,
    ended: _Outcome,
    task_list: TaskList,
    options: RunOptions,
    failure: str | None,
    ran: Sequence[Task],
    error_type: type[RunError],
) -> None:
    """Save the file after a task, unless the run saves once at the end."""
    if _defers_saving(options):
        _finish_deferred_task(task, task_list, options, failure, ran, error_type)
        return

    if ended.landed is not None and options.repo is not None:
        await _save_committed(
            options.repo, [task], task_list, options.tasks_file, ended.landed
        )
        return

    _save(task, task_list, options.tasks_file, failure, error_type)


def _finish_deferred_task(
    task: Task,
    task_list: TaskList,
    options: RunOptions,
    failure: str | None,
    ran: Sequence[Task],
    error_type: type[RunError],
) -> None:
    """End a deferred run at a failed task; a passed one waits for the commit."""
    if failure is None:
        return

    _finish_deferred(
        task_list, ran, options, failed=task, failure=failure, error_type=error_type
    )


def _shown(task: Task, options: RunOptions) -> Task:
    """Copy the task for the front end, saying `passed` where it is not yet complete."""
    if task.state == COMPLETED and _defers_saving(options):
        return replace(task, state=PASSED)

    return replace(task)


async def _run_task(
    task: Task,
    task_list: TaskList,
    options: RunOptions,
    front_end: FrontEnd,
) -> _Outcome:
    """Run the task's session, the gates, and the commit; return the state and error to save."""
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

    state, error = await run_gates(
        replace(task), task_list.gate_list(), options.gate_timeout, front_end
    )
    per_task = options.commit_mode is CommitMode.TASK
    if state != COMPLETED or options.repo is None or not per_task:
        return _Outcome(state, error)

    return await _commit_task(
        task, task_list, options.repo, options.tasks_file, front_end
    )


async def _commit_task(
    task: Task,
    task_list: TaskList,
    repo: Repo,
    tasks_file: str,
    front_end: FrontEnd,
) -> _Outcome:
    """Commit the task's work; return completed, or failed with git's reason."""
    before = await head(repo)
    try:
        reason = await commit(repo, replace(task), front_end)
        # Read inside the try, so a stop here still saves the commit that landed.
        landed = None if reason is not None else await _landed_since(repo, before)
    except asyncio.CancelledError:
        # Unless git had already made the commit, nothing is saved, so the next
        # run picks the task up again.
        await _save_if_landed(repo, before, [task], task_list, tasks_file, front_end)
        raise RunCancelledError(
            f"task {task.id}: {task.name}: stopped during commit"
        ) from None

    if reason is None:
        return _Outcome(COMPLETED, "", landed=landed)

    return _Outcome(FAILED, f"commit failed: {reason}", commit_failed=True)


async def _landed_since(repo: Repo, before: str | None) -> str | None:
    """Return the hash HEAD moved to since before, or None when no commit was made.

    A task that changed nothing makes no commit, so there is no hash to name.
    """
    after = await head(repo)
    if after == before:
        return None

    return after


def _save(
    task: Task,
    task_list: TaskList,
    tasks_file: str,
    failure: str | None,
    error_type: type[RunError] = RunError,
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
        raise error_type(f"{failure}\n{exc}") from exc
