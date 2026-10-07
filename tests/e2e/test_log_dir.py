"""The run log in plain mode: every real run leaves one in <dir>/logs; a dry run never does.

--log-dir does not exist, so the run folder is the only place the log can go.
"""

import json
from pathlib import Path

from e2e_helpers import (
    VALID_TASKS_YAML,
    run_alr,
    run_dry_run,
    run_args,
    write_tasks_yaml,
)


def test_log_dir_is_an_unknown_flag(tmp_path: Path, fake_claude: Path) -> None:
    """A returning --log-dir would pass here as a plain run; exit 2 and the usage line tell it apart."""
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    log_dir = tmp_path / "elsewhere"

    res = run_alr(*run_args(tasks_path), f"--log-dir={log_dir}")

    assert res.code == 2, res
    assert f"unrecognized arguments: --log-dir={log_dir}\n" in res.err
    assert not log_dir.exists()
    assert not (tmp_path / "logs").exists()
    assert tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML


def test_plain_run_leaves_a_log_in_the_run_folder(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr(*run_args(tasks_path))

    assert res.code == 0, res
    (folder,) = (tmp_path / "logs").iterdir()
    ledger = [
        json.loads(line)
        for line in (folder / "run.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert ledger[0]["event"] == "run_started"
    assert ledger[-1]["event"] == "run_finished"
    assert (folder / "task-1.log").is_file()


def test_commit_run_with_a_log_folder_git_can_see_is_refused(
    tmp_path: Path, git_repo: Path, fake_claude: Path
) -> None:
    # Only the task file is ignored, so the logs folder beside it stays visible.
    (git_repo / ".git" / "info" / "exclude").write_text(
        "work/tasks.yaml\n", encoding="utf-8"
    )
    work = git_repo / "work"
    work.mkdir()
    tasks_path = write_tasks_yaml(work, VALID_TASKS_YAML)
    record_file = git_repo / "run" / "record.ndjson"

    res = run_alr(
        *run_args(tasks_path),
        "--commit",
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 1, res
    assert (
        "error: --commit needs the log directory ignored by git or outside the repository: work/logs\n"
        in res.err
    )
    assert not record_file.exists(), "claude must not run"
    assert not (work / "logs").exists()


def test_dry_run_leaves_no_logs_folder(tmp_path: Path, fake_claude: Path) -> None:
    run_dry_run(tmp_path, VALID_TASKS_YAML)

    assert not (tmp_path / "logs").exists()
