"""--install-skill: every bundled skill lands in the user's skills folder."""

from collections.abc import Callable
from pathlib import Path

import pytest

from alr.errors import SkillInstallError
from alr import skillinstall
from alr.skillinstall import install


SKILLS = ("alr-task-file", "alr-task-review")


def _skill(home: Path, name: str) -> Path:
    return home / ".claude" / "skills" / name


def test_install_copies_every_skill_under_the_home_folder(tmp_path: Path) -> None:
    install(tmp_path)

    for name in SKILLS:
        assert (_skill(tmp_path, name) / "SKILL.md").is_file()


def test_install_returns_the_paths_in_sorted_name_order(tmp_path: Path) -> None:
    paths = install(tmp_path)

    assert paths == [_skill(tmp_path, name) for name in SKILLS]


def test_install_replaces_an_existing_copy(tmp_path: Path) -> None:
    path = _skill(tmp_path, "alr-task-file")
    install(tmp_path)
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


def _failing_copy(partial: str) -> Callable[..., None]:
    """A copytree that leaves a partial copy behind, then fails like a full disk."""

    def copy(source: object, target: Path, **_: object) -> None:
        Path(target).mkdir(parents=True)
        (Path(target) / partial).write_text("half", encoding="utf-8")
        raise OSError("no space left")

    return copy


def test_a_failed_copy_leaves_the_old_skill_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _skill(tmp_path, "alr-task-file")
    install(tmp_path)
    (path / "SKILL.md").write_text("mine", encoding="utf-8")
    monkeypatch.setattr(skillinstall.shutil, "copytree", _failing_copy("part.txt"))

    with pytest.raises(SkillInstallError, match="no space left"):
        install(tmp_path)

    assert (path / "SKILL.md").read_text(encoding="utf-8") == "mine"
    assert not (path / "part.txt").exists()
    assert sorted(entry.name for entry in path.parent.iterdir()) == list(SKILLS)


def test_an_error_without_a_strerror_never_says_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skillinstall.shutil, "copytree", _failing_copy("part.txt"))

    with pytest.raises(SkillInstallError) as caught:
        install(tmp_path)

    assert "None" not in str(caught.value)


def test_a_symlink_at_the_target_is_refused_and_kept(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / ".claude" / "skills" / "alr-task-file"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    with pytest.raises(SkillInstallError, match="is a symlink; remove it yourself"):
        install(tmp_path)

    assert link.is_symlink()
    assert real.is_dir()


def test_a_plain_file_at_the_target_is_refused_and_kept(tmp_path: Path) -> None:
    target = tmp_path / ".claude" / "skills" / "alr-task-file"
    target.parent.mkdir(parents=True)
    target.write_text("mine", encoding="utf-8")

    with pytest.raises(SkillInstallError, match="is a file; remove it yourself"):
        install(tmp_path)

    assert target.read_text(encoding="utf-8") == "mine"


def test_a_symlink_at_the_second_skills_target_stops_after_the_first(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = _skill(tmp_path, "alr-task-review")
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    with pytest.raises(SkillInstallError, match=f"{link} is a symlink"):
        install(tmp_path)

    assert (_skill(tmp_path, "alr-task-file") / "SKILL.md").is_file()
    assert link.is_symlink()
