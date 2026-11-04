"""The user config folder, ~/.local/alr: create it once the user agrees, then parse and validate config.yaml."""

import errno
import shutil
import tempfile
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from alr.errors import (
    CONFIG_SETUP_MISSING,
    CONFIG_UNWRITABLE,
    INVALID_CONFIG,
    RefusedError,
    quote,
)

CONFIG_FILE = "config.yaml"

_MAX_MAJOR_VERSION = 1
_YOLO = "yolo"
# The old way to name a sandbox file; see _refuse_removed_sandbox_keys.
_CUSTOM = "custom"
_SANDBOX_FILE = "sandbox_file"
# The same name as alr.sandboxes.SANDBOXES, which this module cannot import at the top.
_SANDBOXES = "sandboxes"

_TOP_KEYS = ("version", "defaults")
_DEFAULTS_KEYS = ("sandbox", "prompts")
_SANDBOX_KEYS = ("permissiveness",)
_PERMISSIVENESS_KEYS = ("default",)
_PROMPTS_KEYS = ("task_review", "task_remediation")
_TASK_REVIEW_KEYS = ("prompt", "max_task_review_rounds")
_TASK_REMEDIATION_KEYS = ("prompt",)


@dataclass(frozen=True)
class SandboxDefault:
    """The sandbox a run uses when no flag chooses one."""

    # "yolo", or the name of a file in the sandboxes folder.
    name: str


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


def paths_to_create(home: Path | None = None) -> list[Path]:
    """List everything create_folder makes, in the order it makes it, so the user can be told first.

    That is each missing parent of the config folder (only the ones that do
    not exist, so nothing already there is listed), the config folder, its
    subfolders, then the files. The subfolders come from the shipped file
    names, so a new shipped folder shows up here without a change.
    """
    folder = config_dir(home)
    names = default_file_names()

    missing_parents: list[Path] = []
    parent = folder.parent
    while not parent.exists():
        missing_parents.append(parent)
        parent = parent.parent

    subfolders = _subfolders(names)
    return [
        *reversed(missing_parents),
        folder,
        *(folder / subfolder for subfolder in subfolders),
        *(folder / name for name in names),
    ]


def _subfolders(names: list[str]) -> list[PurePosixPath]:
    """List the folders the shipped files sit in, outermost first, each once."""
    subfolders: dict[PurePosixPath, None] = {}
    for name in names:
        for parent in reversed(PurePosixPath(name).parents):
            # The last parent is "." (the config folder itself), which has no parts.
            if parent.parts:
                subfolders[parent] = None

    return list(subfolders)


def load(home: Path | None = None, dry_run: bool = False) -> Config:
    """Read the config from the config folder.

    This never writes. Callers check the folder exists first: a real run asks
    before creating it (see create_folder) and a dry run refuses without it.
    A folder that is gone anyway, or a file or link in its place, is refused.
    A dry run counts a shipped sandbox whose file is missing as there, since
    a real run would add it before it reads the config.
    """
    folder = config_dir(home)
    _refuse_unless_folder_exists(folder)
    return _read_folder(folder, dry_run)


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


def parse(text: str, folder: Path, dry_run: bool = False) -> Config:
    """Turn config.yaml's text into a Config, refusing anything alr does not understand.

    A typo caught here stops a run at startup, not hours later when a review
    first reads the setting. The sandbox default is checked against the
    sandboxes folder inside the config folder; dry_run is explained in load.
    """
    path = folder / CONFIG_FILE
    top = _load_mapping(text, path)
    _refuse_unknown_keys(top, _TOP_KEYS, path, "the file")
    _check_version(top, path)

    defaults = _section(top, "defaults", path, _DEFAULTS_KEYS, "the file")
    sandbox = _read_sandbox(defaults, folder, path, dry_run)
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


def _refuse_unless_folder_exists(folder: Path) -> None:
    """Refuse a missing config folder by name, so a missing config.yaml is never blamed for it.

    A file or link in the folder's place is refused by _folder_exists.
    """
    if _folder_exists(folder):
        return

    raise RefusedError(CONFIG_SETUP_MISSING, f"the config folder is missing ({folder})")


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


def _read_folder(folder: Path, dry_run: bool) -> Config:
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

    return parse(text, folder, dry_run)


