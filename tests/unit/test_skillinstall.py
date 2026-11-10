"""The skill sync: every bundled skill lands in the user's skills folder."""

import os
import shutil
import stat
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import pytest

from alr import skillinstall
from alr.skillinstall import (
    SkillChange,
    SyncReport,
    pending,
    skills_folder,
    sync,
)


SKILLS = ("alr-task-file", "alr-task-review")


def _skill(home: Path, name: str) -> Path:
    return home / ".claude" / "skills" / name


def _failing_copy(partial: str) -> Callable[..., None]:
    """A copytree that leaves a partial copy behind, then fails like a full disk."""

    def copy(source: object, target: Path, **_: object) -> None:
        Path(target).mkdir(parents=True)
        (Path(target) / partial).write_text("half", encoding="utf-8")
        raise OSError("no space left")

    return copy


def test_an_error_without_a_strerror_never_says_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(skillinstall.shutil, "copytree", _failing_copy("part.txt"))

    report = sync(tmp_path)

    assert report.warnings != []
    assert all("None" not in warning for warning in report.warnings)


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


def test_a_dangling_symlink_inside_an_installed_skill_makes_it_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = _skill(tmp_path, "alr-task-file")
    sync(tmp_path)
    (path / "dangling").symlink_to(tmp_path / "gone")

    report = sync(tmp_path)

    assert report.changes == [SkillChange(path, "Updated", "")]
    assert report.warnings == []
    assert not (path / "dangling").is_symlink()


def test_an_empty_folder_inside_an_installed_skill_makes_it_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = _skill(tmp_path, "alr-task-file")
    sync(tmp_path)
    (path / "empty").mkdir()

    report = sync(tmp_path)

    assert report.changes == [SkillChange(path, "Updated", "")]
    assert report.warnings == []
    assert not (path / "empty").exists()


def test_an_unreadable_folder_inside_an_installed_skill_warns_in_sync_and_is_skipped_by_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked_folder: None
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    sync(tmp_path)
    locked = _skill(tmp_path, "alr-task-file") / "locked"
    locked.mkdir()
    locked.chmod(0o000)

    try:
        report = sync(tmp_path)
        waiting = pending(tmp_path)
    finally:
        locked.chmod(0o755)

    assert report.changes == []
    assert len(report.warnings) == 1
    assert report.warnings[0].startswith("skill alr-task-file: ")
    assert waiting == []


def test_a_failed_rename_puts_the_old_skill_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = _skill(tmp_path, "alr-task-file")
    sync(tmp_path)
    (path / "SKILL.md").write_text("mine", encoding="utf-8")
    (path / "stale.txt").write_text("old", encoding="utf-8")
    real_rename = Path.rename

    def rename(self: Path, new: Path) -> Path:
        # Only the move of the staged copy into place fails; moving the old one
        # aside, and back, works. The staged copy keeps the skill's name.
        if self.parent.name.startswith(".install-") and self.name == path.name:
            raise OSError("rename blocked")

        return real_rename(self, new)

    monkeypatch.setattr(Path, "rename", rename)

    report = sync(tmp_path)

    assert report.changes == []
    assert report.warnings == [f"skill alr-task-file: {path}: rename blocked"]
    assert (path / "SKILL.md").read_text(encoding="utf-8") == "mine"
    assert (path / "stale.txt").read_text(encoding="utf-8") == "old"
    assert sorted(entry.name for entry in path.parent.iterdir()) == list(SKILLS)


def test_a_removal_failure_after_the_swap_still_reports_updated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked_folder: None
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = _skill(tmp_path, "alr-task-file")
    sync(tmp_path)
    stuck = path / "stuck"
    stuck.mkdir()
    (stuck / "file.txt").write_text("old", encoding="utf-8")
    stuck.chmod(0o500)

    try:
        report = sync(tmp_path)
    finally:
        _make_deletable(path.parent)

    assert report.changes == [SkillChange(path, "Updated", "")]
    assert report.warnings == []
    assert (path / "SKILL.md").read_text(encoding="utf-8") == _bundled_text(
        "alr-task-file"
    )
    assert not (path / "stuck").exists()


def _make_deletable(root: Path) -> None:
    """Undo the modes a test set, so pytest can clean the temporary folder."""
    for folder, _, _ in os.walk(root):
        Path(folder).chmod(0o755)


def test_a_read_only_bundled_source_still_syncs_and_the_copy_is_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    package = tmp_path / "package"
    shutil.copytree(files("alr") / "skills", package / "skills")
    monkeypatch.setattr(skillinstall, "files", lambda _: package)
    _set_modes(package / "skills", folder_mode=0o555, file_mode=0o444)
    home = tmp_path / "home"

    try:
        report = sync(home)
    finally:
        _set_modes(package / "skills", folder_mode=0o755, file_mode=0o644)

    assert report.warnings == []
    assert len(report.changes) == len(SKILLS)
    mode = (_skill(home, "alr-task-file") / "SKILL.md").stat().st_mode
    assert mode & stat.S_IWUSR


def _set_modes(root: Path, folder_mode: int, file_mode: int) -> None:
    """Set every file and folder under root, and root itself, to the given modes."""
    # Files first, folders deepest first, so a folder is locked only after its contents.
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(folder_mode if path.is_dir() else file_mode)

    root.chmod(folder_mode)
