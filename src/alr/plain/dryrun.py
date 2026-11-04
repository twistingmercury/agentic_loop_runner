"""The dry run: validate the task file and report on it, without starting Claude or writing a file."""

from datetime import datetime
from pathlib import Path
from typing import TextIO

from alr.engine.actions import first_line, action_limit
from alr.engine.loop import read_tasks
from alr.errors import INVALID_TASK_FILE, RefusedError, TaskFileError
from alr.plain.report import print_tasks, refuse_failed_report
from alr.record.runlog import check_writable, run_dir
from alr.tasks import Action, TaskList


async def dry_run(
    out: TextIO,
    tasks_file: str,
    action_timeout: str,
    sandbox_file: str,
    sandbox_name: str = "",
    added_on_real_run: bool = False,
) -> None:
    """Apply the checks a real run starts with, in its order, and write what it would run under.

    sandbox_name is the sandbox picked by name, and added_on_real_run says its
    file is a shipped one that a real run would add first.
    """
    task_list = _read_for_dry_run(tasks_file)
    path = run_dir(Path(tasks_file).parent / "logs", datetime.now().astimezone())
    refuse_failed_report(out, task_list)
    check_writable(path)
    print_tasks(out, task_list)
    _print_action_limits(out, task_list, action_timeout)
    _print_sandbox(out, sandbox_file, sandbox_name, added_on_real_run)
    out.write(f"{tasks_file} is valid\n")


def _print_sandbox(
    out: TextIO, sandbox_file: str, sandbox_name: str, added_on_real_run: bool
) -> None:
    """Name the sandbox settings file, and the sandbox it was picked as when it was picked by name."""
    if added_on_real_run:
        out.write(
            f"sandbox settings: {sandbox_file} ({sandbox_name}; added on a real run)\n"
        )
        return

    if sandbox_name:
        out.write(f"sandbox settings: {sandbox_file} ({sandbox_name})\n")
        return

    if sandbox_file:
        out.write(f"sandbox settings: {sandbox_file}\n")


def _read_for_dry_run(tasks_file: str) -> TaskList:
    """Read the task file, failed tasks and all, so a bad file is refused before anything else is looked at."""
    try:
        return read_tasks(tasks_file)
    except TaskFileError as exc:
        raise RefusedError(INVALID_TASK_FILE, str(exc)) from exc


def _print_action_limits(out: TextIO, task_list: TaskList, override: str) -> None:
    """Write one line per action, each task's own ones first, then after_task, then after_run: the limit it would run under and where the limit came from.

    Every task's actions are listed, completed ones included: the dry run reports the file, not the run.
    """
    for task in task_list.tasks:
        _print_list_limits(out, f"task {task.id} actions", task.actions, override)

    lists = (
        ("after_task", task_list.after_task_list()),
        ("after_run", task_list.after_run_list()),
    )
    for key, actions in lists:
        _print_list_limits(out, key, actions, override)


def _print_list_limits(
    out: TextIO, key: str, actions: list[Action], override: str
) -> None:
    """Print one line per action: its first line, its time limit, and where the limit came from."""
    for action in actions:
        limit, source = action_limit(action, override)
        out.write(f"{key}: {first_line(action.command)}: {limit} ({source})\n")
