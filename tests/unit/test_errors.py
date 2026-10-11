import pytest

from alr.errors import (
    FAILED_TASKS,
    AfterRunFailedError,
    CommitFailedError,
    FailedTasksError,
    RefusedError,
    RunCancelledError,
    RunError,
    UsageError,
    quote,
)
from alr.tasks import Shared, TaskList


@pytest.mark.parametrize(
    ("text", "quoted"),
    [
        ('echo "hi"', '"echo \\"hi\\""'),
        ("héllo", '"héllo"'),
    ],
)
def test_quote(text: str, quoted: str) -> None:
    assert quote(text) == quoted


def test_a_cancelled_run_is_a_run_error() -> None:
    assert issubclass(RunCancelledError, RunError)


def test_exit_codes() -> None:
    assert RunError("x").exit_code == 1
    assert UsageError("x").exit_code == 2
    assert RefusedError("dirty-tree", "x").exit_code == 3
    assert CommitFailedError("x").exit_code == 4
    assert AfterRunFailedError("x").exit_code == 4


def test_a_refusal_names_its_reason_and_keeps_the_message() -> None:
    error = RefusedError("dirty-tree", "--commit needs a clean work tree")

    assert str(error) == "refused: dirty-tree: --commit needs a clean work tree"
    assert error.reason == "dirty-tree"
    assert error.message == "--commit needs a clean work tree"


def test_failed_tasks_is_a_refusal() -> None:
    error = FailedTasksError(TaskList(shared=Shared(prompt=""), tasks=[]))

    assert isinstance(error, RefusedError)
    assert error.reason == FAILED_TASKS
    assert str(error).startswith("refused: failed-tasks: fix the failed tasks")


def test_a_commit_failure_is_a_run_error() -> None:
    assert issubclass(CommitFailedError, RunError)


def test_an_after_run_failure_is_a_run_error_but_not_a_commit_failure() -> None:
    assert issubclass(AfterRunFailedError, RunError)
    assert not issubclass(AfterRunFailedError, CommitFailedError)
