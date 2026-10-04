"""--log-dir in plain mode: a real run refuses it, and a dry run ignores it.

alr has no full-screen view yet, so only the flag's plain-mode rules can be
pinned here.
"""

from dataclasses import dataclass
from pathlib import Path

from e2e_helpers import (
    ONE_TASK_TABLE,
    VALID_TASKS_YAML,
    Result,
    fake_claude_records,
    read_tasks_yaml,
    run_alr,
    run_dry_run,
    task_states,
    write_prompt,
    write_tasks_yaml,
)

LOG_DIR_PLAIN_ERROR = "error: --log-dir only works with the full-screen view\n"


@dataclass(frozen=True)
class LogDirRun:
    res: Result
    tasks_path: Path
    log_dir: Path
    calls: int


def run_with_log_dir(tmp_path: Path, *flags: str, empty: bool = False) -> LogDirRun:
    """Run a one-task file with --log-dir, set to an empty value when asked, plus flags."""
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    log_dir = tmp_path / "logs"
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        "--skip-permissions",
        "-p",
        str(prompt_path),
        "-t",
        str(tasks_path),
        "--log-dir=" if empty else f"--log-dir={log_dir}",
        *flags,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )
    calls = len(fake_claude_records(record_file))
    return LogDirRun(res, tasks_path, log_dir, calls)


def assert_log_dir_refused(run: LogDirRun) -> None:
    assert run.res.code == 1, run.res
    assert run.res.err == LOG_DIR_PLAIN_ERROR
    assert run.res.out == ""
    assert run.calls == 0, "claude must not run"
    assert not run.log_dir.exists()
    assert run.tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML


def test_log_dir_no_tui_is_refused(tmp_path: Path, fake_claude: Path) -> None:
    assert_log_dir_refused(run_with_log_dir(tmp_path, "--no-tui"))


def test_log_dir_no_terminal_is_refused(tmp_path: Path, fake_claude: Path) -> None:
    """With no terminal the run is plain without --no-tui, so the same refusal applies."""
    assert_log_dir_refused(run_with_log_dir(tmp_path))


def test_log_dir_empty_value_counts_as_not_passed(
    tmp_path: Path, fake_claude: Path
) -> None:
    run = run_with_log_dir(tmp_path, "--no-tui", empty=True)

    assert run.res.code == 0, run.res
    assert run.calls == 1, "the run must go ahead"
    assert task_states(read_tasks_yaml(run.tasks_path))[1] == "completed"
    assert not run.log_dir.exists()


def test_log_dir_dry_run_ignores_it(tmp_path: Path, fake_claude: Path) -> None:
    log_dir = tmp_path / "logs"

    res, tasks_path = run_dry_run(tmp_path, VALID_TASKS_YAML, "--log-dir", str(log_dir))

    assert res.code == 0, res
    # The output is the same as without --log-dir.
    assert res.out == f"{ONE_TASK_TABLE}{tasks_path} is valid\n"
    assert res.err == ""
    assert not log_dir.exists()