def _create_all_at_once(source: Path, names: list[str], folder: Path) -> None:
    """Build the folder in a staging folder beside it, then rename it into place.

    The staging folder shares the target's parent so the rename stays on one
    filesystem, which is what makes it a single step. It is removed whatever
    happens, a KeyboardInterrupt included. tempfile.mkdtemp makes the staging
    folder mode 0700 and the rename keeps it, so the config folder is private
    to the user.
    """
    staging = Path(tempfile.mkdtemp(dir=folder.parent, prefix=".alr-create-"))
    try:
        _copy_defaults(source, names, staging)
        _rename_unless_taken(staging, folder)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _default_names(source: Path) -> list[str]:
    """List the shipped files relative to their folder, config.yaml last.

    The order only decides the order of the setup list the user sees. It does
    not protect a half-made folder: the atomic rename in _create_all_at_once
    guarantees the folder appears whole or not at all.
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
    loser just discards its copy and returns. It reads nothing: the caller
    exits 0 and tells the user to run alr again, which then finds the
    winner's folder, which may hold edits.
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
    defaults: dict[str, Any], folder: Path, path: Path, dry_run: bool
) -> SandboxDefault | None:
    """Read the optional sandbox section; without it no sandbox is chosen."""
    if "sandbox" not in defaults:
        return None

    sandbox = _section(defaults, "sandbox", path, _SANDBOX_KEYS, "defaults")
    permissiveness = _permissiveness(sandbox, path)
    # Before the unknown-key check, so an old config gets advice, not a bare refusal.
    _refuse_removed_keys(permissiveness, folder, path)
    _refuse_unknown_keys(permissiveness, _PERMISSIVENESS_KEYS, path, "permissiveness")
    name = _required(permissiveness, "default", path, "permissiveness")
    _refuse_unknown_sandbox(name, folder, path, dry_run)
    return SandboxDefault(name)


def _permissiveness(sandbox: dict[str, Any], path: Path) -> dict[str, Any]:
    """Return the permissiveness mapping, whose keys _read_sandbox checks itself."""
    if "permissiveness" not in sandbox:
        raise _refuse(path, "permissiveness is missing from sandbox")

    permissiveness = sandbox["permissiveness"]
    if not isinstance(permissiveness, dict):
        raise _refuse(path, "permissiveness must be a mapping of keys to values")

    return permissiveness


def _refuse_removed_keys(
    permissiveness: dict[str, Any], folder: Path, path: Path
) -> None:
    """Refuse the custom value and the sandbox_file key with advice on what to do instead.

    A custom sandbox file now lives in the sandboxes folder and is picked by
    name, so a config written for the old way says to move the file.
    """
    removed = []
    if _SANDBOX_FILE in permissiveness:
        removed.append(_SANDBOX_FILE)

    if permissiveness.get("default") == _CUSTOM:
        removed.append(_CUSTOM)

    for key in removed:
        raise _refuse(
            path,
            f"{key}: no longer supported; move the file into "
            f"{folder / _SANDBOXES} and use its name as default",
        )


def _refuse_unknown_sandbox(name: Any, folder: Path, path: Path, dry_run: bool) -> None:
    """Refuse a default that is neither yolo nor a sandbox in the sandboxes folder."""
    if name == _YOLO:
        return

    # Imported here because alr.sandboxes imports config_dir from this module.
    from alr.sandboxes import join_names, names_in

    names = names_in(folder / _SANDBOXES, dry_run)
    if name in names:
        return

    raise _refuse(
        path,
        f"default {name} is not a sandbox; the sandboxes are: {join_names(names)}",
    )


def _prompt_path(section: dict[str, Any], name: str, folder: Path, path: Path) -> str:
    """Return a prompt file's absolute path, refusing one that cannot be read."""
    prompt = _required(section, "prompt", path, name)
    key = f"defaults.prompts.{name}.prompt"
    prompt_path = _absolute(prompt, key, folder, path)
    _refuse_unreadable(prompt_path, key, path)
    return str(prompt_path)


def _refuse_unreadable(file_path: Path, key: str, path: Path) -> None:
    """Refuse a file the config names when alr cannot open it.

    Opening it, not just checking it exists, catches a file without read
    permission or a folder in its place, at startup rather than mid-run.
    """
    try:
        file_path.open("rb").close()
    except OSError as error:
        reason = error.strerror or str(error)
        raise _refuse(path, f"{key} {file_path} cannot be read: {reason}") from error


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
