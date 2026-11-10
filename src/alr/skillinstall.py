"""Keep the bundled skills in the user's skills folder: the startup sync and --install-skill."""

import os
import shutil
import tempfile
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from alr.errors import SkillInstallError


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


def _try_classify(source: Path, target: Path) -> SkillChange | None:
    try:
        return _classify(source, target)
    except OSError:
        return None


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
    """Same relative file paths with the same bytes; modes and times are ignored."""
    return _read_files(source) == _read_files(target)


def _read_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def install(home: Path | None = None) -> list[Path]:
    """Replace each installed skill with the bundled one and return where they went.

    Skills install in sorted name order and the first failure stops the rest.
    Nothing compares the copies later (gralph ADR-022), so a missing or edited
    skill never stops a run.
    """
    skills_folder = (home or Path.home()) / ".claude" / "skills"
    with as_file(files("alr") / "skills") as bundled:
        names = _bundled_names(bundled)
        return [_install_one(bundled / name, skills_folder / name) for name in names]


def _bundled_names(bundled: Path) -> list[str]:
    """Skill folders only: skip files, hidden entries, and `__pycache__`-style entries."""
    return sorted(
        entry.name
        for entry in bundled.iterdir()
        if entry.is_dir() and not entry.name.startswith((".", "_"))
    )


def _install_one(source: Path, target: Path) -> Path:
    _refuse_foreign(target)
    try:
        _replace(source, target)
    except OSError as exc:
        raise SkillInstallError(
            f"--install-skill: {target}: {exc.strerror or exc}"
        ) from exc

    return target


def _refuse_foreign(target: Path) -> None:
    """Never delete what is not a plain folder: the user put a link or file there on purpose."""
    if target.is_symlink():
        raise SkillInstallError(
            f"--install-skill: {target} is a symlink; remove it yourself"
        )

    if target.exists() and not target.is_dir():
        raise SkillInstallError(
            f"--install-skill: {target} is a file; remove it yourself"
        )


def _replace(source: Path, target: Path) -> None:
    """Copy beside the target first, so a failed copy never leaves the user without a skill."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".install-") as staging:
        staged = Path(staging) / target.name
        shutil.copytree(source, staged)
        _remove_existing(target)
        staged.rename(target)


def _remove_existing(target: Path) -> None:
    """Remove what stands at the target; a link is unlinked, never followed."""
    if target.is_symlink() or target.is_file():
        target.unlink()
        return

    if target.is_dir():
        shutil.rmtree(target)
