"""--dry-run: validate the task file and report on it, starting nothing and writing nothing."""

from pathlib import Path

from e2e_helpers import (
    ONE_TASK_TABLE,
    VALID_TASKS_YAML,
    run_alr,
    run_args,
    run_dry_run,
    shared_block,
    write_tasks_yaml,
)


def test_dry_run_valid_file(tmp_path: Path, fake_claude: Path) -> None:
    res, tasks_path = run_dry_run(tmp_path, VALID_TASKS_YAML)

    assert res.code == 0, res
    assert res.out == f"{ONE_TASK_TABLE}{tasks_path} is valid\n"


def test_dry_run_invalid_file(tmp_path: Path, fake_claude: Path) -> None:
    res, tasks_path = run_dry_run(
        tmp_path,
        shared_block() + "tasks:\n  - {id: 1, name: a, prompt: p, state: bogus}\n",
    )

    assert res.code == 3, res
    assert "error: refused: invalid-task-file: failed to parse tasks yaml" in res.err
    assert "failed to start loop runner" not in res.err
    assert res.err.count("failed to parse tasks yaml") == 1
    assert f"{tasks_path} is valid" not in res.out


def test_dry_run_refuses_a_failed_task_like_a_real_run(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_yaml = (
        shared_block()
        + """tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
  - id: 2
    name: Second task
    prompt: Do the second thing.
    state: failed
    error: boom
"""
    )

    # run_dry_run also asserts the file is untouched and claude never ran.
    res, _ = run_dry_run(tmp_path, tasks_yaml)

    assert res.code == 3, res
    assert res.err.splitlines()[0].startswith("error: refused: failed-tasks: ")
    assert res.out == (
        "Some tasks failed previous runs:\n"
        "   ID  STATE      NAME\n"
        "   --  ---------  ----\n"
        "    1  PENDING    First task\n"
        "❌  2  \033[1;91mFAILED   \033[0m  Second task  \033[1;91m← Needs review!\033[0m\n"
    )


def test_dry_run_missing_dir_flag() -> None:
    res = run_alr("--dry-run")

    assert res.code == 2, res
    # Usage follows the error and names every flag, so match the error line.
    assert "error: required flag --dir not set\n" in res.err


def test_dry_run_with_reset_failed_is_refused(tmp_path: Path) -> None:
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)

    res = run_alr("-d", str(tmp_path), "--dry-run", "--reset-failed")

    assert res.code == 2, res
    assert (
        "error: --reset-failed cannot be used with --dry-run (a dry run writes nothing)\n"
        in res.err
    )
    assert tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML


def test_dry_run_valid_gates_are_not_run(tmp_path: Path, fake_claude: Path) -> None:
    marker = tmp_path / "gate-ran"
    tasks_yaml = (
        shared_block(gates=[{"cmd": f"touch '{marker}'"}])
        + "tasks:\n  - id: 1\n    name: First task\n    prompt: p\n"
    )

    res, tasks_path = run_dry_run(tmp_path, tasks_yaml)

    assert res.code == 0, res
    assert f"{tasks_path} is valid\n" in res.out
    assert not marker.exists(), "a dry run must never run a gate"


def test_dry_run_invalid_gates(tmp_path: Path, fake_claude: Path) -> None:
    tasks_yaml = (
        shared_block(gates=[{"command": "go test ./..."}])
        + "tasks:\n  - id: 1\n    name: First task\n    prompt: p\n"
    )

    res, tasks_path = run_dry_run(tmp_path, tasks_yaml)

    assert res.code == 3, res
    assert (
        "failed to parse tasks yaml: shared.gates[0]: command: unknown key; a gate has only cmd"
        in res.err
    )
    assert f"{tasks_path} is valid" not in res.out


def test_dry_run_refuses_a_read_only_log_folder_like_a_real_run(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    logs = tmp_path / "logs"
    logs.mkdir()
    logs.chmod(0o500)

    try:
        res = run_alr("-d", str(tmp_path), "--dry-run")
    finally:
        logs.chmod(0o700)

    assert res.code == 3, res
    assert res.err.splitlines()[0].startswith("error: refused: log-unwritable: ")
    assert res.out == ""
    assert list(logs.iterdir()) == []
    assert tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML


def test_dry_run_refuses_a_dangling_logs_symlink_like_a_real_run(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    logs = tmp_path / "logs"
    logs.symlink_to("/nonexistent/x")

    dry = run_alr("-d", str(tmp_path), "--dry-run")
    real = run_alr(*run_args(tasks_path))

    assert dry.code == 3, dry
    assert dry.err.splitlines()[0].startswith("error: refused: log-unwritable: ")
    assert dry.out == ""
    assert real.code == 3, real
    assert real.err.splitlines()[0].startswith("error: refused: log-unwritable: ")
    assert "File exists" in real.err
    assert "File exists" in dry.err
    assert logs.is_symlink()
    assert tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML


def test_dry_run_accepts_a_logs_symlink_to_an_existing_folder(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "logs").symlink_to(target)

    res = run_alr("-d", str(tmp_path), "--dry-run")

    assert res.code == 0, res
    assert res.out == f"{ONE_TASK_TABLE}{tasks_path} is valid\n"
    assert list(target.iterdir()) == []
