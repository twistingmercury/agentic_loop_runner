import pytest
from pathlib import Path
from alr.tasks import parse_tasks, TaskStatus


def test_parse_tasks_success():
    yaml = Path(__file__).parent / "data" / "tasks.yaml"
    task_list = parse_tasks(yaml)

    assert len(task_list.tasks) == 2

    task_1 = task_list.tasks[0]
    assert task_1.id == 1
    assert task_1.title == "Standup project structure"
    assert task_1.status == TaskStatus.PENDING
    assert task_1.agent == "python_software_engineer"
    assert task_1.checkpoint == ""
    assert len(task_1.prompt) > 1

    task_2 = task_list.tasks[1]
    assert task_2.id == 2
    assert task_2.title == "Write hello world app"
    assert task_2.status == TaskStatus.PENDING
    assert task_2.agent == "python_software_engineer"
    assert task_2.checkpoint == ""
    assert len(task_2.prompt) > 1


def test_parse_tasks_no_file():
    with pytest.raises(FileNotFoundError):
        yaml = "does_not_exist.yaml"
        parse_tasks(yaml)
