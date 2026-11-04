"""The Claude flags that set what a session may do."""

import json
from pathlib import Path

from alr.errors import INVALID_SANDBOX_SETTINGS, RefusedError

_SANDBOX_FLAG = "--sandbox"
_SETTINGS_FLAG = "--sandbox-settings"


def bypass_args() -> list[str]:
    """Return the flags for a session with no sandbox and no permission checks."""
    return ["--dangerously-skip-permissions"]


def sandbox_args(path: str, name: str = "") -> list[str]:
    """Return the flags for a sandboxed session, from the Claude Code settings file at path.

    name is the sandbox the user picked by name, empty for a file given with
    --sandbox-settings; a refusal names the flag the user used and the file.
    The settings travel inline, so there is no temporary file to clean up, and
    the file at path is never written.
    """
    source = _source(path, name)
    return _args_from(_read_settings(path, source), source)


def settings_args(text: str, path: str, name: str = "") -> list[str]:
    """Return the flags for a sandboxed session, from Claude Code settings held in text.

    path and name say where the text belongs, for the refusal.
    """
    return _args_from(text, _source(path, name))


def _source(path: str, name: str) -> str:
    """Name the flag and the file a refusal is about: "--sandbox five: <path>" or "--sandbox-settings <path>".

    A file given with --sandbox-settings is named once, by the path the user
    typed.
    """
    if name:
        return f"{_SANDBOX_FLAG} {name}: {path}"

    return f"{_SETTINGS_FLAG} {path}"


def _args_from(text: str, source: str) -> list[str]:
    settings = _force_sandbox(text, source)
    return ["--permission-mode", "acceptEdits", "--settings", settings]


def _read_settings(path: str, source: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RefusedError(
            INVALID_SANDBOX_SETTINGS,
            f"{source}: is not accessible: {exc.strerror}",
        ) from exc


def _force_sandbox(text: str, source: str) -> str:
    """Return the settings with the three sandbox keys a file must not weaken set.

    They turn the sandbox on, leave the session no way to ask its way out, and
    stop it carrying on unsandboxed when the sandbox cannot start.
    """
    try:
        settings = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        # Settings nested deeply enough overflow the parser.
        raise RefusedError(
            INVALID_SANDBOX_SETTINGS, f"{source}: must be a JSON object: {exc}"
        ) from exc

    if not isinstance(settings, dict):
        raise RefusedError(INVALID_SANDBOX_SETTINGS, f"{source}: must be a JSON object")

    sandbox = settings.get("sandbox", {})
    if not isinstance(sandbox, dict):
        raise RefusedError(
            INVALID_SANDBOX_SETTINGS, f"{source}: sandbox: must be a JSON object"
        )

    sandbox["enabled"] = True
    sandbox["allowUnsandboxedCommands"] = False
    sandbox["failIfUnavailable"] = True
    settings["sandbox"] = sandbox
    # ASCII only, so the argument can be encoded whatever the file holds, a
    # lone surrogate included.
    return json.dumps(settings, separators=(",", ":"))


def _reject_constant(name: str) -> None:
    """Refuse NaN and Infinity: the settings are re-serialized for Claude, and they would come out as invalid JSON."""
    raise ValueError(f"invalid JSON constant {name}")
