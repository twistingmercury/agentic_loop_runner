"""Keep the bundled skills in the user's skills folder: the startup sync."""

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path


@dataclass(frozen=True)
class SkillChange:
    """One skill the sync adds or replaces.

    `verb` is "Added" or "Updated". `replaced` is "symlink" or "file" when
    something other than a folder stood at the name, and "" otherwise.
    """

    path: Path
    verb: str
    replaced: str


@dataclass(frozen=True)
class SyncReport:
    """What a sync did: the skills it changed and the warnings it raised.

    Each warning is the text after `warning: `.
    """

    changes: list[SkillChange]
    warnings: list[str]


def skills_folder(home: Path | None = None) -> Path:
    """Where Claude Code looks for personal skills.

    `$CLAUDE_CONFIG_DIR/skills` when that variable is set and not empty, used
    exactly as given; otherwise `<home>/.claude/skills`. Path.home() raises
    RuntimeError when no home folder can be found.
    """
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if config_dir:
        return Path(config_dir) / "skills"

    return (home or Path.home()) / ".claude" / "skills"


def sync(home: Path | None = None) -> SyncReport:
    """Make each bundled skill in the skills folder match the bundled copy.

    Skills sync in sorted name order. A skill that cannot be synced becomes a
    warning and the rest go on, so this never raises: an unwritable skills
    folder must not stop a run.
    """
    try:
        folder = skills_folder(home)
    except RuntimeError as exc:
        return SyncReport(changes=[], warnings=[f"skills folder: {exc}"])

    changes: list[SkillChange] = []
    warnings: list[str] = []
    with as_file(files("alr") / "skills") as bundled:
        for name in _bundled_names(bundled):
            one = _sync_one(bundled / name, folder / name)
            changes.extend(one.changes)
            warnings.extend(one.warnings)

    return SyncReport(changes=changes, warnings=warnings)


def pending(home: Path | None = None) -> list[SkillChange]:
    """What `sync` would change, in the same order, without writing anything.

    A skill that cannot be read is left out: a real run reports it as a
    warning, and a dry run has no business failing over it.
    """
    try:
        folder = skills_folder(home)
    except RuntimeError:
        return []

    changes: list[SkillChange] = []
    with as_file(files("alr") / "skills") as bundled:
        for name in _bundled_names(bundled):
            change = _try_classify(bundled / name, folder / name)
            if change is not None:
                changes.append(change)

    return changes


def _sync_one(source: Path, target: Path) -> SyncReport:
    """Classify one skill, apply the change, and report both."""
    try:
        change = _classify(source, target)
        if change is None:
            return SyncReport(changes=[], warnings=[])

        _replace(source, change.path)
    except OSError as exc:
        reason = exc.strerror or exc
        return SyncReport(
            changes=[], warnings=[f"skill {target.name}: {target}: {reason}"]
        )

    if not change.replaced:
        return SyncReport(changes=[change], warnings=[])

    warning = f"{target} was a {change.replaced}; replaced it with the bundled skill"
    return SyncReport(changes=[change], warnings=[warning])


def _classify(source: Path, target: Path) -> SkillChange | None:
    """Decide what one skill needs; None means the installed copy is identical."""
    # is_symlink comes first: exists() follows a link, so a dangling one would look missing.
    if target.is_symlink():
        return SkillChange(target, "Updated", "symlink")

    if not target.exists():
        return SkillChange(target, "Added", "")

    if not target.is_dir():
        return SkillChange(target, "Updated", "file")

    if _same_tree(source, target):
        return None

    return SkillChange(target, "Updated", "")


def _same_tree(source: Path, target: Path) -> bool:
    """Same relative paths holding the same things; modes and times are ignored.

    Files, folders, and symlinks all count, so an empty folder or a dangling
    link inside an installed skill makes it different. Raises OSError when a
    folder cannot be read: unreadable is not the same as identical.
    """
    return _read_entries(source) == _read_entries(target)


def _read_entries(root: Path) -> dict[Path, tuple[str, bytes]]:
    """Map every path under root to its kind and content, without following links.

    A file holds its bytes, a symlink its link text, a folder nothing.
    """
    entries: dict[Path, tuple[str, bytes]] = {}
    for folder, folder_names, file_names in os.walk(
        root, followlinks=False, onerror=_raise
    ):
        for name in folder_names + file_names:
            path = Path(folder) / name
            entries[path.relative_to(root)] = _describe(path)

    return entries


def _raise(error: OSError) -> None:
    """Make os.walk fail on a folder it cannot read instead of skipping it."""
    raise error


def _describe(path: Path) -> tuple[str, bytes]:
    """The kind of entry at path and what it holds; a link is never followed."""
    if path.is_symlink():
        return ("symlink", os.fsencode(path.readlink()))

    if path.is_dir():
        return ("folder", b"")

    return ("file", path.read_bytes())


def _bundled_names(bundled: Path) -> list[str]:
    """Skill folders only: skip files, hidden entries, and `__pycache__`-style entries."""
    return sorted(
        entry.name
        for entry in bundled.iterdir()
        if entry.is_dir() and not entry.name.startswith((".", "_"))
    )


def _replace(source: Path, target: Path) -> None:
    """Put a copy of source at target, keeping the old skill until the copy is in place.

    The copy is made beside the target, the old entry is renamed aside inside
    the same staging folder, and only then is the copy renamed into place. A
    failure never leaves the user without the skill.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=".sync-"))
    aside = staging / "old"
    try:
        staged = staging / target.name
        shutil.copytree(source, staged)
        _make_owner_writable(staged)
        _swap(staged, target, aside)
    finally:
        _discard_staging(staging, aside, target)


def _make_owner_writable(root: Path) -> None:
    """Give the owner write access to every folder and file under root, root included.

    copytree copies the modes of a read-only package, and Linux will not move
    a folder its owner cannot write to. Symlinks are skipped: chmod would
    follow them out of the tree.
    """
    root.chmod(root.stat().st_mode | 0o700)
    for folder, folder_names, file_names in os.walk(root, followlinks=False):
        for name in folder_names + file_names:
            path = Path(folder) / name
            if path.is_symlink():
                continue

            # A folder also needs read and search access, or the walk cannot enter it.
            extra = 0o700 if path.is_dir() else 0o200
            path.chmod(path.stat().st_mode | extra)


def _swap(staged: Path, target: Path, aside: Path) -> None:
    """Rename what stands at target to aside, then staged to target.

    If the second rename fails the old entry is renamed back, so the user
    keeps the skill they had.
    """
    if not _present(target):
        staged.rename(target)
        return

    target.rename(aside)
    try:
        staged.rename(target)
    except OSError:
        aside.rename(target)
        raise


def _discard_staging(staging: Path, aside: Path, target: Path) -> None:
    """Remove the staging folder, and with it the old skill once the new one is in place.

    A failure here is not raised: the skill is already in place (or put back),
    so the run has done its job and the leftover is only clutter. The one
    exception is when the old skill could not be put back; it then sits in the
    staging folder, which is kept so the user's files are not lost.
    """
    if _present(aside) and not _present(target):
        return

    with contextlib.suppress(OSError):
        shutil.rmtree(staging)


def _present(path: Path) -> bool:
    """True when anything stands at path, a dangling symlink included."""
    return path.is_symlink() or path.exists()


def _try_classify(source: Path, target: Path) -> SkillChange | None:
    """Classify one skill for a dry run; None when it is identical or unreadable."""
    try:
        return _classify(source, target)
    except OSError:
        return None
