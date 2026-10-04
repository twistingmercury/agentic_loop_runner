"""Gates: commands from the task file's top-level list that alr runs itself after each session."""

import time
from pathlib import Path

import pytest

from helpers import fake_claude_records

from e2e.conftest import (
    assert_no_tmp_file,
    expected_stdin,
    read_tasks_yaml,
    run_alr,
    run_args,
    run_dry_run,
    task_errors,
    task_states,
    write_prompt,
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
BAD_DURATION = "--gate-timeout: must be a duration string such as 90s or 10m"
NOT_POSITIVE = "--gate-timeout: must be greater than zero"


def test_gates_passing_gates_complete_the_task(
    tmp_path: Path, fake_claude: Path
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    marker = tmp_path / "gate-ran"
    tasks_path = write_tasks_yaml(
        tmp_path,
        f"gates:\n  - cmd: echo gate-output\n  - cmd: touch '{marker}'\n{ONE_TASK}",
    )
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    assert "gate: echo gate-output\ngate-output\n" in res.out
    assert marker.exists()

    records = fake_claude_records(record_file)
    assert len(records) == 1
    # Gates are never sent to claude.
    assert records[0]["stdin"] == expected_stdin(
        "Body.", 1, "First task", "Do the first thing."
    )

    after = read_tasks_yaml(tasks_path)
    assert task_states(after) == {1: "completed"}
    assert after["gates"] == [{"cmd": "echo gate-output"}, {"cmd": f"touch '{marker}'"}]
    assert_no_tmp_file(tasks_path)


def test_gates_failing_gate_fails_the_task_and_blocks_the_next_run(
    tmp_path: Path, fake_claude: Path
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    never = tmp_path / "never"
    tasks_path = write_tasks_yaml(
        tmp_path,
        f'gates:\n  - cmd: test "a" = "b"\n  - cmd: touch \'{never}\'\n{TWO_TASKS}',
    )
    record_file = tmp_path / "record.ndjson"
    args = run_args(prompt_path, tasks_path)
    env = {"FAKECLAUDE_RECORD_FILE": str(record_file)}

    res = run_alr(*args, env=env)

    gate_err = 'gate "test \\"a\\" = \\"b\\"" failed: exit status 1'
    assert res.code != 0, res
    assert f"task 1: First task failed: {gate_err}" in res.err
    assert not never.exists(), "gates after a failed gate must not run"
    assert len(fake_claude_records(record_file)) == 1

    after = read_tasks_yaml(tasks_path)
    assert task_states(after) == {1: "failed", 2: "pending"}
    assert task_errors(after)[1] == gate_err
    assert_no_tmp_file(tasks_path)

    # A gate failure is an ordinary failed task: the saved file must load
    # again, and the next run must refuse to start.
    again = run_alr(*args, env=env)

    assert again.code != 0, again
    assert "Some tasks failed previous runs:" in again.out
    assert len(fake_claude_records(record_file)) == 1


def test_gates_skipped_when_the_session_fails(
    tmp_path: Path, fake_claude: Path
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    marker = tmp_path / "gate-ran"
    tasks_path = write_tasks_yaml(
        tmp_path, f"gates:\n  - cmd: touch '{marker}'\n{ONE_TASK}"
    )

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={"FAKECLAUDE_OUTPUT": '{"state":"failed","error":"something broke"}\n'},
    )

    assert res.code != 0, res
    assert "task 1: First task failed: something broke" in res.err
    assert "gate:" not in res.out
    assert not marker.exists(), "a failed session must not run gates"
    assert task_errors(read_tasks_yaml(tasks_path))[1] == "something broke"


def test_gates_timed_out_gate_fails_the_task(tmp_path: Path, fake_claude: Path) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    never = tmp_path / "never"
    tasks_path = write_tasks_yaml(
        tmp_path,
        f"gates:\n  - cmd: sleep 30\n    timeout: 200ms\n  - cmd: touch '{never}'\n{ONE_TASK}",
    )

    started = time.monotonic()
    res = run_alr(*run_args(prompt_path, tasks_path), timeout=20)

    gate_err = 'gate "sleep 30" timed out after 200ms'
    assert res.code != 0, res
    # The timeout kills the gate instead of waiting out its sleep.
    assert time.monotonic() - started < 15
    assert f"task 1: First task failed: {gate_err}" in res.err
    assert not never.exists(), "gates after a timed-out gate must not run"

    after = read_tasks_yaml(tasks_path)
    assert task_states(after)[1] == "failed"
    assert task_errors(after)[1] == gate_err
    assert after["gates"][0]["timeout"] == "200ms"
    assert_no_tmp_file(tasks_path)


def test_gates_gate_timeout_flag_overrides_the_gate_timeout(
    tmp_path: Path, fake_claude: Path
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(
        tmp_path,
        f"gates:\n  - cmd: sleep 30\n    timeout: 30s\n  - cmd: echo no-timeout\n{ONE_TASK}",
    )

    started = time.monotonic()
    res = run_alr(
        *run_args(prompt_path, tasks_path), "--gate-timeout", "200ms", timeout=20
    )

    gate_err = 'gate "sleep 30" timed out after 200ms'
    assert res.code != 0, res
    assert time.monotonic() - started < 15, "the flag must cut the gate short"
    assert f"task 1: First task failed: {gate_err}" in res.err

    after = read_tasks_yaml(tasks_path)
    assert task_errors(after)[1] == gate_err
    # The flag is never written to the task file.
    assert after["gates"] == [
        {"cmd": "sleep 30", "timeout": "30s"},
        {"cmd": "echo no-timeout"},
    ]


@pytest.mark.parametrize(
    ("flags", "want"),
    [
        pytest.param(
            [],
            "gate: go test ./...: 30s (gate)\ngate: make lint: 10m (default)\n",
            id="gate and default",
        ),
        pytest.param(
            ["--gate-timeout", "2m"],
            "gate: go test ./...: 2m (flag)\ngate: make lint: 2m (flag)\n",
            id="flag",
        ),
    ],
)
def test_gates_dry_run_lists_effective_timeouts(
    tmp_path: Path, fake_claude: Path, flags: list[str], want: str
) -> None:
    tasks_yaml = """gates:
  - cmd: go test ./...
    timeout: 30s
  - cmd: make lint
tasks:
  - id: 1
    name: First task
    prompt: p
"""

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
def test_gates_bad_gate_timeout_flag_runs_nothing(
    tmp_path: Path, fake_claude: Path, value: str, dry_run: bool, want: str
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_yaml = "tasks:\n  - id: 1\n    name: First task\n    prompt: p\n"
    tasks_path = write_tasks_yaml(tmp_path, tasks_yaml)
    record_file = tmp_path / "record.ndjson"
    flags = ["--dry-run"] if dry_run else []

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        f"--gate-timeout={value}",
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
        pytest.param("--gate-timeout=30", id="unitless"),
        pytest.param("--gate-timeout=", id="explicitly empty"),
    ],
)
def test_gates_bad_gate_timeout_flag_is_reported_before_anything_else(arg: str) -> None:
    """With --prompt and --tasks also missing, only a check that runs first reports the bad value."""
    res = run_alr(arg)

    assert res.code == 1, res
    assert f"error: {BAD_DURATION}\n" in res.err
    assert "required flag" not in res.err


def test_gates_multi_line_gate_error_names_only_the_first_line(
    tmp_path: Path, fake_claude: Path
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(
        tmp_path,
        f"gates:\n  - cmd: |\n      echo first-line\n      exit 3\n{ONE_TASK}",
    )

    res = run_alr(*run_args(prompt_path, tasks_path))

    assert res.code != 0, res
    assert (
        task_errors(read_tasks_yaml(tasks_path))[1]
        == 'gate "echo first-line" failed: exit status 3'
    )


def test_gates_task_level_gates_are_rejected(tmp_path: Path, fake_claude: Path) -> None:
    tasks_yaml = 'tasks:\n  - id: 1\n    name: First task\n    prompt: do it\n    gates:\n      - cmd: "true"\n'

    res, _ = run_dry_run(tmp_path, tasks_yaml)

    assert res.code == 1, res
    assert (
        "tasks[0] (id 1): gates: gates are set once for the whole file now, as a top-level gates: list; see the HOWTO"
        in res.err
    )


def test_gates_shared_gates_run_for_every_task(
    tmp_path: Path, fake_claude: Path
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    marker = tmp_path / "gate-runs"
    tasks_path = write_tasks_yaml(
        tmp_path, f"gates:\n  - cmd: echo ran >> '{marker}'\n{TWO_TASKS}"
    )
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    # The shared gate runs once per task.
    assert marker.read_text(encoding="utf-8") == "ran\nran\n"

    records = fake_claude_records(record_file)
    # Gates are never sent to claude.
    assert [record["stdin"] for record in records] == [
        expected_stdin("Body.", 1, "First task", "Do the first thing."),
        expected_stdin("Body.", 2, "Second task", "Do the second thing."),
    ]

    after = read_tasks_yaml(tasks_path)
    assert task_states(after) == {1: "completed", 2: "completed"}
    assert after["gates"] == [{"cmd": f"echo ran >> '{marker}'"}]
