"""--install-skill: the bundled skill lands in the user's skills folder."""

from pathlib import Path

import pytest

from alr.errors import SkillInstallError
from alr.skillinstall import install


def test_install_copies_the_skill_under_the_home_folder(tmp_path: Path) -> None:
    path = install(tmp_path)

    assert path == tmp_path / ".claude" / "skills" / "gralph-docs-writer"
    assert (path / "SKILL.md").is_file()


def test_install_replaces_an_existing_copy(tmp_path: Path) -> None:
    path = install(tmp_path)
    (path / "SKILL.md").write_text("edited", encoding="utf-8")
    (path / "stale.txt").write_text("old", encoding="utf-8")

    install(tmp_path)

    assert (path / "SKILL.md").read_text(encoding="utf-8") != "edited"
    assert not (path / "stale.txt").exists()


def test_install_reports_a_folder_it_cannot_write(tmp_path: Path) -> None:
    blocker = tmp_path / ".claude"
    blocker.write_text("a file, not a folder", encoding="utf-8")

    with pytest.raises(SkillInstallError):
        install(tmp_path)
