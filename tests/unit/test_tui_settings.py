"""RunSettings: when a run can start, and what it starts with."""

from pathlib import Path

import pytest

from alr.engine.sandbox import bypass_args
from alr.tui.settings import Permissions, RunSettings


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        (RunSettings(), "choose a run folder"),
        (RunSettings(tasks_file="f/tasks.yaml"), "choose how sessions run"),
        (
            RunSettings(tasks_file="f/tasks.yaml", permissions=Permissions.SANDBOX),
            "choose a sandbox settings file",
        ),
    ],
    ids=["no folder", "no permission choice", "sandbox without a file"],
)
def test_missing_names_the_first_thing_to_settle(
    settings: RunSettings, reason: str
) -> None:
    assert settings.missing() == reason


@pytest.mark.parametrize(
    "permissions", [Permissions.SKIP, Permissions.SANDBOX], ids=["skip", "sandbox"]
)
def test_missing_is_none_when_everything_is_settled(permissions: Permissions) -> None:
    settings = RunSettings(
        tasks_file="f/tasks.yaml",
        permissions=permissions,
        sandbox_file="f/sandbox.json",
    )

    assert settings.missing() is None


def test_session_args_for_skip_is_the_bypass_flag() -> None:
    settings = RunSettings(permissions=Permissions.SKIP)

    assert settings.session_args() == bypass_args()


def test_session_args_for_sandbox_reads_the_settings_file(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox.json"
    sandbox.write_text("{}", encoding="utf-8")
    settings = RunSettings(permissions=Permissions.SANDBOX, sandbox_file=str(sandbox))

    assert settings.session_args()[:2] == ["--permission-mode", "acceptEdits"]


@pytest.mark.parametrize(
    ("log_dir", "want"),
    [("", "run/logs"), ("elsewhere", "elsewhere")],
    ids=["beside the task file", "explicit --log-dir"],
)
def test_log_folder(log_dir: str, want: str) -> None:
    settings = RunSettings(tasks_file="run/tasks.yaml", log_dir=log_dir)

    assert settings.log_folder() == Path(want)
