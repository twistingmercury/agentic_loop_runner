"""The skill sync and --install-skill: every bundled skill lands in the user's skills folder."""

import shutil
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import pytest

from alr.errors import SkillInstallError
from alr import skillinstall
from alr.skillinstall import (
    SkillChange,
    SyncReport,
    install,
    pending,
    skills_folder,
    sync,
)


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


def _bundled_text(name: str) -> str:
    return (files("alr") / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def _snapshot(root: Path) -> dict[str, str]:
    """Every path under root with what it holds, so a test can prove nothing changed."""
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            found[str(path)] = f"link to {path.readlink()}"
        elif path.is_dir():
            found[str(path)] = "folder"
        else:
            found[str(path)] = path.read_text(encoding="utf-8")

    return found


def test_skills_folder_is_under_the_home_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    assert skills_folder(tmp_path) == tmp_path / ".claude" / "skills"


def test_skills_folder_follows_claude_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))

    assert skills_folder(tmp_path) == tmp_path / "elsewhere" / "skills"


def test_an_empty_claude_config_dir_counts_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")

    assert skills_folder(tmp_path) == tmp_path / ".claude" / "skills"


def test_sync_adds_every_missing_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    report = sync(tmp_path)

    expected = [SkillChange(_skill(tmp_path, name), "Added", "") for name in SKILLS]
    assert report == SyncReport(changes=expected, warnings=[])
    for name in SKILLS:
        assert (_skill(tmp_path, name) / "SKILL.md").read_text(
            encoding="utf-8"
        ) == _bundled_text(name)


def test_sync_changes_nothing_when_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    sync(tmp_path)

    report = sync(tmp_path)

    assert report == SyncReport(changes=[], warnings=[])


def test_sync_updates_a_differing_skill_and_removes_an_extra_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = _skill(tmp_path, "alr-task-file")
    sync(tmp_path)
    (path / "SKILL.md").write_text("edited", encoding="utf-8")
    (path / "stale.txt").write_text("old", encoding="utf-8")

    report = sync(tmp_path)

    assert report.changes == [SkillChange(path, "Updated", "")]
    assert report.warnings == []
    assert (path / "SKILL.md").read_text(encoding="utf-8") == _bundled_text(
        "alr-task-file"
    )
    assert not (path / "stale.txt").exists()


def test_sync_leaves_a_folder_under_another_name_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    mine = tmp_path / ".claude" / "skills" / "my-own-skill"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("mine", encoding="utf-8")
    note = mine.parent / "notes.txt"
    note.write_text("keep", encoding="utf-8")

    sync(tmp_path)

    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "mine"
    assert note.read_text(encoding="utf-8") == "keep"


def test_sync_replaces_a_symlink_without_touching_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    real = tmp_path / "real"
    real.mkdir()
    (real / "keep.txt").write_text("kept", encoding="utf-8")
    link = _skill(tmp_path, "alr-task-file")
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    report = sync(tmp_path)

    assert report.changes == [
        SkillChange(link, "Updated", "symlink"),
        SkillChange(_skill(tmp_path, "alr-task-review"), "Added", ""),
    ]
    assert report.warnings == [
        f"{link} was a symlink; replaced it with the bundled skill"
    ]
    assert not link.is_symlink()
    assert (link / "SKILL.md").is_file()
    assert (real / "keep.txt").read_text(encoding="utf-8") == "kept"
    assert not (real / "SKILL.md").exists()


def test_sync_replaces_a_dangling_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    link = _skill(tmp_path, "alr-task-file")
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "gone")

    report = sync(tmp_path)

    assert report.changes[0] == SkillChange(link, "Updated", "symlink")
    assert report.warnings == [
        f"{link} was a symlink; replaced it with the bundled skill"
    ]
    assert not link.is_symlink()
    assert (link / "SKILL.md").is_file()


def test_sync_replaces_a_plain_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    target = _skill(tmp_path, "alr-task-file")
    target.parent.mkdir(parents=True)
    target.write_text("mine", encoding="utf-8")

    report = sync(tmp_path)

    assert report.changes[0] == SkillChange(target, "Updated", "file")
    assert report.warnings == [
        f"{target} was a file; replaced it with the bundled skill"
    ]
    assert (target / "SKILL.md").is_file()


def test_sync_warns_and_goes_on_when_the_folder_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    (tmp_path / ".claude").write_text("a file, not a folder", encoding="utf-8")

    report = sync(tmp_path)

    assert report.changes == []
    assert len(report.warnings) == len(SKILLS)
    for name, warning in zip(SKILLS, report.warnings, strict=True):
        assert warning.startswith(f"skill {name}: {_skill(tmp_path, name)}: ")


def test_a_failed_copy_leaves_the_old_skill_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = _skill(tmp_path, "alr-task-file")
    sync(tmp_path)
    (path / "SKILL.md").write_text("mine", encoding="utf-8")
    monkeypatch.setattr(skillinstall.shutil, "copytree", _failing_copy("part.txt"))

    report = sync(tmp_path)

    assert report.changes == []
    assert report.warnings == [f"skill alr-task-file: {path}: no space left"]
    assert (path / "SKILL.md").read_text(encoding="utf-8") == "mine"
    assert not (path / "part.txt").exists()
    assert sorted(entry.name for entry in path.parent.iterdir()) == list(SKILLS)


def test_sync_warns_when_the_home_folder_cannot_be_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(skillinstall.Path, "home", no_home)

    assert sync() == SyncReport(
        changes=[], warnings=["skills folder: Could not determine home directory."]
    )
    assert pending() == []


def test_an_unreadable_installed_file_warns_in_sync_and_is_skipped_by_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked_folder: None
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    sync(tmp_path)
    locked = _skill(tmp_path, "alr-task-file") / "SKILL.md"
    locked.chmod(0o000)

    try:
        report = sync(tmp_path)
        waiting = pending(tmp_path)
    finally:
        locked.chmod(0o644)

    assert report.changes == []
    assert len(report.warnings) == 1
    assert report.warnings[0].startswith("skill alr-task-file: ")
    assert waiting == []


def _arrange(tmp_path: Path, scenario: str) -> None:
    """Install both skills, then put alr-task-file into the named state."""
    path = _skill(tmp_path, "alr-task-file")
    sync(tmp_path)
    if scenario == "missing":
        shutil.rmtree(path)

    if scenario == "differing":
        (path / "extra.txt").write_text("extra", encoding="utf-8")

    if scenario == "symlink":
        shutil.rmtree(path)
        path.symlink_to(tmp_path)

    if scenario == "file":
        shutil.rmtree(path)
        path.write_text("mine", encoding="utf-8")


@pytest.mark.parametrize(
    ("scenario", "verb", "replaced"),
    [
        ("missing", "Added", ""),
        ("differing", "Updated", ""),
        ("symlink", "Updated", "symlink"),
        ("file", "Updated", "file"),
        ("identical", None, None),
    ],
)
def test_pending_reports_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    verb: str | None,
    replaced: str | None,
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    _arrange(tmp_path, scenario)
    before = _snapshot(tmp_path)

    changes = pending(tmp_path)

    expected = []
    if verb is not None and replaced is not None:
        expected = [SkillChange(_skill(tmp_path, "alr-task-file"), verb, replaced)]

    assert changes == expected
    assert _snapshot(tmp_path) == before
