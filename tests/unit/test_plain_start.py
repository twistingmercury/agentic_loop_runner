"""The plain real run: load, check the repository, then run the loop."""

from pathlib import Path

import pytest
from helpers import commit_subjects, write_file

from alr.engine.sandbox import bypass_args
from alr.errors import (
    FAILED_TASKS_MESSAGE,
    FailedTasksError,
    PromptError,
    RepoError,
    RunError,
    TaskFileError,
)
from alr.plain.start import start

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


def _causes(exc: BaseException) -> list[BaseException]:
    """The errors exc was raised from, nearest first."""
    chain = []
    while exc.__cause__ is not None:
        exc = exc.__cause__
        chain.append(exc)

    return chain


async def _start(prompt_file: str, tasks_file: str, commit: bool = False) -> None:
    await start(prompt_file, tasks_file, "", bypass_args(), commit)


async def test_start_missing_prompt_file(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    with pytest.raises(PromptError) as caught:
        await _start(str(tmp_path / "missing.md"), tasks_file)

    assert "failed to start loop runner" in str(caught.value)
    assert "prompt file" in str(caught.value)


async def test_start_missing_tasks_file(tmp_path: Path) -> None:
    prompt_file = write_file(tmp_path / "prompt.md", "Do the task.\n")

    with pytest.raises(TaskFileError) as caught:
        await _start(prompt_file, str(tmp_path / "missing.yaml"))

    assert "failed to start loop runner" in str(caught.value)
    assert "tasks file" in str(caught.value)


async def test_start_empty_prompt(tmp_path: Path) -> None:
    prompt_file = write_file(tmp_path / "prompt.md", "")
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    with pytest.raises(PromptError, match="the prompt file is empty"):
        await _start(prompt_file, tasks_file)


async def test_start_whitespace_only_prompt(tmp_path: Path) -> None:
    prompt_file = write_file(tmp_path / "prompt.md", "  \t\n  ")
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    with pytest.raises(PromptError, match="the prompt file is just whitespace"):
        await _start(prompt_file, tasks_file)


async def test_start_invalid_tasks_yaml(tmp_path: Path) -> None:
    prompt_file = write_file(tmp_path / "prompt.md", "Do the task.\n")
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: bogus}\n",
    )

    with pytest.raises(TaskFileError, match="failed to parse tasks yaml"):
        await _start(prompt_file, tasks_file)


async def test_start_load_failures_keep_the_error_chain(tmp_path: Path) -> None:
    prompt_file = write_file(tmp_path / "prompt.md", "Do the task.\n")
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    with pytest.raises(PromptError) as caught:
        await _start(str(tmp_path / "missing.md"), tasks_file)

    assert any(isinstance(cause, FileNotFoundError) for cause in _causes(caught.value))

    with pytest.raises(TaskFileError) as caught:
        await _start(prompt_file, str(tmp_path / "missing.yaml"))

    assert any(isinstance(cause, FileNotFoundError) for cause in _causes(caught.value))


async def test_start_run_loop_failure_is_wrapped_with_loop_error_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The folder holds no claude, so the first session cannot start.
    monkeypatch.setenv("PATH", str(tmp_path))
    prompt_file = write_file(tmp_path / "prompt.md", "Do the task.\n")
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    with pytest.raises(RunError) as caught:
        await _start(prompt_file, tasks_file)

    assert str(caught.value).startswith("loop error: ")
    assert "task 1: First task failed" in str(caught.value)


async def test_start_success(record: Path, tmp_path: Path) -> None:
    prompt_file = write_file(tmp_path / "prompt.md", "Follow the plan.\n")
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    await _start(prompt_file, tasks_file)

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
    prompt_file = write_file(tmp_path / "prompt.md", "Follow the plan.\n")
    tasks_file = write_file(tmp_path / "tasks.yaml", content)

    with pytest.raises(FailedTasksError) as caught:
        await _start(prompt_file, tasks_file)

    assert str(caught.value) == FAILED_TASKS_MESSAGE
    printed = capsys.readouterr().out
    assert printed.startswith("Some tasks failed previous runs:\n")
    assert "Second task" in printed, "the task table follows the notice"
    assert _calls(record) == 0, "claude must never be invoked"
    assert Path(tasks_file).read_text(encoding="utf-8") == content, (
        "the tasks file must be unchanged"
    )


async def test_start_commit_refuses_dirty_tree_before_any_session(
    record: Path, git_repo: Path, outside: Path
) -> None:
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    prompt_file = write_file(outside / "prompt.md", "prompt\n")
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    with pytest.raises(RepoError) as caught:
        await _start(prompt_file, tasks_file, commit=True)

    assert str(caught.value) == (
        "--commit needs a clean work tree; commit, stash, or remove:\n?? stray.txt"
    )
    assert _calls(record) == 0, "claude must not run"
    assert Path(tasks_file).read_text(encoding="utf-8") == ONE_TASK_YAML, (
        "the tasks file must be untouched"
    )


async def test_start_commit_flag_commits_the_task(
    record: Path, git_repo: Path, outside: Path
) -> None:
    prompt_file = write_file(outside / "prompt.md", "prompt\n")
    tasks_file = write_file(
        outside / "tasks.yaml",
        "shared:\n  prompt: shared\n  gates:\n    - cmd: echo work > one.txt\ntasks:\n  - id: 1\n    name: First\n    prompt: p1\n",
    )

    await _start(prompt_file, tasks_file, commit=True)

    assert commit_subjects(git_repo) == ["First", "init"]


async def test_start_without_commit_flag_ignores_a_dirty_tree(
    record: Path, git_repo: Path, outside: Path
) -> None:
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    prompt_file = write_file(outside / "prompt.md", "prompt\n")
    tasks_file = write_file(outside / "tasks.yaml", ONE_TASK_YAML)

    await _start(prompt_file, tasks_file)

    assert commit_subjects(git_repo) == ["init"]
