"""The command line itself: --version, --help, required flags, missing files, and --no-tui."""

from pathlib import Path

import pytest

from e2e_helpers import (
    TWO_TASKS_YAML,
    Result,
    run_alr,
    run_args,
    write_tasks_yaml,
)


def test_version_flag() -> None:
    res = run_alr("--version")

    assert res.code == 0, res
    assert res.out.startswith("alr version: ")
    assert res.out.count("\n") == 1


def test_help_flag() -> None:
    res = run_alr("--help")

    assert res.code == 0, res
    for flag in (
        "--dir",
        "--tasks",
        "--sandbox-settings",
        "--skip-permissions",
        "--gate-timeout",
        "--commit",
        "--log-dir",
        "--dry-run",
        "--no-tui",
        "--version",
    ):
        assert flag in res.out

    assert "--prompt" not in res.out


def test_missing_tasks() -> None:
    res = run_alr()

    assert res.code == 1, res
    assert "error: required flag --tasks not set\n" in res.err


def test_nonexistent_tasks_file(tmp_path: Path) -> None:
    tasks_path = tmp_path / "missing.yaml"

    res = run_alr(*run_args(tasks_path))

    assert res.code == 1, res
    assert (
        f'tasks file "{tasks_path}" is not accessible: No such file or directory'
        in res.err
    )


def run_two_tasks(folder: Path, *flags: str) -> tuple[Result, str]:
    """Run a fresh copy of the two-task file and return the result and the saved file."""
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, TWO_TASKS_YAML)
    res = run_alr(*run_args(tasks_path), *flags)
    return res, tasks_path.read_text(encoding="utf-8")


def test_non_terminal_defaults_to_plain(tmp_path: Path, fake_claude: Path) -> None:
    """With no terminal attached, a run without --no-tui matches one with it."""
    plain, plain_saved = run_two_tasks(tmp_path / "plain", "--no-tui")
    default, default_saved = run_two_tasks(tmp_path / "default")

    assert plain.code == 0, plain
    assert default == plain
    assert default_saved == plain_saved


def test_no_tui_is_accepted_and_the_run_is_plain(
    tmp_path: Path, fake_claude: Path
) -> None:
    res, saved = run_two_tasks(tmp_path / "plain", "--no-tui")

    assert res.code == 0, res
    assert res.err == ""
    # Plain output: each prompt echoed, then the session's own result line.
    assert res.out.count('{"state":"completed","error":""}\n') == 2
    assert "1: First task\n\nDo the first thing.\n" in res.out
    assert saved.count("state: completed") == 2
