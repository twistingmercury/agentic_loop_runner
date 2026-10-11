"""What plain mode prints before a run: the task table, the failed-tasks notice, the repository notice."""

from contextlib import ExitStack
from typing import TextIO

from alr.engine.commit import (
    Repo,
    check_clean,
    check_log_dir,
    check_unlocked,
    find_repo,
    held_lock,
)
from alr.engine.loop import refuse_failed
from alr.errors import FailedTasksError
from alr.tasks import COMPLETED, FAILED, Task, TaskList

_RED = "\033[1;91m"
_GREEN = "\033[92m"
_RESET = "\033[0m"
_STATE_WIDTH = len(COMPLETED)


def print_tasks(out: TextIO, task_list: TaskList) -> None:
    """Write a table of the tasks: id, state (green when completed, red when failed), and name."""
    id_width = max(len("ID"), *(len(str(task.id)) for task in task_list.tasks))
    out.write(f"   {'ID':>{id_width}}  {'STATE':<{_STATE_WIDTH}}  NAME\n")
    out.write(f"   {'-' * id_width}  {'-' * _STATE_WIDTH}  ----\n")
    for task in task_list.tasks:
        out.write(_row(task, id_width))


def _row(task: Task, id_width: int) -> str:
    emoji, color, reset, note = "  ", "", "", ""
    if task.state == FAILED:
        emoji, color, reset = "❌", _RED, _RESET
        note = f"  {_RED}← Needs review!{_RESET}"

    if task.state == COMPLETED:
        emoji, color, reset = "✅", _GREEN, _RESET

    state = f"{color}{task.state.upper():<{_STATE_WIDTH}}{reset}"
    return f"{emoji} {task.id:>{id_width}}  {state}  {task.name}{note}\n"


def refuse_failed_report(out: TextIO, task_list: TaskList) -> None:
    """Refuse a list that holds a failed task, writing the notice and the table first."""
    try:
        refuse_failed(task_list)
    except FailedTasksError:
        out.write("Some tasks failed previous runs:\n")
        print_tasks(out, task_list)
        raise


async def repo_for(
    out: TextIO, tasks_file: str, commit: bool, run_dir: str, locks: ExitStack | None
) -> Repo | None:
    """Open the work tree for a --commit run; None without the flag or outside a repository.

    The run's log folder is checked before the tree is: a tree dirty only
    with that folder needs it ignored, not committed. The commit lock comes
    before both, because a run that holds it leaves the tree dirty, and the
    second run is to be told it is locked out. With locks, the lock is taken
    on it and held until it closes; with None, as in a dry run, it is only
    looked at.
    """
    if not commit:
        return None

    repo = await find_repo(tasks_file)
    if repo is None:
        # Said out loud, so a run that commits nothing is not mistaken for one that did.
        out.write("commit: not a git repository, nothing will be committed\n")
        return None

    if locks is None:
        check_unlocked(repo)
    else:
        locks.enter_context(held_lock(repo))

    await check_log_dir(repo, run_dir)
    await check_clean(repo)
    return repo
