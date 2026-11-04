"""The sandbox presets: Tight and Large, turned into a Claude Code settings file."""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

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
# Excluded commands run outside the sandbox but still go through the
# permission flow, so each pattern is also allowed in the permissions block.
DOCKER_COMMANDS = ("docker *", "./build/build.sh", "./build/build.sh *")


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

    document: dict[str, Any] = {"sandbox": sandbox}
    if switches.docker:
        sandbox["excludedCommands"] = list(DOCKER_COMMANDS)
        document["permissions"] = {
            "allow": [f"Bash({command})" for command in DOCKER_COMMANDS]
        }

    if switches.hide_credentials:
        sandbox["filesystem"] = {"denyRead": list(CREDENTIAL_PATHS)}

    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def preset_path(tasks_file: str) -> str:
    """Return the sandbox.json path beside the task file, which names a preset in the view; nothing writes it."""
    return str(Path(tasks_file).parent / "sandbox.json")
