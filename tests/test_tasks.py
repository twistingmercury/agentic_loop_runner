import pytest, tempfile, yaml
from pathlib import Path

from pydantic import ValidationError

from alr.tasks import parse_tasks, TaskState

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
    assert task_1.state == TaskState.PENDING
    assert len(task_1.prompt) > 1

    task_2 = task_list.tasks[1]
    assert task_2.id == 2
    assert task_2.name == "Write hello world app"
    assert task_2.state == TaskState.ABANDONED
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
