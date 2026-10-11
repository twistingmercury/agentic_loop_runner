import json
from pathlib import Path

import pytest

from alr.engine.sandbox import folder_sandbox, sandbox_args
from alr.errors import RefusedError

FORCED = {"allowUnsandboxedCommands": False, "enabled": True, "failIfUnavailable": True}

PASS_THROUGH_IN = """{
  "permissions": {"deny": ["WebFetch"]},
  "sandbox": {
    "filesystem": {"denyRead": ["~/"], "allowWrite": ["/tmp/build"]},
    "network": {"allowedDomains": ["proxy.golang.org"], "httpProxyPort": 12345678901234567890}
  }
}"""
PASS_THROUGH_WANT = (
    '{"permissions":{"deny":["WebFetch"]},"sandbox":{"allowUnsandboxedCommands":false,'
    '"enabled":true,"failIfUnavailable":true,"filesystem":{"denyRead":["~/"],'
    '"allowWrite":["/tmp/build"]},"network":{"allowedDomains":["proxy.golang.org"],'
    '"httpProxyPort":12345678901234567890}}}'
)


def _write_settings(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "sandbox.json"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.parametrize(
    ("content", "want"),
    [
        pytest.param(
            "{}",
            '{"sandbox":{"allowUnsandboxedCommands":false,"enabled":true,"failIfUnavailable":true}}',
            id="empty object",
        ),
        pytest.param(
            '{"sandbox":{"enabled":false,"allowUnsandboxedCommands":true,"failIfUnavailable":false}}',
            '{"sandbox":{"allowUnsandboxedCommands":false,"enabled":true,"failIfUnavailable":true}}',
            id="forced keys set the other way",
        ),
        pytest.param(
            PASS_THROUGH_IN, PASS_THROUGH_WANT, id="everything else passes through"
        ),
    ],
)
def test_sandbox_args_forces_sandbox_keys(
    tmp_path: Path, content: str, want: str
) -> None:
    path = _write_settings(tmp_path, content)

    args = sandbox_args(str(path))

    assert args[:3] == ["--permission-mode", "acceptEdits", "--settings"]
    assert len(args) == 4
    # Key order and number spelling may differ from gralph's; the settings
    # Claude reads must not.
    assert json.loads(args[3]) == json.loads(want)
    assert "\n" not in args[3]
    # The settings file must not be written.
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    ("content", "want_error"),
    [
        pytest.param(
            "sandbox: true", "--sandbox-settings: must be a JSON object", id="not JSON"
        ),
        pytest.param("[]", "--sandbox-settings: must be a JSON object", id="array"),
        pytest.param(
            '{"sandbox":null}',
            "--sandbox-settings: sandbox: must be a JSON object",
            id="sandbox null",
        ),
        # The settings are re-serialized for Claude, so NaN would turn into invalid JSON.
        pytest.param(
            '{"sandbox":{"x":NaN}}',
            "--sandbox-settings: must be a JSON object: ",
            id="NaN",
        ),
        pytest.param(
            '\ufeff{"sandbox":{}}',
            "--sandbox-settings: must be a JSON object: ",
            id="byte order mark",
        ),
        pytest.param(
            "[" * 100_000,
            "--sandbox-settings: must be a JSON object: ",
            id="deeply nested",
        ),
    ],
)
def test_sandbox_args_rejects_bad_files(
    tmp_path: Path, content: str, want_error: str
) -> None:
    with pytest.raises(RefusedError) as excinfo:
        sandbox_args(str(_write_settings(tmp_path, content)))

    assert excinfo.value.reason == "invalid-sandbox-settings"
    assert want_error in str(excinfo.value)


@pytest.mark.parametrize("name", ["missing.json"], ids=["missing file"])
def test_sandbox_args_unreadable_path(tmp_path: Path, name: str) -> None:
    path = str(tmp_path / name)

    with pytest.raises(RefusedError) as excinfo:
        sandbox_args(path)

    assert excinfo.value.reason == "invalid-sandbox-settings"
    assert "--sandbox-settings" in str(excinfo.value)
    assert path in str(excinfo.value)


@pytest.mark.parametrize("text", ["\\ud800"], ids=["lone surrogate"])
def test_sandbox_args_settings_can_always_be_encoded(tmp_path: Path, text: str) -> None:
    # The argument goes to a child process, so it must be encodable whatever the file holds.
    path = _write_settings(tmp_path, '{"sandbox":{"note":"' + text + '"}}')

    settings = sandbox_args(str(path))[3]

    settings.encode("utf-8")
    assert json.loads(settings)["sandbox"]["note"] == json.loads(f'"{text}"')


def test_folder_sandbox_keeps_a_relative_dir_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings(tmp_path, "{}")
    monkeypatch.chdir(tmp_path.parent)

    assert folder_sandbox(tmp_path.name) == f"{tmp_path.name}/sandbox.json"
