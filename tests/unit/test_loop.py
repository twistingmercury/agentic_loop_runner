"""The run loop: loading the inputs, one session per pending task, a save after each."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import (
    SHARED,
    cancel_run,
    fake_claude_records,
    run_loop,
    saved_tasks,
    write_file,
)

from alr import tasks
from alr.engine import loop
from alr.engine.sandbox import bypass_args
from alr.errors import (
    FailedTasksError,
    RunError,
    TaskFileError,
    quote,
)
from alr.plain.frontend import PlainFrontEnd
from alr.tasks import COMPLETED, FAILED, PENDING, Shared, Task, TaskList

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
BOGUS_STATE_YAML = (
    "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: bogus}\n"
)
TWO_PENDING_YAML = (
    "shared:\n  prompt: shared\ntasks:\n"
    "  - {id: 1, name: First, prompt: p1, state: pending}\n"
    "  - {id: 2, name: Second, prompt: p2, state: pending}\n"
)


async def _wait_until(condition: Callable[[], bool], what: str) -> None:
    deadline = asyncio.get_running_loop().time() + 5
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail(what)

        await asyncio.sleep(0.01)


def test_load_tasks_missing_file(tmp_path: Path) -> None:
    path = str(tmp_path / "missing.yaml")

    with pytest.raises(TaskFileError) as caught:
        loop.load_tasks(path)

    assert (
        str(caught.value)
        == f"tasks file {quote(path)} is not accessible: No such file or directory"
    )


def test_load_tasks_read_error(tmp_path: Path) -> None:
    with pytest.raises(TaskFileError, match="not accessible"):
        loop.load_tasks(str(tmp_path))


def test_load_tasks_invalid_yaml(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", BOGUS_STATE_YAML)

    with pytest.raises(TaskFileError, match="failed to parse tasks yaml"):
        loop.load_tasks(path)


def test_load_tasks_rejects_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    # A Latin-1 e-acute; reading it leniently would let the next save replace it.
    content = (
        b"shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: Caf\xe9, prompt: p}\n"
    )
    path = tmp_path / "tasks.yaml"
    path.write_bytes(content)

    with pytest.raises(TaskFileError, match="is not valid UTF-8") as caught:
        loop.load_tasks(str(path))

    assert quote(str(path)) in str(caught.value)
    assert path.read_bytes() == content


def test_load_tasks_success(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    got = loop.load_tasks(path)

    assert [task.id for task in got.tasks] == [1, 2]


def test_load_tasks_failed_task_returns_list_and_err_failed_tasks(
    tmp_path: Path,
) -> None:
    path = write_file(
        tmp_path / "tasks.yaml",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p}\n  - {id: 2, name: b, prompt: p, state: failed}\n",
    )

    with pytest.raises(FailedTasksError) as caught:
        loop.load_tasks(path)

    assert str(caught.value) == loop.FAILED_TASKS_MESSAGE
    assert len(caught.value.task_list.tasks) == 2


def test_load_tasks_parse_error_is_not_err_failed_tasks(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", BOGUS_STATE_YAML)

    with pytest.raises(TaskFileError) as caught:
        loop.load_tasks(path)

    assert not isinstance(caught.value, FailedTasksError)


async def test_run_loop_happy_path_invokes_in_order_with_exact_stdin(
    record: Path, tmp_path: Path
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    prompt = "Follow the runbook."
    task_list = TaskList(
        shared=Shared(prompt=prompt),
        tasks=[
            Task(id=1, name="First", prompt="Do the first thing."),
            Task(id=2, name="Second", prompt="Do the second thing."),
            Task(id=3, name="Third", prompt="Do the third thing."),
        ],
    )

    await run_loop(task_list, tasks_file)

    records = fake_claude_records(record)
    assert len(records) == 3
    for call, task in zip(records, task_list.tasks, strict=True):
        assert call["stdin"] == f"{prompt}\n\n{task.text()}\n"

    assert [task.state for task in saved_tasks(tasks_file).tasks] == [COMPLETED] * 3


async def test_run_loop_non_zero_exit_stops_at_first_task(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "1")
    task_list = TaskList(
        shared=SHARED,
        tasks=[
            Task(id=1, name="First", prompt="p1"),
            Task(id=2, name="Second", prompt="p2"),
        ],
    )

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert str(caught.value) == "task 1: First failed: exit status 1"
    assert len(fake_claude_records(record)) == 1, (
        "expected the second task never to run"
    )
    assert [task.state for task in saved_tasks(tasks_file).tasks] == [FAILED, PENDING]


async def test_run_loop_claude_missing_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty directory: no claude anywhere on PATH.
    monkeypatch.setenv("PATH", str(tmp_path))
    tasks_file = tmp_path / "tasks.yaml"
    task_list = TaskList(shared=SHARED, tasks=[Task(id=1, name="Only", prompt="p")])

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert str(caught.value) == "task 1: Only failed: claude: not found on PATH"
    saved = saved_tasks(tasks_file)
    assert [task.state for task in saved.tasks] == [FAILED]


async def test_run_loop_claude_that_cannot_be_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No execute bit at all, which exec refuses even for root.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "claude").chmod(0o644)
    monkeypatch.setenv("PATH", str(bin_dir))
    tasks_file = tmp_path / "tasks.yaml"
    task_list = TaskList(shared=SHARED, tasks=[Task(id=1, name="Only", prompt="p")])

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert str(caught.value) == (
        "task 1: Only failed: claude: cannot be started: Permission denied"
    )
    saved = saved_tasks(tasks_file)
    assert [task.state for task in saved.tasks] == [FAILED]


async def test_run_loop_context_already_cancelled(record: Path, tmp_path: Path) -> None:
    # Deliberately never created.
    tasks_file = tmp_path / "tasks.yaml"
    task_list = TaskList(shared=SHARED, tasks=[Task(id=1, name="Only", prompt="p")])

    run = asyncio.ensure_future(run_loop(task_list, tasks_file))
    # A coroutine that has not started cannot turn the cancel into its own error.
    await asyncio.sleep(0)
    await cancel_run(run)

    assert fake_claude_records(record) == [], "expected claude never to be invoked"
    assert not tasks_file.exists(), "expected the tasks file never to be written"


async def test_run_loop_prints_prompt_to_stdout(
    record: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    task_list = TaskList(
        shared=Shared(prompt="Follow the runbook."),
        tasks=[
            Task(id=1, name="First", prompt="Do the first thing."),
            Task(id=2, name="Second", prompt="Do the second thing."),
        ],
    )

    await run_loop(task_list, tmp_path / "tasks.yaml")

    output = capfd.readouterr().out
    for task in task_list.tasks:
        assert task.text() in output


async def test_run_loop_empty_task_list(record: Path, tmp_path: Path) -> None:
    await run_loop(TaskList(shared=SHARED, tasks=[]), tmp_path / "tasks.yaml")

    assert fake_claude_records(record) == []


async def test_run_loop_skips_completed_tasks(
    record: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    task_list = TaskList(
        shared=SHARED,
        tasks=[
            Task(id=1, name="First", prompt="p1", state=COMPLETED),
            Task(id=2, name="Second", prompt="p2", state=PENDING),
        ],
    )

    await run_loop(task_list, tasks_file)

    output = capfd.readouterr().out
    assert "task 1: First already completed, skipping" in output
    assert "p1" not in output, "a completed task's prompt must not be echoed"
    assert len(fake_claude_records(record)) == 1, (
        "claude must only run for the pending task"
    )
    assert [task.state for task in saved_tasks(tasks_file).tasks] == [
        COMPLETED,
        COMPLETED,
    ]


async def test_run_task_plain_argv_is_print_then_session_args(
    record: Path, tmp_path: Path
) -> None:
    session_args = [
        "--permission-mode",
        "acceptEdits",
        "--settings",
        '{"sandbox":{"enabled":true}}',
    ]
    task_list = TaskList(
        shared=SHARED, tasks=[Task(id=1, name="First", prompt="Do the first thing.")]
    )

    await run_loop(task_list, tmp_path / "tasks.yaml", session_args=session_args)

    assert [call["argv"] for call in fake_claude_records(record)] == [
        ["--print", *session_args]
    ]


@pytest.mark.parametrize(
    ("exit_code", "output", "want_state", "want_error"),
    [
        pytest.param("", None, COMPLETED, "", id="zero exit, completed result"),
        pytest.param(
            "",
            '{"state":"failed","error":"go vet failed"}\n',
            FAILED,
            "go vet failed",
            id="zero exit, failed result with error",
        ),
        pytest.param(
            "",
            '{"state":"failed","error":""}\n',
            FAILED,
            "session reported failed with no error",
            id="zero exit, failed result with blank error",
        ),
        pytest.param(
            "",
            "",
            FAILED,
            "no valid result line in session output",
            id="zero exit, missing result line",
        ),
        pytest.param(
            "",
            "not json at all\n",
            FAILED,
            "no valid result line in session output",
            id="zero exit, invalid json result line",
        ),
        pytest.param(
            "1",
            '{"state":"failed","error":"the build broke"}\n',
            FAILED,
            "the build broke",
            id="non-zero exit, valid result with error",
        ),
        pytest.param(
            "1",
            None,
            FAILED,
            "exit status 1",
            id="non-zero exit, no result line falls back to the exit text",
        ),
        pytest.param(
            "",
            '```json\n{"state":"completed","error":""}\n```\n',
            COMPLETED,
            "",
            id="fenced result line is still found",
        ),
    ],
)
async def test_run_loop_result_line_outcomes(
    record: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: str,
    output: str | None,
    want_state: str,
    want_error: str,
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    if exit_code:
        monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", exit_code)

    if output is not None:
        monkeypatch.setenv("FAKECLAUDE_OUTPUT", output)

    task_list = TaskList(shared=SHARED, tasks=[Task(id=1, name="Only", prompt="p")])

    if want_state == COMPLETED:
        await run_loop(task_list, tasks_file)
    else:
        with pytest.raises(RunError) as caught:
            await run_loop(task_list, tasks_file)

        assert want_error in str(caught.value)

    saved = saved_tasks(tasks_file)
    assert [(task.state, task.error) for task in saved.tasks] == [
        (want_state, want_error)
    ]


async def test_run_loop_non_zero_exit_uses_json_error_not_exit_status(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "5")
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", '{"state":"failed","error":"boom"}\n')
    task_list = TaskList(
        shared=SHARED, tasks=[Task(id=1, name="First task", prompt="p")]
    )

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert str(caught.value) == "task 1: First task failed: boom"
    saved = saved_tasks(tasks_file)
    assert [(task.state, task.error) for task in saved.tasks] == [(FAILED, "boom")]


async def test_run_loop_clears_stale_error_when_task_completes(
    record: Path, tmp_path: Path
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    task_list = TaskList(
        shared=SHARED,
        tasks=[
            Task(id=1, name="Only", prompt="p", state=PENDING, error="exit status 1")
        ],
    )

    await run_loop(task_list, tasks_file)

    assert "error:" not in tasks_file.read_text(encoding="utf-8"), (
        "a cleared error must be omitted from the saved yaml, not written empty"
    )
    saved = saved_tasks(tasks_file)
    assert [(task.state, task.error) for task in saved.tasks] == [(COMPLETED, "")]


async def test_run_loop_result_line_failure_stops_later_tasks(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    monkeypatch.setenv(
        "FAKECLAUDE_OUTPUT", '{"state":"failed","error":"tests failed"}\n'
    )
    task_list = TaskList(
        shared=SHARED,
        tasks=[
            Task(id=1, name="First", prompt="p1"),
            Task(id=2, name="Second", prompt="p2"),
        ],
    )

    with pytest.raises(RunError, match="tests failed"):
        await run_loop(task_list, tasks_file)

    assert len(fake_claude_records(record)) == 1, (
        "the second task must never run after the first fails via its result line"
    )
    saved = saved_tasks(tasks_file)
    assert [(task.state, task.error) for task in saved.tasks] == [
        (FAILED, "tests failed"),
        (PENDING, ""),
    ]


async def test_run_loop_cancel_mid_task_stops_quickly(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "ready"
    tasks_file = tmp_path / "tasks.yaml"
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    monkeypatch.setenv("FAKECLAUDE_READY_FILE", str(ready))
    write_file(tasks_file, TWO_PENDING_YAML)
    task_list = tasks.parse_tasks(TWO_PENDING_YAML)

    run = asyncio.ensure_future(run_loop(task_list, tasks_file))
    await _wait_until(ready.exists, "fake claude never became ready")
    error = await cancel_run(run)

    assert "task 1" in str(error)
    # The fake records a call before it blocks, so one record is the blocked task.
    assert len(fake_claude_records(record)) == 1, (
        "expected the second task never to start"
    )
    assert tasks_file.read_text(encoding="utf-8") == TWO_PENDING_YAML, (
        "the tasks file must be unchanged after a mid-task cancel"
    )


async def test_run_loop_saves_after_each_completed_task(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    monkeypatch.setenv("FAKECLAUDE_BLOCK_ON", "block-second")
    tasks_yaml = TWO_PENDING_YAML.replace("p2", "block-second")
    write_file(tasks_file, tasks_yaml)
    task_list = tasks.parse_tasks(tasks_yaml)

    run = asyncio.ensure_future(run_loop(task_list, tasks_file))
    # The fake's ready file is written by every call, so the second record is
    # what shows that task 2's session has started.
    await _wait_until(
        lambda: len(fake_claude_records(record)) == 2,
        "fake claude never started task 2",
    )
    error = await cancel_run(run)

    assert "task 2" in str(error)
    assert [task.state for task in saved_tasks(tasks_file).tasks] == [
        COMPLETED,
        PENDING,
    ]


def _unsavable_tasks_file(tmp_path: Path) -> tuple[Path, TaskList]:
    """A two-task list and a task file path whose folder is gone.

    A read-only folder would not do: the Docker build runs the tests as root,
    which ignores folder permissions, so the save would succeed there.
    """
    return tmp_path / "removed" / "tasks.yaml", tasks.parse_tasks(TWO_PENDING_YAML)


async def test_run_loop_failed_save_after_completed_task_stops_run(
    record: Path, tmp_path: Path
) -> None:
    tasks_file, task_list = _unsavable_tasks_file(tmp_path)

    with pytest.raises(RunError, match="task 1: First: failed to save task state"):
        await run_loop(task_list, tasks_file)

    assert len(fake_claude_records(record)) == 1, (
        "task 2 must never start once the save has failed"
    )


async def test_run_loop_failed_save_after_failed_task_reports_both(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", '{"state":"failed","error":"boom"}')
    tasks_file, task_list = _unsavable_tasks_file(tmp_path)

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert "task 1: First failed: boom" in str(caught.value)
    assert "failed to save tasks to" in str(caught.value)


async def test_plain_session_survives_output_that_is_not_utf8(
    fake_claude: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    script = fake_claude / "claude"
    script.write_text(
        '#!/bin/sh\ncat >/dev/null\nprintf \'\\377\\376 binary\\n{"state":"completed","error":""}\\n\'\n'
    )
    tasks_file = tmp_path / "tasks.yaml"
    task_list = TaskList(shared=SHARED, tasks=[Task(id=1, name="a", prompt="p")])
    tasks.save_tasks(str(tasks_file), task_list)

    await run_loop(task_list, tasks_file, session_args=[])

    assert task_list.tasks[0].state == COMPLETED
    assert " binary\n" in capfd.readouterr().out


class _RecordingFrontEnd(PlainFrontEnd):
    """The plain front end that notes run_done and can fail in task_started."""

    def __init__(self, crash: BaseException | None = None) -> None:
        super().__init__()
        self.crash = crash
        self.done: list[str | None] = []

    def task_started(self, task: Task) -> None:
        if self.crash is not None:
            raise self.crash

        super().task_started(task)

    def run_done(self, error: str | None, seconds: float) -> None:
        self.done.append(error)


async def _run_recorded(
    task_list: TaskList, tasks_file: Path, front_end: _RecordingFrontEnd
) -> None:
    options = loop.RunOptions(
        tasks_file=str(tasks_file), gate_timeout="", session_args=bypass_args()
    )
    await loop.run(task_list, options, front_end)


async def test_run_done_names_a_crash_and_the_crash_propagates(
    record: Path, tmp_path: Path
) -> None:
    front_end = _RecordingFrontEnd(crash=RuntimeError("boom"))
    task_list = tasks.parse_tasks(TWO_PENDING_YAML)

    with pytest.raises(RuntimeError, match="boom"):
        await _run_recorded(task_list, tmp_path / "tasks.yaml", front_end)

    assert front_end.done == ["RuntimeError: boom"]


async def test_run_done_reports_a_cancellation_with_a_non_empty_error(
    record: Path, tmp_path: Path
) -> None:
    front_end = _RecordingFrontEnd(crash=asyncio.CancelledError())
    task_list = tasks.parse_tasks(TWO_PENDING_YAML)

    with pytest.raises(asyncio.CancelledError):
        await _run_recorded(task_list, tmp_path / "tasks.yaml", front_end)

    assert front_end.done == ["CancelledError"]


async def test_run_done_reports_a_failed_task_and_a_clean_run(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    clean = _RecordingFrontEnd()
    await _run_recorded(tasks.parse_tasks(TWO_PENDING_YAML), tasks_file, clean)

    assert clean.done == [None]

    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "1")
    failed = _RecordingFrontEnd()
    with pytest.raises(RunError):
        await _run_recorded(tasks.parse_tasks(TWO_PENDING_YAML), tasks_file, failed)

    assert failed.done == ["task 1: First failed: exit status 1"]
