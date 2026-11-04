"""The sandboxes folder, ~/.local/alr/sandboxes: add the shipped files, list the sandboxes, find one by name."""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from alr.config import config_dir
from alr.errors import (
    CONFIG_UNWRITABLE,
    INVALID_CONFIG,
    RefusedError,
    UsageError,
)

SANDBOXES = "sandboxes"

# The names a user can type as a flag value. A leading dot never matches, so
# dotfiles (and the temporary files add_missing writes) are not listed. yolo is
# left out so it keeps meaning the bypass.
_NAME = re.compile(r"[A-Za-z0-9_-]+")
_BYPASS = "yolo"
_SUFFIX = ".json"

_NOT_VALID_JSON = "not valid JSON"
_CANNOT_BE_READ = "cannot be read"


@dataclass(frozen=True)
class Sandbox:
    """A sandbox file the user can pick."""

    name: str
    path: Path
    # Empty when the file reads as a JSON object, else why it cannot be used.
    problem: str


def sandboxes_dir(home: Path | None = None) -> Path:
    """Name the folder the sandbox files live in, so every caller agrees where it is."""
    return config_dir(home) / SANDBOXES


def add_missing(home: Path | None = None) -> list[Path]:
    """Copy each shipped sandbox that is not in the folder yet, and return the files added.

    The folder is made when it is missing. A file already there, edited or
    not, is never touched: the user's edits must survive every run.
    """
    folder = sandboxes_dir(home)
    try:
        _refuse_unless_folder(folder)
        folder.mkdir(exist_ok=True)
        return _copy_missing(folder)
    except OSError as error:
        raise RefusedError(
            CONFIG_UNWRITABLE,
            f"cannot add the sandboxes to {folder}: {error.strerror or error}",
        ) from error


def list_sandboxes(home: Path | None = None) -> list[Sandbox]:
    """List the sandboxes in the folder, sorted by name, each marked with its problem.

    A broken file is still listed so the user sees why it cannot be used.
    """
    folder = sandboxes_dir(home)
    try:
        entries = list(folder.iterdir())
    except FileNotFoundError:
        return []

    found = [_sandbox_from(entry) for entry in entries if _is_listed(entry)]
    return sorted(found, key=_name_of)


def find(name: str, home: Path | None = None, dry_run: bool = False) -> Path:
    """Return the file of the sandbox called name, refusing a name that is not one.

    A dry run writes nothing, so a shipped file that is not in the folder yet
    is found anyway: a real run would add it, and the dry run must give the
    same answer.
    """
    path = sandboxes_dir(home) / f"{name}{_SUFFIX}"
    names = _names(home, dry_run)
    if name not in names:
        raise UsageError(
            f"--sandbox: no sandbox named {name}; the sandboxes are: {_join(names)}"
        )

    return path


def read_text(path: Path) -> str:
    """Return the text of a sandbox file, or of the package copy when it is a missing shipped file.

    Undecodable bytes are replaced, as sandbox_args does when a run reads the file.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        if path.name not in _shipped_names():
            raise

        return _shipped_text(path.name)


def names_text(home: Path | None = None) -> str:
    """Return the sandbox names as "a, b", or "(none)", for messages."""
    return _join(_names(home, dry_run=False))


def _names(home: Path | None, dry_run: bool) -> list[str]:
    """List the names a run could pick, adding the shipped ones a real run would add."""
    names = [sandbox.name for sandbox in list_sandboxes(home)]
    if not dry_run:
        return names

    folder = sandboxes_dir(home)
    for shipped in _shipped_names():
        if not _exists(folder / shipped):
            names.append(shipped.removesuffix(_SUFFIX))

    return sorted(set(names))


def _join(names: list[str]) -> str:
    return ", ".join(names) if names else "(none)"


def _name_of(sandbox: Sandbox) -> str:
    return sandbox.name


def _refuse_unless_folder(folder: Path) -> None:
    """Refuse a sandboxes path that is something other than a folder; the user put it there."""
    if _exists(folder) and not folder.is_dir():
        raise RefusedError(
            INVALID_CONFIG, f"{folder} is not a folder; remove it yourself"
        )


def _exists(path: Path) -> bool:
    """Say whether anything is at path, a dangling link included, so nothing is ever replaced."""
    return path.exists() or path.is_symlink()


def _shipped_names() -> list[str]:
    """List the file names alr ships, sorted."""
    shipped = files("alr") / "defaults" / SANDBOXES
    names = [item.name for item in shipped.iterdir() if item.name.endswith(_SUFFIX)]
    return sorted(names)


def _shipped_text(file_name: str) -> str:
    shipped = files("alr") / "defaults" / SANDBOXES / file_name
    return shipped.read_text(encoding="utf-8")


def _copy_missing(folder: Path) -> list[Path]:
    """Copy each shipped file the folder lacks and return the paths copied."""
    added: list[Path] = []
    with as_file(files("alr") / "defaults" / SANDBOXES) as source:
        for file_name in _shipped_names():
            target = folder / file_name
            if _exists(target):
                continue

            if _link_copy(source / file_name, target):
                added.append(target)

    return added


def _link_copy(source: Path, target: Path) -> bool:
    """Copy source to target without ever replacing a file, and say whether this call made it.

    The copy is written to a temporary file in the same folder and then
    hard-linked into place. A link fails when target exists, so a file another
    alr (or the user) created in the meantime wins and is left alone, and a
    killed run leaves no half-written sandbox, only a dot-named temporary file
    that listing skips and the context manager removes.
    """
    content = source.read_bytes()
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".alr-add-", delete_on_close=False
    ) as staged:
        staged.write(content)
        staged.close()
        try:
            os.link(staged.name, target)
        except FileExistsError:
            return False

    return True


def _is_listed(entry: Path) -> bool:
    """Say whether a folder entry is a sandbox the user can pick."""
    if entry.suffix != _SUFFIX:
        return False

    name = entry.stem
    if name == _BYPASS or not _NAME.fullmatch(name):
        return False

    return entry.is_file()


def _sandbox_from(entry: Path) -> Sandbox:
    return Sandbox(entry.stem, entry, _problem_with(entry))


def _problem_with(path: Path) -> str:
    """Return why a sandbox file cannot be used, or an empty string when it can."""
    try:
        text = read_text(path)
    except OSError:
        return _CANNOT_BE_READ

    try:
        settings = json.loads(text)
    except (ValueError, RecursionError):
        # Settings nested deeply enough overflow the parser.
        return _NOT_VALID_JSON

    if not isinstance(settings, dict):
        return _NOT_VALID_JSON

    return ""
