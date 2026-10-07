"""The checks the dialogs make on an answer, using the same functions a run uses."""

from dataclasses import dataclass
from pathlib import Path

from alr.engine.commit import work_tree_root
from alr.engine.loop import load_tasks
from alr.engine.sandbox import folder_sandbox
from alr.errors import RepoError, UsageError
from alr.tasks import TaskList


@dataclass(frozen=True)
class FolderCheck:
    """A run folder that passed: its task file, the loaded tasks, and its sandbox.json if any."""

    tasks_file: str
    task_list: TaskList
    sandbox_file: str


async def check_folder(folder: str) -> FolderCheck:
    """Accept a run folder, or raise why not.

    The view always commits, and commits go into the work tree around the
    working directory (as in the engine), so that is what must exist.
    """
    tasks_file = Path(folder) / "tasks.yaml"
    if not tasks_file.is_file():
        raise UsageError(f"no tasks.yaml in {folder}")

    task_list = load_tasks(str(tasks_file))
    if await work_tree_root() is None:
        raise RepoError(
            "not a git repository: start alr inside the project's work tree"
        )

    return FolderCheck(str(tasks_file), task_list, folder_sandbox(folder) or "")
