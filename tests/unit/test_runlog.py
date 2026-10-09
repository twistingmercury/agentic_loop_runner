"""The run log: a ledger line per event and a detail file per task."""

import errno
import json
import os
import stat
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alr.errors import RefusedError, RunLogError
from alr.record import runlog
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
from alr.record.runlog import Info, RunLog, run_dir
from alr.tasks import COMPLETED, FAILED, Gate, RunResult, Task

STAMP = '"time":"2026-10-01T14:02:11-04:00"'

# A zone that is not UTC, so the tests show the times written are the clock's
# own local time.
EDT = timezone(timedelta(hours=-4), "EDT")

INFO = Info(
    version="v0.10.0",
    tasks_file="run/tasks.yaml",
    permissions="sandbox",
    sandbox_settings="run/sandbox.json",
    commit="task",
)


def fixed_now() -> datetime:
    return datetime(2026, 10, 1, 14, 2, 11, tzinfo=EDT)


@pytest.fixture
def opened(tmp_path: Path) -> Iterator[tuple[RunLog, Path]]:
    path = tmp_path / "logs" / "20261001T140211"
    log = RunLog.open(path, INFO, now=fixed_now)
    yield log, path
    log.close()


def record(log: RunLog, *events: Event) -> None:
    for event in events:
        log.record(event)


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").removesuffix("\n").split("\n")


def ledger_lines(path: Path) -> list[str]:
    return read_lines(path / "run.jsonl")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_run_dir_is_named_by_local_start_time() -> None:
    assert run_dir(Path("logs"), fixed_now()) == Path("logs") / "20261001T140211"


def test_open_creates_private_folder_and_run_started(
    opened: tuple[RunLog, Path],
) -> None:
    _, path = opened

    assert mode(path.parent) == 0o700
    assert mode(path) == 0o700
    assert mode(path / "run.jsonl") == 0o600
    want = (
        "{" + STAMP + ',"event":"run_started","version":"v0.10.0",'
        '"tasks_file":"run/tasks.yaml","permissions":"sandbox",'
        '"sandbox_settings":"run/sandbox.json","commit":"task"}'
    )
    lines = ledger_lines(path)
    assert lines == [want]
    assert "prompt_file" not in lines[0]


def test_open_leaves_out_what_was_not_passed(tmp_path: Path) -> None:
    path = tmp_path / "run"
    info = Info(
        version="dev",
        tasks_file="t.yaml",
        permissions="skip",
        gate_timeout="90s",
    )
    RunLog.open(path, info, now=fixed_now).close()

    want = (
        "{" + STAMP + ',"event":"run_started","version":"dev",'
        '"tasks_file":"t.yaml","permissions":"skip","gate_timeout":"90s",'
        '"commit":"none"}'
    )
    assert ledger_lines(path) == [want]


