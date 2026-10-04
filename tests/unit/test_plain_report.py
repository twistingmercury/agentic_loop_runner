"""The task table and the failed-tasks notice."""

import io
from pathlib import Path

import pytest

from alr.errors import FailedTasksError
from alr.plain.report import load_tasks_report, print_tasks
from alr.tasks import COMPLETED, FAILED, Task, TaskList


def test_load_tasks_report_failed_task_writes_notice_and_table(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n  - {id: 1, name: a, prompt: p}\n  - {id: 2, name: b, prompt: p, state: failed}\n",
        encoding="utf-8",
    )

    got = io.StringIO()
    with pytest.raises(FailedTasksError) as caught:
        load_tasks_report(got, str(path))

    want = io.StringIO()
    want.write("Some tasks failed previous runs:\n")
    print_tasks(want, caught.value.task_list)
    assert got.getvalue() == want.getvalue()


def test_load_tasks_report_clean_file_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n  - {id: 1, name: First task, prompt: Do the first thing.}\n",
        encoding="utf-8",
    )

    got = io.StringIO()
    task_list = load_tasks_report(got, str(path))

    assert len(task_list.tasks) == 1
    assert got.getvalue() == ""


def test_print_tasks_colors_states_and_widens_the_id_column() -> None:
    task_list = TaskList(
        tasks=[
            Task(id=1, name="First task", prompt="p", state=COMPLETED),
            Task(id=2, name="Second task", prompt="p", state=FAILED, error="boom"),
            Task(id=100, name="Third task", prompt="p"),
        ]
    )

    got = io.StringIO()
    print_tasks(got, task_list)

    assert got.getvalue() == (
        "    ID  STATE      NAME\n"
        "   ---  ---------  ----\n"
        "✅   1  \033[92mCOMPLETED\033[0m  First task\n"
        "❌   2  \033[1;91mFAILED   \033[0m  Second task  \033[1;91m← Needs review!\033[0m\n"
        "   100  PENDING    Third task\n"
    )
