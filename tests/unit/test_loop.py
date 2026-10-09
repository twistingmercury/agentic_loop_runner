"""The run loop: loading the inputs, one session per pending task, a save after each."""

import asyncio
import signal
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
from alr.engine.commit import CommitMode
from alr.engine.stop import Stop
from alr.errors import (
    CommitFailedError,
    FailedTasksError,
    RefusedError,
    RunError,
    RunLogError,
    TaskFileError,
    quote,
)
from alr.plain.frontend import PlainFrontEnd
from alr.tasks import (
    COMPLETED,
    FAILED,
    PENDING,
    RunResult,
    Shared,
    Task,
    TaskList,
)

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


def test_run_options_default_to_never_committing() -> None:
    assert loop.RunOptions("tasks.yaml", "", []).commit_mode is CommitMode.NONE


async def _wait_until(condition: Callable[[], bool], what: str) -> None:
    deadline = asyncio.get_running_loop().time() + 5
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail(what)

        await asyncio.sleep(0.01)


def test_read_tasks_missing_file(tmp_path: Path) -> None:
    path = str(tmp_path / "missing.yaml")

    with pytest.raises(TaskFileError, match="not accessible"):
        loop.read_tasks(path)


def test_read_tasks_invalid_yaml(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", BOGUS_STATE_YAML)

    with pytest.raises(TaskFileError, match="failed to parse tasks yaml"):
        loop.read_tasks(path)


def test_read_tasks_rejects_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    # A Latin-1 e-acute; reading it leniently would let the next save replace it.
    content = (
        b"shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: Caf\xe9, prompt: p}\n"
    )
    path = tmp_path / "tasks.yaml"
    path.write_bytes(content)

    with pytest.raises(TaskFileError, match="is not valid UTF-8") as caught:
        loop.read_tasks(str(path))

    assert quote(str(path)) in str(caught.value)
    assert path.read_bytes() == content


def test_read_tasks_success(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    got = loop.read_tasks(path)

    assert [task.id for task in got.tasks] == [1, 2]


FAILED_AND_COMPLETED_YAML = (
    "shared:\n  prompt: shared\ntasks:\n"
    "  - {id: 1, name: Done, prompt: p, state: completed}\n"
    "  - {id: 2, name: Broke, prompt: p, state: failed, error: boom}\n"
    "  - {id: 3, name: Later, prompt: p}\n"
)


def test_read_tasks_accepts_a_file_with_a_failed_task(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", FAILED_AND_COMPLETED_YAML)

    got = loop.read_tasks(path)

    assert [task.state for task in got.tasks] == [COMPLETED, FAILED, PENDING]


def test_read_tasks_still_rejects_a_bad_file(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", BOGUS_STATE_YAML)

    with pytest.raises(TaskFileError, match="failed to parse tasks yaml"):
        loop.read_tasks(path)


def test_reset_failed_resets_only_failed_tasks_and_clears_their_error(
    tmp_path: Path,
) -> None:
    path = write_file(tmp_path / "tasks.yaml", FAILED_AND_COMPLETED_YAML)

    count = loop.reset_failed(path)

    saved = saved_tasks(Path(path))
    assert count == 1
    assert [(task.state, task.error) for task in saved.tasks] == [
        (COMPLETED, ""),
        (PENDING, ""),
        (PENDING, ""),
    ]


def test_reset_failed_with_nothing_failed_leaves_the_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    def forbidden(*_: object) -> None:
        raise AssertionError("the file must not be rewritten")

    monkeypatch.setattr(loop, "save_tasks", forbidden)

    assert loop.reset_failed(path) == 0
    assert Path(path).read_text(encoding="utf-8") == VALID_TASKS_YAML


def test_refuse_failed_names_the_list_it_found_a_failed_task_in(
    tmp_path: Path,
) -> None:
    path = write_file(tmp_path / "tasks.yaml", FAILED_AND_COMPLETED_YAML)
    task_list = loop.read_tasks(path)

    with pytest.raises(FailedTasksError) as caught:
        loop.refuse_failed(task_list)

    assert caught.value.task_list is task_list
    assert caught.value.reason == "failed-tasks"


def test_refuse_failed_lets_a_list_without_one_through(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    loop.refuse_failed(loop.read_tasks(path))


def test_reset_in_memory_resets_only_failed_tasks_and_writes_nothing(
    tmp_path: Path,
) -> None:
    path = write_file(tmp_path / "tasks.yaml", FAILED_AND_COMPLETED_YAML)
    task_list = loop.read_tasks(path)

    count = loop.reset_in_memory(task_list)

    assert count == 1
    assert [(task.state, task.error) for task in task_list.tasks] == [
        (COMPLETED, ""),
        (PENDING, ""),
        (PENDING, ""),
    ]
    assert Path(path).read_text(encoding="utf-8") == FAILED_AND_COMPLETED_YAML


def test_save_for_start_saves_the_list(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", FAILED_AND_COMPLETED_YAML)
    task_list = loop.read_tasks(path)
    loop.reset_in_memory(task_list)

    loop.save_for_start(path, task_list)

    assert [task.state for task in saved_tasks(Path(path)).tasks] == [
        COMPLETED,
        PENDING,
        PENDING,
    ]


def test_save_for_start_refuses_when_the_file_cannot_be_saved(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", FAILED_AND_COMPLETED_YAML)
    task_list = loop.read_tasks(path)
    # A folder where the temporary file goes fails for every user, root included.
    (tmp_path / "tasks.yaml.tmp").mkdir()

    with pytest.raises(RefusedError) as caught:
        loop.save_for_start(path, task_list)

    assert caught.value.reason == "task-file-unwritable"
    assert "failed to save tasks to" in caught.value.message
    assert Path(path).read_text(encoding="utf-8") == FAILED_AND_COMPLETED_YAML


def test_read_tasks_parse_error_is_not_err_failed_tasks(tmp_path: Path) -> None:
    path = write_file(tmp_path / "tasks.yaml", BOGUS_STATE_YAML)

    with pytest.raises(TaskFileError) as caught:
        loop.read_tasks(path)

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

    assert "claude" in str(caught.value)
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

    assert "claude" in str(caught.value)
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
            "1",
            '{"state":"failed","error":"the build broke"}\n',
            FAILED,
            "the build broke",
            id="non-zero exit, valid result with error",
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
        self.endings: list[tuple[RunResult, int]] = []

    def task_started(self, task: Task) -> None:
        if self.crash is not None:
            raise self.crash

        super().task_started(task)

    def run_done(
        self, error: str | None, seconds: float, result: RunResult, exit_code: int
    ) -> None:
        self.done.append(error)
        self.endings.append((result, exit_code))


async def test_run_done_names_a_crash_and_the_crash_propagates(
    record: Path, tmp_path: Path
) -> None:
    front_end = _RecordingFrontEnd(crash=RuntimeError("boom"))
    task_list = tasks.parse_tasks(TWO_PENDING_YAML)

    with pytest.raises(RuntimeError, match="boom"):
        await run_loop(task_list, tmp_path / "tasks.yaml", front_end=front_end)

    assert len(front_end.done) == 1
    assert "boom" in str(front_end.done[0])


@pytest.mark.parametrize(
    "stop", [asyncio.CancelledError(), KeyboardInterrupt()], ids=["cancel", "ctrl-c"]
)
async def test_run_done_records_a_stop_as_a_stop(
    record: Path, tmp_path: Path, stop: BaseException
) -> None:
    front_end = _RecordingFrontEnd(crash=stop)
    task_list = tasks.parse_tasks(TWO_PENDING_YAML)

    with pytest.raises(type(stop)):
        await run_loop(task_list, tmp_path / "tasks.yaml", front_end=front_end)

    assert front_end.done == ["stopped"]


class _BrokenLogFrontEnd(_RecordingFrontEnd):
    """A front end whose run log cannot take the closing record."""

    def run_done(
        self, error: str | None, seconds: float, result: RunResult, exit_code: int
    ) -> None:
        raise RunLogError("disk full")


async def test_a_run_log_error_does_not_hide_the_error_that_ended_the_run(
    record: Path, tmp_path: Path
) -> None:
    crash = CommitFailedError("commit failed: hook said no")
    front_end = _BrokenLogFrontEnd(crash=crash)

    with pytest.raises(CommitFailedError) as exc:
        await run_loop(
            tasks.parse_tasks(TWO_PENDING_YAML),
            tmp_path / "tasks.yaml",
            front_end=front_end,
        )

    assert exc.value is crash
    assert any("disk full" in note for note in exc.value.__notes__)


async def test_a_run_log_error_alone_still_ends_the_run(
    record: Path, tmp_path: Path
) -> None:
    with pytest.raises(RunLogError, match="disk full"):
        await run_loop(
            tasks.parse_tasks(TWO_PENDING_YAML),
            tmp_path / "tasks.yaml",
            front_end=_BrokenLogFrontEnd(),
        )


async def test_run_done_reports_a_failed_task_and_a_clean_run(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    clean = _RecordingFrontEnd()
    await run_loop(tasks.parse_tasks(TWO_PENDING_YAML), tasks_file, front_end=clean)

    assert clean.done == [None]

    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "1")
    failed = _RecordingFrontEnd()
    with pytest.raises(RunError):
        await run_loop(
            tasks.parse_tasks(TWO_PENDING_YAML), tasks_file, front_end=failed
        )

    assert len(failed.done) == 1
    assert failed.done[0] is not None


async def test_a_clean_run_ends_completed_with_exit_0(
    record: Path, tmp_path: Path
) -> None:
    front_end = _RecordingFrontEnd()

    await run_loop(
        tasks.parse_tasks(TWO_PENDING_YAML),
        tmp_path / "tasks.yaml",
        front_end=front_end,
    )

    assert front_end.endings == [(RunResult.COMPLETED, 0)]


async def test_a_failed_task_ends_failed_with_exit_1(
    record: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "1")
    front_end = _RecordingFrontEnd()

    with pytest.raises(RunError):
        await run_loop(
            tasks.parse_tasks(TWO_PENDING_YAML),
            tmp_path / "tasks.yaml",
            front_end=front_end,
        )

    assert front_end.endings == [(RunResult.FAILED, 1)]


@pytest.mark.parametrize(
    ("stop", "exit_code"),
    [(Stop(), 130), (Stop(signal.SIGTERM), 143)],
    ids=["sigint", "sigterm"],
)
async def test_a_stop_ends_stopped_with_the_signals_exit(
    record: Path, tmp_path: Path, stop: Stop, exit_code: int
) -> None:
    front_end = _RecordingFrontEnd(crash=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await run_loop(
            tasks.parse_tasks(TWO_PENDING_YAML),
            tmp_path / "tasks.yaml",
            front_end=front_end,
            stop=stop,
        )

    assert front_end.endings == [(RunResult.STOPPED, exit_code)]
