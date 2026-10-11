"""Actions: the commands alr runs itself after a session reports completed."""

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

from alr.engine import actions
from alr.engine.actions import action_env
from alr.errors import RunError
from alr.tasks import COMPLETED, FAILED, PENDING, Action, Shared, Task, TaskList

pytestmark = pytest.mark.usefixtures("fake_claude")

# The odd duration makes a leaked child findable: `pgrep -f 'sleep 7919'`.
DESCENDANT_SLEEP = "sleep 7919"


def _touch_action(path: Path) -> Action:
    """An action that leaves proof it ran."""
    return Action(command=f"touch '{path}'")


def _with_actions_task(*task_actions: Action) -> TaskList:
    return TaskList(
        shared=Shared(prompt="shared", after_task=list(task_actions)),
        tasks=[Task(id=1, name="First", prompt="p1")],
    )


def _spawning_action(pid_file: Path, timeout: str) -> Action:
    """An action whose shell stays the parent of a long-lived child.

    A bare `sleep` action proves nothing about the process group: the shell
    replaces itself with it, so killing the one direct child kills everything.
    The pid file is renamed into place so it is never seen half-written.
    """
    cmd = (
        f"{DESCENDANT_SLEEP} >/dev/null 2>&1 & printf %s $! > '{pid_file}.tmp'"
        f" && mv '{pid_file}.tmp' '{pid_file}'; wait"
    )
    return Action(command=cmd, timeout=timeout)


@pytest.fixture
def descendant_pid_file(tmp_path: Path) -> Iterator[Path]:
    """Where a spawning action writes its child's PID; a failing test cannot leave the child running."""
    pid_file = tmp_path / "descendant-pid"
    yield pid_file
    if not pid_file.exists():
        return

    with contextlib.suppress(ProcessLookupError):
        os.kill(int(pid_file.read_text()), signal.SIGKILL)


def _stdins(record: Path) -> list[str]:
    return [json.loads(line)["stdin"] for line in record.read_text().splitlines()]


async def test_run_loop_actions_pass_completes_task(tmp_path: Path) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    first, second = tmp_path / "action1", tmp_path / "action2"
    # The second action passes only if the first already ran: file order.
    task_actions = [
        _touch_action(first),
        Action(command=f"test -e '{first}' && touch '{second}'"),
    ]

    await run_loop(_with_actions_task(*task_actions), tasks_file)

    assert first.exists()
    assert second.exists()
    saved = saved_tasks(tasks_file)
    assert [(task.state, task.error) for task in saved.tasks] == [(COMPLETED, "")]
    assert saved.after_task_list() == task_actions, "actions must survive the save"


async def test_run_loop_file_actions_run_after_every_completed_task(
    tmp_path: Path,
) -> None:
    count = tmp_path / "action-runs"
    task_list = TaskList(
        shared=Shared(
            prompt="shared", after_task=[Action(command=f"echo ran >> '{count}'")]
        ),
        tasks=[
            Task(id=1, name="First", prompt="p1"),
            Task(id=2, name="Done", prompt="p2", state=COMPLETED),
            Task(id=3, name="Third", prompt="p3"),
        ],
    )

    await run_loop(task_list, tmp_path / "tasks.yaml")

    assert count.read_text() == "ran\nran\n", (
        "the file's actions run once per task that ran, and not for one already completed"
    )


async def test_run_loop_action_failure_fails_task_and_stops_run(
    tmp_path: Path, record: Path
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    never = tmp_path / "never"
    task_list = TaskList(
        shared=Shared(
            prompt="shared",
            after_task=[Action(command='test "a" = "b"'), _touch_action(never)],
        ),
        tasks=[
            Task(id=1, name="First", prompt="p1"),
            Task(id=2, name="Second", prompt="p2"),
        ],
    )
    want_error = 'action "test \\"a\\" = \\"b\\"" failed: exit status 1'

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert str(caught.value) == f"task 1: First failed: {want_error}"
    assert not never.exists(), "actions after a failed action must not run"
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
async def test_run_loop_actions_skipped_when_session_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    want_error: str,
) -> None:
    monkeypatch.setenv(variable, value)
    marker = tmp_path / "action-ran"

    with pytest.raises(RunError) as caught:
        await run_loop(
            _with_actions_task(_touch_action(marker)), tmp_path / "tasks.yaml"
        )

    assert want_error in str(caught.value)
    assert "action" not in str(caught.value)
    assert not marker.exists(), "a failed session must not run actions"


