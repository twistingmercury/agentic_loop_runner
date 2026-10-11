"""Errors that end a run before or outside a task's own outcome."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alr.tasks import TaskList

FAILED_TASKS_MESSAGE = (
    "fix the failed tasks and set their state to pending before running"
)

DIRTY_TREE = "dirty-tree"
TASK_FILE_VISIBLE = "task-file-visible"
LOG_FOLDER_VISIBLE = "log-folder-visible"
FAILED_TASKS = "failed-tasks"
INVALID_TASK_FILE = "invalid-task-file"
INVALID_SANDBOX_SETTINGS = "invalid-sandbox-settings"
LOG_UNWRITABLE = "log-unwritable"
TASK_FILE_UNWRITABLE = "task-file-unwritable"
GIT_ERROR = "git-error"
LOCKED = "locked"


def quote(text: str) -> str:
    """Wrap text in double quotes, escaping what needs it, for names in error messages."""
    return json.dumps(text, ensure_ascii=False)


class AlrError(Exception):
    """Base for every error alr reports as `error: <message>`; exit_code is the code it exits with."""

    exit_code = 1


class UsageError(AlrError):
    """A flag is missing, conflicts with another, or has a bad value."""

    exit_code = 2


class RefusedError(AlrError):
    """alr declined to start; nothing ran and nothing was written."""

    exit_code = 3

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"refused: {reason}: {message}")
        self.reason = reason
        self.message = message


class TaskFileError(AlrError):
    """The task file cannot be read, parsed, validated, or saved."""


class FailedTasksError(RefusedError):
    """The task file already holds a failed task."""

    def __init__(self, task_list: TaskList) -> None:
        super().__init__(FAILED_TASKS, FAILED_TASKS_MESSAGE)
        self.task_list = task_list


class RunLogError(AlrError):
    """The run log cannot be opened or written."""


class RunError(AlrError):
    """A task failed, or its state could not be saved."""


class CommitFailedError(RunError):
    """A task's work passed its actions but git would not commit it."""

    exit_code = 4


class AfterRunFailedError(RunError):
    """An after_run action failed; every task is still completed."""

    exit_code = 4


class RunCancelledError(RunError):
    """The run was stopped before a task finished."""


class SkillInstallError(AlrError):
    """The bundled skill cannot be installed."""
