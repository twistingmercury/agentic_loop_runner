"""--validate: the dry run, then a fresh review of the task file, run as the installed command."""

import os
import signal
import stat
import time
from pathlib import Path

from e2e_helpers import (
    DRY_RUN_TASKS_YAML,
    VALID_TASKS_YAML,
    fake_claude_records,
    run_alr,
    start_alr,
    wait_for_file,
    wait_process_gone,
    write_tasks_yaml,
)

FINDINGS = "task=1 | size | too big | five files\n"
REVIEW_SKIPPED = "review skipped: the dry run found problems; fix them and run again\n"


def review_logs(folder: Path) -> list[Path]:
    return sorted((folder / "logs").glob("review-*.log"))


def test_validate_prints_the_review_and_writes_the_log(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, DRY_RUN_TASKS_YAML)
    record = tmp_path / "record.ndjson"

    res = run_alr(
        "-d",
        str(tmp_path),
        "--validate",
        env={"FAKECLAUDE_OUTPUT": FINDINGS, "FAKECLAUDE_RECORD_FILE": str(record)},
    )

    [log] = review_logs(tmp_path)
    assert res.code == 0, res
    assert res.out.endswith(
        f"{tasks_path} is valid\n"
        f"review: asking Claude to read {tasks_path} (this uses tokens)\n"
        f"{FINDINGS}"
        f"review log: {log}\n"
    )
    assert FINDINGS in log.read_text(encoding="utf-8")
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    [call] = fake_claude_records(record)
    assert str(tasks_path) in call["stdin"]


def test_validate_skips_the_review_on_a_warning(
    tmp_path: Path, fake_claude: Path
) -> None:
    # No finish block, so the dry run warns.
    write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    record = tmp_path / "record.ndjson"

    res = run_alr(
        "-d",
        str(tmp_path),
        "--validate",
        env={"FAKECLAUDE_RECORD_FILE": str(record)},
    )

    assert res.code == 0, res
    assert res.out.endswith(REVIEW_SKIPPED)
    assert "review:" not in res.out
    assert not record.exists()
    assert not (tmp_path / "logs").exists()


def test_validate_reports_a_failed_review(tmp_path: Path, fake_claude: Path) -> None:
    write_tasks_yaml(tmp_path, DRY_RUN_TASKS_YAML)

    res = run_alr(
        "-d",
        str(tmp_path),
        "--validate",
        env={"FAKECLAUDE_EXIT_CODE": "1", "FAKECLAUDE_STDERR": "boom\n"},
    )

    assert res.code == 1, res
    assert res.err == "error: review: claude failed (exit status 1): boom\n"
    [log] = review_logs(tmp_path)
    assert "error: review: claude failed (exit status 1): boom" in log.read_text(
        encoding="utf-8"
    )


def test_validate_is_rejected_with_reset_failed(
    tmp_path: Path, fake_claude: Path
) -> None:
    write_tasks_yaml(tmp_path, DRY_RUN_TASKS_YAML)
    record = tmp_path / "record.ndjson"

    res = run_alr(
        "-d",
        str(tmp_path),
        "--validate",
        "--reset-failed",
        env={"FAKECLAUDE_RECORD_FILE": str(record)},
    )

    assert res.code == 2, res
    assert "--reset-failed cannot be used with --validate" in res.err
    assert not record.exists()


def test_validate_passes_sandbox_and_action_timeout_to_the_review(
    tmp_path: Path, fake_claude: Path, home: Path
) -> None:
    write_tasks_yaml(tmp_path, DRY_RUN_TASKS_YAML)
    tight = home / ".local" / "alr" / "sandboxes" / "tight.json"
    tight.unlink()
    record = tmp_path / "record.ndjson"

    named = run_alr(
        "-d",
        str(tmp_path),
        "--validate",
        "--sandbox",
        "tight",
        env={"FAKECLAUDE_RECORD_FILE": str(record)},
    )

    assert named.code == 0, named
    assert f"Added {tight}\n" in named.out
    assert "added on a real run" not in named.out
    assert tight.is_file()
    [call] = fake_claude_records(record)
    assert str(tight) in call["stdin"]

    started = time.monotonic()
    limited = run_alr(
        "-d",
        str(tmp_path),
        "--validate",
        "--action-timeout",
        "1s",
        env={"FAKECLAUDE_BLOCK": "1"},
        timeout=30,
    )

    assert limited.code == 1, limited
    assert limited.err == "error: review: claude did not finish within 1s\n"
    assert time.monotonic() - started < 20


def test_validate_stops_on_sighup(tmp_path: Path, fake_claude: Path) -> None:
    write_tasks_yaml(tmp_path, DRY_RUN_TASKS_YAML)
    ready_file = tmp_path / "ready"
    env = {"FAKECLAUDE_BLOCK": "1", "FAKECLAUDE_READY_FILE": str(ready_file)}

    with start_alr("-d", str(tmp_path), "--validate", env=env) as process:
        try:
            # The fake claude writes its pid once it is blocking in the review.
            claude_pid = int(wait_for_file(ready_file, 10))
            process.send_signal(signal.SIGHUP)
            out, err = process.communicate(timeout=10)
            assert process.returncode == 129, (out, err)
            assert err == "error: interrupted\n"
            [log] = review_logs(tmp_path)
            assert log.read_text(encoding="utf-8").endswith("\n\nstopped by SIGHUP\n")
            # Checked before the cleanup below, so it sees what alr itself left behind.
            wait_process_gone(claude_pid, 10)
        finally:
            process.kill()
            # Only a failing test gets here with claude still running.
            kill_pid_in(ready_file)


def kill_pid_in(pid_file: Path) -> None:
    """SIGKILL the process named in pid_file, so a failing test leaves no claude running."""
    try:
        os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
    except (OSError, ValueError):
        # No file, or the process is already gone: nothing is left to stop.
        pass
