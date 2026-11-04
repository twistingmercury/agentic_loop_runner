import json
from pathlib import Path

import pytest

from alr.engine.sandbox import sandbox_args, settings_args, settings_problem
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
    ("content", "problem"),
    [
        pytest.param("sandbox: true", "must be a JSON object: ", id="not JSON"),
        pytest.param("[]", "must be a JSON object", id="array"),
        pytest.param(
            '{"sandbox":null}', "sandbox: must be a JSON object", id="sandbox null"
        ),
        # The settings are re-serialized for Claude, so NaN would turn into invalid JSON.
        pytest.param(
            '{"sandbox":{"x":NaN}}',
            "must be a JSON object: invalid JSON constant NaN",
            id="NaN",
        ),
        pytest.param(
            '\ufeff{"sandbox":{}}', "must be a JSON object: ", id="byte order mark"
        ),
        pytest.param("[" * 100_000, "must be a JSON object: ", id="deeply nested"),
    ],
)
def test_sandbox_args_rejects_bad_files(
    tmp_path: Path, content: str, problem: str
) -> None:
    path = str(_write_settings(tmp_path, content))

    with pytest.raises(RefusedError) as excinfo:
        sandbox_args(path)

    assert excinfo.value.reason == "invalid-sandbox-settings"
    assert excinfo.value.message.startswith(f"--sandbox-settings {path}: {problem}")


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        pytest.param("nope", "must be a JSON object: Expecting value", id="not JSON"),
        pytest.param('{"sandbox": 5}', "sandbox: must be a JSON object", id="sandbox"),
        pytest.param(
            '{"x": NaN}', "must be a JSON object: invalid JSON constant NaN", id="NaN"
        ),
    ],
)
def test_a_bad_sandbox_picked_by_name_is_refused_naming_the_flag_and_the_file(
    tmp_path: Path, content: str, problem: str
) -> None:
    path = str(_write_settings(tmp_path, content))

    with pytest.raises(RefusedError) as excinfo:
        sandbox_args(path, "five")

    assert excinfo.value.reason == "invalid-sandbox-settings"
    assert excinfo.value.message.startswith(f"--sandbox five: {path}: {problem}")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        pytest.param("{}", "", id="empty object"),
        pytest.param('{"sandbox": {}}', "", id="sandbox object"),
        pytest.param("nope", "not valid JSON", id="not JSON"),
        pytest.param("[]", "not valid JSON", id="array"),
        pytest.param('{"x": NaN}', "not valid JSON", id="NaN"),
        pytest.param("[" * 100_000, "not valid JSON", id="deeply nested"),
        pytest.param('{"sandbox": 5}', "sandbox is not an object", id="sandbox 5"),
        pytest.param(
            '{"sandbox": null}', "sandbox is not an object", id="sandbox null"
        ),
    ],
)
def test_settings_problem_says_what_a_run_would_refuse(text: str, problem: str) -> None:
    assert settings_problem(text) == problem


def test_settings_args_refuses_text_naming_the_flag_and_the_file() -> None:
    with pytest.raises(RefusedError) as excinfo:
        settings_args("[]", "/s/five.json", "five")

    assert (
        excinfo.value.message == "--sandbox five: /s/five.json: must be a JSON object"
    )


def test_sandbox_args_unreadable_path(tmp_path: Path) -> None:
    path = str(tmp_path / "missing.json")

    with pytest.raises(RefusedError) as excinfo:
        sandbox_args(path)

    assert excinfo.value.reason == "invalid-sandbox-settings"
    assert excinfo.value.message == (
        f"--sandbox-settings {path}: is not accessible: No such file or directory"
    )


def test_a_named_sandbox_that_cannot_be_read_is_refused_naming_the_flag_and_the_file(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "missing.json")

    with pytest.raises(RefusedError) as excinfo:
        sandbox_args(path, "five")

    assert excinfo.value.message == (
        f"--sandbox five: {path}: is not accessible: No such file or directory"
    )


@pytest.mark.parametrize("text", ["\\ud800"], ids=["lone surrogate"])
def test_sandbox_args_settings_can_always_be_encoded(tmp_path: Path, text: str) -> None:
    # The argument goes to a child process, so it must be encodable whatever the file holds.
    path = _write_settings(tmp_path, '{"sandbox":{"note":"' + text + '"}}')

    settings = sandbox_args(str(path))[3]

    settings.encode("utf-8")
    assert json.loads(settings)["sandbox"]["note"] == json.loads(f'"{text}"')


def test_settings_args_equals_sandbox_args_for_the_same_text(tmp_path: Path) -> None:
    path = _write_settings(tmp_path, PASS_THROUGH_IN)

    assert sandbox_args(str(path)) == settings_args(PASS_THROUGH_IN, str(path))
