"""prepare: the checks a run starts with, run again on Start, and nothing written before they pass."""

from dataclasses import replace
from pathlib import Path

import pytest
from helpers import block_saving, write_file

from alr.engine.stop import Stop
from alr.errors import FailedTasksError, RefusedError, RunLogError
from alr.record.runlog import RunLog
from alr.tasks import Action, parse_tasks
from alr.tui.prepare import prepare
from alr.tui.settings import Permissions, RunSettings

TASKS = """shared:
  prompt: do it
tasks:
  - id: 1
    name: First
    prompt: p
"""


def _settings(folder: Path, **changes: object) -> RunSettings:
    tasks_file = write_file(folder / "run" / "tasks.yaml", TASKS)
    settings = RunSettings(
        tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False
    )
    return replace(settings, **changes)


async def test_prepare_puts_the_apps_stop_into_the_run_options(
    work_dir: Path,
) -> None:
    stop = Stop()

    prepared = await prepare(_settings(work_dir), stop)

    assert prepared.options.stop is stop


async def test_prepare_returns_the_options_for_a_valid_setup(work_dir: Path) -> None:
    settings = _settings(work_dir)

    prepared = await prepare(settings)

    assert prepared.options.tasks_file == settings.tasks_file
    assert prepared.log is None
    prepared.close()


async def test_closing_a_prepared_run_twice_is_harmless(work_dir: Path) -> None:
    prepared = await prepare(_settings(work_dir, logging=True))

    prepared.close()
    prepared.close()


async def test_prepare_gives_the_run_the_log_folder_even_with_logging_off(
    work_dir: Path,
) -> None:
    settings = _settings(work_dir, logging=False)

    prepared = await prepare(settings)

    assert prepared.options.log_dir == str(settings.log_folder())
    prepared.close()


async def test_prepare_saves_edited_actions_once_every_check_passes(
    work_dir: Path,
) -> None:
    settings = _settings(work_dir, after_task=[Action(command="true", timeout="5s")])

    prepared = await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.after_task_list() == [Action(command="true", timeout="5s")]
    assert prepared.task_list.after_task_list() == saved.after_task_list()


async def test_prepare_save_keeps_a_users_own_tmp_file(work_dir: Path) -> None:
    settings = _settings(work_dir, after_task=[Action(command="true")])
    users = Path(f"{settings.tasks_file}.tmp")
    users.write_text("precious", encoding="utf-8")

    await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.after_task_list() == [Action(command="true")]
    assert users.read_bytes() == b"precious"


async def test_prepare_opens_the_run_log_in_an_ignored_folder(work_dir: Path) -> None:
    settings = _settings(work_dir, logging=True)

    prepared = await prepare(settings)
    prepared.close()

    assert prepared.log is not None
    assert list((work_dir / "run" / "logs").glob("*/run.jsonl"))


async def test_prepare_leaves_the_task_file_alone_when_the_log_cannot_open(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path, info: object) -> RunLog:
        raise RunLogError("no room")

    monkeypatch.setattr(RunLog, "open", refuse)
    settings = _settings(work_dir, logging=True, after_task=[Action(command="true")])
    before = Path(settings.tasks_file).read_bytes()

    with pytest.raises(RunLogError):
        await prepare(settings)

    assert Path(settings.tasks_file).read_bytes() == before


async def test_prepare_refuses_and_removes_the_run_folder_when_saving_the_actions_fails(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(work_dir, logging=True, after_task=[Action(command="true")])
    block_saving(monkeypatch)
    before = Path(settings.tasks_file).read_bytes()

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "task-file-unwritable"
    assert "failed to save tasks to" in caught.value.message
    assert Path(settings.tasks_file).read_bytes() == before
    assert not (work_dir / "run" / "logs").exists(), "the logs/ it made is removed too"


async def test_prepare_keeps_a_logs_folder_that_was_there_when_saving_the_actions_fails(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(work_dir, logging=True, after_task=[Action(command="true")])
    (work_dir / "run" / "logs").mkdir()
    block_saving(monkeypatch)

    with pytest.raises(RefusedError):
        await prepare(settings)

    assert list((work_dir / "run" / "logs").iterdir()) == [], "only the run folder goes"


FAILED_TASKS = TASKS + "    state: failed\n    error: boom\n"


def _failed_settings(folder: Path, **changes: object) -> RunSettings:
    settings = _settings(folder, **changes)
    Path(settings.tasks_file).write_text(FAILED_TASKS, encoding="utf-8")
    return settings


async def test_prepare_reset_failed_resets_the_list_and_saves_the_file(
    work_dir: Path,
) -> None:
    settings = _failed_settings(work_dir, reset_failed=True)

    prepared = await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert [(task.state, task.error) for task in prepared.task_list.tasks] == [
        ("pending", "")
    ]
    assert [(task.state, task.error) for task in saved.tasks] == [("pending", "")]


async def test_prepare_reset_failed_and_edited_actions_are_saved_together(
    work_dir: Path,
) -> None:
    settings = _failed_settings(
        work_dir, reset_failed=True, after_task=[Action(command="true")]
    )

    await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.tasks[0].state == "pending"
    assert saved.after_task_list() == [Action(command="true")]


async def test_prepare_reset_failed_leaves_the_file_alone_when_the_log_cannot_open(
    work_dir: Path,
) -> None:
    settings = _failed_settings(work_dir, reset_failed=True, logging=True)
    # A file where the log folder belongs fails for every user, root included.
    (work_dir / "run" / "logs").write_text("a file, not a folder", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "log-unwritable"
    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS


async def test_prepare_reset_failed_that_cannot_save_refuses_and_removes_the_run_folder(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _failed_settings(work_dir, reset_failed=True, logging=True)
    block_saving(monkeypatch)

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "task-file-unwritable"
    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS
    assert not (work_dir / "run" / "logs").exists(), "the logs/ it made is removed too"


async def test_prepare_reset_failed_that_cannot_save_keeps_a_logs_folder_that_was_there(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _failed_settings(work_dir, reset_failed=True, logging=True)
    (work_dir / "run" / "logs").mkdir()
    block_saving(monkeypatch)

    with pytest.raises(RefusedError):
        await prepare(settings)

    assert list((work_dir / "run" / "logs").iterdir()) == [], "only the run folder goes"


async def test_prepare_reset_failed_that_cannot_save_refuses_without_a_log(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _failed_settings(work_dir, reset_failed=True, logging=False)
    block_saving(monkeypatch)

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "task-file-unwritable"


async def test_prepare_refuses_a_failed_task_and_writes_nothing(
    work_dir: Path,
) -> None:
    # Edited actions too: a refusal must not save them either.
    settings = _failed_settings(
        work_dir, logging=True, after_task=[Action(command="true")]
    )

    with pytest.raises(FailedTasksError):
        await prepare(settings)

    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS
    assert not (work_dir / "run" / "logs").exists()
