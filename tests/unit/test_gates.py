"""Gates: the commands alr runs itself after a session reports completed."""

import asyncio
import contextlib
import json
import os
import signal
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from helpers import cancel_run, run_loop, saved_tasks, wait_for_text, wait_until_gone

from alr.engine import gates
from alr.errors import RunError
from alr.tasks import COMPLETED, FAILED, PENDING, Gate, Shared, Task, TaskList

pytestmark = pytest.mark.usefixtures("fake_claude")

# The odd duration makes a leaked child findable: `pgrep -f 'sleep 7919'`.
DESCENDANT_SLEEP = "sleep 7919"


def _touch_gate(path: Path) -> Gate:
    """A gate that leaves proof it ran."""
    return Gate(cmd=f"touch '{path}'")


def _gated_task(*task_gates: Gate) -> TaskList:
    return TaskList(
        shared=Shared(prompt="shared", gates=list(task_gates)),
        tasks=[Task(id=1, name="First", prompt="p1")],
    )


def _spawning_gate(pid_file: Path, timeout: str) -> Gate:
    """A gate whose shell stays the parent of a long-lived child.

    A bare `sleep` gate proves nothing about the process group: the shell
    replaces itself with it, so killing the one direct child kills everything.
    The pid file is renamed into place so it is never seen half-written.
    """
    cmd = (
        f"{DESCENDANT_SLEEP} >/dev/null 2>&1 & printf %s $! > '{pid_file}.tmp'"
        f" && mv '{pid_file}.tmp' '{pid_file}'; wait"
    )
    return Gate(cmd=cmd, timeout=timeout)


@pytest.fixture
def descendant_pid_file(tmp_path: Path) -> Iterator[Path]:
    """Where a spawning gate writes its child's PID; a failing test cannot leave the child running."""
    pid_file = tmp_path / "descendant-pid"
    yield pid_file
    if not pid_file.exists():
        return

    with contextlib.suppress(ProcessLookupError):
        os.kill(int(pid_file.read_text()), signal.SIGKILL)


def _stdins(record: Path) -> list[str]:
    return [json.loads(line)["stdin"] for line in record.read_text().splitlines()]


async def test_run_loop_gates_pass_completes_task(tmp_path: Path) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    first, second = tmp_path / "gate1", tmp_path / "gate2"
    # The second gate passes only if the first already ran: file order.
    task_gates = [
        _touch_gate(first),
        Gate(cmd=f"test -e '{first}' && touch '{second}'"),
    ]

    await run_loop(_gated_task(*task_gates), tasks_file)

    assert first.exists()
    assert second.exists()
    saved = saved_tasks(tasks_file)
    assert [(task.state, task.error) for task in saved.tasks] == [(COMPLETED, "")]
    assert saved.gate_list() == task_gates, "gates must survive the save"


async def test_run_loop_file_gates_run_after_every_completed_task(
    tmp_path: Path,
) -> None:
    count = tmp_path / "gate-runs"
    task_list = TaskList(
        shared=Shared(prompt="shared", gates=[Gate(cmd=f"echo ran >> '{count}'")]),
        tasks=[
            Task(id=1, name="First", prompt="p1"),
            Task(id=2, name="Done", prompt="p2", state=COMPLETED),
            Task(id=3, name="Third", prompt="p3"),
        ],
    )

    await run_loop(task_list, tmp_path / "tasks.yaml")

    assert count.read_text() == "ran\nran\n", (
        "the file's gates run once per task that ran, and not for one already completed"
    )


