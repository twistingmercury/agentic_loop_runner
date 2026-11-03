"""The user config folder, ~/.local/alr: create it once the user agrees, then parse and validate config.yaml."""

import errno
import shutil
import tempfile
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
    if home is None:
        home = _find_home()

    return home / ".local" / "alr"


def _find_home() -> Path:
    """Find the home folder, refusing when the system cannot say where it is.

    Path.home() raises RuntimeError when HOME is unset and the user has no
    passwd entry, as in a container run with an unnamed user id.
    """
    try:
        return Path.home()
    except RuntimeError as error:
        raise RefusedError(
            CONFIG_UNWRITABLE, f"cannot find the home folder: {error}"
        ) from error


def config_folder_exists(home: Path | None = None) -> bool:
    """Say whether the config folder is there, refusing what is not a plain folder."""
    return _folder_exists(config_dir(home))


def default_file_names() -> list[str]:
    """List the files create_folder writes, relative to the folder, so the user can be told first."""
    with as_file(files("alr") / "defaults") as source:
        return _default_names(source)


def load(home: Path | None = None) -> Config:
    """Read the config from the config folder.

    This never writes. A real run asks before creating the folder (see
    create_folder) and a dry run writes nothing (requirement 9), so when the
    folder is missing the copy shipped in the package is read instead.
    """
    folder = config_dir(home)
    if _folder_exists(folder):
        return _read_folder(folder)

    return _read_packaged()


def create_folder(home: Path | None = None) -> None:
    """Create the config folder with its default files.

    The caller has already told the user what is created and been told to go
    ahead. The folder appears all at once or not at all, so a killed run or a
    second alr starting at the same moment never sees it without its
    config.yaml.
    """
    folder = config_dir(home)
    with as_file(files("alr") / "defaults") as source:
        names = _default_names(source)
        try:
            folder.parent.mkdir(parents=True, exist_ok=True)
            _create_all_at_once(source, names, folder)
        except OSError as error:
            raise RefusedError(
                CONFIG_UNWRITABLE,
                f"cannot create {folder}: {error.strerror or error}",
            ) from error


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


def _folder_exists(folder: Path) -> bool:
    """Say whether the config folder is there, refusing what is not a plain folder.

    Checking a path whose parent cannot be searched raises PermissionError,
    which must end as a refusal, not a traceback.
    """
    try:
        _refuse_foreign(folder)
        return folder.exists()
    except OSError as error:
        raise RefusedError(
            CONFIG_UNWRITABLE, f"cannot check {folder}: {error.strerror or error}"
        ) from error


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


def _create_all_at_once(source: Path, names: list[str], folder: Path) -> None:
    """Build the folder in a staging folder beside it, then rename it into place.

    The staging folder shares the target's parent so the rename stays on one
    filesystem, which is what makes it a single step. It is removed whatever
    happens, a KeyboardInterrupt included.
    """
    staging = Path(tempfile.mkdtemp(dir=folder.parent, prefix=".alr-create-"))
    try:
        _copy_defaults(source, names, staging)
        _rename_unless_taken(staging, folder)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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


def _copy_defaults(source: Path, names: list[str], staging: Path) -> None:
    """Copy every shipped file into the staging folder, making its subfolders."""
    for name in names:
        target = staging / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source / name, target)


def _rename_unless_taken(staging: Path, folder: Path) -> None:
    """Rename the staging folder to the target, accepting that another alr got there first.

    The winner's folder is whole (it was renamed in the same way), so the
    loser discards its copy and reads the winner's, which may hold edits.
    """
    try:
        staging.rename(folder)
    except OSError as error:
        if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise


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
    try:
        is_file = prompt_path.is_file()
    except OSError as error:
        raise _refuse(
            path, f"{name} prompt {prompt_path} cannot be checked: {error}"
        ) from error

    if not is_file:
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
