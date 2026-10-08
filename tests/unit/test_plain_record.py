"""The plain front end reports run events to an optional recorder, and prints what it always did."""

import asyncio
import sys
import time
from pathlib import Path

import pytest
from helpers import git, kill_holder_and_let_pipes_close, run_loop, wait_for_text

from alr.engine import proc
from alr.engine.commit import open_repo
from alr.errors import RunError
from alr.plain.frontend import _READ_SIZE, PlainFrontEnd, _tee
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
from alr.tasks import Gate, Shared, Task, TaskList

pytestmark = pytest.mark.usefixtures("fake_claude")

RESULT = '{"state":"completed","error":""}'


def _task_list(*task_gates: Gate) -> TaskList:
    return TaskList(
        shared=Shared(prompt="shared prompt", gates=list(task_gates)),
        tasks=[Task(id=1, name="First", prompt="p1")],
    )


async def _run(
    tasks_file: Path, *task_gates: Gate, repo_wanted: bool = False
) -> list[Event]:
    events: list[Event] = []
    repo = await open_repo(str(tasks_file)) if repo_wanted else None
    await run_loop(
        _task_list(*task_gates),
        tasks_file,
        repo=repo,
        front_end=PlainFrontEnd(events.append),
    )
    return events


def _lines(events: list[Event]) -> list[str]:
    return [event.line for event in events if isinstance(event, Activity)]


async def test_each_step_records_its_event(tmp_path: Path) -> None:
    gate = Gate(cmd="true")

    events = await _run(tmp_path / "tasks.yaml", gate)

    kinds = [type(event) for event in events if not isinstance(event, Activity)]
    assert kinds == [
        TaskStarted,
        SessionFinished,
        GateStarted,
        GateFinished,
        TaskFinished,
        RunDone,
    ]
    started = events[0]
    assert isinstance(started, TaskStarted)
    assert started.task.id == 1
    [finished] = [e for e in events if isinstance(e, GateFinished)]
    assert finished.gate == gate
    assert finished.error is None
    [done] = [e for e in events if isinstance(e, RunDone)]
    assert done.error is None


async def test_gate_started_is_reported_once_after_its_marker_and_before_it_finishes(
    tmp_path: Path,
) -> None:
    gate = Gate(cmd="true")

    events = await _run(tmp_path / "tasks.yaml", gate)

    [started] = [e for e in events if isinstance(e, GateStarted)]
    assert started.gate == gate
    marker = events.index(Activity(started.task, "→ gate true"))
    assert events.index(started) == marker + 1
    [finished] = [e for e in events if isinstance(e, GateFinished)]
    assert events.index(started) < events.index(finished)


async def test_commit_started_is_reported_once_after_its_marker_and_before_committed(
    git_repo: Path,
) -> None:
    tasks_file = git_repo / "run" / "tasks.yaml"

    events = await _run(tasks_file, Gate(cmd="echo work > one.txt"), repo_wanted=True)

    [started] = [e for e in events if isinstance(e, CommitStarted)]
    marker = events.index(Activity(started.task, "→ commit First"))
    assert events.index(started) == marker + 1
    [committed] = [e for e in events if isinstance(e, Committed)]
    assert events.index(started) < events.index(committed)


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


async def test_the_gate_marker_names_only_the_first_line(tmp_path: Path) -> None:
    events = await _run(tmp_path / "tasks.yaml", Gate(cmd="echo hi\necho there"))

    assert "→ gate echo hi" in _lines(events)


