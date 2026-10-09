"""The plain real run: load, check the repository, then run the loop."""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    cancel_run,
    commit_subjects,
    saved_tasks,
    wait_for_text,
    wait_until_gone,
    write_file,
    write_nested_file,
)

from alr.engine.commit import CommitMode
from alr.engine.sandbox import bypass_args
from alr.errors import (
    FAILED_TASKS_MESSAGE,
    CommitFailedError,
    FailedTasksError,
    RefusedError,
    RunError,
    RunLogError,
)
from alr.plain.start import start
from alr.record.events import Activity, Event
from alr.record.runlog import RunLog, run_dir

VALID_TASKS_YAML = """shared:
  prompt: shared
tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
  - id: 2
    name: Second task
    prompt: Do the second thing.
"""
ONE_TASK_YAML = (
    "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p1}\n"
)


def _calls(record: Path) -> int:
    """How many times the fake claude was started."""
    if not record.exists():
        return 0

    return len(record.read_text(encoding="utf-8").splitlines())


async def _start(
    tasks_file: str,
    commit: CommitMode = CommitMode.NONE,
    reset_failed: bool = False,
    sandbox_settings: str = "",
) -> None:
    await start(
        tasks_file,
        "",
        bypass_args(),
        commit,
        reset_failed,
        sandbox_settings=sandbox_settings,
    )


def _run_folder(tasks_file: str) -> Path:
    """The one run folder a run left under logs/ beside the task file."""
    (folder,) = (Path(tasks_file).parent / "logs").iterdir()
    return folder


def _ledger(folder: Path) -> list[dict[str, Any]]:
    lines = (folder / "run.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


async def test_start_missing_tasks_file(tmp_path: Path) -> None:
    with pytest.raises(RefusedError) as caught:
        await _start(str(tmp_path / "missing.yaml"))

    assert caught.value.reason == "invalid-task-file"
    assert "missing.yaml" in str(caught.value)


async def test_start_invalid_tasks_yaml(tmp_path: Path) -> None:
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: bogus}\n",
    )

    with pytest.raises(RefusedError, match="state") as caught:
        await _start(tasks_file)

    assert caught.value.reason == "invalid-task-file"


async def test_start_run_loop_failure_is_wrapped_with_loop_error_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The folder holds no claude, so the first session cannot start.
    monkeypatch.setenv("PATH", str(tmp_path))
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    with pytest.raises(RunError) as caught:
        await _start(tasks_file)

    assert str(caught.value).startswith("loop error: ")
    assert "task 1" in str(caught.value)
    assert "First task" in str(caught.value)


async def test_start_commit_failure_keeps_its_exit_code_under_the_loop_prefix(
    record: Path, git_repo: Path
) -> None:
    hook = write_nested_file(
        git_repo / ".git" / "hooks" / "pre-commit",
        "#!/bin/sh\necho hook-says-no >&2\nexit 1\n",
    )
    hook.chmod(0o700)
    tasks_file = write_file(
        git_repo / "run" / "tasks.yaml",
        "shared:\n  prompt: shared\n  gates:\n    - cmd: echo work > one.txt\n"
        "tasks:\n  - {id: 1, name: First, prompt: p1}\n",
    )

    with pytest.raises(CommitFailedError) as caught:
        await _start(tasks_file, commit=CommitMode.TASK)

    assert caught.value.exit_code == 4
    assert (
        str(caught.value)
        == "loop error: task 1: First failed: commit failed: hook-says-no"
    )


