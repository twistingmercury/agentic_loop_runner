"""prepare: the checks a run starts with, run again on Start, and nothing written before they pass."""

from dataclasses import replace
from pathlib import Path

import pytest
from helpers import write_file

from alr.engine.commit import CommitMode
from alr.errors import RepoError
from alr.tasks import Gate, parse_tasks
from alr.tui.prepare import prepare
from alr.tui.settings import Permissions, RunSettings

TASKS = """shared:
  prompt: do it
tasks:
  - id: 1
    name: First
    prompt: p
"""


def _settings(repo: Path, **changes: object) -> RunSettings:
    tasks_file = write_file(repo / "run" / "tasks.yaml", TASKS)
    settings = RunSettings(
        tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False
    )
    return replace(settings, **changes)


async def test_prepare_returns_the_options_for_a_valid_setup(git_repo: Path) -> None:
    prepared = await prepare(_settings(git_repo, commit_mode=CommitMode.AFTER_ALL))

    assert prepared.options.commit_mode is CommitMode.AFTER_ALL
    assert prepared.options.repo is not None
    assert prepared.log is None


async def test_prepare_outside_a_repository_is_an_error(
    outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)
    settings = RunSettings(
        tasks_file=write_file(outside / "tasks.yaml", TASKS),
        permissions=Permissions.SKIP,
        logging=False,
    )

    with pytest.raises(RepoError, match="not a git repository"):
        await prepare(settings)


async def test_prepare_dirty_tree_is_an_error_and_saves_no_gates(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true")])
    before = Path(settings.tasks_file).read_text(encoding="utf-8")
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")

    with pytest.raises(RepoError, match="clean work tree"):
        await prepare(settings)

    assert Path(settings.tasks_file).read_text(encoding="utf-8") == before


async def test_prepare_saves_edited_gates_once_every_check_passes(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true", timeout="5s")])

    prepared = await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.gate_list() == [Gate(cmd="true", timeout="5s")]
    assert prepared.task_list.gate_list() == saved.gate_list()


async def test_prepare_log_folder_git_can_see_is_an_error(git_repo: Path) -> None:
    settings = _settings(git_repo, logging=True, log_dir=str(git_repo / "visible"))

    with pytest.raises(RepoError, match="add it to .gitignore"):
        await prepare(settings)


async def test_prepare_opens_the_run_log_in_an_ignored_folder(git_repo: Path) -> None:
    settings = _settings(git_repo, logging=True)

    prepared = await prepare(settings)
    prepared.close()

    assert prepared.log is not None
    assert list((git_repo / "run" / "logs").glob("*/run.jsonl"))
