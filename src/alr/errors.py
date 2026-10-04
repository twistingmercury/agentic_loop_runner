"""Errors that end a run before or outside a task's own outcome."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alr.tasks import TaskList

FAILED_TASKS_MESSAGE = (
    "fix the failed tasks and set their state to pending before running"
)


class AlrError(Exception):
    """Base for every error alr reports as `error: <message>` with exit code 1."""


class UsageError(AlrError):
    """A flag is missing, conflicts with another, or has a bad value."""


class TaskFileError(AlrError):
    """The task file cannot be read, parsed, validated, or saved."""


class PromptError(AlrError):
    """The prompt file is missing, unreadable, or empty."""


class FailedTasksError(AlrError):
    """The task file already holds a failed task."""

    def __init__(self, task_list: TaskList) -> None:
        super().__init__(FAILED_TASKS_MESSAGE)
        self.task_list = task_list


class SandboxError(AlrError):
    """The sandbox settings file is missing, unreadable, or not a JSON object."""


class RepoError(AlrError):
    """--commit cannot use the repository as it is."""


class RunLogError(AlrError):
    """The run log cannot be opened or written."""


class RunError(AlrError):
    """A task failed, or its state could not be saved."""


class RunCancelled(RunError):
    """The run was stopped before a task finished."""
