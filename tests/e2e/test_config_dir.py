"""The config folder, ~/.local/alr: made on the first real run, left alone after, never made by a dry run."""

from pathlib import Path

from e2e_helpers import (
    FIRST_RUN_PREFIX,
    VALID_TASKS_YAML,
    fake_claude_records,
    run_alr,
    write_tasks_yaml,
)

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


def test_first_run_creates_the_folder_and_says_so(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    folder = home / ".local" / "alr"

    res = run_alr("--yolo", f"--dir={tmp_path}", env={"HOME": str(home)})

    assert res.code == 0, res
    said = [line for line in res.err.splitlines() if line.startswith(FIRST_RUN_PREFIX)]
    assert len(said) == 1, res.err
    assert str(folder) in said[0]
    assert "config.yaml" in said[0]
    assert (folder / "config.yaml").is_file()
    assert (folder / "prompts" / "task_review.md").is_file()
    assert (folder / "prompts" / "task_remediation.md").is_file()


def test_a_second_run_leaves_edits_alone(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = home / ".local" / "alr"
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    run_alr("--yolo", f"--dir={tmp_path}", env={"HOME": str(home)})
    review_prompt = folder / "prompts" / "task_review.md"
    review_prompt.write_text("my own review prompt\n", encoding="utf-8")
    config_before = (folder / "config.yaml").read_text(encoding="utf-8")
    # The first run completed the task; a fresh file gives the second run work to do.
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr("--yolo", f"--dir={tmp_path}", env={"HOME": str(home)})

    assert res.code == 0, res
    assert FIRST_RUN_PREFIX not in res.err
    assert review_prompt.read_text(encoding="utf-8") == "my own review prompt\n"
    assert (folder / "config.yaml").read_text(encoding="utf-8") == config_before


def test_dry_run_creates_nothing(tmp_path: Path, home: Path, fake_claude: Path) -> None:
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr("--dry-run", f"--dir={tmp_path}", env={"HOME": str(home)})

    assert res.code == 0, res
    assert FIRST_RUN_PREFIX not in res.err
    assert not (home / ".local").exists()
    assert list(home.iterdir()) == []


def test_a_yolo_default_runs_without_a_flag(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = home / ".local" / "alr"
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    run_alr("--yolo", f"--dir={tmp_path}", env={"HOME": str(home)})
    (folder / "config.yaml").write_text(YOLO_CONFIG, encoding="utf-8")
    # The first run completed the task; a fresh file gives the second run work to do.
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        f"--dir={tmp_path}",
        env={"HOME": str(home), "FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    records = fake_claude_records(record_file)
    assert [record["argv"] for record in records] == [
        ["--print", "--dangerously-skip-permissions"]
    ]
