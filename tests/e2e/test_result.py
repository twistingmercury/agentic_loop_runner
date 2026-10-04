"""The result line: the last non-blank line of a session's output decides the task's outcome."""

from pathlib import Path

from e2e.conftest import (
    TWO_TASKS_YAML,
    Result,
    assert_no_tmp_file,
    expected_stdin,
    read_fake_claude_records,
    read_tasks_yaml,
    run_alr,
    run_args,
    task_errors,
    task_states,
    write_prompt,
    write_tasks_yaml,
)

ONE_TASK_YAML = """tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
"""
NO_RESULT_LINE = "no valid result line in session output"


def run_one_task(tmp_path: Path, env: dict[str, str]) -> tuple[Result, Path]:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(tmp_path, ONE_TASK_YAML)
    return run_alr(*run_args(prompt_path, tasks_path), env=env), tasks_path


def assert_saved_failed(tasks_path: Path, error: str) -> None:
    after = read_tasks_yaml(tasks_path)
    assert task_states(after)[1] == "failed"
    assert task_errors(after)[1] == error
    assert_no_tmp_file(tasks_path)


def test_loop_json_failed_result_writes_error_and_stops_loop(
    tmp_path: Path, fake_claude: Path
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(tmp_path, TWO_TASKS_YAML)
    record_file = tmp_path / "record.ndjson"
    result_line = '{"state":"failed","error":"something broke"}\n'

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={
            "FAKECLAUDE_RECORD_FILE": str(record_file),
            "FAKECLAUDE_OUTPUT": result_line,
        },
    )

    assert res.code != 0, res
    assert "task 1: First task failed: something broke" in res.err
    # The session's output still reaches stdout, though alr also reads it.
    assert result_line in res.out

    records = read_fake_claude_records(record_file)
    assert len(records) == 1, "the loop must stop before the second task"
    # The error text is never sent to claude.
    assert records[0]["stdin"] == expected_stdin(
        "Body.", 1, "First task", "Do the first thing."
    )

    after = read_tasks_yaml(tasks_path)
    assert task_states(after) == {1: "failed", 2: "pending"}
    assert task_errors(after) == {1: "something broke", 2: ""}
    assert_no_tmp_file(tasks_path)


def test_loop_missing_result_line_treated_as_failed(
    tmp_path: Path, fake_claude: Path
) -> None:
    res, tasks_path = run_one_task(tmp_path, {"FAKECLAUDE_OUTPUT": "some text\n"})

    assert res.code != 0, res
    assert f"task 1: First task failed: {NO_RESULT_LINE}" in res.err
    assert_saved_failed(tasks_path, NO_RESULT_LINE)


def test_loop_unknown_result_state_treated_as_failed(
    tmp_path: Path, fake_claude: Path
) -> None:
    res, tasks_path = run_one_task(
        tmp_path, {"FAKECLAUDE_OUTPUT": '{"state":"done"}\n'}
    )

    assert res.code != 0, res
    assert f"task 1: First task failed: {NO_RESULT_LINE}" in res.err
    assert_saved_failed(tasks_path, NO_RESULT_LINE)


def test_loop_non_zero_exit_with_failed_json_uses_json_error(
    tmp_path: Path, fake_claude: Path
) -> None:
    res, tasks_path = run_one_task(
        tmp_path,
        {
            "FAKECLAUDE_EXIT_CODE": "5",
            "FAKECLAUDE_OUTPUT": '{"state":"failed","error":"boom"}\n',
        },
    )

    assert res.code != 0, res
    assert "task 1: First task failed: boom" in res.err
    assert "exit status" not in res.err
    assert_saved_failed(tasks_path, "boom")


def test_loop_non_zero_exit_with_no_result_line_uses_exit_error(
    tmp_path: Path, fake_claude: Path
) -> None:
    res, tasks_path = run_one_task(tmp_path, {"FAKECLAUDE_EXIT_CODE": "1"})

    assert res.code != 0, res
    assert "task 1: First task failed: exit status 1" in res.err
    assert_saved_failed(tasks_path, "exit status 1")


def test_loop_non_zero_exit_with_completed_json_fails(
    tmp_path: Path, fake_claude: Path
) -> None:
    res, tasks_path = run_one_task(
        tmp_path,
        {
            "FAKECLAUDE_EXIT_CODE": "2",
            "FAKECLAUDE_OUTPUT": '{"state":"completed","error":""}\n',
        },
    )

    assert res.code == 1, res
    assert "task 1: First task failed: exit status 2" in res.err
    assert_saved_failed(tasks_path, "exit status 2")


def test_loop_fenced_result_line_completes(tmp_path: Path, fake_claude: Path) -> None:
    fenced_output = (
        '{"state":"failed","error":"stale"}\n'
        "```json\n"
        '{"state":"completed","error":""}\n'
        "```\n"
    )

    res, tasks_path = run_one_task(tmp_path, {"FAKECLAUDE_OUTPUT": fenced_output})

    assert res.code == 0, res
    assert fenced_output in res.out

    after = read_tasks_yaml(tasks_path)
    assert task_states(after)[1] == "completed"
    assert task_errors(after)[1] == ""
    assert_no_tmp_file(tasks_path)


def test_loop_stale_error_cleared_when_task_completes(
    tmp_path: Path, fake_claude: Path
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(
        tmp_path, ONE_TASK_YAML + "    state: pending\n    error: boom old\n"
    )
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    records = read_fake_claude_records(record_file)
    assert len(records) == 1
    assert "boom old" not in records[0]["stdin"]

    assert task_states(read_tasks_yaml(tasks_path))[1] == "completed"
    # The key is gone, not merely blank.
    assert "error:" not in tasks_path.read_text(encoding="utf-8")
    assert_no_tmp_file(tasks_path)
