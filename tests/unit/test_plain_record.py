"""The plain front end reports run events to an optional recorder, and prints what it always did."""

import asyncio
from pathlib import Path

import pytest
from helpers import git, run_loop

from alr.engine.commit import open_repo
from alr.plain.frontend import _READ_SIZE, PlainFrontEnd, _tee
from alr.record.events import (
    Activity,
    Committed,
    Event,
    GateFinished,
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
    assert kinds == [TaskStarted, SessionFinished, GateFinished, TaskFinished, RunDone]
    started = events[0]
    assert isinstance(started, TaskStarted)
    assert started.task.id == 1
    [finished] = [e for e in events if isinstance(e, GateFinished)]
    assert finished.gate == gate
    assert finished.error is None
    [done] = [e for e in events if isinstance(e, RunDone)]
    assert done.error is None


async def test_claude_stdout_lines_arrive_in_order_with_the_last_unterminated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", f"one\r\ntwo\n{RESULT}")

    events = await _run(tmp_path / "tasks.yaml")

    assert _lines(events) == ["one", "two", RESULT]


async def test_the_gate_marker_names_only_the_first_line(tmp_path: Path) -> None:
    events = await _run(tmp_path / "tasks.yaml", Gate(cmd="echo hi\necho there"))

    assert "→ gate echo hi" in _lines(events)


async def test_gate_output_is_not_recorded(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    events = await _run(tmp_path / "tasks.yaml", Gate(cmd="echo from-gate"))

    assert "from-gate" not in _lines(events)
    assert "from-gate" in capfd.readouterr().out


async def test_commit_marker_and_hash_are_recorded_but_git_output_is_not(
    git_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    tasks_file = git_repo / "run" / "tasks.yaml"

    events = await _run(tasks_file, Gate(cmd="echo work > one.txt"), repo_wanted=True)

    assert "→ commit First" in _lines(events)
    assert not any("file changed" in line for line in _lines(events))
    assert "file changed" in capfd.readouterr().out
    [committed] = [e for e in events if isinstance(e, Committed)]
    assert committed.commit_hash == git(git_repo, "rev-parse", "HEAD").strip()


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

    await _tee(reader, chunks, recorder)

    assert events == [Activity(task, line.decode("utf-8", errors="replace"))]
    assert "\ufffd" not in events[0].line[:-1]
    assert chunks[0][-1:] == b"\xc3"
    assert capsysbinary.readouterr().out == line
    assert b"".join(chunks) == line
