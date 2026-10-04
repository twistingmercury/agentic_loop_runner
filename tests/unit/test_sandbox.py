import errno
import json
import os
from pathlib import Path

import pytest

from alr.engine.sandbox import bypass_args, sandbox_args
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
    ],
)
def test_sandbox_args_rejects_bad_files(
    tmp_path: Path, content: str, want_error: str
) -> None:
    with pytest.raises(SandboxError) as excinfo:
        sandbox_args(str(_write_settings(tmp_path, content)))

    assert want_error in str(excinfo.value)


def test_sandbox_args_unreadable_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    reasons = {
        str(missing): os.strerror(errno.ENOENT),
        str(tmp_path): os.strerror(errno.EISDIR),
    }

    for path, reason in reasons.items():
        with pytest.raises(SandboxError) as excinfo:
            sandbox_args(path)

        assert str(excinfo.value) == (
            f'--sandbox-settings: file "{path}" is not accessible: {reason}'
        )


# gralph rejects what Go's encoding/json rejects; Python's json module is
# looser unless told otherwise.
@pytest.mark.parametrize(
    "content",
    [
        pytest.param('{"sandbox":{"x":NaN}}', id="NaN"),
        pytest.param('﻿{"sandbox":{}}', id="byte order mark"),
        pytest.param("[" * 100_000, id="deeply nested"),
    ],
)
def test_sandbox_args_rejects_what_go_json_rejects(
    tmp_path: Path, content: str
) -> None:
    with pytest.raises(SandboxError) as excinfo:
        sandbox_args(str(_write_settings(tmp_path, content)))

    assert str(excinfo.value).startswith("--sandbox-settings: must be a JSON object: ")


def test_sandbox_args_keeps_non_ascii_text(tmp_path: Path) -> None:
    path = _write_settings(tmp_path, '{"sandbox":{"note":"héllo"}}')

    settings = sandbox_args(str(path))[3]

    assert "héllo" in settings
    assert json.loads(settings) == {"sandbox": {"note": "héllo", **FORCED}}
