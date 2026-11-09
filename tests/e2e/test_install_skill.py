"""--install-skill: the installed wheel carries the skills and copies them under HOME."""

from pathlib import Path

from e2e_helpers import run_alr


def test_install_skill_copies_every_skill_under_home(tmp_path: Path) -> None:
    res = run_alr("--install-skill", env={"HOME": str(tmp_path)})

    skills = tmp_path / ".claude" / "skills"
    installed = [skills / "alr-task-file", skills / "alr-task-review"]
    assert res.code == 0, res
    assert res.out == "".join(f"{path}\n" for path in installed)
    for path in installed:
        assert (path / "SKILL.md").is_file()


def test_install_skill_fails_when_the_folder_cannot_be_written(
    tmp_path: Path,
) -> None:
    (tmp_path / ".claude").write_text("a file, not a folder", encoding="utf-8")

    res = run_alr("--install-skill", env={"HOME": str(tmp_path)})

    assert res.code == 1, res
    assert res.err.startswith("error: ")
