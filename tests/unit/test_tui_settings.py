"""RunSettings: when a run can start, and what it starts with."""

from dataclasses import replace
from pathlib import Path

import pytest

from alr.engine.presets import Preset, Switches, render, switches_for
from alr.engine.sandbox import bypass_args, settings_args
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


def test_log_folder_is_beside_the_task_file() -> None:
    settings = RunSettings(tasks_file="run/tasks.yaml")

    assert settings.log_folder() == Path("run/logs")


def _preset_settings(**changes: object) -> RunSettings:
    settings = RunSettings(
        tasks_file="t/tasks.yaml",
        permissions=Permissions.SANDBOX,
        preset=Preset.TIGHT,
        switches=switches_for(Preset.TIGHT),
        sandbox_file="t/sandbox.json",
    )
    return replace(settings, **changes)


def test_a_preset_answer_is_not_missing_once_the_folder_is_known() -> None:
    assert _preset_settings().missing() is None


def test_a_preset_answer_without_a_folder_asks_for_the_folder() -> None:
    settings = _preset_settings(tasks_file="", sandbox_file="")

    assert settings.missing() == "choose a run folder"


def test_a_preset_answer_with_a_folder_but_no_file_path_asks_for_the_folder() -> None:
    assert _preset_settings(sandbox_file="").missing() == "choose a run folder"


def test_session_args_for_a_preset_come_from_the_rendered_document() -> None:
    expected = settings_args(render(switches_for(Preset.TIGHT)))

    assert _preset_settings().session_args() == expected


@pytest.mark.parametrize(
    ("settings", "text"),
    [
        (RunSettings(), "not chosen"),
        (RunSettings(permissions=Permissions.SKIP), "skip"),
        (
            RunSettings(permissions=Permissions.SANDBOX, sandbox_file="s.json"),
            "sandbox",
        ),
        (_preset_settings(), "sandbox (tight)"),
        (
            _preset_settings(preset=Preset.LARGE, switches=switches_for(Preset.LARGE)),
            "sandbox (large)",
        ),
        (
            _preset_settings(
                switches=Switches(web=True, docker=False, hide_credentials=True)
            ),
            "sandbox (tight, adjusted)",
        ),
        (
            _preset_settings(
                preset=Preset.LARGE,
                switches=Switches(web=True, docker=False, hide_credentials=True),
            ),
            "sandbox (large, adjusted)",
        ),
    ],
    ids=["unset", "skip", "file", "tight", "large", "tight adjusted", "large adjusted"],
)
def test_permissions_text(settings: RunSettings, text: str) -> None:
    assert settings.permissions_text() == text
