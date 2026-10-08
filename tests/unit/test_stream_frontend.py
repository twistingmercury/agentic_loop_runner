"""The stream front end: every process captured, every step reported as an event."""

import asyncio
import time
from pathlib import Path

import pytest
from helpers import (
    cancel_run,
    fake_claude_records,
    git,
    kill_holder_and_let_pipes_close,
    run_loop,
    wait_for_text,
    write_file,
)

from alr.engine import loop, proc
from alr.engine.commit import Repo, open_repo
from alr.engine.sandbox import bypass_args
from alr.errors import RunError
from alr.record.events import (
    Activity,
    CommitStarted,
    Committed,
    Event,
    GateFinished,
    GateStarted,
    RunDone,
    SessionFinished,
    TaskFinished,
    TaskStarted,
)
from alr.stream.frontend import StreamFrontEnd
from alr.tasks import COMPLETED, FAILED, Gate, Shared, Task, TaskList

pytestmark = pytest.mark.usefixtures("fake_claude")

SESSION_KINDS = [TaskStarted, Activity, SessionFinished, TaskFinished, RunDone]


def _task_list(*task_gates: Gate, names: tuple[str, ...] = ("First",)) -> TaskList:
    return TaskList(
        shared=Shared(prompt="shared prompt", gates=list(task_gates)),
        tasks=[
            Task(id=index, name=name, prompt=f"p{index}")
            for index, name in enumerate(names, start=1)
        ],
    )


async def _run(
    task_list: TaskList,
    tasks_file: Path,
    *,
    gate_timeout: str = "",
    session_args: list[str] | None = None,
    repo: Repo | None = None,
    events: list[Event] | None = None,
) -> list[Event]:
    events = [] if events is None else events
    await run_loop(
        task_list,
        tasks_file,
        gate_timeout=gate_timeout,
        session_args=session_args,
        repo=repo,
        front_end=StreamFrontEnd(events.append),
    )
    return events


async def _run_ending_in_a_failed_task(
    task_list: TaskList, tasks_file: Path, *, gate_timeout: str = ""
) -> list[Event]:
    events: list[Event] = []
    with pytest.raises(RunError):
        await _run(task_list, tasks_file, gate_timeout=gate_timeout, events=events)

    return events


def _kinds(events: list[Event]) -> list[type]:
    return [type(event) for event in events]


def _of(events: list[Event], kind: type) -> list:
    return [event for event in events if isinstance(event, kind)]


def _lines(events: list[Event]) -> list[str]:
    return [event.line for event in events if isinstance(event, Activity)]


async def test_session_argv_and_stdin(record: Path, tmp_path: Path) -> None:
    await _run(_task_list(), tmp_path / "tasks.yaml")

    [call] = fake_claude_records(record)
    assert call["argv"] == [
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]
    assert call["stdin"] == "shared prompt\n\n" + Task(1, "First", "p1").text() + "\n"


async def test_session_argv_with_sandbox_args(record: Path, tmp_path: Path) -> None:
    session_args = ["--permission-mode", "acceptEdits", "--settings", '{"s":1}']

    await _run(_task_list(), tmp_path / "tasks.yaml", session_args=session_args)

    [call] = fake_claude_records(record)
    assert call["argv"] == [
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        *session_args,
    ]


async def test_success_reports_the_session(tmp_path: Path) -> None:
    events = await _run(_task_list(), tmp_path / "tasks.yaml")

    assert _kinds(events) == SESSION_KINDS
    assert events[1] == Activity(events[1].task, "working")
    assert events[1].task.id == 1
    assert events[3].task.state == COMPLETED
    assert events[3].error is None
    assert events[4].error is None


async def test_result_reporting_failed_fails_the_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", '{"state":"failed","error":"boom"}')

    events = await _run_ending_in_a_failed_task(_task_list(), tmp_path / "tasks.yaml")

    assert _kinds(events) == SESSION_KINDS
    assert events[3].task.state == FAILED
    assert events[3].task.error == "boom"


async def test_big_event_still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BIG_EVENT", "200000")

    events = await _run(_task_list(), tmp_path / "tasks.yaml")

    assert events[-2].task.state == COMPLETED


async def test_stderr_arrives_as_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_STDERR", "warn one\nwarn two\n")

    events = await _run(_task_list(), tmp_path / "tasks.yaml")

    assert sorted(_lines(events)) == ["warn one", "warn two", "working"]


