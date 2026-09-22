import tempfile
from pathlib import Path

import pytest

from alr.main import main

TEST_YAML = str(Path(__file__).parent / "data" / "tasks.yaml")
TEST_PROMPT = str(Path(__file__).parent / "data" / "prompt.md")

MALFORMED_YAML = """
tasks:
  - id: 1
    title: "Standup project structure"
    status: pending
  prompt: |
    Objective:
    Create a new empty python project
    Stand up a new python project, targeting Python >= 3.12
"""

GOOD_YAML = """
tasks:
  - id: 1
    name: "Standup project structure"
    prompt: |
      Objective:
      Create a new empty python project
      Stand up a new python project, targeting Python >= 3.12
  - id: 2
    name: "Write hello world app"
    prompt: |
      Objective:
      Create a main.py file that when executed writes "hello, world"
      to the console.    
"""


def test_main_exists():
    assert callable(main)


def test_main_dry_run_success():
    args = ["--tasks", TEST_YAML, "--dry-run"]
    main(args)


def test_main_file_not_found():
    with pytest.raises(SystemExit) as exc:
        args = ["--tasks", "bad_file.yaml", "--dry-run"]
        main(args)

    assert "No such file or directory" in str(exc.value.code)


def test_malformed_yaml():
    with tempfile.NamedTemporaryFile(
        mode="w+", delete=True, encoding="utf-8"
    ) as temp_yaml:
        temp_yaml.write(MALFORMED_YAML)
        temp_yaml.flush()
        with pytest.raises(SystemExit) as exc:
            args = ["--tasks", temp_yaml.name, "--dry-run"]
            main(args)

    assert "while parsing a block collection" in str(exc.value.code)


@pytest.mark.parametrize(
    "argv, expected_error",
    [
        (["-t", TEST_YAML], "-prompt"),
        (["-p", TEST_PROMPT], "-tasks"),
    ],
)
def test_input_args_behaviour(capsys, argv: list[str], expected_error: str):
    with pytest.raises(SystemExit) as exc:
        main(argv)

    assert expected_error in capsys.readouterr().err


def test_main_dry_run(capsys):
    args = ["-t", TEST_YAML, "-d"]
    main(args)
    tout = capsys.readouterr().out
    assert "tasks yaml looks good!" in tout
    assert "\u26a0" in tout
