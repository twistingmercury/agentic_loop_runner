"""Actions: commands from the task file's shared.after_task list that alr runs itself after each session."""

import time
from pathlib import Path

import pytest

from e2e_helpers import (
    assert_no_tmp_file,
    expected_stdin,
    fake_claude_records,
    read_task_log,
    read_tasks_yaml,
    run_alr,
    run_args,
    shared_block,
    run_dry_run,
    task_errors,
    task_states,
    write_tasks_yaml,
)

ONE_TASK = """tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
"""
TWO_TASKS = (
    ONE_TASK
    + """  - id: 2
    name: Second task
    prompt: Do the second thing.
"""
)
BAD_DURATION = "--action-timeout: must be a duration string such as 90s or 10m"
NOT_POSITIVE = "--action-timeout: must be greater than zero"


def test_actions_passing_actions_complete_the_task(
    tmp_path: Path, fake_claude: Path
) -> None:
    marker = tmp_path / "action-ran"
    tasks_path = write_tasks_yaml(
        tmp_path,
        shared_block(
            "Body.", [{"action": "echo action-output"}, {"action": f"touch '{marker}'"}]
        )
        + ONE_TASK,
    )
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(tasks_path),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    assert "action: echo action-output\naction-output\n" in res.out
    assert marker.exists()

    records = fake_claude_records(record_file)
    assert len(records) == 1
    # Actions are never sent to claude.
    assert records[0]["stdin"] == expected_stdin(
        "Body.", 1, "First task", "Do the first thing."
    )

    after = read_tasks_yaml(tasks_path)
    assert task_states(after) == {1: "completed"}
    assert after["shared"]["after_task"] == [
        {"action": "echo action-output"},
        {"action": f"touch '{marker}'"},
    ]
    assert_no_tmp_file(tasks_path)


def test_actions_failing_action_fails_the_task_and_blocks_the_next_run(
    tmp_path: Path, fake_claude: Path
) -> None:
    never = tmp_path / "never"
    tasks_path = write_tasks_yaml(
        tmp_path,
        shared_block(
            "Body.", [{"action": 'test "a" = "b"'}, {"action": f"touch '{never}'"}]
        )
        + TWO_TASKS,
    )
    record_file = tmp_path / "record.ndjson"
    args = run_args(tasks_path)
    env = {"FAKECLAUDE_RECORD_FILE": str(record_file)}

    res = run_alr(*args, env=env)

    action_err = 'action "test \\"a\\" = \\"b\\"" failed: exit status 1'
    assert res.code != 0, res
    assert f"task 1: First task failed: {action_err}" in res.err
    assert not never.exists(), "actions after a failed action must not run"
    assert len(fake_claude_records(record_file)) == 1

    after = read_tasks_yaml(tasks_path)
    assert task_states(after) == {1: "failed", 2: "pending"}
    assert task_errors(after)[1] == action_err
    assert_no_tmp_file(tasks_path)

    # An action failure is an ordinary failed task: the saved file must load
    # again, and the next run must refuse to start.
    again = run_alr(*args, env=env)

    assert again.code == 3, again
    assert "Some tasks failed previous runs:" in again.out
    assert len(fake_claude_records(record_file)) == 1


def test_actions_failing_action_output_is_in_the_task_log_and_on_the_terminal(
    tmp_path: Path, fake_claude: Path
) -> None:
    cmd = "echo action-said-out; echo action-said-err >&2; exit 4"
    tasks_path = write_tasks_yaml(
        tmp_path, shared_block("Body.", [{"action": cmd}]) + ONE_TASK
    )

    res = run_alr(*run_args(tasks_path))

    assert res.code != 0, res
    assert "action-said-out" in res.out
    assert "action-said-err" in res.err
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "failed"}
    log = read_task_log(tasks_path, 1)
    assert "action-said-out" in log
    assert "action-said-err" in log


def test_actions_skipped_when_the_session_fails(
    tmp_path: Path, fake_claude: Path
) -> None:
    marker = tmp_path / "action-ran"
    tasks_path = write_tasks_yaml(
        tmp_path, shared_block("Body.", [{"action": f"touch '{marker}'"}]) + ONE_TASK
    )

    res = run_alr(
        *run_args(tasks_path),
        env={"FAKECLAUDE_OUTPUT": '{"state":"failed","error":"something broke"}\n'},
    )

    assert res.code != 0, res
    assert "task 1: First task failed: something broke" in res.err
    assert "action:" not in res.out
    assert not marker.exists(), "a failed session must not run actions"
    assert task_errors(read_tasks_yaml(tasks_path))[1] == "something broke"


def test_actions_timed_out_action_fails_the_task(
    tmp_path: Path, fake_claude: Path
) -> None:
    never = tmp_path / "never"
    tasks_path = write_tasks_yaml(
        tmp_path,
        shared_block(
            "Body.",
            [
                {"action": "sleep 30", "timeout": "200ms"},
                {"action": f"touch '{never}'"},
            ],
        )
        + ONE_TASK,
    )

    started = time.monotonic()
    res = run_alr(*run_args(tasks_path), timeout=20)

    action_err = 'action "sleep 30" timed out after 200ms'
    assert res.code != 0, res
    # The timeout kills the action instead of waiting out its sleep.
    assert time.monotonic() - started < 15
    assert f"task 1: First task failed: {action_err}" in res.err
    assert not never.exists(), "actions after a timed-out action must not run"

    after = read_tasks_yaml(tasks_path)
    assert task_states(after)[1] == "failed"
    assert task_errors(after)[1] == action_err
    assert after["shared"]["after_task"][0]["timeout"] == "200ms"
    assert_no_tmp_file(tasks_path)


