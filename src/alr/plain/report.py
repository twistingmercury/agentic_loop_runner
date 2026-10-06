"""What plain mode prints before a run: the task table, the failed-tasks notice, the repository notice."""

from typing import TextIO

from alr.engine.commit import Repo, open_repo
from alr.engine.loop import load_tasks
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


def load_tasks_report(out: TextIO, tasks_file: str) -> TaskList:
    """Load the task file; when it holds a failed task, write the notice and the table first."""
    try:
        return load_tasks(tasks_file)
    except FailedTasksError as exc:
        out.write("Some tasks failed previous runs:\n")
        print_tasks(out, exc.task_list)
        raise


async def repo_for(out: TextIO, tasks_file: str, commit: bool) -> Repo | None:
    """Open the work tree for a --commit run; None without the flag or outside a repository."""
    if not commit:
        return None

    repo = await open_repo(tasks_file)
    if repo is None:
        # Said out loud, so a run that commits nothing is not mistaken for one that did.
        out.write("commit: not a git repository, nothing will be committed\n")

    return repo
