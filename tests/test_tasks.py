import pytest, tempfile, yaml
from pathlib import Path

from pydantic import ValidationError

from alr.tasks import parse_tasks, TaskStatus

MISSING_ID = """
tasks:
  - title: "Standup project structure"
    status: pending
    agent: python_software_engineer
    checkpoint: ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

ID_IS_ZERO = """
tasks:
  - id: 0
    title: "Standup project structure"
    status: pending
    agent: python_software_engineer
    checkpoint: ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

ID_IS_NEGATIVE = """
tasks:
  - id: -1
    title: "Standup project structure"
    status: pending
    agent: python_software_engineer
    checkpoint: ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

TITLE_IS_BLANK = """
tasks:
  - id: 1
    title: ""
    status: pending
    agent: python_software_engineer
    checkpoint: ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

AGENT_IS_BLANK = """
tasks:
  - id: 1
    title: "Standup project structure"
    status: pending
    agent: ""
    checkpoint: ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

PROMPT_IS_BLANK = """
tasks:
  - id: 1
    title: "Standup project structure"
    status: pending
    agent: "python_software_engineer"
    checkpoint: ""
    prompt: ""
"""

IDS_ARE_DUPE = """
tasks:
  - id: 1
    title: "Standup project structure"
    status: pending
    agent: python_software_engineer
    checkpoint: ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
  - id: 1
    title: "Write hello world app"
    status: pending
    agent: python_software_engineer
    checkpoint: ""
    prompt: |
      Objective:
      Create a main.py file that when executed writes "hello, world"
      to the console.
"""

TASKS_ARE_EMPTY = """
tasks: []
"""

STATUS_IS_INVALID = """
tasks:
  - id: 1
    title: "Standup project structure"
    status: BOGUS
    agent: "python_software_engineer"
    checkpoint: ""
    prompt: |
      Objective:
      Create a main.py file that when executed writes "hello, world"
      to the console.
"""

INVALID_ID = """
tasks:
  - id: "1"
    title: "Standup project structure"
    status: pending
    agent: "python_software_engineer"
    checkpoint: ""
    prompt: |
      Objective:
      Create a main.py file that when executed writes "hello, world"
      to the console.
"""

MALFORMED_YAML = """
tasks:
  - id: 1
    title: "Standup project structure"
    status: pending
    agent: python_software_engineer
    checkpoint: ""
  prompt: |
    Objective:
    Create a new empty python project
    Stand up a new python project, targeting Python >= 3.12
"""

TITLE_IS_WHITESPACE = """
tasks:
  - id: 0
    title: "      "
    status: pending
    agent: python_software_engineer
    checkpoint: ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""

CUSTOM_TAGS = """
tasks:
  - id: 0
    name: "billy"
    title: "      "
    status: pending
    agent: python_software_engineer
    checkpoint: ""
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
"""


def test_parse_tasks_success():
    yaml_file = Path(__file__).parent / "data" / "tasks.yaml"
    task_list = parse_tasks(yaml_file)

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
        bad_file = "does_not_exist.yaml"
        parse_tasks(bad_file)


@pytest.mark.parametrize(
    "tasks_yaml",
    [
        MISSING_ID,
        ID_IS_ZERO,
        ID_IS_NEGATIVE,
        TITLE_IS_BLANK,
        AGENT_IS_BLANK,
        PROMPT_IS_BLANK,
        IDS_ARE_DUPE,
        TASKS_ARE_EMPTY,
        STATUS_IS_INVALID,
        INVALID_ID,
        TITLE_IS_WHITESPACE,
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
