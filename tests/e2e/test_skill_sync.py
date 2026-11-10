"""The skill sync: a real run keeps the two bundled skills in the skills folder, and a dry run only reports."""

import shutil
from pathlib import Path

from e2e_helpers import (
    DRY_RUN_TASKS_YAML,
    VALID_TASKS_YAML,
    Result,
    fake_claude_records,
    run_alr,
    run_args,
    write_tasks_yaml,
)

SKILL_NAMES = ("alr-task-file", "alr-task-review")


def forget_skills(home: Path) -> None:
    """Remove the skills the conftest fixture synced, so a run starts on a machine that never had them."""
    shutil.rmtree(home / ".claude")


def real_run(tmp_path: Path, env: dict[str, str] | None = None) -> Result:
    """The lightest real run: one task, the fake claude, no sandbox."""
    tasks_path = write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
    return run_alr(*run_args(tasks_path), env=env)


def added_lines(skills: Path) -> str:
    """The lines a run prints when it adds both skills."""
    return "".join(f"Added {skills / name}\n" for name in SKILL_NAMES)


def test_a_first_real_run_adds_both_skills(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    forget_skills(home)
    skills = home / ".claude" / "skills"

    res = real_run(tmp_path)

    assert res.code == 0, res
    assert res.out.startswith(added_lines(skills))
    for name in SKILL_NAMES:
        assert (skills / name / "SKILL.md").is_file()


def test_a_second_run_says_nothing_about_skills(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    forget_skills(home)
    first = real_run(tmp_path)
    assert first.code == 0, first

    res = real_run(tmp_path)

    assert res.code == 0, res
    assert str(home / ".claude" / "skills") not in res.out
    assert "warning" not in res.err


def test_an_edited_skill_is_updated(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    skills = home / ".claude" / "skills"
    edited = skills / "alr-task-file" / "SKILL.md"
    bundled_text = edited.read_text(encoding="utf-8")
    edited.write_text("my own edit\n", encoding="utf-8")

    res = real_run(tmp_path)

    assert res.code == 0, res
    assert res.out.startswith(f"Updated {skills / 'alr-task-file'}\n")
    assert f"{skills / 'alr-task-review'}" not in res.out
    assert edited.read_text(encoding="utf-8") == bundled_text


def test_a_symlinked_skill_is_replaced_with_a_warning(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    skills = home / ".claude" / "skills"
    linked_target = tmp_path / "versioned-skill"
    linked_target.mkdir()
    (linked_target / "SKILL.md").write_text("mine\n", encoding="utf-8")
    link = skills / "alr-task-file"
    shutil.rmtree(link)
    link.symlink_to(linked_target, target_is_directory=True)

    res = real_run(tmp_path)

    assert res.code == 0, res
    warning = f"warning: {link} was a symlink; replaced it with the bundled skill"
    assert warning in res.err.splitlines()
    assert f"Updated {link}" in res.out.splitlines()
    assert not link.is_symlink()
    assert (link / "SKILL.md").is_file()
    assert (linked_target / "SKILL.md").read_text(encoding="utf-8") == "mine\n"


def test_a_dry_run_says_updated_for_an_edited_skill(tmp_path: Path, home: Path) -> None:
    skills = home / ".claude" / "skills"
    edited = skills / "alr-task-file" / "SKILL.md"
    edited.write_text("my own edit\n", encoding="utf-8")
    write_tasks_yaml(tmp_path, DRY_RUN_TASKS_YAML)

    res = run_alr("-d", str(tmp_path), "--dry-run")

    assert res.code == 0, res
    assert f"skill: {skills / 'alr-task-file'} (updated on a real run)\n" in res.out
    assert edited.read_text(encoding="utf-8") == "my own edit\n"


def test_claude_config_dir_moves_the_skills_folder(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    forget_skills(home)
    config = tmp_path / "claude-config"
    skills = config / "skills"

    res = real_run(tmp_path, env={"CLAUDE_CONFIG_DIR": str(config)})

    assert res.code == 0, res
    assert res.out.startswith(added_lines(skills))
    for name in SKILL_NAMES:
        assert (skills / name / "SKILL.md").is_file()
    assert not (home / ".claude").exists()


def test_an_unwritable_skills_folder_warns_and_the_run_goes_on(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    forget_skills(home)
    (home / ".claude").write_text("a file, not a folder", encoding="utf-8")
    record_file = tmp_path / "record.ndjson"

    res = real_run(tmp_path, env={"FAKECLAUDE_RECORD_FILE": str(record_file)})

    assert res.code == 0, res
    assert "warning: skill alr-task-file: " in res.err
    assert "warning: skill alr-task-review: " in res.err
    assert len(fake_claude_records(record_file)) == 1


def test_a_dry_run_reports_and_writes_nothing(tmp_path: Path, home: Path) -> None:
    forget_skills(home)
    write_tasks_yaml(tmp_path, DRY_RUN_TASKS_YAML)
    skills = home / ".claude" / "skills"

    res = run_alr("-d", str(tmp_path), "--dry-run")

    assert res.code == 0, res
    assert res.out.startswith(
        "".join(
            f"skill: {skills / name} (added on a real run)\n" for name in SKILL_NAMES
        )
    )
    assert not (home / ".claude").exists()


def test_install_skill_is_rejected(home: Path) -> None:
    res = run_alr("--install-skill")

    assert res.code == 2, res
    assert "unrecognized arguments: --install-skill" in res.err
