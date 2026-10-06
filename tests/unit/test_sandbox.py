import errno
import json
import os
from pathlib import Path

import pytest

from alr.engine.sandbox import bypass_args, folder_sandbox, sandbox_args
from alr.errors import SandboxError

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


def test_bypass_args() -> None:
    assert bypass_args() == ["--dangerously-skip-permissions"]


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
        pytest.param("", "--sandbox-settings: must be a JSON object", id="empty file"),
        pytest.param(
            "sandbox: true", "--sandbox-settings: must be a JSON object", id="not JSON"
        ),
        pytest.param("null", "--sandbox-settings: must be a JSON object", id="null"),
        pytest.param("[]", "--sandbox-settings: must be a JSON object", id="array"),
        pytest.param('"x"', "--sandbox-settings: must be a JSON object", id="string"),
        pytest.param(
            '{"sandbox":null}',
            "--sandbox-settings: sandbox: must be a JSON object",
            id="sandbox null",
        ),
        pytest.param(
            '{"sandbox":[]}',
            "--sandbox-settings: sandbox: must be a JSON object",
            id="sandbox array",
        ),
        pytest.param(
            '{"sandbox":true}',
            "--sandbox-settings: sandbox: must be a JSON object",
            id="sandbox bool",
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
    with pytest.raises(SandboxError) as excinfo:
        sandbox_args(str(_write_settings(tmp_path, content)))

    assert want_error in str(excinfo.value)


@pytest.mark.parametrize(
    ("name", "code"),
    [("missing.json", errno.ENOENT), ("", errno.EISDIR)],
    ids=["missing file", "directory"],
)
def test_sandbox_args_unreadable_path(tmp_path: Path, name: str, code: int) -> None:
    path = str(tmp_path / name) if name else str(tmp_path)

    with pytest.raises(SandboxError) as excinfo:
        sandbox_args(path)

    assert str(excinfo.value) == (
        f'--sandbox-settings: file "{path}" is not accessible: {os.strerror(code)}'
    )


@pytest.mark.parametrize(
    "text", ["héllo", "\\ud800"], ids=["non-ASCII", "lone surrogate"]
)
def test_sandbox_args_settings_can_always_be_encoded(tmp_path: Path, text: str) -> None:
    # The argument goes to a child process, so it must be encodable whatever the file holds.
    path = _write_settings(tmp_path, '{"sandbox":{"note":"' + text + '"}}')

    settings = sandbox_args(str(path))[3]

    settings.encode("utf-8")
    assert json.loads(settings)["sandbox"]["note"] == json.loads(f'"{text}"')


def test_folder_sandbox_names_the_file_as_dir_was_given(tmp_path: Path) -> None:
    _write_settings(tmp_path, "{}")

    assert folder_sandbox(str(tmp_path)) == str(tmp_path / "sandbox.json")


def test_folder_sandbox_keeps_a_relative_dir_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_settings(tmp_path, "{}")
    monkeypatch.chdir(tmp_path.parent)

    assert folder_sandbox(tmp_path.name) == f"{tmp_path.name}/sandbox.json"


def test_folder_sandbox_without_a_file(tmp_path: Path) -> None:
    assert folder_sandbox(str(tmp_path)) is None


def test_folder_sandbox_ignores_a_directory_of_that_name(tmp_path: Path) -> None:
    (tmp_path / "sandbox.json").mkdir()

    assert folder_sandbox(str(tmp_path)) is None
