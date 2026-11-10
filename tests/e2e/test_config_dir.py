"""The config folder, ~/.local/alr: set up only after a typed yes, never by a dry run, left alone after."""

import json
from pathlib import Path

import pytest
from e2e_helpers import (
    END_OF_INPUT,
    NO,
    SETUP_PROMPT,
    VALID_TASKS_YAML,
    YES,
    fake_claude_records,
    read_tasks_yaml,
    run_alr,
    run_alr_in_terminal,
    task_states,
    write_tasks_yaml,
)

REFUSED = "error: refused: config-setup-missing: "

YOLO_CONFIG = """version: 1.0.0
defaults:
  sandbox:
    permissiveness:
      default: yolo
  prompts:
    task_review:
      prompt: ./prompts/task_review.md
      max_task_review_rounds: 1
    task_remediation:
      prompt: ./prompts/task_remediation.md
"""


@pytest.fixture(autouse=True)
def set_up_config() -> None:
    """Replace the conftest fixture: every test here starts on a machine that was never set up."""


def set_up(home: Path) -> Path:
    """Set up the config folder the way a user does, and return it."""
    done = run_alr_in_terminal(keys=YES)

    assert done.code == 0, done
    return home / ".local" / "alr"


@pytest.mark.parametrize("keys", [YES, "yes\n", "Y\n", "YES\n"])
def test_yes_sets_up_the_folder_and_starts_no_run(
    keys: str, tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    record_file = tmp_path / "record.ndjson"
    folder = home / ".local" / "alr"

    res = run_alr_in_terminal(
        "--yolo",
        "--plain",
        f"--dir={tmp_path}",
        keys=keys,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    for name in (
        "config.yaml",
        "prompts/task_review.md",
        "prompts/task_remediation.md",
        "sandboxes/tight.json",
        "sandboxes/large.json",
    ):
        assert (folder / name).is_file()
        assert str(folder / name) in res.shown
    assert "configuration is set up" in res.shown
    assert "Run alr again" in res.shown
    assert res.err == ""
    assert fake_claude_records(record_file) == []
    assert tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML


def test_the_prompt_names_every_folder_it_will_create(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr_in_terminal("--yolo", "--plain", f"--dir={tmp_path}", keys=NO)

    config_folder = home / ".local" / "alr"
    listed = [line.strip() for line in res.shown.splitlines()]
    assert str(home / ".local") in listed
    assert str(config_folder) in listed
    assert str(config_folder / "prompts") in listed
    assert str(config_folder / "sandboxes") in listed


@pytest.mark.parametrize(
    "keys", [NO, "\n", "maybe\n", END_OF_INPUT], ids=["n", "empty", "maybe", "EOF"]
)
def test_anything_but_yes_creates_nothing_and_refuses(
    keys: str, tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    record_file = tmp_path / "record.ndjson"

    res = run_alr_in_terminal(
        "--yolo",
        "--plain",
        f"--dir={tmp_path}",
        keys=keys,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 3, res
    assert "Nothing was created." in res.shown
    assert res.err.startswith(REFUSED)
    assert len(res.err.splitlines()) == 1
    assert list(home.iterdir()) == []
    assert fake_claude_records(record_file) == []


def test_no_terminal_refuses_without_asking_or_writing(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr("--yolo", f"--dir={tmp_path}")

    assert res.code == 3, res
    assert res.out == ""
    assert res.err.startswith(REFUSED)
    assert "run alr in a terminal once to create it" in res.err
    assert len(res.err.splitlines()) == 1
    assert list(home.iterdir()) == []


def test_a_run_after_the_setup_proceeds(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    set_up(home)

    res = run_alr("--yolo", f"--dir={tmp_path}")

    assert res.code == 0, res
    assert res.err == ""
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}


def test_a_run_after_the_setup_leaves_edits_alone(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = set_up(home)
    review_prompt = folder / "prompts" / "task_review.md"
    review_prompt.write_text("my own review prompt\n", encoding="utf-8")
    config_before = (folder / "config.yaml").read_text(encoding="utf-8")
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr("--yolo", f"--dir={tmp_path}")

    assert res.code == 0, res
    assert res.err == ""
    assert review_prompt.read_text(encoding="utf-8") == "my own review prompt\n"
    assert (folder / "config.yaml").read_text(encoding="utf-8") == config_before


def test_a_yolo_default_runs_without_a_flag(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = set_up(home)
    (folder / "config.yaml").write_text(YOLO_CONFIG, encoding="utf-8")
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(f"--dir={tmp_path}", env={"FAKECLAUDE_RECORD_FILE": str(record_file)})

    assert res.code == 0, res
    records = fake_claude_records(record_file)
    assert [record["argv"] for record in records] == [
        ["--print", "--dangerously-skip-permissions"]
    ]


def test_a_sandbox_default_runs_that_sandbox_beside_a_folders_sandbox_json(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = set_up(home)
    mine = '{"sandbox": {"filesystem": {"denyRead": ["~/"]}}}'
    (folder / "sandboxes" / "my_sandbox.json").write_text(mine, encoding="utf-8")
    (folder / "config.yaml").write_text(
        YOLO_CONFIG.replace("default: yolo", "default: my_sandbox"), encoding="utf-8"
    )
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    # The run folder's own file says nothing: the default still decides.
    (tmp_path / "sandbox.json").write_text("null", encoding="utf-8")
    record_file = tmp_path / "record.ndjson"

    res = run_alr(f"--dir={tmp_path}", env={"FAKECLAUDE_RECORD_FILE": str(record_file)})

    assert res.code == 0, res
    (record,) = fake_claude_records(record_file)
    settings = json.loads(record["argv"][4])
    assert settings["sandbox"]["filesystem"] == {"denyRead": ["~/"]}
    assert settings["sandbox"]["enabled"] is True


def test_a_default_naming_no_sandbox_is_refused_before_any_task(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = set_up(home)
    (folder / "config.yaml").write_text(
        YOLO_CONFIG.replace("default: yolo", "default: nope"), encoding="utf-8"
    )
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr(f"--dir={tmp_path}")

    assert res.code == 3, res
    assert res.err.startswith("error: refused: invalid-config: ")
    assert "default nope is not a sandbox; the sandboxes are: large, tight" in res.err
    assert not (tmp_path / "logs").exists()


def test_dry_run_in_a_terminal_refuses_without_asking_or_creating(
    tmp_path: Path, home: Path
) -> None:
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr_in_terminal("--dry-run", f"--dir={tmp_path}")

    assert res.code == 3, res
    assert SETUP_PROMPT not in res.shown
    assert res.shown == ""
    assert res.err.startswith(REFUSED)
    assert len(res.err.splitlines()) == 1
    assert list(home.iterdir()) == []


def test_dry_run_without_a_terminal_refuses_and_creates_nothing(
    tmp_path: Path, home: Path
) -> None:
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr("--dry-run", f"--dir={tmp_path}")

    assert res.code == 3, res
    assert res.out == ""
    assert res.err.startswith(REFUSED)
    assert len(res.err.splitlines()) == 1
    assert list(home.iterdir()) == []


def test_version_neither_asks_nor_creates(home: Path) -> None:
    res = run_alr_in_terminal("--version")

    assert res.code == 0, res
    assert SETUP_PROMPT not in res.shown
    assert res.err == ""
    assert not (home / ".local").exists()
