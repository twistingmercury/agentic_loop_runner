"""--dir: a run folder stands in for --tasks and --prompt."""

from pathlib import Path

from helpers import fake_claude_records

from e2e.conftest import (
    expected_stdin,
    read_tasks_yaml,
    run_alr,
    task_states,
    write_prompt,
    write_tasks_yaml,
)

DIR_TASKS_YAML = """tasks:
  - id: 1
    name: Folder task
    prompt: Do the folder thing.
"""


def run_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "run"
    folder.mkdir()
    return folder


def test_dir_runs_the_folders_files(tmp_path: Path, fake_claude: Path) -> None:
    folder = run_folder(tmp_path)
    write_prompt(folder, "FOLDER PROMPT\n")
    tasks_path = write_tasks_yaml(folder, DIR_TASKS_YAML)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        "--skip-permissions",
        "-d",
        str(folder),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    records = fake_claude_records(record_file)
    assert len(records) == 1
    assert records[0]["stdin"] == expected_stdin(
        "FOLDER PROMPT", 1, "Folder task", "Do the folder thing."
    )
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}


def test_dir_tasks_flag_wins(tmp_path: Path, fake_claude: Path) -> None:
    folder = run_folder(tmp_path)
    write_prompt(folder, "FOLDER PROMPT\n")
    folder_tasks = write_tasks_yaml(folder, DIR_TASKS_YAML)
    other_tasks = write_tasks_yaml(
        tmp_path,
        "tasks:\n  - id: 7\n    name: Other task\n    prompt: Do the other thing.\n",
    )
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        "--skip-permissions",
        "-d",
        str(folder),
        "-t",
        str(other_tasks),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    records = fake_claude_records(record_file)
    assert len(records) == 1
    assert records[0]["stdin"] == expected_stdin(
        "FOLDER PROMPT", 7, "Other task", "Do the other thing."
    )
    assert folder_tasks.read_text(encoding="utf-8") == DIR_TASKS_YAML


def test_dir_missing_tasks_file(tmp_path: Path, fake_claude: Path) -> None:
    folder = run_folder(tmp_path)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        "--skip-permissions",
        "-d",
        str(folder),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 1, res
    assert res.err == f"error: --dir: no tasks.yaml in {folder}\n"
    assert fake_claude_records(record_file) == []


def test_dir_dry_run_needs_no_prompt(tmp_path: Path, fake_claude: Path) -> None:
    folder = run_folder(tmp_path)
    tasks_path = write_tasks_yaml(folder, DIR_TASKS_YAML)

    res = run_alr("--dry-run", "-d", str(folder))

    assert res.code == 0, res
    assert f"{tasks_path} is valid\n" in res.out
