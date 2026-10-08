"""The command line itself: --version, --help, required flags, missing files, and --plain."""

import re
from pathlib import Path

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
    assert re.fullmatch(r"alr version: \S+ [a-z]+/\S+\n", res.out)


def test_help_flag() -> None:
    res = run_alr("--help")

    assert res.code == 0, res
    for flag in (
        "--dir",
        "--sandbox-settings",
        "--yolo",
        "--gate-timeout",
        "--commit",
        "--dry-run",
        "--plain",
        "--version",
    ):
        assert flag in res.out

    assert "--prompt" not in res.out


def test_missing_tasks() -> None:
    res = run_alr()

    assert res.code == 1, res
    assert "error: required flag --dir not set\n" in res.err


def test_skip_permissions_is_an_unknown_flag(tmp_path: Path, fake_claude: Path) -> None:
    """--yolo replaced it; exit 2 and the unknown-flag line keep the old name from coming back as an alias."""
    tasks_path = write_tasks_yaml(tmp_path, TWO_TASKS_YAML)

    res = run_alr("--skip-permissions", f"--dir={tasks_path.parent}")

    assert res.code == 2, res
    assert "unrecognized arguments: --skip-permissions\n" in res.err
    assert tasks_path.read_text(encoding="utf-8") == TWO_TASKS_YAML


def run_two_tasks(folder: Path, *flags: str) -> tuple[Result, str]:
    """Run a fresh copy of the two-task file and return the result and the saved file."""
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, TWO_TASKS_YAML)
    res = run_alr(*run_args(tasks_path), *flags)
    return res, tasks_path.read_text(encoding="utf-8")


def test_non_terminal_defaults_to_plain(tmp_path: Path, fake_claude: Path) -> None:
    """With no terminal attached, a run without --plain matches one with it."""
    plain, plain_saved = run_two_tasks(tmp_path / "plain", "--plain")
    default, default_saved = run_two_tasks(tmp_path / "default")

    assert plain.code == 0, plain
    assert default == plain
    assert default_saved == plain_saved


def test_plain_is_accepted_and_the_run_is_plain(
    tmp_path: Path, fake_claude: Path
) -> None:
    res, saved = run_two_tasks(tmp_path / "plain", "--plain")

    assert res.code == 0, res
    assert res.err == ""
    # Plain output: each prompt echoed, then the session's own result line.
    assert res.out.count('{"state":"completed","error":""}\n') == 2
    assert "1: First task\n\nDo the first thing.\n" in res.out
    assert saved.count("state: completed") == 2
