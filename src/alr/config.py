"""The user config file, ~/.local/alr/config.yaml: parse and validate it."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from alr.errors import INVALID_CONFIG, RefusedError, quote

CONFIG_FILE = "config.yaml"

_MAX_MAJOR_VERSION = 1
_SANDBOX_MODES = ("tight", "large", "yolo", "custom")
_CUSTOM = "custom"

_TOP_KEYS = ("version", "defaults")
_DEFAULTS_KEYS = ("sandbox", "prompts")
_SANDBOX_KEYS = ("permissiveness",)
_PERMISSIVENESS_KEYS = ("default", "sandbox_file")
_PROMPTS_KEYS = ("task_review", "task_remediation")
_TASK_REVIEW_KEYS = ("prompt", "max_task_review_rounds")
_TASK_REMEDIATION_KEYS = ("prompt",)


@dataclass(frozen=True)
class SandboxDefault:
    """The sandbox mode a run uses when no flag and no run folder sandbox.json choose one."""

    mode: str
    # An absolute path; empty unless the mode is custom.
    sandbox_file: str


@dataclass(frozen=True)
class Config:
    """What config.yaml holds; every path is absolute."""

    sandbox: SandboxDefault | None
    task_review_prompt: str
    task_review_max_rounds: int
    task_remediation_prompt: str


def config_dir(home: Path | None = None) -> Path:
    """Name the one folder alr keeps its config in, so every caller agrees where it is."""
    return (home or Path.home()) / ".local" / "alr"


def parse(text: str, folder: Path) -> Config:
    """Turn config.yaml's text into a Config, refusing anything alr does not understand.

    A typo caught here stops a run at startup, not hours later when a review
    first reads the setting.
    """
    path = folder / CONFIG_FILE
    top = _load_mapping(text, path)
    _refuse_unknown_keys(top, _TOP_KEYS, path, "the file")
    _check_version(top, path)

    defaults = _section(top, "defaults", path, _DEFAULTS_KEYS, "the file")
    sandbox = _read_sandbox(defaults, folder, path)
    prompts = _section(defaults, "prompts", path, _PROMPTS_KEYS, "defaults")
    review = _section(prompts, "task_review", path, _TASK_REVIEW_KEYS, "prompts")
    remediation = _section(
        prompts, "task_remediation", path, _TASK_REMEDIATION_KEYS, "prompts"
    )

    return Config(
        sandbox=sandbox,
        task_review_prompt=_prompt_path(review, "task_review", folder, path),
        task_review_max_rounds=_read_rounds(review, path),
        task_remediation_prompt=_prompt_path(
            remediation, "task_remediation", folder, path
        ),
    )


def _refuse(path: Path, problem: str) -> RefusedError:
    """Build the refusal every problem uses, naming the file."""
    return RefusedError(INVALID_CONFIG, f"{path}: {problem}")


def _load_mapping(text: str, path: Path) -> dict[str, Any]:
    """Read the YAML, which must be a mapping at the top."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise _refuse(path, f"not valid YAML: {error}") from error

    if not isinstance(loaded, dict):
        raise _refuse(path, "the file must be a mapping of keys to values")

    return loaded


def _refuse_unknown_keys(
    mapping: dict[str, Any], known: tuple[str, ...], path: Path, where: str
) -> None:
    """Refuse a key alr does not know, so a typo cannot pass silently."""
    for key in mapping:
        if key not in known:
            raise _refuse(path, f"unknown key {quote(str(key))} in {where}")


def _section(
    parent: dict[str, Any],
    name: str,
    path: Path,
    known: tuple[str, ...],
    where: str,
) -> dict[str, Any]:
    """Return a required child mapping after checking its keys."""
    if name not in parent:
        raise _refuse(path, f"{name} is missing from {where}")

    child = parent[name]
    if not isinstance(child, dict):
        raise _refuse(path, f"{name} must be a mapping of keys to values")

    _refuse_unknown_keys(child, known, path, name)
    return child


def _required(mapping: dict[str, Any], name: str, path: Path, where: str) -> Any:
    """Return a required value."""
    if name not in mapping:
        raise _refuse(path, f"{name} is missing from {where}")

    return mapping[name]


def _check_version(top: dict[str, Any], path: Path) -> None:
    """Refuse a format newer than this alr understands."""
    version = _required(top, "version", path, "the file")
    if not isinstance(version, str):
        raise _refuse(path, "version must be text such as 1.0.0")

    major_text = version.split(".")[0]
    try:
        major = int(major_text)
    except ValueError as error:
        raise _refuse(path, f"version {quote(version)} must look like 1.0.0") from error

    if major > _MAX_MAJOR_VERSION:
        raise _refuse(
            path,
            f"version {quote(version)} is newer than this alr understands "
            f"(major version {_MAX_MAJOR_VERSION} at most)",
        )


def _read_sandbox(
    defaults: dict[str, Any], folder: Path, path: Path
) -> SandboxDefault | None:
    """Read the optional sandbox section; without it no mode is chosen."""
    if "sandbox" not in defaults:
        return None

    sandbox = _section(defaults, "sandbox", path, _SANDBOX_KEYS, "defaults")
    permissiveness = _section(
        sandbox, "permissiveness", path, _PERMISSIVENESS_KEYS, "sandbox"
    )
    mode = _required(permissiveness, "default", path, "permissiveness")
    if mode not in _SANDBOX_MODES:
        raise _refuse(
            path,
            f"default {quote(str(mode))} must be one of {', '.join(_SANDBOX_MODES)}",
        )

    return SandboxDefault(mode, _sandbox_file(permissiveness, mode, folder, path))


def _sandbox_file(
    permissiveness: dict[str, Any], mode: str, folder: Path, path: Path
) -> str:
    """Return the custom sandbox file's absolute path, or empty for the other modes."""
    given = "sandbox_file" in permissiveness
    if mode != _CUSTOM:
        if given:
            raise _refuse(path, f"sandbox_file only goes with default: {_CUSTOM}")

        return ""

    if not given:
        raise _refuse(path, f"default: {_CUSTOM} needs a sandbox_file")

    return str(_absolute(permissiveness["sandbox_file"], "sandbox_file", folder, path))


def _prompt_path(section: dict[str, Any], name: str, folder: Path, path: Path) -> str:
    """Return a prompt file's absolute path, refusing one that is not there."""
    prompt = _required(section, "prompt", path, name)
    prompt_path = _absolute(prompt, f"{name} prompt", folder, path)
    if not prompt_path.is_file():
        raise _refuse(
            path, f"{name} prompt {quote(str(prompt))} is not a file: {prompt_path}"
        )

    return str(prompt_path)


def _absolute(value: Any, name: str, folder: Path, path: Path) -> Path:
    """Resolve a path from the file against the config folder."""
    if not isinstance(value, str) or not value:
        raise _refuse(path, f"{name} must be a path")

    return (folder / value).absolute()


def _read_rounds(review: dict[str, Any], path: Path) -> int:
    """Read the review cap, a whole number of at least 1."""
    rounds = _required(review, "max_task_review_rounds", path, "task_review")
    # bool is an int in Python, and `true` is not a number of rounds.
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise _refuse(
            path,
            f"max_task_review_rounds must be a whole number of at least 1, got {rounds!r}",
        )

    return rounds
