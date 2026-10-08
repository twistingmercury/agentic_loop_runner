"""prepare: the checks a run starts with, run again on Start, and nothing written before they pass."""

from dataclasses import replace
from pathlib import Path

import pytest
from helpers import write_file

from alr.engine.commit import CommitMode, Repo, open_repo
from alr.errors import RepoError, RunLogError, TaskFileError
from alr.record.runlog import RunLog
from alr.tasks import Gate, parse_tasks
from alr.tui import prepare as prepare_module
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
    prepared = await prepare(_settings(git_repo, commit_mode=CommitMode.RUN))

    assert prepared.options.commit_mode is CommitMode.RUN
    assert prepared.options.repo is not None
    assert prepared.log is None


def _outside_settings(
    outside: Path, monkeypatch: pytest.MonkeyPatch, **changes: object
) -> RunSettings:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)
    settings = RunSettings(
        tasks_file=write_file(outside / "tasks.yaml", TASKS),
        permissions=Permissions.SKIP,
        logging=False,
    )
    return replace(settings, **changes)


@pytest.mark.parametrize("mode", list(CommitMode))
async def test_prepare_outside_a_repository_runs_without_commits(
    outside: Path, monkeypatch: pytest.MonkeyPatch, mode: CommitMode
) -> None:
    looked_up: list[str] = []

    async def spy(tasks_file: str) -> Repo | None:
        looked_up.append(tasks_file)
        return await open_repo(tasks_file)

    monkeypatch.setattr(prepare_module, "open_repo", spy)
    settings = _outside_settings(outside, monkeypatch, commit_mode=mode)

    prepared = await prepare(settings)

    assert prepared.options.repo is None
    assert prepared.options.commit_mode is mode
    # Only a mode that commits looks for the repository; with none git is never touched.
    assert bool(looked_up) is (mode is not CommitMode.NONE)


async def test_prepare_outside_a_repository_records_that_nothing_commits(
    outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _outside_settings(outside, monkeypatch, logging=True)

    prepared = await prepare(settings)
    prepared.close()

    ledger = next((outside / "logs").glob("*/run.jsonl"))
    assert '"commit":false' in ledger.read_text(encoding="utf-8")


async def test_prepare_in_a_repository_records_that_commits_happen(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, logging=True, commit_mode=CommitMode.TASK)
    prepared = await prepare(settings)
    prepared.close()

    ledger = next((git_repo / "run" / "logs").glob("*/run.jsonl"))
    assert '"commit":true' in ledger.read_text(encoding="utf-8")


async def test_prepare_dirty_tree_is_an_error_and_saves_no_gates(
    git_repo: Path,
) -> None:
    settings = _settings(
        git_repo, gates=[Gate(cmd="true")], commit_mode=CommitMode.TASK
    )
    before = Path(settings.tasks_file).read_text(encoding="utf-8")
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")

    with pytest.raises(RepoError, match="clean work tree"):
        await prepare(settings)

    assert Path(settings.tasks_file).read_text(encoding="utf-8") == before


async def test_prepare_with_none_never_opens_the_repository(git_repo: Path) -> None:
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")

    prepared = await prepare(_settings(git_repo, commit_mode=CommitMode.NONE))

    assert prepared.options.repo is None
    assert prepared.options.commit_mode is CommitMode.NONE


async def test_prepare_saves_edited_gates_once_every_check_passes(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true", timeout="5s")])

    prepared = await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.gate_list() == [Gate(cmd="true", timeout="5s")]
    assert prepared.task_list.gate_list() == saved.gate_list()


async def test_prepare_log_folder_git_can_see_is_an_error(git_repo: Path) -> None:
    # Only the task file is ignored, so the logs folder beside it stays visible.
    (git_repo / ".git" / "info" / "exclude").write_text(
        "visible/tasks.yaml\n", encoding="utf-8"
    )
    (git_repo / "visible").mkdir()
    tasks_file = write_file(git_repo / "visible" / "tasks.yaml", TASKS)
    settings = RunSettings(
        tasks_file=tasks_file,
        permissions=Permissions.SKIP,
        logging=True,
        commit_mode=CommitMode.TASK,
    )

    with pytest.raises(RepoError, match="add it to .gitignore"):
        await prepare(settings)


async def test_prepare_opens_the_run_log_in_an_ignored_folder(git_repo: Path) -> None:
    settings = _settings(git_repo, logging=True)

    prepared = await prepare(settings)
    prepared.close()

    assert prepared.log is not None
    assert list((git_repo / "run" / "logs").glob("*/run.jsonl"))


async def test_prepare_leaves_the_task_file_alone_when_the_log_cannot_open(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path, info: object) -> RunLog:
        raise RunLogError("no room")

    monkeypatch.setattr(RunLog, "open", refuse)
    settings = _settings(git_repo, logging=True, gates=[Gate(cmd="true")])
    before = Path(settings.tasks_file).read_bytes()

    with pytest.raises(RunLogError):
        await prepare(settings)

    assert Path(settings.tasks_file).read_bytes() == before


async def test_prepare_closes_the_log_when_saving_the_gates_fails(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: str, task_list: object) -> None:
        raise TaskFileError("read-only")

    closed: list[RunLog] = []
    close = RunLog.close

    def spy(self: RunLog) -> None:
        closed.append(self)
        close(self)

    monkeypatch.setattr(prepare_module, "save_tasks", refuse)
    monkeypatch.setattr(RunLog, "close", spy)
    settings = _settings(git_repo, logging=True, gates=[Gate(cmd="true")])

    with pytest.raises(TaskFileError):
        await prepare(settings)

    assert len(closed) == 1