async def test_cancel_leaves_the_tasks_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "ready"
    tasks_file = tmp_path / "tasks.yaml"
    tasks_yaml = "shared:\n  prompt: shared prompt\ntasks:\n  - {id: 1, name: First, prompt: p1, state: pending}\n"
    write_file(tasks_file, tasks_yaml)
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    monkeypatch.setenv("FAKECLAUDE_READY_FILE", str(ready))
    events: list[Event] = []
    options = loop.RunOptions(str(tasks_file), "", bypass_args())

    run = asyncio.ensure_future(
        loop.run(_task_list(), options, StreamFrontEnd(events.append))
    )
    await wait_for_text(ready)
    await cancel_run(run)

    assert TaskFinished not in _kinds(events)
    assert SessionFinished not in _kinds(events)
    assert tasks_file.read_text(encoding="utf-8") == tasks_yaml


async def test_writes_nothing_to_the_terminal(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    gate = Gate(cmd="echo out; echo err >&2")

    await _run(_task_list(gate), tmp_path / "tasks.yaml")

    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""


async def test_session_that_cannot_be_started_reports_why(tmp_path: Path) -> None:
    front_end = StreamFrontEnd([].append)

    result = await front_end.run_session(
        Task(1, "First", "p1"), [str(tmp_path / "missing")], ""
    )

    assert result.text == ""
    assert result.error is not None
    assert "missing" in result.error


async def test_two_tasks_report_in_order(tmp_path: Path) -> None:
    task_list = _task_list(names=("First", "Second"))

    events = await _run(task_list, tmp_path / "tasks.yaml")

    assert _kinds(events) == [*SESSION_KINDS[:-1], *SESSION_KINDS]
    assert [event.task.id for event in _of(events, TaskFinished)] == [1, 2]


async def test_failed_task_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "1")
    task_list = _task_list(names=("First", "Second"))

    events = await _run_ending_in_a_failed_task(task_list, tmp_path / "tasks.yaml")

    assert _kinds(events) == SESSION_KINDS
    assert events[3].task.state == FAILED
    assert events[3].error is not None
    assert events[4].error == events[3].error


async def test_skipped_completed_task_reports_only_the_end(tmp_path: Path) -> None:
    task_list = _task_list()
    task_list.tasks[0].state = COMPLETED

    events = await _run(task_list, tmp_path / "tasks.yaml")

    assert _kinds(events) == [RunDone]


async def test_session_finished_carries_the_session_outcome_and_durations(
    tmp_path: Path,
) -> None:
    events = await _run(_task_list(), tmp_path / "tasks.yaml")

    session, task, done = events[2], events[3], events[4]
    assert session.task.id == 1
    assert session.task.state == COMPLETED
    assert session.seconds > 0
    assert task.seconds >= session.seconds
    assert done.seconds >= task.seconds


async def test_session_finished_carries_a_failed_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", '{"state":"failed","error":"boom"}')

    events = await _run_ending_in_a_failed_task(_task_list(), tmp_path / "tasks.yaml")

    [session] = _of(events, SessionFinished)
    assert session.task.state == FAILED
    assert session.task.error == "boom"


async def test_gate_output_arrives_as_activity(tmp_path: Path) -> None:
    gate = Gate(cmd="echo from-stdout\necho from-stderr >&2")

    events = await _run(_task_list(gate), tmp_path / "tasks.yaml")

    lines = _lines(events)
    assert "→ gate echo from-stdout" in lines, "only the first line names the gate"
    assert "from-stdout" in lines
    assert "from-stderr" in lines
    assert _kinds(events)[-2:] == [TaskFinished, RunDone]


# printf leaves no newline, and the last line must still be reported.
async def test_gate_output_without_a_trailing_newline_arrives_as_activity(
    tmp_path: Path,
) -> None:
    events = await _run(_task_list(Gate(cmd="printf x")), tmp_path / "tasks.yaml")

    assert "x" in _lines(events)


async def test_gate_failure_reports_a_failed_task(tmp_path: Path) -> None:
    events = await _run_ending_in_a_failed_task(
        _task_list(Gate(cmd="exit 3")), tmp_path / "t.yaml"
    )

    [finished] = _of(events, TaskFinished)
    assert finished.task.state == FAILED
    assert finished.task.error == 'gate "exit 3" failed: exit status 3'
    assert finished.error is not None


async def test_gate_finished_reports_each_gate_that_ran(tmp_path: Path) -> None:
    gates = [Gate(cmd="true"), Gate(cmd="sleep 5", timeout="100ms"), Gate(cmd="true")]

    events = await _run_ending_in_a_failed_task(
        _task_list(*gates), tmp_path / "tasks.yaml"
    )

    passed, timed_out = _of(events, GateFinished)
    assert passed.task.id == 1
    assert passed.gate.cmd == "true"
    assert passed.error is None
    assert passed.timed_out is False
    assert passed.limit == "10m"
    assert timed_out.timed_out is True
    assert timed_out.limit == "100ms"
    assert timed_out.seconds >= 0.1


