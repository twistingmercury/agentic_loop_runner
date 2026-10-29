"""--install-skill: copy the bundled alr-task-file skill into the user's skills folder."""

import shutil
import tempfile
from importlib.resources import as_file, files
from pathlib import Path

from alr.errors import SkillInstallError

_NAME = "alr-task-file"


def install(home: Path | None = None) -> Path:
    """Replace the installed skill with the bundled one and return where it went.

    Nothing compares the two later (gralph ADR-022), so a missing or edited
    skill never stops a run.
    """
    target = (home or Path.home()) / ".claude" / "skills" / _NAME
    _refuse_foreign(target)
    try:
        with as_file(files("alr") / "skills" / _NAME) as source:
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
        staged = Path(staging) / _NAME
        shutil.copytree(source, staged)
        if target.exists():
            shutil.rmtree(target)

        staged.rename(target)
