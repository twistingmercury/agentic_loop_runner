"""The Claude flags that set what a session may do."""

import json
from pathlib import Path

from alr.errors import SandboxError, quote

_FLAG = "--sandbox-settings"


def bypass_args() -> list[str]:
    """Return the flags for a session with no sandbox and no permission checks."""
    return ["--dangerously-skip-permissions"]


def folder_sandbox(dir: str) -> str | None:
    """Return the run folder's sandbox.json as dir was given, or None when it has none.

    A directory of that name does not count.
    """
    path = Path(dir) / "sandbox.json"
    return str(path) if path.is_file() else None


def sandbox_args(path: str) -> list[str]:
    """Return the flags for a sandboxed session, from the Claude Code settings file at path.

    The settings travel inline, so there is no temporary file to clean up, and
    the file at path is never written.
    """
    settings = _force_sandbox(_read_settings(path))
    return ["--permission-mode", "acceptEdits", "--settings", settings]


def _read_settings(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SandboxError(
            f"{_FLAG}: file {quote(path)} is not accessible: {exc.strerror}"
        ) from exc


def _force_sandbox(text: str) -> str:
    """Return the settings with the three sandbox keys a file must not weaken set.

    They turn the sandbox on, leave the session no way to ask its way out, and
    stop it carrying on unsandboxed when the sandbox cannot start.
    """
    try:
        settings = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        # Settings nested deeply enough overflow the parser.
        raise SandboxError(f"{_FLAG}: must be a JSON object: {exc}") from exc

    if not isinstance(settings, dict):
        raise SandboxError(f"{_FLAG}: must be a JSON object")

    sandbox = settings.get("sandbox", {})
    if not isinstance(sandbox, dict):
        raise SandboxError(f"{_FLAG}: sandbox: must be a JSON object")

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