async def test_gate_finished_carries_a_non_zero_exit(tmp_path: Path) -> None:
    events = await _run_ending_in_a_failed_task(
        _task_list(Gate(cmd="exit 3")), tmp_path / "tasks.yaml", gate_timeout="30s"
    )

    [finished] = _of(events, GateFinished)
    assert finished.error == "exit status 3"
    assert finished.timed_out is False
    assert finished.limit == "30s", "the flag is the limit that applied"


async def test_cancel_during_a_gate_reports_no_gate_finished(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    gate = Gate(cmd=f"echo ready > '{ready}'; sleep 60")
    events: list[Event] = []
    options = loop.RunOptions(str(tmp_path / "tasks.yaml"), "", bypass_args())

    run = asyncio.ensure_future(
        loop.run(_task_list(gate), options, StreamFrontEnd(events.append))
    )
    await wait_for_text(ready)
    await cancel_run(run)

    assert _of(events, GateFinished) == []


async def test_commit_arrives_as_activity_and_carries_the_new_hash(
    git_repo: Path,
) -> None:
    tasks_file = git_repo / "run" / "tasks.yaml"
    repo = await open_repo(str(tasks_file))

    events = await _run(
        _task_list(Gate(cmd="echo work > one.txt")), tasks_file, repo=repo
    )

    assert "→ commit First" in _lines(events)
    assert "1 file changed" in "\n".join(_lines(events))
    assert _kinds(events)[-3:] == [Committed, TaskFinished, RunDone]
    [committed] = _of(events, Committed)
    assert committed.commit_hash == git(git_repo, "rev-parse", "HEAD").strip()
    assert committed.task.id == 1


async def test_nothing_staged_reports_no_committed(git_repo: Path) -> None:
    tasks_file = git_repo / "run" / "tasks.yaml"
    repo = await open_repo(str(tasks_file))

    events = await _run(_task_list(), tasks_file, repo=repo)

    assert _of(events, Committed) == []
    assert "→ commit First" not in _lines(events)


async def test_gate_started_is_reported_once_after_its_marker_and_before_it_finishes(
    tmp_path: Path,
) -> None:
    gate = Gate(cmd="echo hi")

    events = await _run(_task_list(gate), tmp_path / "tasks.yaml")

    [started] = _of(events, GateStarted)
    assert started.gate == gate
    assert started.task.id == 1
    marker = events.index(Activity(started.task, "→ gate echo hi"))
    assert events.index(started) == marker + 1
    assert events.index(started) < events.index(_of(events, GateFinished)[0])


async def test_commit_started_is_reported_once_after_its_marker_and_before_committed(
    git_repo: Path,
) -> None:
    tasks_file = git_repo / "run" / "tasks.yaml"
    repo = await open_repo(str(tasks_file))

    events = await _run(
        _task_list(Gate(cmd="echo work > one.txt")), tasks_file, repo=repo
    )

    [started] = _of(events, CommitStarted)
    assert started.task.id == 1
    marker = events.index(Activity(started.task, "→ commit First"))
    assert events.index(started) == marker + 1
    assert events.index(started) < events.index(_of(events, Committed)[0])


async def test_a_gate_that_exits_while_a_background_process_holds_its_pipes_returns_after_the_grace_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)
    pid_file = tmp_path / "holder.pid"
    gate = Gate(cmd=f"sleep 30 & echo $! > {pid_file}; echo hi")
    started = time.monotonic()

    try:
        events = await asyncio.wait_for(
            _run(_task_list(gate), tmp_path / "tasks.yaml"), 10
        )
        elapsed = time.monotonic() - started
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    assert "hi" in _lines(events)
    assert elapsed < 5
    [finished] = _of(events, GateFinished)
    assert finished.error is None


async def test_a_gate_that_exited_in_time_does_not_time_out_on_an_escaped_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)
    pid_file = tmp_path / "escaped.pid"
    # setsid takes the sleeper out of the group a time limit would stop.
    gate = Gate(cmd=f"setsid sh -c 'echo $$ > {pid_file}; exec sleep 30' & echo hi")
    events: list[Event] = []

    try:
        await asyncio.wait_for(
            _run(
                _task_list(gate),
                tmp_path / "tasks.yaml",
                gate_timeout="1s",
                events=events,
            ),
            10,
        )
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    [finished] = _of(events, GateFinished)
    assert finished.timed_out is False
    assert finished.error is None
    assert "hi" in _lines(events)