async def test_run_loop_gate_failure_fails_task_and_stops_run(
    tmp_path: Path, record: Path
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    never = tmp_path / "never"
    task_list = TaskList(
        shared=Shared(
            prompt="shared", gates=[Gate(cmd='test "a" = "b"'), _touch_gate(never)]
        ),
        tasks=[
            Task(id=1, name="First", prompt="p1"),
            Task(id=2, name="Second", prompt="p2"),
        ],
    )
    want_error = 'gate "test \\"a\\" = \\"b\\"" failed: exit status 1'

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert str(caught.value) == f"task 1: First failed: {want_error}"
    assert not never.exists(), "gates after a failed gate must not run"
    assert len(_stdins(record)) == 1, "the second task must never run"
    # The quoted command must survive the yaml save and reload.
    assert [(task.state, task.error) for task in saved_tasks(tasks_file).tasks] == [
        (FAILED, want_error),
        (PENDING, ""),
    ]


@pytest.mark.parametrize(
    ("variable", "value", "want_error"),
    [
        pytest.param("FAKECLAUDE_EXIT_CODE", "1", "exit status 1", id="non-zero exit"),
        pytest.param(
            "FAKECLAUDE_OUTPUT",
            '{"state":"failed","error":"nope"}',
            "nope",
            id="session reports failed",
        ),
    ],
)
async def test_run_loop_gates_skipped_when_session_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    want_error: str,
) -> None:
    monkeypatch.setenv(variable, value)
    marker = tmp_path / "gate-ran"

    with pytest.raises(RunError) as caught:
        await run_loop(_gated_task(_touch_gate(marker)), tmp_path / "tasks.yaml")

    assert want_error in str(caught.value)
    assert "gate" not in str(caught.value)
    assert not marker.exists(), "a failed session must not run gates"


async def test_run_loop_gates_never_reach_claude(tmp_path: Path, record: Path) -> None:

    await run_loop(
        _gated_task(Gate(cmd="true # gate-only-text")), tmp_path / "tasks.yaml"
    )

    assert _stdins(record) == ["shared\n\n1: First\n\np1\n"], (
        "the stdin contract must not change when a task has gates"
    )


