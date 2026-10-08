"""--dir: a run folder holding tasks.yaml is the only way to name the task file."""

from pathlib import Path

from e2e_helpers import (
    expected_stdin,
    fake_claude_records,
    read_tasks_yaml,
    run_alr,
    task_states,
    shared_block,
    write_tasks_yaml,
)

DIR_TASKS_YAML = (
    shared_block("FOLDER PROMPT")
    + """tasks:
  - id: 1
    name: Folder task
    prompt: Do the folder thing.
"""
)


def run_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "run"
    folder.mkdir()
    return folder


def test_dir_runs_with_only_tasks_yaml(tmp_path: Path, fake_claude: Path) -> None:
    folder = run_folder(tmp_path)
    tasks_path = write_tasks_yaml(folder, DIR_TASKS_YAML)
    assert [path.name for path in folder.iterdir()] == ["tasks.yaml"]
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


def test_dir_dry_run(tmp_path: Path, fake_claude: Path) -> None:
    folder = run_folder(tmp_path)
    tasks_path = write_tasks_yaml(folder, DIR_TASKS_YAML)

    res = run_alr("--dry-run", "-d", str(folder))

    assert res.code == 0, res
    assert f"{tasks_path} is valid\n" in res.out
