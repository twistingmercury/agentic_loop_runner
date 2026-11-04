"""The sandboxes folder, ~/.local/alr/sandboxes: add the shipped files, list the sandboxes, find one by name."""

import errno
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
from alr.engine.sandbox import settings_problem

SANDBOXES = "sandboxes"

# The names a user can type as a flag value. A leading dot never matches, so
# dotfiles (and the temporary files add_missing writes) are not listed. yolo is
# left out so it keeps meaning the bypass.
_NAME = re.compile(r"[A-Za-z0-9_-]+")
_BYPASS = "yolo"
_SUFFIX = ".json"

_CANNOT_BE_READ = "cannot be read"


@dataclass(frozen=True)
class Sandbox:
    """A sandbox file the user can pick."""

    name: str
    path: Path
    # Empty when a run can use the file, else why it cannot.
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
        raise _cannot_add(folder, _reason(error)) from error


def check_can_add_missing(home: Path | None = None) -> None:
    """Refuse what add_missing would refuse, without writing anything.

    A dry run calls this where a real run calls add_missing, so both give the
    same verdict. A real run only writes when a shipped file is missing: into
    the folder, or into the config folder when the folder itself is missing.
    os.access says whether it could, without trying; it needs the search
    permission as well as the write permission to make an entry.
    """
    folder = sandboxes_dir(home)
    try:
        _refuse_unless_folder(folder)
        missing = _missing_shipped(folder)
        writes_into = folder if folder.exists() else folder.parent
    except OSError as error:
        raise _cannot_add(folder, _reason(error)) from error

    if not missing:
        return

    if os.access(writes_into, os.W_OK | os.X_OK):
        return

    # The text the OS gives a real run for the same refusal.
    raise _cannot_add(folder, os.strerror(errno.EACCES))


def list_sandboxes(home: Path | None = None) -> list[Sandbox]:
    """List the sandboxes in the folder, sorted by name, each marked with its problem.

    A broken file is still listed so the user sees why it cannot be used. A
    path that is not a folder is refused as invalid-config, and a folder alr
    cannot list as config-unwritable; a folder that is not there lists nothing.
    """
    return _list_in(sandboxes_dir(home))


def _list_in(folder: Path) -> list[Sandbox]:
    """List the sandboxes in a folder; list_sandboxes explains the order and the broken files.

    A path that is not a folder is refused as add_missing refuses it, and a
    folder alr cannot list is refused, so no caller meets an OSError.
    """
    # One handler for the listing and the check of each entry: a folder alr may
    # read but not search lists its names, then fails on the first entry.
    try:
        _refuse_unless_folder(folder)
        entries = list(folder.iterdir())
        found = [_sandbox_from(entry) for entry in entries if _is_listed(entry)]
    except FileNotFoundError:
        return []
    except OSError as error:
        raise RefusedError(
            CONFIG_UNWRITABLE,
            f"cannot list the sandboxes in {folder}: {_reason(error)}",
        ) from error

    return sorted(found, key=_name_of)


def find(name: str, home: Path | None = None, dry_run: bool = False) -> Path:
    """Return the file of the sandbox called name, refusing a name that is not one.

    A dry run writes nothing, so a shipped file that is not in the folder yet
    is found anyway: a real run would add it, and the dry run must give the
    same answer.
    """
    folder = sandboxes_dir(home)
    path = folder / f"{name}{_SUFFIX}"
    names = names_in(folder, dry_run)
    if name not in names:
        raise UsageError(
            f"--sandbox: no sandbox named {name}; "
            f"the sandboxes are: {join_names(names)}"
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
    return join_names(names_in(sandboxes_dir(home), dry_run=False))


def names_in(folder: Path, dry_run: bool) -> list[str]:
    """List the names a run could pick from a sandboxes folder.

    A dry run adds the shipped names a real run would add, so both give the
    same verdict. Config validation calls this with the folder it is parsing.
    """
    names = [sandbox.name for sandbox in _list_in(folder)]
    if not dry_run:
        return names

    for shipped in _missing_shipped(folder):
        names.append(shipped.removesuffix(_SUFFIX))

    return sorted(set(names))


def join_names(names: list[str]) -> str:
    """Join names as "a, b", or "(none)" when there are none, for messages."""
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


def _missing_shipped(folder: Path) -> list[str]:
    """List the shipped file names the folder lacks, so the real run, the dry run, and the check agree on them."""
    return [name for name in _shipped_names() if not _exists(folder / name)]


def _reason(error: OSError) -> str:
    """Say why the OS refused, in the OS's own words."""
    return error.strerror or str(error)


def _cannot_add(folder: Path, reason: str) -> RefusedError:
    """Build the refusal for sandboxes alr cannot add, which the real run and the dry run both give."""
    return RefusedError(
        CONFIG_UNWRITABLE, f"cannot add the sandboxes to {folder}: {reason}"
    )


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
        for file_name in _missing_shipped(folder):
            target = folder / file_name
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
    """Return why a sandbox file cannot be used, or an empty string when it can.

    The reading rules are the run's own, so a sandbox is marked broken exactly
    when a run would refuse it.
    """
    try:
        text = read_text(path)
    except OSError:
        return _CANNOT_BE_READ

    return settings_problem(text)