@pytest.mark.parametrize(
    ("cmd", "want_error"),
    [
        pytest.param(
            'test "$(printf ab | tr a-z A-Z)" = AB', "", id="pipes and substitution"
        ),
        pytest.param('x=1\ntest "$x" = 1', "", id="multi-line command"),
        pytest.param(
            "alr-no-such-command",
            'gate "alr-no-such-command" failed: exit status 127',
            id="command not found",
        ),
        pytest.param(
            'echo "unterminated',
            'gate "echo \\"unterminated" failed: exit status',
            id="shell syntax error",
        ),
    ],
)
async def test_run_loop_gate_shell_behavior(
    tmp_path: Path, cmd: str, want_error: str
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    task_list = _gated_task(Gate(cmd=cmd))

    if not want_error:
        await run_loop(task_list, tasks_file)
        assert [task.state for task in saved_tasks(tasks_file).tasks] == [COMPLETED]
        return

    with pytest.raises(RunError):
        await run_loop(task_list, tasks_file)

    saved = saved_tasks(tasks_file)
    assert [task.state for task in saved.tasks] == [FAILED]
    assert want_error in saved.tasks[0].error


async def test_run_loop_plain_prints_gate_line_and_output(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    await run_loop(_gated_task(Gate(cmd="echo gate-says-hi")), tmp_path / "tasks.yaml")

    assert "gate: echo gate-says-hi\ngate-says-hi\n" in capfd.readouterr().out


async def test_run_loop_plain_passes_gate_stderr_through(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    await run_loop(
        _gated_task(Gate(cmd="echo gate-stderr-marker >&2")), tmp_path / "tasks.yaml"
    )

    assert "gate-stderr-marker\n" in capfd.readouterr().err, (
        "a gate's stderr is where a failing check explains itself"
    )


@contextlib.contextmanager
def _stdin_from(path: Path) -> Iterator[None]:
    """Point the process's real stdin at a file, which a child would inherit."""
    original = os.dup(0)
    with path.open("rb") as source:
        os.dup2(source.fileno(), 0)

    try:
        yield
    finally:
        os.dup2(original, 0)
        os.close(original)


async def test_run_loop_gate_stdin_is_empty_not_inherited(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    # Under pytest stdin is already empty, so a gate that inherited it would
    # still read nothing; a file with content shows the difference.
    stdin_file = tmp_path / "stdin"
    stdin_file.write_text("alr-stdin-marker\n")
    # The brackets show everything the gate read before end-of-file.
    gate = Gate(cmd="printf 'gate-read=[%s]\\n' \"$(cat)\"")

    with _stdin_from(stdin_file):
        await run_loop(_gated_task(gate), tmp_path / "tasks.yaml")

    output = capfd.readouterr().out
    assert "gate-read=[]\n" in output, "the gate must see end-of-file before any input"
    assert "alr-stdin-marker" not in output, "alr's stdin must not reach a gate"


@pytest.mark.parametrize(
    "timeout",
    [pytest.param("", id="plain"), pytest.param("30s", id="plain with timeout")],
)
async def test_run_cancel_during_gate_leaves_file_untouched(
    tmp_path: Path, timeout: str
) -> None:
    ready = tmp_path / "ready"
    tasks_file = tmp_path / "tasks.yaml"
    tasks_yaml = "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p1, state: pending}\n"
    tasks_file.write_text(tasks_yaml, encoding="utf-8")
    task_list = _gated_task(
        Gate(cmd=f"echo ready > '{ready}'; sleep 60", timeout=timeout)
    )

    run = asyncio.ensure_future(run_loop(task_list, tasks_file))
    await wait_for_text(ready)
    error = await cancel_run(run)

    assert "task 1: First: stopped during gate" in str(error)
    assert tasks_file.read_text(encoding="utf-8") == tasks_yaml, (
        "the tasks file must be byte-for-byte unchanged after a cancel during a gate"
    )


async def test_run_gate_shell_that_cannot_be_executed_fails_the_task(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No execute bit at all, which exec refuses even for root. Only this and
    # the fake claude are on PATH, because exec moves on to the next folder
    # that holds an executable sh.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sh").write_text("#!/bin/true\n", encoding="utf-8")
    (bin_dir / "sh").chmod(0o644)
    monkeypatch.setenv("PATH", f"{fake_claude}:{bin_dir}")
    tasks_file = tmp_path / "tasks.yaml"

    with pytest.raises(RunError) as caught:
        await run_loop(_gated_task(Gate(cmd="true")), tasks_file)

    assert str(caught.value) == (
        'task 1: First failed: gate "true" failed: sh: cannot be started: Permission denied'
    )
    assert [task.state for task in saved_tasks(tasks_file).tasks] == [FAILED]


async def test_run_gate_timeout_fails_task_and_skips_later_gates(
    tmp_path: Path,
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    never = tmp_path / "never"
    task_list = _gated_task(Gate(cmd="sleep 30", timeout="100ms"), _touch_gate(never))
    want_error = 'gate "sleep 30" timed out after 100ms'
    started = time.monotonic()

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert str(caught.value) == f"task 1: First failed: {want_error}"
    assert time.monotonic() - started < 10, (
        "the deadline must kill the gate, not wait out its sleep"
    )
    assert not never.exists(), "gates after a timed-out gate must not run"
    assert [(task.state, task.error) for task in saved_tasks(tasks_file).tasks] == [
        (FAILED, want_error)
    ]


async def test_run_gate_timeout_kills_the_gates_descendants(
    tmp_path: Path, descendant_pid_file: Path
) -> None:
    # Long enough for the shell to record the pid before the deadline.
    task_list = _gated_task(_spawning_gate(descendant_pid_file, "500ms"))

    with pytest.raises(RunError, match="timed out after 500ms"):
        await run_loop(task_list, tmp_path / "tasks.yaml")

    # A process the gate started must die with it on a timeout.
    await wait_until_gone(int(await wait_for_text(descendant_pid_file)))


async def test_run_cancel_during_gate_kills_the_gates_descendants(
    tmp_path: Path, descendant_pid_file: Path
) -> None:
    task_list = _gated_task(_spawning_gate(descendant_pid_file, ""))

    run = asyncio.ensure_future(run_loop(task_list, tmp_path / "tasks.yaml"))
    pid = int(await wait_for_text(descendant_pid_file))
    error = await cancel_run(run)

    assert "task 1: First: stopped during gate" in str(error)
    # A process the gate started must die with it on a cancel.
    await wait_until_gone(pid)


async def test_run_gate_timeout_that_cannot_be_parsed_fails_the_gate(
    tmp_path: Path,
) -> None:
    task_list = _gated_task(Gate(cmd="true", timeout="soon"))

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tmp_path / "tasks.yaml")

    assert 'gate "true" failed:' in str(caught.value)
    assert "must be a duration string such as 90s or 10m" in str(caught.value)


@pytest.mark.parametrize(
    ("gate", "override", "want_limit", "want_source"),
    [
        pytest.param(
            Gate(cmd="x", timeout="30s"),
            "2m",
            "2m",
            "flag",
            id="flag beats a gate's own timeout",
        ),
        pytest.param(Gate(cmd="x"), "90s", "90s", "flag", id="flag beats the default"),
        pytest.param(
            Gate(cmd="x", timeout="30s"), "", "30s", "gate", id="gate beats the default"
        ),
        pytest.param(
            Gate(cmd="x"),
            "",
            gates.DEFAULT_GATE_TIMEOUT,
            "default",
            id="default when neither is set",
        ),
    ],
)
def test_gate_limit_flag_beats_gate_beats_default(
    gate: Gate, override: str, want_limit: str, want_source: str
) -> None:
    assert gates.gate_limit(gate, override) == (want_limit, want_source)


@pytest.mark.parametrize(
    "gate_timeout",
    [
        pytest.param("", id="plain, flag applies to a gate with no timeout"),
    ],
)
async def test_run_gate_timeout_flag_overrides_the_gate_timeout(
    tmp_path: Path, gate_timeout: str
) -> None:
    task_list = _gated_task(Gate(cmd="sleep 30", timeout=gate_timeout))
    started = time.monotonic()

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tmp_path / "tasks.yaml", gate_timeout="100ms")

    assert (
        str(caught.value)
        == 'task 1: First failed: gate "sleep 30" timed out after 100ms'
    )
    assert time.monotonic() - started < 10, "the flag must cut the gate short"


async def test_run_gate_timeout_flag_is_never_saved(tmp_path: Path) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    task_list = _gated_task(Gate(cmd="true", timeout="30s"), Gate(cmd="true"))

    await run_loop(task_list, tasks_file, gate_timeout="45s")

    data = tasks_file.read_text(encoding="utf-8")
    assert data.count("timeout:") == 1, (
        "only the gate that had a timeout may carry the key"
    )
    assert "timeout: 30s" in data
    assert "45s" not in data, "the flag must not reach the tasks file"
    assert "10m" not in data, "the default must not reach the tasks file"
    assert saved_tasks(tasks_file).gate_list() == [
        Gate(cmd="true", timeout="30s"),
        Gate(cmd="true"),
    ]


@pytest.mark.parametrize(
    ("gate", "want_error"),
    [
        pytest.param(
            Gate(cmd="echo first\nexit 3"),
            'gate "echo first" failed: exit status 3',
            id="non-zero exit plain",
        ),
        pytest.param(
            Gate(cmd="echo first\nsleep 30", timeout="100ms"),
            'gate "echo first" timed out after 100ms',
            id="timeout plain",
        ),
    ],
)
async def test_run_multi_line_gate_errors_name_only_the_first_line(
    tmp_path: Path, gate: Gate, want_error: str
) -> None:
    tasks_file = tmp_path / "tasks.yaml"

    with pytest.raises(RunError) as caught:
        await run_loop(_gated_task(gate), tasks_file)

    assert str(caught.value) == f"task 1: First failed: {want_error}"
    saved = saved_tasks(tasks_file)
    assert saved.tasks[0].error == want_error
    assert saved.gate_list()[0].cmd == gate.cmd, "the stored command stays whole"
