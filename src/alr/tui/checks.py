"""The checks the dialogs make on an answer, using the same functions a run uses."""

from dataclasses import dataclass
from pathlib import Path

from alr.engine.loop import load_tasks
from alr.engine.sandbox import folder_sandbox, sandbox_args
from alr.errors import UsageError
from alr.tasks import TaskList


@dataclass(frozen=True)
class FolderCheck:
    """A run folder that passed: its task file, the loaded tasks, and its sandbox.json if any."""

    tasks_file: str
    task_list: TaskList
    sandbox_file: str


async def check_folder(folder: str) -> FolderCheck:
    """Accept a run folder, or raise why not.

    A folder outside a git work tree is fine: the run then commits nothing,
    as in plain mode.
    """
    tasks_file = Path(folder) / "tasks.yaml"
    if not tasks_file.is_file():
        raise UsageError(f"no tasks.yaml in {folder}")

    task_list = load_tasks(str(tasks_file))
    return FolderCheck(str(tasks_file), task_list, folder_sandbox(folder) or "")


def check_sandbox(path: str) -> None:
    """Accept a sandbox settings file, or raise why not; the run reads it the same way."""
    sandbox_args(path)
