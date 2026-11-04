"""RunSettings: when a run can start, and what it starts with."""

import json
from pathlib import Path

import pytest

from alr.engine.sandbox import bypass_args
from alr.errors import RefusedError
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


def test_session_args_for_a_named_sandbox_read_its_file_and_force_the_keys(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "my_sandbox.json"
    sandbox.write_text('{"sandbox": {"enabled": false}}', encoding="utf-8")
    settings = RunSettings(
        permissions=Permissions.SANDBOX,
        sandbox_file=str(sandbox),
        sandbox_name="my_sandbox",
    )

    args = settings.session_args()

    document = json.loads(args[args.index("--settings") + 1])
    assert document["sandbox"]["enabled"] is True


def test_session_args_refuse_a_named_sandbox_naming_the_flag_and_the_file(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "five.json"
    sandbox.write_text('{"sandbox": 5}', encoding="utf-8")
    settings = RunSettings(
        permissions=Permissions.SANDBOX,
        sandbox_file=str(sandbox),
        sandbox_name="five",
    )

    with pytest.raises(RefusedError) as excinfo:
        settings.session_args()

    assert excinfo.value.message == (
        f"--sandbox five: {sandbox}: sandbox: must be a JSON object"
    )


def test_a_named_sandbox_is_not_missing_once_the_folder_is_known() -> None:
    settings = RunSettings(
        tasks_file="t/tasks.yaml",
        permissions=Permissions.SANDBOX,
        sandbox_file="/h/.local/alr/sandboxes/tight.json",
        sandbox_name="tight",
    )

    assert settings.missing() is None


def test_log_folder_is_beside_the_task_file() -> None:
    settings = RunSettings(tasks_file="run/tasks.yaml")

    assert settings.log_folder() == Path("run/logs")


@pytest.mark.parametrize(
    ("settings", "text"),
    [
        (RunSettings(), "not chosen"),
        (RunSettings(permissions=Permissions.SKIP), "skip"),
        (
            RunSettings(
                permissions=Permissions.SANDBOX,
                sandbox_file="/s/tight.json",
                sandbox_name="tight",
            ),
            "sandbox (tight)",
        ),
        (
            RunSettings(permissions=Permissions.SANDBOX, sandbox_file="/s/mine.json"),
            "sandbox (/s/mine.json)",
        ),
    ],
    ids=["unset", "skip", "named sandbox", "settings file"],
)
def test_permissions_text(settings: RunSettings, text: str) -> None:
    assert settings.permissions_text() == text
