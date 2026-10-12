"""The dry run: validate the task file and report on it, without running anything."""

import io
import os
from pathlib import Path

import pytest
from helpers import write_file

from alr.errors import FailedTasksError, RefusedError
from alr.plain.dryrun import dry_run

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
ACTIONS_YAML = """shared:
  prompt: shared
  after_task:
    - action: go test ./...
      timeout: 30s
    - action: |
        echo one
        echo two
    - action: make lint
tasks:
  - id: 1
    name: First
    prompt: p
  - id: 2
    name: Second
    prompt: p
  - id: 3
    name: Third
    prompt: p
"""


async def _dry_run(
    tasks_file: str,
    action_timeout: str = "",
    sandbox_file: str = "",
) -> str:
    out = io.StringIO()
    await dry_run(out, tasks_file, action_timeout, sandbox_file)
    return out.getvalue()


async def test_dry_run_valid_file(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    assert await _dry_run(tasks_file) == (
        "   ID  STATE      NAME\n"
        "   --  ---------  ----\n"
        "    1  PENDING    First task\n"
        "    2  PENDING    Second task\n"
        f"{tasks_file} is valid\n"
    )


async def test_dry_run_invalid_file(tmp_path: Path) -> None:
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: bogus}\n",
    )

    out = io.StringIO()
    with pytest.raises(RefusedError, match="state") as caught:
        await dry_run(out, tasks_file, "", "")

    assert caught.value.reason == "invalid-task-file"
    assert "failed to start loop runner" not in str(caught.value)
    assert str(caught.value).count("failed to parse tasks yaml") == 1
    assert out.getvalue() == ""


FAILED_TASKS_YAML = """shared:
  prompt: shared
tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
    state: completed
  - id: 2
    name: Second task
    prompt: Do the second thing.
    state: failed
    error: boom
  - id: 100
    name: Third task
    prompt: Do the third thing.
"""
FAILED_TASKS_REPORT = (
    "Some tasks failed previous runs:\n"
    "    ID  STATE      NAME\n"
    "   ---  ---------  ----\n"
    "✅   1  \033[92mCOMPLETED\033[0m  First task\n"
    "❌   2  \033[1;91mFAILED   \033[0m  Second task  \033[1;91m← Needs review!\033[0m\n"
    "   100  PENDING    Third task\n"
)


async def test_dry_run_refuses_failed_tasks_after_showing_them(
    tmp_path: Path,
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", FAILED_TASKS_YAML)

    out = io.StringIO()
    with pytest.raises(FailedTasksError) as caught:
        await dry_run(out, tasks_file, "", "")

    assert caught.value.reason == "failed-tasks"
    assert out.getvalue() == FAILED_TASKS_REPORT
    assert Path(tasks_file).read_text(encoding="utf-8") == FAILED_TASKS_YAML, (
        "the tasks file must be unchanged"
    )


# Each action is listed once for the file, not once per task.
@pytest.mark.parametrize(
    ("override", "want"),
    [
        pytest.param(
            "",
            "after_task: go test ./...: 30s (action)\n"
            "after_task: echo one: 10m (default)\n"
            "after_task: make lint: 10m (default)\n",
            id="action and default",
        ),
        pytest.param(
            "2m",
            "after_task: go test ./...: 2m (flag)\n"
            "after_task: echo one: 2m (flag)\n"
            "after_task: make lint: 2m (flag)\n",
            id="flag overrides every action",
        ),
    ],
)
async def test_dry_run_lists_effective_action_timeouts(
    tmp_path: Path, override: str, want: str
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", ACTIONS_YAML)

    got = await _dry_run(tasks_file, action_timeout=override)

    assert "NAME\n" in got, "the task table comes first"
    assert got.endswith(f"Third\n{want}{tasks_file} is valid\n"), (
        "action lines sit between the table and the valid line"
    )


async def test_dry_run_lists_after_run_actions_after_the_after_task_ones(
    tmp_path: Path,
) -> None:
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        "shared:\n  prompt: shared\n  after_task:\n    - action: make lint\n"
        "  after_run:\n    - action: git push --dry-run\n      timeout: 1m\n"
        "    - action: echo done\n"
        "tasks:\n  - {id: 1, name: First, prompt: p}\n",
    )

    got = await _dry_run(tasks_file)

    assert got.endswith(
        "First\nafter_task: make lint: 10m (default)\n"
        "after_run: git push --dry-run: 1m (action)\n"
        "after_run: echo done: 10m (default)\n"
        f"{tasks_file} is valid\n"
    )


async def test_dry_run_no_actions_prints_no_action_lines(tmp_path: Path) -> None:
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p}\n",
    )

    got = await _dry_run(tasks_file, action_timeout="2m")

    # The prefix, not the bare word: the temporary path holds this test's name.
    assert "after_task: " not in got
    assert "after_run: " not in got
    assert got.endswith(f"First\n{tasks_file} is valid\n")


async def test_dry_run_names_sandbox_settings(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    assert await _dry_run(tasks_file, sandbox_file="sandbox.json") == (
        "   ID  STATE      NAME\n"
        "   --  ---------  ----\n"
        "    1  PENDING    First task\n"
        "    2  PENDING    Second task\n"
        "sandbox settings: sandbox.json\n"
        f"{tasks_file} is valid\n"
    )


async def test_dry_run_refuses_a_log_folder_that_is_a_file(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    (tmp_path / "logs").write_text("a file", encoding="utf-8")

    out = io.StringIO()
    with pytest.raises(RefusedError) as caught:
        await dry_run(out, tasks_file, "", "")

    assert caught.value.reason == "log-unwritable"
    assert out.getvalue() == ""


async def test_dry_run_refuses_failed_tasks_before_an_unwritable_log_folder(
    tmp_path: Path,
) -> None:
    # A real run opens its log after the failed-tasks check, so it never gets
    # to say log-unwritable for a file it refuses first.
    tasks_file = write_file(tmp_path / "tasks.yaml", FAILED_TASKS_YAML)
    (tmp_path / "logs").write_text("a file", encoding="utf-8")

    out = io.StringIO()
    with pytest.raises(FailedTasksError):
        await dry_run(out, tasks_file, "", "")

    assert out.getvalue() == FAILED_TASKS_REPORT


async def test_dry_run_refuses_a_read_only_log_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    (tmp_path / "logs").mkdir()
    access = os.access

    def deny_logs(path: str | Path, mode: int, **kwargs: bool) -> bool:
        # chmod 500 would not stop root, so the refusal is made here instead.
        denied = Path(path).name == "logs" and mode & os.W_OK
        return not denied and access(path, mode, **kwargs)

    monkeypatch.setattr(os, "access", deny_logs)

    out = io.StringIO()
    with pytest.raises(RefusedError) as caught:
        await dry_run(out, tasks_file, "", "")

    assert caught.value.reason == "log-unwritable"
    assert out.getvalue() == ""
    assert list((tmp_path / "logs").iterdir()) == []


async def test_dry_run_creates_no_log_folder(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    await _dry_run(tasks_file)

    assert not (tmp_path / "logs").exists()
