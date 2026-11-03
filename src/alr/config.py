"""The user config folder, ~/.local/alr: create it on first run, then parse and validate config.yaml."""

import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

import yaml

from alr.errors import CONFIG_UNWRITABLE, INVALID_CONFIG, RefusedError, quote

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


def print_to_stderr(message: str) -> None:
    """Say what alr is about to do on stderr, so stdout stays the run's own output."""
    print(message, file=sys.stderr)


def load(
    home: Path | None = None,
    *,
    create: bool,
    say: Callable[[str], None] = print_to_stderr,
) -> Config:
    """Read the config, creating the folder with its default files on first run.

    With create=False (a dry run never writes, requirement 9) a missing folder
    is not created; the copy shipped in the package is read instead.
    """
    folder = config_dir(home)
    _refuse_foreign(folder)
    if folder.exists():
        return _read_folder(folder)

    if not create:
        return _read_packaged()

    _create_folder(folder, say)
    return _read_folder(folder)


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


def _refuse_foreign(folder: Path) -> None:
    """Never touch what is not a plain folder: the user put a link or file there on purpose."""
    if folder.is_symlink():
        raise RefusedError(INVALID_CONFIG, f"{folder} is a symlink; remove it yourself")

    if folder.exists() and not folder.is_dir():
        raise RefusedError(INVALID_CONFIG, f"{folder} is a file; remove it yourself")


def _read_packaged() -> Config:
    """Read the config shipped in the package, for a run that must not write."""
    with as_file(files("alr") / "defaults") as source:
        return _read_folder(source)


def _read_folder(folder: Path) -> Config:
    """Read the config.yaml inside a folder, which must already hold one."""
    path = folder / CONFIG_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise _refuse(
            path,
            "the file is missing; restore it, or move the folder away "
            "so alr can create it again",
        ) from error
    except (OSError, UnicodeDecodeError) as error:
        raise _refuse(path, f"cannot be read: {error}") from error

    return parse(text, folder)


def _create_folder(folder: Path, say: Callable[[str], None]) -> None:
    """Say what is about to be created, then create the folder with the default files."""
    with as_file(files("alr") / "defaults") as source:
        names = _default_names(source)
        say(f"alr: first run: creating {folder} with {', '.join(names)}")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for name in names:
                _copy_if_absent(source / name, folder / name)
        except OSError as error:
            raise RefusedError(
                CONFIG_UNWRITABLE,
                f"cannot create {folder}: {error.strerror or error}",
            ) from error


def _default_names(source: Path) -> list[str]:
    """List the shipped files relative to their folder, config.yaml last.

    Copying config.yaml last means a folder holding one never lacks the prompt
    files it points at.
    """
    found = (path for path in source.rglob("*") if path.is_file())
    names = sorted(path.relative_to(source).as_posix() for path in found)
    names.remove(CONFIG_FILE)
    names.append(CONFIG_FILE)
    return names


def _copy_if_absent(source: Path, target: Path) -> None:
    """Copy a file into place unless one is already there.

    The copy goes through a temporary file beside the target, so a reader never
    sees half a file, and the target is checked again just before the move, so
    a second alr starting at the same time loses nothing.
    """
    target.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".create-") as staging:
        staged = Path(staging) / target.name
        shutil.copyfile(source, staged)
        if target.exists():
            return

        os.replace(staged, target)


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
