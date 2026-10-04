"""The run loop from the outside: task order, fail-fast, the prompt echo, and startup failures."""

from pathlib import Path

from helpers import fake_claude_records

from e2e.conftest import (
    TWO_TASKS_YAML,
    VALID_TASKS_YAML,
    assert_no_tmp_file,
    expected_stdin,
    read_tasks_yaml,
    run_alr,
    run_args,
    task_states,
    write_prompt,
    write_tasks_yaml,
)


def test_loop_runs_every_task_in_order(tmp_path: Path, fake_claude: Path) -> None:
    """The ids are out of order, so sorting by id would change the run order and the saved order."""
    prompt_body = "PROMPT BODY MARKER\nFollow the runbook.\n"
    prompt_path = write_prompt(tmp_path, prompt_body)
    tasks_path = write_tasks_yaml(
        tmp_path,
        """tasks:
  - id: 20
    name: First task
    prompt: Do the first thing.
    state: completed
  - id: 30
    name: Second task
    prompt: Do the second thing.
  - id: 10
    name: Third task
    prompt: Do the third thing.
    state: pending
""",
    )
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 0, res
    assert "task 20: First task already completed, skipping" in res.out
    assert "Do the first thing." not in res.out

    records = fake_claude_records(record_file)
    assert len(records) == 2
    want_tasks = [
        (30, "Second task", "Do the second thing."),
        (10, "Third task", "Do the third thing."),
    ]
    for record, (id, name, prompt) in zip(records, want_tasks):
        assert record["argv"] == ["--print", "--dangerously-skip-permissions"]
        assert record["stdin"] == expected_stdin(prompt_body.strip(), id, name, prompt)

    after = read_tasks_yaml(tasks_path)
    assert [task["id"] for task in after["tasks"]] == [20, 30, 10]
    assert task_states(after) == {20: "completed", 30: "completed", 10: "completed"}
    assert_no_tmp_file(tasks_path)


def test_loop_failed_task_refuses_to_run(tmp_path: Path, fake_claude: Path) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_yaml = """tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
  - id: 2
    name: Second task
    prompt: Do the second thing.
    state: failed
    error: boom
"""
    tasks_path = write_tasks_yaml(tmp_path, tasks_yaml)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code == 1, res
    assert "Some tasks failed previous runs:\n" in res.out
    assert "   1  PENDING    First task\n" in res.out
    assert (
        "❌  2  \033[1;91mFAILED   \033[0m  Second task  \033[1;91m← Needs review!\033[0m\n"
        in res.out
    )
    assert (
        "fix the failed tasks and set their state to pending before running" in res.err
    )
    assert fake_claude_records(record_file) == []
    assert tasks_path.read_text(encoding="utf-8") == tasks_yaml
    assert_no_tmp_file(tasks_path)


def test_loop_stops_on_first_failure(tmp_path: Path, fake_claude: Path) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(tmp_path, TWO_TASKS_YAML)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={
            "FAKECLAUDE_EXIT_CODE": "3",
            "FAKECLAUDE_RECORD_FILE": str(record_file),
            "FAKECLAUDE_STDERR": "claude-stderr-marker",
        },
    )

    assert res.code != 0, res
    assert "task 1: First task failed: exit status 3" in res.err
    assert "claude-stderr-marker" in res.err
    assert len(fake_claude_records(record_file)) == 1
    assert "Do the second thing." not in res.out
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "failed", 2: "pending"}
    assert_no_tmp_file(tasks_path)


def test_loop_prompt_echoed_to_stdout(tmp_path: Path, fake_claude: Path) -> None:
    prompt_path = write_prompt(tmp_path, "Follow the runbook.\n")
    tasks_path = write_tasks_yaml(tmp_path, TWO_TASKS_YAML)

    res = run_alr(*run_args(prompt_path, tasks_path))

    assert res.code == 0, res
    shared = "Follow the runbook."
    assert expected_stdin(shared, 1, "First task", "Do the first thing.") in res.out
    assert expected_stdin(shared, 2, "Second task", "Do the second thing.") in res.out


def test_loop_claude_missing_from_path(tmp_path: Path) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(
        tmp_path, "tasks:\n  - id: 1\n    name: Only task\n    prompt: Do the thing.\n"
    )
    empty_path_dir = tmp_path / "empty"
    empty_path_dir.mkdir()

    res = run_alr(*run_args(prompt_path, tasks_path), env={"PATH": str(empty_path_dir)})

    assert res.code != 0, res
    assert "task 1: Only task failed: claude: not found on PATH" in res.err


def test_iterations_flag_rejected() -> None:
    """Without the unknown-flag line and exit code 2, a returning flag would pass as a missing --prompt."""
    res = run_alr("--iterations=3")

    assert res.code == 2, res
    assert "unrecognized arguments: --iterations=3\n" in res.err
    assert "required flag" not in res.err


def assert_startup_failure(tmp_path: Path, prompt: str, tasks: str, want: str) -> None:
    """The run fails before claude is ever started, and says why."""
    prompt_path = write_prompt(tmp_path, prompt)
    tasks_path = write_tasks_yaml(tmp_path, tasks)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(prompt_path, tasks_path),
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert res.code != 0, res
    assert "failed to start loop runner" in res.err
    assert want in res.err
    assert fake_claude_records(record_file) == []


def test_start_empty_prompt(tmp_path: Path, fake_claude: Path) -> None:
    assert_startup_failure(tmp_path, "", VALID_TASKS_YAML, "the prompt file is empty")


def test_start_whitespace_prompt(tmp_path: Path, fake_claude: Path) -> None:
    assert_startup_failure(
        tmp_path, "   \t\n  ", VALID_TASKS_YAML, "the prompt file is just whitespace"
    )


def test_start_abandoned_state_rejected(tmp_path: Path, fake_claude: Path) -> None:
    assert_startup_failure(
        tmp_path,
        "Body.\n",
        VALID_TASKS_YAML + "    state: abandoned\n",
        'tasks[0] (id 1): state: must be pending, completed, or failed, got "abandoned"',
    )


def test_start_empty_task_list(tmp_path: Path, fake_claude: Path) -> None:
    assert_startup_failure(
        tmp_path, "Body.\n", "tasks: []\n", "tasks: must contain at least one task"
    )