async def test_run_loop_actions_never_reach_claude(
    tmp_path: Path, record: Path
) -> None:

    await run_loop(
        _with_actions_task(Action(command="true # action-only-text")),
        tmp_path / "tasks.yaml",
    )

    assert _stdins(record) == ["shared\n\n1: First\n\np1\n"], (
        "the stdin contract must not change when a task has actions"
    )


@pytest.mark.parametrize(
    ("command", "want_error"),
    [
        pytest.param(
            'test "$(printf ab | tr a-z A-Z)" = AB', "", id="pipes and substitution"
        ),
        pytest.param('x=1\ntest "$x" = 1', "", id="multi-line command"),
        pytest.param(
            "alr-no-such-command",
            'action "alr-no-such-command" failed: exit status 127',
            id="command not found",
        ),
        pytest.param(
            'echo "unterminated',
            'action "echo \\"unterminated" failed: exit status',
            id="shell syntax error",
        ),
    ],
)
async def test_run_loop_action_shell_behavior(
    tmp_path: Path, command: str, want_error: str
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    task_list = _with_actions_task(Action(command=command))

    if not want_error:
        await run_loop(task_list, tasks_file)
        assert [task.state for task in saved_tasks(tasks_file).tasks] == [COMPLETED]
        return

    with pytest.raises(RunError):
        await run_loop(task_list, tasks_file)

    saved = saved_tasks(tasks_file)
    assert [task.state for task in saved.tasks] == [FAILED]
    assert want_error in saved.tasks[0].error


async def test_run_loop_plain_prints_action_line_and_output(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    await run_loop(
        _with_actions_task(Action(command="echo action-says-hi")),
        tmp_path / "tasks.yaml",
    )

    assert "action: echo action-says-hi\naction-says-hi\n" in capfd.readouterr().out


async def test_run_loop_plain_passes_action_stderr_through(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    await run_loop(
        _with_actions_task(Action(command="echo action-stderr-marker >&2")),
        tmp_path / "tasks.yaml",
    )

    assert "action-stderr-marker\n" in capfd.readouterr().err, (
        "an action's stderr is where a failing check explains itself"
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


async def test_run_loop_action_stdin_is_empty_not_inherited(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    # Under pytest stdin is already empty, so an action that inherited it would
    # still read nothing; a file with content shows the difference.
    stdin_file = tmp_path / "stdin"
    stdin_file.write_text("alr-stdin-marker\n")
    # The brackets show everything the action read before end-of-file.
    action = Action(command="printf 'action-read=[%s]\\n' \"$(cat)\"")

    with _stdin_from(stdin_file):
        await run_loop(_with_actions_task(action), tmp_path / "tasks.yaml")

    output = capfd.readouterr().out
    assert "action-read=[]\n" in output, (
        "the action must see end-of-file before any input"
    )
    assert "alr-stdin-marker" not in output, "alr's stdin must not reach an action"


@pytest.mark.parametrize(
    "timeout",
    [pytest.param("", id="plain"), pytest.param("30s", id="plain with timeout")],
)
async def test_run_cancel_during_action_leaves_file_untouched(
    tmp_path: Path, timeout: str
) -> None:
    ready = tmp_path / "ready"
    tasks_file = tmp_path / "tasks.yaml"
    tasks_yaml = "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: First, prompt: p1, state: pending}\n"
    tasks_file.write_text(tasks_yaml, encoding="utf-8")
    task_list = _with_actions_task(
        Action(command=f"echo ready > '{ready}'; sleep 60", timeout=timeout)
    )

    run = asyncio.ensure_future(run_loop(task_list, tasks_file))
    await wait_for_text(ready)
    error = await cancel_run(run)

    assert "task 1: First: stopped during action" in str(error)
    assert tasks_file.read_text(encoding="utf-8") == tasks_yaml, (
        "the tasks file must be byte-for-byte unchanged after a cancel during an action"
    )


async def test_run_action_shell_that_cannot_be_executed_fails_the_task(
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
        await run_loop(_with_actions_task(Action(command="true")), tasks_file)

    assert 'action "true" failed' in str(caught.value)
    assert [task.state for task in saved_tasks(tasks_file).tasks] == [FAILED]


async def test_run_action_timeout_fails_task_and_skips_later_actions(
    tmp_path: Path,
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    never = tmp_path / "never"
    task_list = _with_actions_task(
        Action(command="sleep 30", timeout="100ms"), _touch_action(never)
    )
    want_error = 'action "sleep 30" timed out after 100ms'
    started = time.monotonic()

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tasks_file)

    assert str(caught.value) == f"task 1: First failed: {want_error}"
    assert time.monotonic() - started < 10, (
        "the deadline must kill the action, not wait out its sleep"
    )
    assert not never.exists(), "actions after a timed-out action must not run"
    assert [(task.state, task.error) for task in saved_tasks(tasks_file).tasks] == [
        (FAILED, want_error)
    ]


async def test_run_action_timeout_kills_the_actions_descendants(
    tmp_path: Path, descendant_pid_file: Path
) -> None:
    # Long enough for the shell to record the pid before the deadline.
    task_list = _with_actions_task(_spawning_action(descendant_pid_file, "500ms"))

    with pytest.raises(RunError, match="timed out after 500ms"):
        await run_loop(task_list, tmp_path / "tasks.yaml")

    # A process the action started must die with it on a timeout.
    await wait_until_gone(int(await wait_for_text(descendant_pid_file)))


async def test_run_cancel_during_action_kills_the_actions_descendants(
    tmp_path: Path, descendant_pid_file: Path
) -> None:
    task_list = _with_actions_task(_spawning_action(descendant_pid_file, ""))

    run = asyncio.ensure_future(run_loop(task_list, tmp_path / "tasks.yaml"))
    pid = int(await wait_for_text(descendant_pid_file))
    error = await cancel_run(run)

    assert "task 1: First: stopped during action" in str(error)
    # A process the action started must die with it on a cancel.
    await wait_until_gone(pid)


async def test_run_action_timeout_that_cannot_be_parsed_fails_the_action(
    tmp_path: Path,
) -> None:
    task_list = _with_actions_task(Action(command="true", timeout="soon"))

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tmp_path / "tasks.yaml")

    assert 'action "true" failed' in str(caught.value)


@pytest.mark.parametrize(
    ("action", "override", "want_limit", "want_source"),
    [
        pytest.param(
            Action(command="x", timeout="30s"),
            "2m",
            "2m",
            "flag",
            id="flag beats an action's own timeout",
        ),
        pytest.param(
            Action(command="x"), "90s", "90s", "flag", id="flag beats the default"
        ),
        pytest.param(
            Action(command="x", timeout="30s"),
            "",
            "30s",
            "action",
            id="action beats the default",
        ),
        pytest.param(
            Action(command="x"),
            "",
            actions.DEFAULT_ACTION_TIMEOUT,
            "default",
            id="default when neither is set",
        ),
    ],
)
def test_action_limit_flag_beats_action_beats_default(
    action: Action, override: str, want_limit: str, want_source: str
) -> None:
    assert actions.action_limit(action, override) == (want_limit, want_source)


@pytest.mark.parametrize(
    "action_timeout",
    [
        pytest.param("", id="plain, flag applies to an action with no timeout"),
    ],
)
async def test_run_action_timeout_flag_overrides_the_action_timeout(
    tmp_path: Path, action_timeout: str
) -> None:
    task_list = _with_actions_task(Action(command="sleep 30", timeout=action_timeout))
    started = time.monotonic()

    with pytest.raises(RunError) as caught:
        await run_loop(task_list, tmp_path / "tasks.yaml", action_timeout="100ms")

    assert (
        str(caught.value)
        == 'task 1: First failed: action "sleep 30" timed out after 100ms'
    )
    assert time.monotonic() - started < 10, "the flag must cut the action short"


async def test_run_action_timeout_flag_is_never_saved(tmp_path: Path) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    task_list = _with_actions_task(
        Action(command="true", timeout="30s"), Action(command="true")
    )

    await run_loop(task_list, tasks_file, action_timeout="45s")

    data = tasks_file.read_text(encoding="utf-8")
    assert data.count("timeout:") == 1, (
        "only the action that had a timeout may carry the key"
    )
    assert "timeout: 30s" in data
    assert "45s" not in data, "the flag must not reach the tasks file"
    assert "10m" not in data, "the default must not reach the tasks file"
    assert saved_tasks(tasks_file).after_task_list() == [
        Action(command="true", timeout="30s"),
        Action(command="true"),
    ]


@pytest.mark.parametrize(
    ("action", "want_error"),
    [
        pytest.param(
            Action(command="echo first\nexit 3"),
            'action "echo first" failed: exit status 3',
            id="non-zero exit plain",
        ),
        pytest.param(
            Action(command="echo first\nsleep 30", timeout="100ms"),
            'action "echo first" timed out after 100ms',
            id="timeout plain",
        ),
    ],
)
async def test_run_multi_line_action_errors_name_only_the_first_line(
    tmp_path: Path, action: Action, want_error: str
) -> None:
    tasks_file = tmp_path / "tasks.yaml"

    with pytest.raises(RunError) as caught:
        await run_loop(_with_actions_task(action), tasks_file)

    assert str(caught.value) == f"task 1: First failed: {want_error}"
    saved = saved_tasks(tasks_file)
    assert saved.tasks[0].error == want_error
    assert saved.after_task_list()[0].command == action.command, (
        "the stored command stays whole"
    )


def test_action_env_for_a_task(tmp_path: Path) -> None:
    task = Task(id=7, name='fix "auth"; $(x)', prompt="p")
    env = action_env(task, str(tmp_path / "tasks.yaml"), str(tmp_path / "logs"))

    assert env == {
        "ALR_TASK_ID": "7",
        "ALR_TASK_NAME": 'fix "auth"; $(x)',
        "ALR_TASK_FILE": str(tmp_path / "tasks.yaml"),
        "ALR_LOG_DIR": str(tmp_path / "logs"),
    }


def test_action_env_after_the_run_has_no_task_variables(tmp_path: Path) -> None:
    env = action_env(None, str(tmp_path / "tasks.yaml"), str(tmp_path / "logs"))

    assert sorted(env) == ["ALR_LOG_DIR", "ALR_TASK_FILE"]


def test_action_env_makes_relative_paths_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    env = action_env(None, "tasks.yaml", "logs")

    assert env == {
        "ALR_TASK_FILE": str(tmp_path / "tasks.yaml"),
        "ALR_LOG_DIR": str(tmp_path / "logs"),
    }


async def test_the_loop_gives_an_action_the_task_and_the_run_paths(
    tmp_path: Path,
) -> None:
    seen = tmp_path / "seen"
    command = 'printf "%s|%s|%s" "$ALR_TASK_ID" "$ALR_TASK_FILE" "$ALR_LOG_DIR"'
    task_list = _with_actions_task(Action(command=f"{command} > '{seen}'"))
    tasks_file = tmp_path / "tasks.yaml"

    await run_loop(task_list, tasks_file, repo=None)

    assert seen.read_text() == f"1|{tasks_file}|{tmp_path / 'logs'}"
