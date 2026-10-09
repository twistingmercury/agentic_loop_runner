"""The dry run: validate the task file and report on it, without running anything."""

import io
import os
from pathlib import Path

import pytest
from helpers import write_file, write_nested_file

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
ONE_TASK_YAML = (
    "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p1}\n"
)
GATES_YAML = """shared:
  prompt: shared
  gates:
    - cmd: go test ./...
      timeout: 30s
    - cmd: |
        echo one
        echo two
    - cmd: make lint
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
    gate_timeout: str = "",
    sandbox_file: str = "",
    commit: bool = False,
) -> str:
    out = io.StringIO()
    await dry_run(out, tasks_file, gate_timeout, sandbox_file, commit)
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
        await dry_run(out, tasks_file, "", "", False)

    assert caught.value.reason == "invalid-task-file"
    assert "failed to start loop runner" not in str(caught.value)
    assert str(caught.value).count("failed to parse tasks yaml") == 1
    assert out.getvalue() == ""


async def test_dry_run_commit_invalid_task_file_is_refused_once(
    git_repo: Path,
) -> None:
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", "tasks: not-a-list\n")

    out = io.StringIO()
    with pytest.raises(RefusedError) as caught:
        await dry_run(out, tasks_file, "", "", True)

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
        await dry_run(out, tasks_file, "", "", False)

    assert caught.value.reason == "failed-tasks"
    assert out.getvalue() == FAILED_TASKS_REPORT
    assert Path(tasks_file).read_text(encoding="utf-8") == FAILED_TASKS_YAML, (
        "the tasks file must be unchanged"
    )


async def test_dry_run_commit_refuses_failed_tasks_on_a_clean_repository(
    git_repo: Path,
) -> None:
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", FAILED_TASKS_YAML)

    out = io.StringIO()
    with pytest.raises(FailedTasksError):
        await dry_run(out, tasks_file, "", "", True)

    assert out.getvalue() == FAILED_TASKS_REPORT


async def test_dry_run_commit_tells_a_dirty_tree_before_failed_tasks(
    git_repo: Path,
) -> None:
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", FAILED_TASKS_YAML)

    out = io.StringIO()
    with pytest.raises(RefusedError) as caught:
        await dry_run(out, tasks_file, "", "", True)

    assert caught.value.reason == "dirty-tree"
    assert out.getvalue() == ""


async def test_dry_run_commit_refuses_a_log_folder_git_can_see(
    git_repo: Path,
) -> None:
    # Only the task file is ignored, so the logs folder beside it stays visible.
    exclude = git_repo / ".git" / "info" / "exclude"
    exclude.write_text("work/tasks.yaml\n", encoding="utf-8")
    tasks_file = str(write_nested_file(git_repo / "work" / "tasks.yaml", ONE_TASK_YAML))

    out = io.StringIO()
    with pytest.raises(RefusedError, match="log directory") as caught:
        await dry_run(out, tasks_file, "", "", True)

    assert caught.value.reason == "log-folder-visible"
    assert "is valid" not in out.getvalue()
    assert not (git_repo / "work" / "logs").exists(), "a dry run writes nothing"


# Each gate is listed once for the file, not once per task.
@pytest.mark.parametrize(
    ("override", "want"),
    [
        pytest.param(
            "",
            "gate: go test ./...: 30s (gate)\n"
            "gate: echo one: 10m (default)\n"
            "gate: make lint: 10m (default)\n",
            id="gate and default",
        ),
        pytest.param(
            "2m",
            "gate: go test ./...: 2m (flag)\n"
            "gate: echo one: 2m (flag)\n"
            "gate: make lint: 2m (flag)\n",
            id="flag overrides every gate",
        ),
    ],
)
async def test_dry_run_lists_effective_gate_timeouts(
    tmp_path: Path, override: str, want: str
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", GATES_YAML)

    got = await _dry_run(tasks_file, gate_timeout=override)

    assert "NAME\n" in got, "the task table comes first"
    assert got.endswith(f"Third\n{want}{tasks_file} is valid\n"), (
        "gate lines sit between the table and the valid line"
    )


async def test_dry_run_no_gates_prints_no_gate_lines(tmp_path: Path) -> None:
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p}\n",
    )

    got = await _dry_run(tasks_file, gate_timeout="2m")

    # The prefix, not the bare word: the temporary path holds this test's name.
    assert "gate: " not in got
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


async def test_dry_run_commit_clean_repository_names_the_work_tree(
    git_repo: Path, outside: Path
) -> None:
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    got = await _dry_run(tasks_file, commit=True)

    assert got.endswith(f"commit: {git_repo}\n{tasks_file} is valid\n")


async def test_dry_run_commit_dirty_repository_is_an_error(
    git_repo: Path, outside: Path
) -> None:
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    out = io.StringIO()
    with pytest.raises(
        RefusedError, match="--commit needs a clean work tree"
    ) as caught:
        await dry_run(out, tasks_file, "", "", True)

    assert caught.value.reason == "dirty-tree"

    assert "is valid" not in out.getvalue()


async def test_dry_run_commit_broken_repository_is_an_error(
    git_repo: Path, outside: Path
) -> None:
    with (git_repo / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write("[broken\n")

    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    out = io.StringIO()
    with pytest.raises(RefusedError, match="--commit: git rev-parse: ") as caught:
        await dry_run(out, tasks_file, "", "", True)

    assert caught.value.reason == "git-error"

    assert "is valid" not in out.getvalue()


async def test_dry_run_commit_outside_a_repository_says_nothing_will_be_committed(
    outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    got = await _dry_run(tasks_file, commit=True)

    assert got.endswith(
        "commit: not a git repository, nothing will be committed\n"
        f"{tasks_file} is valid\n"
    )


async def test_dry_run_commit_without_the_flag_git_is_never_consulted(
    git_repo: Path, outside: Path
) -> None:
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    assert "commit:" not in await _dry_run(tasks_file)


async def test_dry_run_refuses_a_log_folder_that_is_a_file(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    (tmp_path / "logs").write_text("a file", encoding="utf-8")

    out = io.StringIO()
    with pytest.raises(RefusedError) as caught:
        await dry_run(out, tasks_file, "", "", False)

    assert caught.value.reason == "log-unwritable"
    assert out.getvalue() == ""


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
        await dry_run(out, tasks_file, "", "", False)

    assert caught.value.reason == "log-unwritable"
    assert out.getvalue() == ""
    assert list((tmp_path / "logs").iterdir()) == []


async def test_dry_run_creates_no_log_folder(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    await _dry_run(tasks_file)

    assert not (tmp_path / "logs").exists()
