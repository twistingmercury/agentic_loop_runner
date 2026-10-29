import json
import os
from pathlib import Path

import pytest

from alr.engine.presets import (
    CREDENTIAL_PATHS,
    WEB_DOMAINS,
    Preset,
    Switches,
    preset_path,
    render,
    restore_previous,
    switches_for,
    write_preset,
)
from alr.errors import SANDBOX_FILE_UNWRITABLE, RefusedError

TIGHT_TEXT = """{
  "sandbox": {
    "allowUnsandboxedCommands": false,
    "enabled": true,
    "failIfUnavailable": true,
    "filesystem": {
      "denyRead": [
        "~/.ssh",
        "~/.aws",
        "~/.gnupg",
        "~/.config/gh",
        "~/.netrc",
        "~/.claude"
      ]
    }
  }
}
"""


def test_tight_switches() -> None:
    assert switches_for(Preset.TIGHT) == Switches(
        web=False, docker=False, hide_credentials=True
    )


def test_large_switches() -> None:
    assert switches_for(Preset.LARGE) == Switches(
        web=True, docker=True, hide_credentials=True
    )


def test_render_tight_is_the_documented_text() -> None:
    assert render(switches_for(Preset.TIGHT)) == TIGHT_TEXT


def test_render_large_has_the_web_list_and_docker() -> None:
    doc = json.loads(render(switches_for(Preset.LARGE)))["sandbox"]

    assert doc["network"] == {"allowedDomains": list(WEB_DOMAINS)}
    assert doc["excludedCommands"] == ["docker *"]
    assert doc["filesystem"] == {"denyRead": list(CREDENTIAL_PATHS)}


def test_an_off_switch_leaves_its_key_absent() -> None:
    doc = json.loads(render(Switches(web=False, docker=False, hide_credentials=False)))[
        "sandbox"
    ]

    assert set(doc) == {"enabled", "allowUnsandboxedCommands", "failIfUnavailable"}


def test_tight_never_excludes_a_command() -> None:
    assert "excludedCommands" not in render(switches_for(Preset.TIGHT))


def test_render_is_deterministic() -> None:
    assert render(switches_for(Preset.LARGE)) == render(switches_for(Preset.LARGE))


def test_write_preset_returns_none_when_there_was_no_file(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.json"

    assert write_preset(str(path), switches_for(Preset.TIGHT)) is None
    assert path.read_text(encoding="utf-8") == render(switches_for(Preset.TIGHT))


def test_write_preset_returns_the_previous_bytes_and_replaces_them(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sandbox.json"
    path.write_bytes(b"old")

    assert write_preset(str(path), switches_for(Preset.LARGE)) == b"old"
    assert path.read_text(encoding="utf-8") == render(switches_for(Preset.LARGE))


def test_write_preset_leaves_no_temporary_file(tmp_path: Path) -> None:
    write_preset(str(tmp_path / "sandbox.json"), switches_for(Preset.TIGHT))

    assert [p.name for p in tmp_path.iterdir()] == ["sandbox.json"]


def test_write_preset_into_an_unwritable_folder_is_refused(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can write to a read-only folder")
    folder = tmp_path / "locked"
    folder.mkdir()
    folder.chmod(0o500)
    path = folder / "sandbox.json"

    try:
        with pytest.raises(RefusedError) as excinfo:
            write_preset(str(path), switches_for(Preset.TIGHT))
    finally:
        folder.chmod(0o700)

    assert excinfo.value.reason == SANDBOX_FILE_UNWRITABLE
    assert excinfo.value.exit_code == 3
    assert str(path) in excinfo.value.message
    assert list(folder.iterdir()) == []


def test_restore_previous_puts_the_bytes_back(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.json"
    path.write_bytes(b"new")

    restore_previous(str(path), b"old")

    assert path.read_bytes() == b"old"


def test_restore_previous_removes_the_file_when_there_was_none(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.json"
    path.write_bytes(b"new")

    restore_previous(str(path), None)

    assert not path.exists()


def test_restore_previous_with_none_and_no_file_is_harmless(tmp_path: Path) -> None:
    restore_previous(str(tmp_path / "sandbox.json"), None)


def test_preset_path_is_beside_the_task_file() -> None:
    assert preset_path("run dir/tasks.yaml") == "run dir/sandbox.json"
