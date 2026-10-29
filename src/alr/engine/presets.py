"""The sandbox presets: Tight and Large, turned into a Claude Code settings file."""

import json
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from alr.errors import SANDBOX_FILE_UNWRITABLE, RefusedError

CREDENTIAL_PATHS = (
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config/gh",
    "~/.netrc",
    "~/.claude",
)
WEB_DOMAINS = (
    "github.com",
    "*.githubusercontent.com",
    "crates.io",
    "*.crates.io",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "proxy.golang.org",
    "sum.golang.org",
    "storage.googleapis.com",
)


class Preset(StrEnum):
    """A named answer to the permissions question."""

    TIGHT = "tight"
    LARGE = "large"


@dataclass(frozen=True)
class Switches:
    """The three yes/no adjustments to a preset."""

    web: bool
    docker: bool
    hide_credentials: bool


def switches_for(preset: Preset) -> Switches:
    """Return the switch positions a preset stands for."""
    large = preset is Preset.LARGE
    return Switches(web=large, docker=large, hide_credentials=True)


def render(switches: Switches) -> str:
    """Return the settings document for switches; a switch that is off leaves its key out."""
    sandbox: dict[str, Any] = {
        "enabled": True,
        "allowUnsandboxedCommands": False,
        "failIfUnavailable": True,
    }
    if switches.web:
        sandbox["network"] = {"allowedDomains": list(WEB_DOMAINS)}

    if switches.docker:
        sandbox["excludedCommands"] = ["docker *"]

    if switches.hide_credentials:
        sandbox["filesystem"] = {"denyRead": list(CREDENTIAL_PATHS)}

    return json.dumps({"sandbox": sandbox}, indent=2, sort_keys=True) + "\n"


def preset_path(tasks_file: str) -> str:
    """Return the sandbox.json path beside the task file."""
    return str(Path(tasks_file).parent / "sandbox.json")


def write_preset(path: str, switches: Switches) -> bytes | None:
    """Write the preset to path atomically; return the file it replaced, or None when there was none."""
    target = Path(path)
    try:
        previous = _read_previous(target)
        _replace_atomically(target, render(switches).encode("utf-8"))
    except OSError as exc:
        raise RefusedError(SANDBOX_FILE_UNWRITABLE, f"{path}: {exc.strerror}") from exc

    return previous


def restore_previous(path: str, previous: bytes | None) -> None:
    """Put back what write_preset replaced: the old bytes, or no file when there were none.

    The old bytes go back atomically, so a restore that fails leaves the
    current file whole instead of truncated.
    """
    target = Path(path)
    try:
        if previous is None:
            target.unlink(missing_ok=True)
            return

        _replace_atomically(target, previous)
    except OSError as exc:
        raise RefusedError(SANDBOX_FILE_UNWRITABLE, f"{path}: {exc.strerror}") from exc


def restore_after_refusal(
    path: str, previous: bytes | None, refusal: RefusedError
) -> None:
    """Restore the preset for a refused run; when that fails too, raise the original refusal.

    The user needs the reason the run was refused, so the failed restore
    rides along as its cause instead of replacing it.
    """
    try:
        restore_previous(path, previous)
    except RefusedError as restore_failure:
        raise refusal from restore_failure


def _read_previous(target: Path) -> bytes | None:
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return None


def _replace_atomically(target: Path, data: bytes) -> None:
    # The temporary file sits beside the target so the rename stays on one filesystem.
    handle = tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".sandbox-", suffix=".json", delete=False
    )
    temp = Path(handle.name)
    try:
        # A write failure can surface only when the handle closes and flushes.
        with handle:
            handle.write(data)
        temp.replace(target)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