async def test_gate_stdout_and_stderr_reach_the_terminal_and_the_record(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    gate = Gate(cmd="echo from-out; echo from-err >&2")

    events = await _run(tmp_path / "tasks.yaml", gate)

    captured = capfd.readouterr()
    assert "from-out" in captured.out
    assert "from-err" in captured.err
    assert "from-err\n" not in captured.out
    assert "from-out\n" not in captured.err
    lines = _lines(events)
    marker = lines.index("→ gate echo from-out; echo from-err >&2")
    assert sorted(lines[marker + 1 : marker + 3]) == ["from-err", "from-out"]


async def test_gate_lines_are_recorded_between_the_marker_and_gate_finished(
    tmp_path: Path,
) -> None:
    events = await _run(tmp_path / "tasks.yaml", Gate(cmd="echo one; echo two"))

    [finished] = [e for e in events if isinstance(e, GateFinished)]
    marker = events.index(Activity(finished.task, "→ gate echo one; echo two"))
    one, two = Activity(finished.task, "one"), Activity(finished.task, "two")
    assert marker < events.index(one) < events.index(two) < events.index(finished)


async def test_gate_last_line_without_a_newline_is_recorded(tmp_path: Path) -> None:
    events = await _run(tmp_path / "tasks.yaml", Gate(cmd="printf 'a\\r\\nb'"))

    lines = _lines(events)
    assert lines[lines.index("a") + 1] == "b"


async def test_failing_gate_still_records_its_output_and_returns_the_exit_text(
    tmp_path: Path,
) -> None:
    events: list[Event] = []

    with pytest.raises(RunError):
        await run_loop(
            _task_list(Gate(cmd="echo boom >&2; exit 3")),
            tmp_path / "tasks.yaml",
            front_end=PlainFrontEnd(events.append),
        )

    [finished] = [e for e in events if isinstance(e, GateFinished)]
    assert "boom" in _lines(events)
    assert finished.error == "exit status 3"


async def test_gate_stderr_is_decoded_leniently(tmp_path: Path) -> None:
    events = await _run(tmp_path / "tasks.yaml", Gate(cmd="printf 'bad\\377\\n' >&2"))

    assert "bad\ufffd" in _lines(events)


def _install_hook(repo: Path, script: str) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    hook.chmod(0o700)


async def test_commit_marker_and_hash_are_recorded(git_repo: Path) -> None:
    tasks_file = git_repo / "run" / "tasks.yaml"

    events = await _run(tasks_file, Gate(cmd="echo work > one.txt"), repo_wanted=True)

    assert "→ commit First" in _lines(events)
    [committed] = [e for e in events if isinstance(e, Committed)]
    assert committed.commit_hash == git(git_repo, "rev-parse", "HEAD").strip()


async def test_git_output_reaches_the_terminal_and_the_record_after_the_commit_marker(
    git_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _install_hook(git_repo, "echo hook-out; echo hook-err >&2")
    tasks_file = git_repo / "run" / "tasks.yaml"

    events = await _run(tasks_file, Gate(cmd="echo work > one.txt"), repo_wanted=True)

    captured = capfd.readouterr()
    # git itself sends a hook's stdout to stderr; the commit summary is stdout.
    assert "hook-out\nhook-err\n" in captured.err
    assert "hook-err" not in captured.out
    assert "file changed" in captured.out
    assert "file changed" not in captured.err
    lines = _lines(events)
    after_marker = lines[lines.index("→ commit First") + 1 :]
    assert {"hook-out", "hook-err"} <= set(after_marker)
    assert any("file changed" in line for line in after_marker)


async def test_failing_git_command_records_its_output_and_returns_the_exit_text(
    git_repo: Path,
) -> None:
    _install_hook(git_repo, "echo hook-refused >&2; exit 1")
    tasks_file = git_repo / "run" / "tasks.yaml"
    events: list[Event] = []

    with pytest.raises(RunError):
        await run_loop(
            _task_list(Gate(cmd="echo work > one.txt")),
            tasks_file,
            repo=await open_repo(str(tasks_file)),
            front_end=PlainFrontEnd(events.append),
        )

    lines = _lines(events)
    assert "hook-refused" in lines[lines.index("→ commit First") + 1 :]
    [finished] = [e for e in events if isinstance(e, TaskFinished)]
    assert finished.error == "task 1: First failed: commit failed: exit status 1"


async def test_cancel_during_git_stops_the_group_gently(tmp_path: Path) -> None:
    ready, stopped = tmp_path / "ready", tmp_path / "stopped"
    script = f"trap 'echo bye > {stopped}; exit 0' TERM; echo ready > {ready}; sleep 60 & wait"
    task = Task(id=1, name="First", prompt="p1")
    run = asyncio.ensure_future(
        PlainFrontEnd().run_git(task, ["sh", "-c", script], str(tmp_path))
    )
    await wait_for_text(ready)

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert stopped.read_text(encoding="utf-8") == "bye\n", (
        "SIGTERM must reach the child before any SIGKILL"
    )


async def test_no_recorder_prints_the_same_output(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", f"one\n{RESULT}")
    gate = Gate(cmd="echo from-gate")

    await run_loop(_task_list(gate), tmp_path / "a.yaml", front_end=PlainFrontEnd())
    plain = capfd.readouterr().out
    await _run(tmp_path / "b.yaml", gate)
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


async def test_a_gate_that_exits_while_a_background_process_holds_its_pipes_returns_after_the_grace_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)
    pid_file = tmp_path / "holder.pid"
    gate = Gate(cmd=f"sleep 30 & echo $! > {pid_file}; echo hi")
    started = time.monotonic()

    try:
        events = await asyncio.wait_for(_run(tmp_path / "tasks.yaml", gate), 10)
        elapsed = time.monotonic() - started
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    assert "hi" in _lines(events)
    assert elapsed < 5
    [finished] = [e for e in events if isinstance(e, GateFinished)]
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
            run_loop(
                _task_list(gate),
                tmp_path / "tasks.yaml",
                gate_timeout="1s",
                front_end=PlainFrontEnd(events.append),
            ),
            10,
        )
    finally:
        await kill_holder_and_let_pipes_close(pid_file)

    [finished] = [e for e in events if isinstance(e, GateFinished)]
    assert finished.timed_out is False
    assert finished.error is None
    assert "hi" in _lines(events)


async def test_a_gate_writing_a_lot_to_both_streams_does_not_block(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    # Far more than a pipe holds, on stderr first while stdout is still open:
    # reading the streams one after the other would leave the gate stuck.
    gate = Gate(
        cmd="head -c 1000000 /dev/zero | tr '\\0' a >&2; "
        "head -c 1000000 /dev/zero | tr '\\0' b"
    )

    events = await asyncio.wait_for(_run(tmp_path / "tasks.yaml", gate), 10)

    captured = capfd.readouterr()
    assert captured.err == "a" * 1_000_000
    assert "b" * 1_000_000 in captured.out
    lines = _lines(events)
    assert "a" * 1_000_000 in lines
    assert "b" * 1_000_000 in lines


async def test_a_gate_reads_end_of_file_from_stdin(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    gate = Gate(cmd="read x; echo got=$x")

    events = await asyncio.wait_for(_run(tmp_path / "tasks.yaml", gate), 10)

    assert "got=\n" in capfd.readouterr().out
    assert "got=" in _lines(events)


async def test_a_git_command_reads_end_of_file_from_stdin(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    task = Task(id=1, name="First", prompt="p1")

    # cat stands in for git: it ends only when stdin does.
    error = await asyncio.wait_for(
        PlainFrontEnd().run_git(task, ["sh", "-c", "cat; echo done"], str(tmp_path)),
        10,
    )

    assert error is None
    assert capfd.readouterr().out == "done\n"
