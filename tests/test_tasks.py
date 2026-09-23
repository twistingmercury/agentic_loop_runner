import os
import shutil

import pytest, tempfile, yaml
from pathlib import Path

from pydantic import ValidationError

from alr.tasks import parse_tasks, save_tasks, TaskState, Task

MISSING_ID = """
tasks:
  - name: "Standup project structure"
    state:  pending
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

ID_IS_ZERO = """
tasks:
  - id: 0
    name: "Standup project structure"
    state:  pending
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

ID_IS_NEGATIVE = """
tasks:
  - id: -1
    name: "Standup project structure"
    state:  pending
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

NAME_IS_BLANK = """
tasks:
  - id: 1
    name: ""
    state:  pending ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

PROMPT_IS_BLANK = """
tasks:
  - id: 1
    name: "Standup project structure"
    state:  pending
    prompt: ""
"""

IDS_ARE_DUPE = """
tasks:
  - id: 1
    name: "Standup project structure"
    state:  pending
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
  - id: 1
    name: "Write hello world app"
    state:  pending
    prompt: |
      Objective:
      Create a main.py file that when executed writes "hello, world"
      to the console.
"""

TASKS_ARE_EMPTY = """
tasks: []
"""

STATE_IS_INVALID = """
tasks:
  - id: 1
    name: "Standup project structure"
    state:  BOGUS
    prompt: |
      Objective:
      Create a main.py file that when executed writes "hello, world"
      to the console.
"""

INVALID_ID = """
tasks:
  - id: "1"
    name: "Standup project structure"
    state:  pending
    prompt: |
      Objective:
      Create a main.py file that when executed writes "hello, world"
      to the console.
"""

MALFORMED_YAML = """
tasks:
  - id: 1
    name: "Standup project structure"
    state:  pending
  prompt: |
    Objective:
    Create a new empty python project
    Stand up a new python project, targeting Python >= 3.12
"""

NAME_IS_WHITESPACE = """
tasks:
  - id: 0
    name: "      "
    state:  pending
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

CUSTOM_TAGS = """
tasks:
  - id: 0
    name: "billy"
    description: "      "
    state:  pending
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""


def test_parse_tasks_success():
    yaml_file = Path(__file__).parent / "data" / "tasks.yaml"
    task_list = parse_tasks(yaml_file)

    assert len(task_list.tasks) == 3

    task_1 = task_list.tasks[0]
    assert task_1.id == 1
    assert task_1.name == "Standup project structure"
    assert task_1.state == TaskState.COMPLETED
    assert len(task_1.prompt) > 1

    task_2 = task_list.tasks[1]
    assert task_2.id == 2
    assert task_2.name == "Write hello world app"
    assert task_2.state == TaskState.FAILED
    assert len(task_2.prompt) > 1


def test_parse_tasks_no_file():
    with pytest.raises(FileNotFoundError):
        bad_file = "does_not_exist.yaml"
        parse_tasks(bad_file)


@pytest.mark.parametrize(
    "tasks_yaml",
    [
        MISSING_ID,
        ID_IS_ZERO,
        ID_IS_NEGATIVE,
        NAME_IS_BLANK,
        PROMPT_IS_BLANK,
        IDS_ARE_DUPE,
        TASKS_ARE_EMPTY,
        STATE_IS_INVALID,
        INVALID_ID,
        NAME_IS_WHITESPACE,
        CUSTOM_TAGS,
    ],
)
def test_missing_expected_fields(tasks_yaml):
    with tempfile.NamedTemporaryFile(
        mode="w+", delete=True, encoding="utf-8"
    ) as temp_yaml:
        temp_yaml.write(tasks_yaml)
        temp_yaml.flush()
        with pytest.raises(ValidationError):
            parse_tasks(temp_yaml.name)


def test_malformed_yaml():
    with tempfile.NamedTemporaryFile(
        mode="w+", delete=True, encoding="utf-8"
    ) as temp_yaml:
        temp_yaml.write(MALFORMED_YAML)
        temp_yaml.flush()
        with pytest.raises(yaml.YAMLError):
            parse_tasks(temp_yaml.name)


def test_string_task():
    test_task = Task(
        id=1,
        name="Write hello world app",
        prompt="Do some stuff",
        state=TaskState.PENDING,
    )
    task_str = str(test_task)

    expected = """1: Write hello world app

Do some stuff"""
    assert task_str == expected


TEST_YAML = Path(__file__).parent / "data" / "tasks.yaml"


def test_save_tasks_round_trip(tmp_path):
    yaml_file = tmp_path / "tasks.yaml"
    shutil.copy(TEST_YAML, yaml_file)

    before = parse_tasks(yaml_file)
    before.tasks[2].state = TaskState.COMPLETED
    save_tasks(yaml_file, before)

    after = parse_tasks(yaml_file)
    assert after == before
    assert after.tasks[2].state == TaskState.COMPLETED


def test_save_tasks_leaves_no_tmp_file(tmp_path):
    yaml_file = tmp_path / "tasks.yaml"
    shutil.copy(TEST_YAML, yaml_file)

    save_tasks(yaml_file, parse_tasks(yaml_file))

    assert [p.name for p in tmp_path.iterdir()] == ["tasks.yaml"]


def test_save_tasks_failure_keeps_original(tmp_path, monkeypatch):
    yaml_file = tmp_path / "tasks.yaml"
    shutil.copy(TEST_YAML, yaml_file)
    original = yaml_file.read_text()

    task_list = parse_tasks(yaml_file)
    task_list.tasks[2].state = TaskState.COMPLETED

    # Make the swap step fail, as if the disk gave out mid-save.
    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(OSError):
        save_tasks(yaml_file, task_list)

    assert yaml_file.read_text() == original
    assert [p.name for p in tmp_path.iterdir()] == ["tasks.yaml"]
