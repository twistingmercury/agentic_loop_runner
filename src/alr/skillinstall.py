"""--install-skill: copy every bundled skill into the user's skills folder."""

import shutil
import tempfile
from importlib.resources import as_file, files
from pathlib import Path

from alr.errors import SkillInstallError


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
        if target.exists():
            shutil.rmtree(target)

        staged.rename(target)
