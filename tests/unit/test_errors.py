import pytest

from alr.errors import (
    FAILED_TASKS,
    AfterRunFailedError,
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
    assert RefusedError("failed-tasks", "x").exit_code == 3
    assert AfterRunFailedError("x").exit_code == 4


def test_exit_codes_are_contiguous() -> None:
    assert {RefusedError.exit_code, AfterRunFailedError.exit_code} == {3, 4}


def test_a_refusal_names_its_reason_and_keeps_the_message() -> None:
    error = RefusedError("log-unwritable", "cannot write the log")

    assert str(error) == "refused: log-unwritable: cannot write the log"
    assert error.reason == "log-unwritable"
    assert error.message == "cannot write the log"


def test_failed_tasks_is_a_refusal() -> None:
    error = FailedTasksError(TaskList(shared=Shared(prompt=""), tasks=[]))

    assert isinstance(error, RefusedError)
    assert error.reason == FAILED_TASKS
    assert str(error).startswith("refused: failed-tasks: fix the failed tasks")


def test_an_after_run_failure_is_a_run_error() -> None:
    assert issubclass(AfterRunFailedError, RunError)
