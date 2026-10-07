"""--install-skill: copy the bundled gralph-docs-writer skill into the user's skills folder."""

import shutil
from importlib.resources import as_file, files
from pathlib import Path

from alr.errors import SkillInstallError

_NAME = "gralph-docs-writer"


def install(home: Path | None = None) -> Path:
    """Replace the installed skill with the bundled one and return where it went.

    Nothing compares the two later (gralph ADR-022), so a missing or edited
    skill never stops a run.
    """
    target = (home or Path.home()) / ".claude" / "skills" / _NAME
    try:
        with as_file(files("alr") / "skills" / _NAME) as source:
            if target.exists():
                shutil.rmtree(target)

            shutil.copytree(source, target)
    except OSError as exc:
        raise SkillInstallError(f"--install-skill: {target}: {exc.strerror}") from exc

    return target
