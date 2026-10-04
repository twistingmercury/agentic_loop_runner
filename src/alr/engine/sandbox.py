"""The Claude flags that set what a session may do."""

import json
from pathlib import Path

from alr.errors import SandboxError, quote

_FLAG = "--sandbox-settings"


def bypass_args() -> list[str]:
    """Return the flags for a session with no sandbox and no permission checks."""
    return ["--dangerously-skip-permissions"]


def sandbox_args(path: str) -> list[str]:
    """Return the flags for a sandboxed session, from the Claude Code settings file at path.

    The settings travel inline, so there is no temporary file to clean up, and
    the file at path is never written.
    """
    settings = _force_sandbox(_read_settings(path))
    return ["--permission-mode", "acceptEdits", "--settings", settings]


def _read_settings(path: str) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise SandboxError(
            f"{_FLAG}: file {quote(path)} is not accessible: {exc.strerror}"
        ) from exc

    # Bytes that are not UTF-8 become U+FFFD instead of failing the read.
    return data.decode("utf-8", errors="replace")


def _force_sandbox(text: str) -> str:
    """Return the settings with the three sandbox keys a file must not weaken set.

    They turn the sandbox on, leave the session no way to ask its way out, and
    stop it carrying on unsandboxed when the sandbox cannot start.
    """
    try:
        settings = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        # Settings nested deeply enough overflow Python's parser; Go's reports
        # them as invalid JSON.
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
    return json.dumps(settings, separators=(",", ":"), ensure_ascii=False)


def _reject_constant(name: str) -> None:
    """Refuse NaN and Infinity, which Python's json accepts and Go's does not."""
    raise ValueError(f"invalid JSON constant {name}")