async def test_start_success(record: Path, tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    await _start(tasks_file)

    assert _calls(record) == 2, "expected claude invoked once per task"


async def test_start_refuses_when_any_task_failed(
    record: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    content = """shared:
  prompt: shared
tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
  - id: 2
    name: Second task
    prompt: Do the second thing.
    state: failed
    error: boom
  - id: 3
    name: Third task
    prompt: Do the third thing.
    state: failed
"""
    tasks_file = write_file(tmp_path / "tasks.yaml", content)

    with pytest.raises(FailedTasksError) as caught:
        await _start(tasks_file)

    assert str(caught.value) == f"refused: failed-tasks: {FAILED_TASKS_MESSAGE}"
    printed = capsys.readouterr().out
    assert printed.startswith("Some tasks failed previous runs:\n")
    assert "Second task" in printed, "the task table follows the notice"
    assert _calls(record) == 0, "claude must never be invoked"
    assert Path(tasks_file).read_text(encoding="utf-8") == content, (
        "the tasks file must be unchanged"
    )


async def test_start_reset_failed_runs_the_failed_task_without_a_word(
    record: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    content = ONE_TASK_YAML.replace("p1}", "p1, state: failed, error: boom}")
    tasks_file = write_file(tmp_path / "tasks.yaml", content)

    await _start(tasks_file, reset_failed=True)

    assert _calls(record) == 1
    printed = capsys.readouterr().out.lower()
    assert "reset" not in printed
    assert "failed" not in printed


async def test_start_reset_failed_still_wraps_a_bad_file(tmp_path: Path) -> None:
    with pytest.raises(RefusedError, match="failed to start loop runner"):
        await _start(str(tmp_path / "missing.yaml"), reset_failed=True)


async def test_an_invalid_task_file_is_refused(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", "tasks: not-a-list\n")

    with pytest.raises(RefusedError) as caught:
        await _start(tasks_file)

    assert caught.value.reason == "invalid-task-file"
    assert str(caught.value).count("failed to parse tasks yaml") == 1
    assert str(caught.value).count("failed to start loop runner") == 1
    assert not (tmp_path / "logs").exists(), "a refusal writes no run log"


FAILED_TASK_YAML = ONE_TASK_YAML.replace("p1}", "p1, state: failed, error: boom}")


async def test_reset_failed_waits_for_the_other_refusals(
    record: Path, git_repo: Path
) -> None:
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", FAILED_TASK_YAML)
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        await _start(tasks_file, commit=CommitMode.RUN, reset_failed=True)

    path = Path(tasks_file)
    assert caught.value.reason == "dirty-tree"
    assert saved_tasks(path).tasks[0].state == "failed", "a refused run changes nothing"
    assert path.read_text(encoding="utf-8") == FAILED_TASK_YAML
    assert not (path.parent / "logs").exists(), "a refusal writes no run log"
    assert _calls(record) == 0, "claude must not run"


async def test_reset_failed_leaves_the_file_alone_when_the_log_cannot_open(
    record: Path, tmp_path: Path
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", FAILED_TASK_YAML)
    # A file where the log folder belongs fails for every user, root included.
    (tmp_path / "logs").write_text("a file, not a folder", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        await _start(tasks_file, reset_failed=True)

    assert caught.value.reason == "log-unwritable"
    assert Path(tasks_file).read_text(encoding="utf-8") == FAILED_TASK_YAML
    assert _calls(record) == 0, "claude must not run"


async def test_reset_failed_that_cannot_save_refuses_and_leaves_no_run_folder(
    record: Path, tmp_path: Path
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", FAILED_TASK_YAML)
    # A folder where the temporary file goes fails the save for every user, root included.
    (tmp_path / "tasks.yaml.tmp").mkdir()

    with pytest.raises(RefusedError) as caught:
        await _start(tasks_file, reset_failed=True)

    assert caught.value.reason == "task-file-unwritable"
    assert "failed to save tasks to" in caught.value.message
    assert Path(tasks_file).read_text(encoding="utf-8") == FAILED_TASK_YAML
    assert list((tmp_path / "logs").iterdir()) == [], "the run folder is removed"
    assert _calls(record) == 0, "claude must not run"


async def test_reset_failed_runs_the_reset_list_and_saves_it(
    record: Path, tmp_path: Path
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", FAILED_TASK_YAML)

    await _start(tasks_file, reset_failed=True)

    saved = saved_tasks(Path(tasks_file)).tasks[0]
    assert (saved.state, saved.error) == ("completed", "")
    assert _calls(record) == 1


async def test_a_failed_task_is_refused_after_the_repository_checks(
    record: Path, git_repo: Path
) -> None:
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", FAILED_TASK_YAML)
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        await _start(tasks_file, commit=CommitMode.RUN)

    assert caught.value.reason == "dirty-tree"


async def test_a_failed_task_is_refused_on_a_clean_tree(
    record: Path, git_repo: Path
) -> None:
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", FAILED_TASK_YAML)

    with pytest.raises(RefusedError) as caught:
        await _start(tasks_file, commit=CommitMode.RUN)

    path = Path(tasks_file)
    assert caught.value.reason == "failed-tasks"
    assert path.read_text(encoding="utf-8") == FAILED_TASK_YAML
    assert not (path.parent / "logs").exists(), "a refusal writes no run log"


async def test_reset_failed_on_a_clean_tree_resets_and_runs(
    record: Path, git_repo: Path
) -> None:
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", FAILED_TASK_YAML)

    await _start(tasks_file, commit=CommitMode.RUN, reset_failed=True)

    assert _calls(record) == 1


async def test_start_commit_refuses_dirty_tree_before_any_session(
    record: Path, git_repo: Path, outside: Path
) -> None:
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    with pytest.raises(RefusedError) as caught:
        await _start(tasks_file, commit=CommitMode.TASK)

    assert caught.value.reason == "dirty-tree"
    assert "--commit needs a clean work tree" in str(caught.value)
    assert _calls(record) == 0, "claude must not run"
    assert Path(tasks_file).read_text(encoding="utf-8") == ONE_TASK_YAML, (
        "the tasks file must be untouched"
    )


async def test_start_commit_flag_commits_the_task(
    record: Path, git_repo: Path, outside: Path
) -> None:
    tasks_file = write_file(
        outside / "tasks.yaml",
        "shared:\n  prompt: shared\n  gates:\n    - cmd: echo work > one.txt\ntasks:\n  - id: 1\n    name: First\n    prompt: p1\n",
    )

    await _start(tasks_file, commit=CommitMode.TASK)

    assert commit_subjects(git_repo) == ["First", "init"]


async def test_start_without_commit_flag_ignores_a_dirty_tree(
    record: Path, git_repo: Path, outside: Path
) -> None:
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    await _start(tasks_file)

    assert commit_subjects(git_repo) == ["init"]


async def test_start_after_all_mode_makes_one_commit_for_the_run(
    record: Path, git_repo: Path, outside: Path
) -> None:
    tasks_file = write_file(
        outside / "tasks.yaml",
        "shared:\n  prompt: shared\n  gates:\n    - cmd: echo work > one.txt\ntasks:\n  - id: 1\n    name: First\n    prompt: p1\n",
    )

    await _start(tasks_file, commit=CommitMode.RUN)

    assert commit_subjects(git_repo) == ["alr: 1 task completed", "init"]


async def test_start_after_all_mode_outside_a_repository_runs_and_says_so(
    record: Path,
    outside: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)
    tasks_file = write_file(
        outside / "tasks.yaml",
        "shared:\n  prompt: shared\n  gates:\n    - cmd: echo work > one.txt\ntasks:\n  - id: 1\n    name: First\n    prompt: p1\n",
    )

    await _start(tasks_file, commit=CommitMode.RUN)

    assert "commit: not a git repository, nothing will be committed\n" in (
        capsys.readouterr().out
    )
    assert _calls(record) == 1
    assert (outside / "one.txt").exists()
    assert not (outside / ".git").exists()


@pytest.mark.parametrize("mode", [CommitMode.TASK, CommitMode.RUN])
async def test_start_logs_that_the_run_commits_even_outside_a_repository(
    record: Path,
    outside: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: CommitMode,
) -> None:
    # The log names the mode that was asked for, repository or not.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    await _start(tasks_file, commit=mode)

    assert _ledger(_run_folder(tasks_file))[0]["commit"] == mode.value


async def test_start_logs_the_run_beside_the_task_file(
    record: Path, tmp_path: Path
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", ONE_TASK_YAML)

    await _start(tasks_file)

    folder = _run_folder(tasks_file)
    events = [line["event"] for line in _ledger(folder)]
    assert events[0] == "run_started"
    assert events[-1] == "run_finished"
    detail = (folder / "task-1.log").read_text(encoding="utf-8")
    assert detail != "", "Claude's output is in the detail file"


async def test_start_logs_the_run_info(record: Path, tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", ONE_TASK_YAML)

    await _start(tasks_file, sandbox_settings="/some/sandbox.json")

    started = _ledger(_run_folder(tasks_file))[0]
    assert started["tasks_file"] == tasks_file
    assert started["permissions"] == "sandbox"
    assert started["sandbox_settings"] == "/some/sandbox.json"
    assert started["commit"] == "none"


async def test_start_logs_skip_permissions_without_a_sandbox(
    record: Path, tmp_path: Path
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", ONE_TASK_YAML)

    await _start(tasks_file)

    started = _ledger(_run_folder(tasks_file))[0]
    assert started["permissions"] == "skip"
    assert "sandbox_settings" not in started


async def test_start_logs_that_the_run_commits(
    record: Path, git_repo: Path, outside: Path
) -> None:
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    await _start(tasks_file, commit=CommitMode.TASK)

    assert _ledger(_run_folder(tasks_file))[0]["commit"] == "task"


async def test_start_commit_refuses_a_log_folder_git_can_see(
    record: Path, git_repo: Path
) -> None:
    # Only the task file is ignored, so the logs folder beside it stays visible.
    exclude = git_repo / ".git" / "info" / "exclude"
    exclude.write_text("work/tasks.yaml\n", encoding="utf-8")
    tasks_file = str(write_nested_file(git_repo / "work" / "tasks.yaml", ONE_TASK_YAML))

    with pytest.raises(RefusedError, match="log directory") as caught:
        await _start(tasks_file, commit=CommitMode.TASK)

    assert caught.value.reason == "log-folder-visible"
    assert _calls(record) == 0, "claude must not run"
    assert not (git_repo / "work" / "logs").exists()


async def test_start_commit_tells_a_tree_dirty_only_with_its_log_folder_to_ignore_it(
    record: Path, git_repo: Path
) -> None:
    exclude = git_repo / ".git" / "info" / "exclude"
    exclude.write_text("work/tasks.yaml\n", encoding="utf-8")
    tasks_file = str(write_nested_file(git_repo / "work" / "tasks.yaml", ONE_TASK_YAML))
    # A default-mode run leaves its untracked log folder behind.
    await _start(tasks_file)
    calls = _calls(record)

    with pytest.raises(RefusedError, match="log directory") as caught:
        await _start(tasks_file, commit=CommitMode.TASK)

    assert "clean work tree" not in str(caught.value)
    assert _calls(record) == calls, "claude must not run"


async def test_start_commit_with_another_dirty_file_still_reports_the_dirty_tree(
    record: Path, git_repo: Path
) -> None:
    exclude = git_repo / ".git" / "info" / "exclude"
    exclude.write_text("work/tasks.yaml\n", encoding="utf-8")
    tasks_file = str(write_nested_file(git_repo / "work" / "tasks.yaml", ONE_TASK_YAML))
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    # The log folder is ignored here, so only the stray file can make the tree dirty.
    exclude.write_text("work/tasks.yaml\nwork/logs/\n", encoding="utf-8")

    with pytest.raises(
        RefusedError, match="--commit needs a clean work tree"
    ) as caught:
        await _start(tasks_file, commit=CommitMode.TASK)

    assert "stray.txt" in str(caught.value)


async def test_start_commit_accepts_a_log_folder_git_ignores(
    record: Path, git_repo: Path
) -> None:
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", ONE_TASK_YAML)

    await _start(tasks_file, commit=CommitMode.TASK)

    assert (git_repo / "run" / "logs").is_dir()


async def test_start_without_commit_does_not_check_the_log_folder(
    record: Path, git_repo: Path
) -> None:
    exclude = git_repo / ".git" / "info" / "exclude"
    exclude.write_text("work/tasks.yaml\n", encoding="utf-8")
    tasks_file = str(write_nested_file(git_repo / "work" / "tasks.yaml", ONE_TASK_YAML))

    await _start(tasks_file)

    assert (git_repo / "work" / "logs").is_dir()


async def test_start_writes_no_log_when_loading_fails(tmp_path: Path) -> None:
    with pytest.raises(RefusedError):
        await _start(str(tmp_path / "missing.yaml"))

    assert not (tmp_path / "logs").exists()


async def test_start_log_failure_stops_the_run(record: Path, tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", ONE_TASK_YAML)
    # A file where the logs folder should go, so the log cannot be created.
    (tmp_path / "logs").write_text("in the way\n", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        await _start(tasks_file)

    assert caught.value.reason == "log-unwritable"
    assert _calls(record) == 0, "claude must not run"
    assert Path(tasks_file).read_text(encoding="utf-8") == ONE_TASK_YAML


async def test_start_twice_in_the_same_second_keeps_two_run_folders(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", ONE_TASK_YAML)
    same_second = datetime(2026, 10, 7, 15, 56, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "alr.plain.start.run_dir",
        lambda log_dir, _start: run_dir(log_dir, same_second),
    )

    await _start(tasks_file)
    (tmp_path / "tasks.yaml").write_text(ONE_TASK_YAML, encoding="utf-8")
    await _start(tasks_file)

    folders = sorted(p.name for p in (tmp_path / "logs").iterdir())
    assert folders == ["20261007T155618", "20261007T155618-1"]
    for name in folders:
        events = _ledger(tmp_path / "logs" / name)
        assert events[0]["event"] == "run_started"


def _spy_on_close(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the calls to RunLog.close, which still closes the log."""
    closed: list[int] = []
    real_close = RunLog.close

    def close(self: RunLog) -> None:
        closed.append(1)
        real_close(self)

    monkeypatch.setattr(RunLog, "close", close)
    return closed


async def test_start_cancel_ends_the_ledger_with_stopped(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "ready"
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    monkeypatch.setenv("FAKECLAUDE_READY_FILE", str(ready))
    closed = _spy_on_close(monkeypatch)
    tasks_file = write_file(tmp_path / "tasks.yaml", ONE_TASK_YAML)

    run = asyncio.ensure_future(_start(tasks_file))
    await wait_for_text(ready)
    await cancel_run(run)

    last = _ledger(_run_folder(tasks_file))[-1]
    assert last["event"] == "run_finished"
    assert last["result"] == "stopped"
    assert closed == [1], "the log must be closed once"
    assert (tmp_path / "tasks.yaml").read_text(encoding="utf-8") == ONE_TASK_YAML


async def test_start_log_write_failure_stops_the_run_and_the_child(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "ready"
    # Writes its pid, prints a line, which the log records as activity, then blocks as the
    # same process, so the pid in the ready file is the one that must go.
    script = fake_claude / "claude"
    script.write_text(
        f"#!/bin/sh\necho $$ > {ready}\necho working\nexec sleep 300\n",
        encoding="utf-8",
    )
    closed = _spy_on_close(monkeypatch)
    real_record = RunLog.record

    def failing_record(self: RunLog, event: Event) -> None:
        if isinstance(event, Activity):
            raise RunLogError("disk full")

        real_record(self, event)

    monkeypatch.setattr(RunLog, "record", failing_record)
    tasks_file = write_file(tmp_path / "tasks.yaml", ONE_TASK_YAML)

    with pytest.raises(RunLogError, match="disk full"):
        await _start(tasks_file)

    await wait_until_gone(int(await wait_for_text(ready)))
    assert closed == [1], "the log must be closed once"
    ledger = _ledger(_run_folder(tasks_file))
    assert [line["event"] for line in ledger] == [
        "run_started",
        "task_started",
        "run_finished",
    ]
    assert ledger[1]["task"] == 1
    # Not "stopped": no signal asked for it, and alr exits 1 like any other failure.
    assert ledger[-1]["result"] == "failed", "a failed log write must not claim success"
    assert ledger[-1]["exit"] == 1
    assert ledger[-1]["error"] == "RunLogError: disk full"
