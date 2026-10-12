"""The plain front end reports run events to an optional recorder, and prints what it always did."""

import asyncio
import sys
import time
from pathlib import Path

import pytest
from helpers import (
    kill_holder_and_let_pipes_close,
    run_loop,
)

from alr.engine import proc
from alr.errors import RunError
from alr.plain.frontend import _READ_SIZE, PlainFrontEnd, _tee
from alr.record.events import (
    Activity,
    Event,
    ActionFinished,
    ActionStarted,
    RunDone,
    SessionFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import Action, RunResult, Shared, Task, TaskList

pytestmark = pytest.mark.usefixtures("fake_claude")

RESULT = '{"state":"completed","error":""}'


def _task_list(*task_actions: Action) -> TaskList:
    return TaskList(
        shared=Shared(prompt="shared prompt", after_task=list(task_actions)),
        tasks=[Task(id=1, name="First", prompt="p1")],
    )


async def _run(tasks_file: Path, *task_actions: Action) -> list[Event]:
    events: list[Event] = []
    await run_loop(
        _task_list(*task_actions),
        tasks_file,
        front_end=PlainFrontEnd(events.append),
    )
    return events


def _lines(events: list[Event]) -> list[str]:
    return [event.line for event in events if isinstance(event, Activity)]


async def test_each_step_records_its_event(tmp_path: Path) -> None:
    action = Action(command="true")

    events = await _run(tmp_path / "tasks.yaml", action)

    kinds = [type(event) for event in events if not isinstance(event, Activity)]
    assert kinds == [
        TaskStarted,
        SessionFinished,
        ActionStarted,
        ActionFinished,
        TaskFinished,
        RunDone,
    ]
    started = events[0]
    assert isinstance(started, TaskStarted)
    assert started.task.id == 1
    [finished] = [e for e in events if isinstance(e, ActionFinished)]
    assert finished.action == action
    assert finished.error is None
    [done] = [e for e in events if isinstance(e, RunDone)]
    assert done.error is None
    assert (done.result, done.exit_code) == (RunResult.COMPLETED, 0)


async def test_action_started_is_reported_once_after_its_marker_and_before_it_finishes(
    tmp_path: Path,
) -> None:
    action = Action(command="true")

    events = await _run(tmp_path / "tasks.yaml", action)

    [started] = [e for e in events if isinstance(e, ActionStarted)]
    assert started.action == action
    marker = events.index(Activity(started.task, "→ action true"))
    assert events.index(started) == marker + 1
    [finished] = [e for e in events if isinstance(e, ActionFinished)]
    assert events.index(started) < events.index(finished)


async def test_claude_stdout_lines_arrive_in_order_with_the_last_unterminated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", f"one\r\ntwo\n{RESULT}")

    events = await _run(tmp_path / "tasks.yaml")

    assert _lines(events) == ["one", "two", RESULT]


async def test_claude_stderr_reaches_the_terminal_and_the_record_not_the_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKECLAUDE_STDERR", "warn one\r\nwarn two")

    events = await _run(tmp_path / "tasks.yaml")

    captured = capfd.readouterr()
    assert "warn one\r\nwarn two" in captured.err
    assert "warn" not in captured.out
    lines = _lines(events)
    assert lines[-2:] == ["warn one", "warn two"]
    assert RESULT in lines


async def test_claude_stderr_stays_out_of_the_session_result(
    fake_claude: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKECLAUDE_STDERR", "noise\n")
    task = Task(id=1, name="First", prompt="p1")

    result = await PlainFrontEnd().run_session(
        task, [str(fake_claude / "claude")], "prompt"
    )

    assert result.error is None
    assert result.text == RESULT + "\n"
    assert "noise" in capfd.readouterr().err


async def test_a_session_writing_a_lot_to_both_streams_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Far more than a pipe plus the stream buffer holds; the fake writes stdout first, then stderr.
    big = "x" * 100_000
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", f"{big}\n{RESULT}")
    monkeypatch.setenv("FAKECLAUDE_STDERR_BYTES", str(1_000_000))

    events = await asyncio.wait_for(_run(tmp_path / "tasks.yaml"), timeout=30)

    lines = _lines(events)
    assert lines.count("e" * 99) == 10_000
    assert big in lines
    assert RESULT in lines


async def test_the_action_marker_names_only_the_first_line(tmp_path: Path) -> None:
    events = await _run(tmp_path / "tasks.yaml", Action(command="echo hi\necho there"))

    assert "→ action echo hi" in _lines(events)


async def test_action_stdout_and_stderr_reach_the_terminal_and_the_record(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    action = Action(command="echo from-out; echo from-err >&2")

    events = await _run(tmp_path / "tasks.yaml", action)

    captured = capfd.readouterr()
    assert "from-out" in captured.out
    assert "from-err" in captured.err
    assert "from-err\n" not in captured.out
    assert "from-out\n" not in captured.err
    lines = _lines(events)
    marker = lines.index("→ action echo from-out; echo from-err >&2")
    assert sorted(lines[marker + 1 : marker + 3]) == ["from-err", "from-out"]


async def test_action_lines_are_recorded_between_the_marker_and_action_finished(
    tmp_path: Path,
) -> None:
    events = await _run(tmp_path / "tasks.yaml", Action(command="echo one; echo two"))

    [finished] = [e for e in events if isinstance(e, ActionFinished)]
    marker = events.index(Activity(finished.task, "→ action echo one; echo two"))
    one, two = Activity(finished.task, "one"), Activity(finished.task, "two")
    assert marker < events.index(one) < events.index(two) < events.index(finished)


async def test_action_last_line_without_a_newline_is_recorded(tmp_path: Path) -> None:
    events = await _run(tmp_path / "tasks.yaml", Action(command="printf 'a\\r\\nb'"))

    lines = _lines(events)
    assert lines[lines.index("a") + 1] == "b"


async def test_failing_action_still_records_its_output_and_returns_the_exit_text(
    tmp_path: Path,
) -> None:
    events: list[Event] = []

    with pytest.raises(RunError):
        await run_loop(
            _task_list(Action(command="echo boom >&2; exit 3")),
            tmp_path / "tasks.yaml",
            front_end=PlainFrontEnd(events.append),
        )

    [finished] = [e for e in events if isinstance(e, ActionFinished)]
    assert "boom" in _lines(events)
    assert finished.error == "exit status 3"


async def test_action_stderr_is_decoded_leniently(tmp_path: Path) -> None:
    events = await _run(
        tmp_path / "tasks.yaml", Action(command="printf 'bad\\377\\n' >&2")
    )

    assert "bad\ufffd" in _lines(events)


async def test_no_recorder_prints_the_same_output(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", f"one\n{RESULT}")
    action = Action(command="echo from-action")

    await run_loop(_task_list(action), tmp_path / "a.yaml", front_end=PlainFrontEnd())
    plain = capfd.readouterr().out
    await _run(tmp_path / "b.yaml", action)
    recorded = capfd.readouterr().out

    assert recorded == plain


async def test_one_huge_line_without_a_newline_is_one_activity_and_unchanged_on_the_terminal(
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    # "é" is two bytes; starting it on the last byte of the first read splits it across chunks.
    head = b"x" * (_READ_SIZE - 1) + "é".encode()
    line = head + b"x" * (8 * 1024 * 1024 - len(head) - 1) + b"\xff"
    reader = asyncio.StreamReader(limit=len(line) * 2)
    reader.feed_data(line)
    reader.feed_eof()
    chunks: list[bytes] = []
    events: list[Event] = []
    task = Task(id=1, name="First", prompt="p1")
    recorder = PlainFrontEnd(events.append)._raw_line(task)

    await _tee(reader, sys.stdout.buffer, recorder, chunks)

    assert events == [Activity(task, line.decode("utf-8", errors="replace"))]
    assert "\ufffd" not in events[0].line[:-1]
    assert chunks[0][-1:] == b"\xc3"
    assert capsysbinary.readouterr().out == line
    assert b"".join(chunks) == line


async def test_a_action_that_exits_while_a_background_process_holds_its_pipes_returns_after_the_grace_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_DRAIN_GRACE", 0.2)
    pid_file = tmp_path / "holder.pid"
    action = Action(command=f"sleep 30 & echo $! > {pid_file}; echo hi")
    started = time.monotonic()

    try:
        events = await asyncio.wait_for(_run(tmp_path / "tasks.yaml", action), 10)
        elapsed = time.monotonic() - started
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    assert "hi" in _lines(events)
    assert elapsed < 5
    [finished] = [e for e in events if isinstance(e, ActionFinished)]
    assert finished.error is None


async def test_a_action_that_exited_in_time_does_not_time_out_on_an_escaped_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_DRAIN_GRACE", 0.2)
    pid_file = tmp_path / "escaped.pid"
    # setsid takes the sleeper out of the group a time limit would stop.
    action = Action(
        command=f"setsid sh -c 'echo $$ > {pid_file}; exec sleep 30' & echo hi"
    )
    events: list[Event] = []

    try:
        await asyncio.wait_for(
            run_loop(
                _task_list(action),
                tmp_path / "tasks.yaml",
                action_timeout="1s",
                front_end=PlainFrontEnd(events.append),
            ),
            10,
        )
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    [finished] = [e for e in events if isinstance(e, ActionFinished)]
    assert finished.timed_out is False
    assert finished.error is None
    assert "hi" in _lines(events)


async def test_a_action_writing_a_lot_to_both_streams_does_not_block(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    # Far more than a pipe holds, on stderr first while stdout is still open:
    # reading the streams one after the other would leave the action stuck.
    action = Action(
        command="head -c 1000000 /dev/zero | tr '\\0' a >&2; "
        "head -c 1000000 /dev/zero | tr '\\0' b"
    )

    events = await asyncio.wait_for(_run(tmp_path / "tasks.yaml", action), 10)

    captured = capfd.readouterr()
    assert captured.err == "a" * 1_000_000
    assert "b" * 1_000_000 in captured.out
    lines = _lines(events)
    assert "a" * 1_000_000 in lines
    assert "b" * 1_000_000 in lines


async def test_a_action_reads_end_of_file_from_stdin(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    action = Action(command="read x; echo got=$x")

    events = await asyncio.wait_for(_run(tmp_path / "tasks.yaml", action), 10)

    assert "got=\n" in capfd.readouterr().out
    assert "got=" in _lines(events)
