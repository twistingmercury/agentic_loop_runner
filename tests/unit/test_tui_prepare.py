"""prepare: the checks a run starts with, run again on Start, and nothing written before they pass."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from helpers import block_saving, write_file, write_nested_file

from alr.engine.commit import CommitMode, Repo, find_repo, held_lock
from alr.engine.stop import Stop
from alr.errors import FailedTasksError, RefusedError, RunLogError
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


async def test_prepare_puts_the_apps_stop_into_the_run_options(
    git_repo: Path,
) -> None:
    stop = Stop()

    prepared = await prepare(_settings(git_repo), stop)

    assert prepared.options.stop is stop


async def test_prepare_returns_the_options_for_a_valid_setup(git_repo: Path) -> None:
    prepared = await prepare(_settings(git_repo, commit_mode=CommitMode.RUN))

    assert prepared.options.commit_mode is CommitMode.RUN
    assert prepared.options.repo is not None
    assert prepared.log is None
    prepared.close()


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
        return await find_repo(tasks_file)

    monkeypatch.setattr(prepare_module, "find_repo", spy)
    settings = _outside_settings(outside, monkeypatch, commit_mode=mode)

    prepared = await prepare(settings)

    assert prepared.options.repo is None
    assert prepared.options.commit_mode is mode
    # Only a mode that commits looks for the repository; with none git is never touched.
    assert bool(looked_up) is (mode is not CommitMode.NONE)


async def test_prepare_records_the_none_mode_outside_a_repository(
    outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _outside_settings(outside, monkeypatch, logging=True)

    prepared = await prepare(settings)
    prepared.close()

    ledger = next((outside / "logs").glob("*/run.jsonl"))
    assert '"commit":"none"' in ledger.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", [CommitMode.TASK, CommitMode.RUN])
async def test_prepare_outside_a_repository_records_the_mode_that_was_asked(
    outside: Path, monkeypatch: pytest.MonkeyPatch, mode: CommitMode
) -> None:
    # The log names the mode that was asked for, repository or not.
    settings = _outside_settings(outside, monkeypatch, logging=True, commit_mode=mode)

    prepared = await prepare(settings)
    prepared.close()

    ledger = next((outside / "logs").glob("*/run.jsonl"))
    assert f'"commit":"{mode.value}"' in ledger.read_text(encoding="utf-8")


async def test_prepare_in_a_repository_records_the_commit_mode(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, logging=True, commit_mode=CommitMode.TASK)
    prepared = await prepare(settings)
    prepared.close()

    ledger = next((git_repo / "run" / "logs").glob("*/run.jsonl"))
    assert '"commit":"task"' in ledger.read_text(encoding="utf-8")


async def test_prepare_dirty_tree_is_an_error_and_saves_no_gates(
    git_repo: Path,
) -> None:
    settings = _settings(
        git_repo, gates=[Gate(cmd="true")], commit_mode=CommitMode.TASK
    )
    before = Path(settings.tasks_file).read_text(encoding="utf-8")
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")

    with pytest.raises(RefusedError, match="clean work tree") as caught:
        await prepare(settings)

    assert caught.value.reason == "dirty-tree"
    assert Path(settings.tasks_file).read_text(encoding="utf-8") == before


def _visible_settings(git_repo: Path, **changes: object) -> RunSettings:
    """Settings whose task file is ignored while its log folder, beside it, is not."""
    exclude = git_repo / ".git" / "info" / "exclude"
    exclude.write_text("work/tasks.yaml\n", encoding="utf-8")
    tasks_file = str(write_nested_file(git_repo / "work" / "tasks.yaml", TASKS))
    settings = RunSettings(
        tasks_file=tasks_file, permissions=Permissions.SKIP, logging=True
    )
    return replace(settings, **changes)


async def test_prepare_tells_a_tree_dirty_only_with_its_log_folder_to_ignore_it(
    git_repo: Path,
) -> None:
    settings = _visible_settings(git_repo)
    # A default-mode run leaves its untracked log folder behind.
    (await prepare(settings)).close()
    settings = replace(settings, commit_mode=CommitMode.TASK)

    with pytest.raises(RefusedError, match="not ignored by git") as caught:
        await prepare(settings)

    assert caught.value.reason == "log-folder-visible"
    assert "clean work tree" not in str(caught.value)


async def test_prepare_with_another_dirty_file_still_reports_the_dirty_tree(
    git_repo: Path,
) -> None:
    settings = _visible_settings(git_repo, commit_mode=CommitMode.TASK, logging=False)
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")

    with pytest.raises(RefusedError, match="clean work tree"):
        await prepare(settings)


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


async def test_prepare_save_keeps_a_users_own_tmp_file(git_repo: Path) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true")])
    users = Path(f"{settings.tasks_file}.tmp")
    users.write_text("precious", encoding="utf-8")

    await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.gate_list() == [Gate(cmd="true")]
    assert users.read_bytes() == b"precious"


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

    with pytest.raises(RefusedError, match="add it to .gitignore") as caught:
        await prepare(settings)

    assert caught.value.reason == "log-folder-visible"
    assert caught.value.message == (
        f"the log folder {settings.log_folder()} is not ignored by git; "
        "add it to .gitignore or turn logging off"
    )


async def test_prepare_keeps_the_reason_when_git_fails_on_the_log_folder(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(repo: Repo | None, run_dir: str) -> None:
        raise RefusedError("git-error", "--commit: git check-ignore: boom")

    monkeypatch.setattr(prepare_module, "check_log_dir", failing)

    with pytest.raises(RefusedError) as caught:
        await prepare(_settings(git_repo, logging=True, commit_mode=CommitMode.TASK))

    assert caught.value.reason == "git-error"
    assert "boom" in caught.value.message


async def test_prepare_opens_the_run_log_in_an_ignored_folder(git_repo: Path) -> None:
    settings = _settings(git_repo, logging=True)

    prepared = await prepare(settings)
    prepared.close()

    assert prepared.log is not None
    assert list((git_repo / "run" / "logs").glob("*/run.jsonl"))


async def test_prepare_hands_the_engine_the_folder_the_log_opened(
    git_repo: Path,
) -> None:
    prepared = await prepare(_settings(git_repo, logging=True))
    prepared.close()

    assert prepared.log is not None
    assert prepared.options.run_dir == str(prepared.log.path)


async def test_prepare_hands_the_engine_the_suffixed_folder_when_the_name_was_taken(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = git_repo / "run" / "logs" / "20261007T155618"
    taken.mkdir(parents=True)
    monkeypatch.setattr(prepare_module, "run_dir", lambda _log_dir, _start: taken)

    prepared = await prepare(_settings(git_repo, logging=True))
    prepared.close()

    assert prepared.options.run_dir == str(taken.with_name(taken.name + "-1"))


async def test_prepare_without_logging_has_no_run_folder_for_the_engine(
    git_repo: Path,
) -> None:
    prepared = await prepare(_settings(git_repo, logging=False))
    prepared.close()

    assert prepared.options.run_dir is None


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


async def test_prepare_refuses_and_removes_the_run_folder_when_saving_the_gates_fails(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(git_repo, logging=True, gates=[Gate(cmd="true")])
    block_saving(monkeypatch)
    before = Path(settings.tasks_file).read_bytes()

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "task-file-unwritable"
    assert "failed to save tasks to" in caught.value.message
    assert Path(settings.tasks_file).read_bytes() == before
    assert not (git_repo / "run" / "logs").exists(), "the logs/ it made is removed too"


async def test_prepare_keeps_a_logs_folder_that_was_there_when_saving_the_gates_fails(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(git_repo, logging=True, gates=[Gate(cmd="true")])
    (git_repo / "run" / "logs").mkdir()
    block_saving(monkeypatch)

    with pytest.raises(RefusedError):
        await prepare(settings)

    assert list((git_repo / "run" / "logs").iterdir()) == [], "only the run folder goes"


FAILED_TASKS = TASKS + "    state: failed\n    error: boom\n"


def _failed_settings(repo: Path, **changes: object) -> RunSettings:
    settings = _settings(repo, **changes)
    Path(settings.tasks_file).write_text(FAILED_TASKS, encoding="utf-8")
    return settings


async def test_prepare_reset_failed_with_a_dirty_tree_leaves_the_file_and_logs_alone(
    git_repo: Path,
) -> None:
    settings = _failed_settings(
        git_repo, reset_failed=True, logging=True, commit_mode=CommitMode.TASK
    )
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "dirty-tree"
    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS
    assert not (git_repo / "run" / "logs").exists()


async def test_prepare_reset_failed_resets_the_list_and_saves_the_file(
    git_repo: Path,
) -> None:
    settings = _failed_settings(git_repo, reset_failed=True)

    prepared = await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert [(task.state, task.error) for task in prepared.task_list.tasks] == [
        ("pending", "")
    ]
    assert [(task.state, task.error) for task in saved.tasks] == [("pending", "")]


async def test_prepare_reset_failed_and_edited_gates_are_saved_together(
    git_repo: Path,
) -> None:
    settings = _failed_settings(git_repo, reset_failed=True, gates=[Gate(cmd="true")])

    await prepare(settings)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.tasks[0].state == "pending"
    assert saved.gate_list() == [Gate(cmd="true")]


async def test_prepare_reset_failed_leaves_the_file_alone_when_the_log_cannot_open(
    git_repo: Path,
) -> None:
    settings = _failed_settings(git_repo, reset_failed=True, logging=True)
    # A file where the log folder belongs fails for every user, root included.
    (git_repo / "run" / "logs").write_text("a file, not a folder", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "log-unwritable"
    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS


async def test_prepare_reset_failed_that_cannot_save_refuses_and_removes_the_run_folder(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _failed_settings(git_repo, reset_failed=True, logging=True)
    block_saving(monkeypatch)

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "task-file-unwritable"
    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS
    assert not (git_repo / "run" / "logs").exists(), "the logs/ it made is removed too"


async def test_prepare_reset_failed_that_cannot_save_keeps_a_logs_folder_that_was_there(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _failed_settings(git_repo, reset_failed=True, logging=True)
    (git_repo / "run" / "logs").mkdir()
    block_saving(monkeypatch)

    with pytest.raises(RefusedError):
        await prepare(settings)

    assert list((git_repo / "run" / "logs").iterdir()) == [], "only the run folder goes"


async def test_prepare_reset_failed_that_cannot_save_refuses_without_a_log(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _failed_settings(git_repo, reset_failed=True, logging=False)
    block_saving(monkeypatch)

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "task-file-unwritable"


async def test_prepare_refuses_a_failed_task_after_the_repository_checks(
    git_repo: Path,
) -> None:
    settings = _failed_settings(git_repo, logging=True, commit_mode=CommitMode.TASK)
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")

    with pytest.raises(RefusedError) as dirty:
        await prepare(settings)

    (git_repo / "stray.txt").unlink()
    with pytest.raises(FailedTasksError):
        await prepare(settings)

    assert dirty.value.reason == "dirty-tree"
    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS
    assert not (git_repo / "run" / "logs").exists()


@pytest.mark.parametrize("mode", [CommitMode.TASK, CommitMode.RUN])
async def test_prepare_refuses_while_another_run_holds_the_lock(
    git_repo: Path, mode: CommitMode
) -> None:
    settings = _settings(
        git_repo, gates=[Gate(cmd="true")], logging=True, commit_mode=mode
    )
    before = Path(settings.tasks_file).read_text(encoding="utf-8")
    repo = await find_repo(settings.tasks_file)

    with held_lock(repo):
        with pytest.raises(RefusedError) as caught:
            await prepare(settings)

    assert caught.value.reason == "locked"
    assert Path(settings.tasks_file).read_text(encoding="utf-8") == before
    assert not (git_repo / "run" / "logs").exists(), "a refusal writes no run log"


async def test_prepare_tells_the_lock_before_a_dirty_tree(git_repo: Path) -> None:
    settings = _settings(git_repo, commit_mode=CommitMode.TASK)
    (git_repo / "stray.txt").write_text("the other run's work", encoding="utf-8")
    repo = await find_repo(settings.tasks_file)

    with held_lock(repo):
        with pytest.raises(RefusedError) as caught:
            await prepare(settings)

    assert caught.value.reason == "locked"


async def test_a_run_without_commits_never_takes_the_lock(git_repo: Path) -> None:
    settings = _settings(git_repo, commit_mode=CommitMode.NONE)
    repo = await find_repo(settings.tasks_file)

    prepared = await prepare(settings)

    with held_lock(repo):
        pass

    prepared.close()


async def test_prepared_holds_the_lock_until_it_is_closed(git_repo: Path) -> None:
    settings = _settings(git_repo, logging=True, commit_mode=CommitMode.RUN)
    repo = await find_repo(settings.tasks_file)

    prepared = await prepare(settings)

    with pytest.raises(RefusedError) as caught:
        with held_lock(repo):
            pass

    assert caught.value.reason == "locked"
    prepared.close()
    with held_lock(repo):
        pass


async def test_closing_a_prepared_run_twice_is_harmless(git_repo: Path) -> None:
    settings = _settings(git_repo, logging=True, commit_mode=CommitMode.RUN)
    repo = await find_repo(settings.tasks_file)
    prepared = await prepare(settings)

    prepared.close()
    prepared.close()

    with held_lock(repo):
        pass


async def test_a_refused_prepare_releases_the_lock(git_repo: Path) -> None:
    settings = _settings(git_repo, commit_mode=CommitMode.TASK)
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")
    repo = await find_repo(settings.tasks_file)

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "dirty-tree"
    with held_lock(repo):
        pass


async def test_a_refused_save_releases_the_lock_and_removes_the_log_folder(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(
        git_repo, gates=[Gate(cmd="true")], logging=True, commit_mode=CommitMode.RUN
    )
    repo = await find_repo(settings.tasks_file)
    block_saving(monkeypatch)

    with pytest.raises(RefusedError) as caught:
        await prepare(settings)

    assert caught.value.reason == "task-file-unwritable"
    assert not (git_repo / "run" / "logs").exists()
    with held_lock(repo):
        pass


async def test_a_stop_during_prepare_releases_the_lock(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stopped(repo: Repo) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(prepare_module, "check_clean", stopped)
    settings = _settings(git_repo, commit_mode=CommitMode.TASK)
    repo = await find_repo(settings.tasks_file)

    # Kept, because dropping the exception would free the lock's frame, and
    # with it the lock, whether or not prepare released it.
    with pytest.raises(asyncio.CancelledError) as stopped_run:
        await prepare(settings)

    with held_lock(repo):
        pass

    assert stopped_run.type is asyncio.CancelledError