@pytest.mark.parametrize("mode", ["none", "task", "run"])
def test_open_writes_the_commit_mode_as_a_string(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "run"

    RunLog.open(path, replace(INFO, commit=mode), now=fixed_now).close()

    assert json.loads(ledger_lines(path)[0])["commit"] == mode


@pytest.mark.parametrize(
    ("result", "exit_code"),
    [
        (RunResult.COMPLETED, 0),
        (RunResult.FAILED, 1),
        (RunResult.COMMIT_FAILED, 4),
        (RunResult.STOPPED, 143),
    ],
)
def test_run_finished_records_the_result_and_the_exit(
    opened: tuple[RunLog, Path], result: RunResult, exit_code: int
) -> None:
    log, path = opened
    error = None if result is RunResult.COMPLETED else "why"

    log.record(RunDone(error, 1.0, result, exit_code))

    line = json.loads(ledger_lines(path)[-1])
    assert line["event"] == "run_finished"
    assert line["result"] == result.value
    assert line["exit"] == exit_code
    assert line.get("error") == error


def test_time_with_a_zero_offset_is_written_as_z(tmp_path: Path) -> None:
    path = tmp_path / "run"

    def utc_now() -> datetime:
        return datetime(2026, 10, 1, 18, 2, 11, tzinfo=timezone.utc)

    RunLog.open(path, INFO, now=utc_now).close()

    assert ledger_lines(path)[0].startswith('{"time":"2026-10-01T18:02:11Z",')


# Two runs started in the same second get the same folder name; the second
# takes a suffixed folder and never writes into the first one's record.
def test_open_existing_run_folder_gets_a_suffixed_folder(
    opened: tuple[RunLog, Path],
) -> None:
    log, path = opened
    before = ledger_lines(path)

    second = RunLog.open(path, INFO, now=fixed_now)
    third = RunLog.open(path, INFO, now=fixed_now)
    second.close()
    third.close()

    assert ledger_lines(path) == before, "the first run's folder is untouched"
    assert len(ledger_lines(path.with_name(path.name + "-1"))) == 1
    assert len(ledger_lines(path.with_name(path.name + "-2"))) == 1
    assert sorted(p.name for p in path.parent.iterdir()) == [
        path.name,
        path.name + "-1",
        path.name + "-2",
    ]


def test_open_unwritable_parent_names_the_run_log_folder(tmp_path: Path) -> None:
    # A file where a folder should be fails the same way for every user, root
    # included, which a read-only folder does not.
    blocker = tmp_path / "logs"
    blocker.write_text("a file, not a folder", encoding="utf-8")
    path = blocker / "nested" / "20261001T140211"

    with pytest.raises(RefusedError) as raised:
        RunLog.open(path, INFO, now=fixed_now)

    assert raised.value.reason == "log-unwritable"
    assert str(raised.value) == (
        "refused: log-unwritable: "
        f"cannot create the run log folder {path}: Not a directory"
    )


def test_open_log_dir_under_a_file_is_an_error(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"")

    with pytest.raises(RefusedError) as raised:
        RunLog.open(file / "logs" / "run", INFO, now=fixed_now)

    assert raised.value.reason == "log-unwritable"


def test_record_completed_run(opened: tuple[RunLog, Path]) -> None:
    log, path = opened
    task = Task(id=1, name="First", prompt="p1")
    done = replace(task, state=COMPLETED)

    record(
        log,
        TaskStarted(task),
        Activity(task, "working"),
        SessionFinished(done, 90.0),
        Activity(task, "→ gate go test ./..."),
        GateFinished(task, Gate(cmd="go test ./..."), "10m", None, False, 41.2),
        Activity(task, "→ commit First"),
        Committed(task, "3f2a9c1"),
        TaskFinished(done, None, 132.0),
        RunDone(None, 133.0),
    )

    want = [
        "{" + STAMP + ',"event":"task_started","task":1,"name":"First"}',
        "{" + STAMP + ',"event":"session_finished","task":1,'
        '"state":"completed","duration":"1m30s"}',
        "{" + STAMP + ',"event":"gate_finished","task":1,"cmd":"go test ./...",'
        '"result":"passed","timeout":"10m","duration":"41.2s"}',
        "{" + STAMP + ',"event":"committed","task":1,"hash":"3f2a9c1"}',
        "{" + STAMP + ',"event":"task_finished","task":1,'
        '"state":"completed","duration":"2m12s"}',
        "{" + STAMP + ',"event":"run_finished","result":"completed",'
        '"exit":0,"duration":"2m13s"}',
    ]
    assert ledger_lines(path)[1:] == want

    detail = path / "task-1.log"
    assert mode(detail) == 0o600
    assert read_lines(detail) == [
        "14:02:11 working",
        "14:02:11 → gate go test ./...",
        "14:02:11 → commit First",
    ]


def test_record_start_events_add_nothing_to_the_log(
    opened: tuple[RunLog, Path],
) -> None:
    log, path = opened
    task = Task(id=1, name="First", prompt="p1")
    before = ledger_lines(path)

    record(log, GateStarted(task, Gate(cmd="check")), CommitStarted(task))

    assert ledger_lines(path) == before
    assert not (path / "task-1.log").exists()


@pytest.mark.parametrize(
    ("error", "timed_out", "want"),
    [
        (None, False, "passed"),
        ("exit status 3", False, "failed"),
        (None, True, "timed_out"),
    ],
)
def test_record_gate_results(
    opened: tuple[RunLog, Path], error: str | None, timed_out: bool, want: str
) -> None:
    log, path = opened
    task = Task(id=7, name="Seventh", prompt="")

    record(log, GateFinished(task, Gate(cmd="check"), "90s", error, timed_out, 0.0))

    assert ledger_lines(path)[1] == (
        "{" + STAMP + ',"event":"gate_finished","task":7,"cmd":"check",'
        f'"result":"{want}","timeout":"90s","duration":"0s"}}'
    )


# Shell syntax and quotes must survive as people wrote them, and only a
# multi-line command's first line is kept.
def test_record_keeps_shell_syntax_readable(opened: tuple[RunLog, Path]) -> None:
    log, path = opened
    task = Task(id=1, name='Say "hi" & <go> é', prompt="")
    gate = Gate(cmd='test -z "$(gofmt -l .)" && echo ok > out.txt\necho second line')

    record(log, TaskStarted(task), GateFinished(task, gate, "10m", None, False, 0.0))

    lines = ledger_lines(path)
    assert '"name":"Say \\"hi\\" & <go> é"' in lines[1]
    assert '"cmd":"test -z \\"$(gofmt -l .)\\" && echo ok > out.txt"' in lines[2]
    for line in lines:
        json.loads(line)


def test_record_failed_task_ends_the_run_as_failed(
    opened: tuple[RunLog, Path],
) -> None:
    log, path = opened
    task = Task(id=2, name="Second", prompt="")
    failed = replace(task, state=FAILED, error='gate "exit 3" failed: exit status 3')
    run_error = 'task 2: Second failed: gate "exit 3" failed: exit status 3'

    record(
        log,
        TaskStarted(task),
        TaskFinished(failed, run_error, 1.0),
        RunDone(run_error, 1.0, RunResult.FAILED, 1),
    )

    lines = ledger_lines(path)
    assert lines[2] == (
        "{" + STAMP + ',"event":"task_finished","task":2,"state":"failed",'
        '"error":"gate \\"exit 3\\" failed: exit status 3","duration":"1s"}'
    )
    assert lines[3] == (
        "{" + STAMP + ',"event":"run_finished","result":"failed",'
        '"error":"task 2: Second failed: gate \\"exit 3\\" failed: exit status 3",'
        '"exit":1,"duration":"1s"}'
    )


def _stopped(error: str, seconds: str) -> str:
    return (
        "{" + STAMP + ',"event":"run_finished","result":"stopped",'
        f'"error":"{error}","exit":130,"duration":"{seconds}"}}'
    )


FIRST = Task(id=1, name="First", prompt="")
SECOND = Task(id=2, name="Second", prompt="")

# A stopped run has no outcome for the task that was running, which matches
# the task file: that task stays as it was. A completed task earlier in the run
# must not make a later stop read as failed.
STOPPED_CASES = [
    pytest.param(
        [
            TaskStarted(FIRST),
            Activity(FIRST, "working"),
            RunDone(
                "task 1: First failed: signal: killed", 2.0, RunResult.STOPPED, 130
            ),
        ],
        _stopped("task 1: First failed: signal: killed", "2s"),
        3,
        "task-1.log",
        "14:02:11 working\n",
        id="a task was running",
    ),
    pytest.param(
        [
            TaskStarted(FIRST),
            TaskFinished(replace(FIRST, state=COMPLETED), None, 0.0),
            TaskStarted(SECOND),
            RunDone("stopped", 0.0, RunResult.STOPPED, 130),
        ],
        _stopped("stopped", "0s"),
        5,
        "task-2.log",
        "",
        id="after a completed task",
    ),
]


@pytest.mark.parametrize(
    ("events", "last_line", "ledger_count", "detail", "detail_text"), STOPPED_CASES
)
def test_record_stopped_run(
    opened: tuple[RunLog, Path],
    events: list[Event],
    last_line: str,
    ledger_count: int,
    detail: str,
    detail_text: str,
) -> None:
    log, path = opened

    record(log, *events)

    lines = ledger_lines(path)
    assert len(lines) == ledger_count
    assert lines[-1] == last_line
    assert (path / detail).read_text(encoding="utf-8") == detail_text


def test_record_each_task_gets_its_own_detail_file(
    opened: tuple[RunLog, Path],
) -> None:
    log, path = opened
    first = Task(id=1, name="First", prompt="")
    second = Task(id=2, name="Second", prompt="")

    record(
        log,
        TaskStarted(first),
        Activity(first, "one"),
        TaskFinished(first, None, 0.0),
        TaskStarted(second),
        Activity(second, "two"),
        TaskFinished(second, None, 0.0),
    )

    assert read_lines(path / "task-1.log") == ["14:02:11 one"]
    assert read_lines(path / "task-2.log") == ["14:02:11 two"]


# A task name, an Info field, or an activity line can hold a lone surrogate,
# which UTF-8 cannot encode; the log writes "?" in its place rather than failing
# the run.
def test_record_lone_surrogates_are_replaced(tmp_path: Path) -> None:
    path = tmp_path / "run"
    info = replace(INFO, tasks_file="run/ta\ud800sks.yaml")
    log = RunLog.open(path, info, now=fixed_now)
    task = Task(id=1, name="Bad\ud800name", prompt="")
    failed = replace(task, state=FAILED, error="bad\udfffend")

    record(
        log,
        TaskStarted(task),
        Activity(task, "x\ud800y"),
        TaskFinished(failed, None, 0.0),
    )
    log.close()

    lines = ledger_lines(path)
    assert '"tasks_file":"run/ta?sks.yaml"' in lines[0]
    assert '"name":"Bad?name"' in lines[1]
    assert '"error":"bad?end"' in lines[2]
    assert read_lines(path / "task-1.log") == ["14:02:11 x?y"]


needs_dev_full = pytest.mark.skipif(
    not Path("/dev/full").exists(),
    reason="/dev/full is Linux-only; macOS has no device that fails writes with ENOSPC",
)


def open_full_detail_log(tmp_path: Path) -> tuple[RunLog, Task]:
    """A log whose first task's detail file is /dev/full, which accepts the open
    and fails every write with ENOSPC, a real OSError from the kernel."""
    path = tmp_path / "run"
    log = RunLog.open(path, INFO, now=fixed_now)
    (path / "task-1.log").symlink_to("/dev/full")
    task = Task(id=1, name="First", prompt="")
    record(log, TaskStarted(task))
    return log, task


@needs_dev_full
def test_record_write_that_fails_is_a_run_log_error(tmp_path: Path) -> None:
    log, task = open_full_detail_log(tmp_path)

    with pytest.raises(RunLogError, match="task-1.log"):
        log.record(Activity(task, "working"))

    with pytest.raises(RunLogError, match="task-1.log"):
        log.close()


def open_fds() -> int:
    return len(os.listdir("/dev/fd"))


@needs_dev_full
def test_record_task_finished_with_a_failing_detail_close_is_a_run_log_error(
    tmp_path: Path,
) -> None:
    before = open_fds()
    log, task = open_full_detail_log(tmp_path)
    with pytest.raises(RunLogError):
        log.record(Activity(task, "working"))

    # The unwritten line is still buffered, so closing the file fails too.
    with pytest.raises(RunLogError, match="task-1.log"):
        log.record(TaskFinished(task, None, 0.0))

    log.close()

    assert open_fds() == before, (
        "the ledger is closed even though the detail close failed"
    )


@pytest.mark.parametrize(
    "event",
    [TaskStarted(FIRST), RunDone(None, 0.0)],
    ids=["task started", "run done"],
)
def test_record_after_close_is_an_error_and_opens_no_detail_file(
    opened: tuple[RunLog, Path], event: Event
) -> None:
    log, path = opened
    log.close()

    with pytest.raises(RunLogError):
        log.record(event)

    assert not (path / "task-1.log").exists()


def test_record_activity_after_the_task_finished_appends_to_its_detail_file(
    opened: tuple[RunLog, Path],
) -> None:
    # The after-all commit reports its marker on the last task, once that task is done.
    log, path = opened
    record(
        log,
        TaskStarted(FIRST),
        Activity(FIRST, "working"),
        TaskFinished(replace(FIRST, state=COMPLETED), None, 0.0),
        Activity(FIRST, "→ commit First"),
    )

    assert read_lines(path / "task-1.log") == [
        "14:02:11 working",
        "14:02:11 → commit First",
    ]


def test_close_twice_is_safe(opened: tuple[RunLog, Path]) -> None:
    log, _ = opened
    log.close()

    log.close()


def test_open_that_cannot_start_the_ledger_removes_the_folder_it_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path) -> None:
        raise runlog._io_error(path, OSError(errno.EACCES, "Permission denied"))

    monkeypatch.setattr(runlog, "_open_append", refuse)
    path = tmp_path / "logs" / "20261001T140211"

    with pytest.raises(RefusedError) as raised:
        RunLog.open(path, INFO, now=fixed_now)

    assert raised.value.reason == "log-unwritable"
    assert "Permission denied" in raised.value.message
    assert not path.exists(), "a refused run leaves no run folder"


def test_discard_removes_the_run_folder_and_close_stays_safe(
    opened: tuple[RunLog, Path],
) -> None:
    log, path = opened

    log.discard()
    log.close()

    assert not path.exists()


def test_discard_leaves_a_folder_that_holds_something_else(
    opened: tuple[RunLog, Path],
) -> None:
    log, path = opened
    stranger = path / "notes.txt"
    stranger.write_text("not ours\n", encoding="utf-8")

    log.discard()

    assert stranger.read_text(encoding="utf-8") == "not ours\n"
    assert not (path / "run.jsonl").exists(), "its own ledger still goes"