def test_actions_action_timeout_flag_overrides_the_action_timeout(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(
        tmp_path,
        shared_block(
            "Body.",
            [{"action": "sleep 30", "timeout": "30s"}, {"action": "echo no-timeout"}],
        )
        + ONE_TASK,
    )

    started = time.monotonic()
    res = run_alr(*run_args(tasks_path), "--action-timeout", "200ms", timeout=20)

    action_err = 'action "sleep 30" timed out after 200ms'
    assert res.code != 0, res
    assert time.monotonic() - started < 15, "the flag must cut the action short"
    assert f"task 1: First task failed: {action_err}" in res.err

    after = read_tasks_yaml(tasks_path)
    assert task_errors(after)[1] == action_err
    # The flag is never written to the task file.
    assert after["shared"]["after_task"] == [
        {"action": "sleep 30", "timeout": "30s"},
        {"action": "echo no-timeout"},
    ]


@pytest.mark.parametrize(
    ("flags", "want"),
    [
        pytest.param(
            [],
            "after_task: go test ./...: 30s (action)\nafter_task: make lint: 10m (default)\n",
            id="action and default",
        ),
        pytest.param(
            ["--action-timeout", "2m"],
            "after_task: go test ./...: 2m (flag)\nafter_task: make lint: 2m (flag)\n",
            id="flag",
        ),
    ],
)
def test_actions_dry_run_lists_effective_timeouts(
    tmp_path: Path, fake_claude: Path, flags: list[str], want: str
) -> None:
    tasks_yaml = (
        shared_block(
            "Body.",
            [{"action": "go test ./...", "timeout": "30s"}, {"action": "make lint"}],
        )
        + "tasks:\n  - id: 1\n    name: First task\n    prompt: p\n"
    )

    res, tasks_path = run_dry_run(tmp_path, tasks_yaml, *flags)

    assert res.code == 0, res
    assert f"First task\n{want}{tasks_path} is valid\n" in res.out


@pytest.mark.parametrize(
    ("value", "dry_run", "want"),
    [
        pytest.param("30", False, BAD_DURATION, id="unitless"),
        pytest.param("30", True, BAD_DURATION, id="unitless dry run"),
        pytest.param("0s", False, NOT_POSITIVE, id="zero"),
        pytest.param("-1m", True, NOT_POSITIVE, id="negative dry run"),
    ],
)
def test_actions_bad_action_timeout_flag_runs_nothing(
    tmp_path: Path, fake_claude: Path, value: str, dry_run: bool, want: str
) -> None:
    tasks_yaml = shared_block("Body.") + ONE_TASK
    tasks_path = write_tasks_yaml(tmp_path, tasks_yaml)
    record_file = tmp_path / "record.ndjson"
    flags = ["--dry-run"] if dry_run else []

    res = run_alr(
        *run_args(tasks_path),
        f"--action-timeout={value}",
        *flags,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code != 0, res
    assert want in res.err
    assert res.out == "", "nothing may load or run before the flag is checked"
    assert not record_file.exists(), "claude must not run"
    assert tasks_path.read_text(encoding="utf-8") == tasks_yaml


@pytest.mark.parametrize(
    "arg",
    [
        pytest.param("--action-timeout=30", id="unitless"),
        pytest.param("--action-timeout=", id="explicitly empty"),
    ],
)
def test_actions_bad_action_timeout_flag_is_reported_before_anything_else(
    arg: str,
) -> None:
    """With --dir also missing, only a check that runs first reports the bad value."""
    res = run_alr(arg)

    assert res.code == 2, res
    assert f"error: {BAD_DURATION}\n" in res.err
    assert "required flag" not in res.err


def test_actions_multi_line_action_error_names_only_the_first_line(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(
        tmp_path,
        shared_block("Body.", [{"action": "echo first-line\nexit 3\n"}]) + ONE_TASK,
    )

    res = run_alr(*run_args(tasks_path))

    assert res.code != 0, res
    assert (
        task_errors(read_tasks_yaml(tasks_path))[1]
        == 'action "echo first-line" failed: exit status 3'
    )


def test_actions_task_level_actions_are_rejected(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_yaml = (
        shared_block()
        + 'tasks:\n  - id: 1\n    name: First task\n    prompt: do it\n    gates:\n      - cmd: "true"\n'
    )

    res, _ = run_dry_run(tmp_path, tasks_yaml)

    assert res.code == 3, res
    assert (
        "tasks[0] (id 1): gates: gates are set once for the whole file, as shared.after_task; see the HOWTO"
        in res.err
    )


def test_actions_shared_actions_run_for_every_task(
    tmp_path: Path, fake_claude: Path
) -> None:
    marker = tmp_path / "action-runs"
    tasks_path = write_tasks_yaml(
        tmp_path,
        shared_block("Body.", [{"action": f"echo ran >> '{marker}'"}]) + TWO_TASKS,
    )
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(tasks_path),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    # The shared action runs once per task.
    assert marker.read_text(encoding="utf-8") == "ran\nran\n"

    records = fake_claude_records(record_file)
    # Actions are never sent to claude.
    assert [record["stdin"] for record in records] == [
        expected_stdin("Body.", 1, "First task", "Do the first thing."),
        expected_stdin("Body.", 2, "Second task", "Do the second thing."),
    ]

    after = read_tasks_yaml(tasks_path)
    assert task_states(after) == {1: "completed", 2: "completed"}
    assert after["shared"]["after_task"] == [{"action": f"echo ran >> '{marker}'"}]
