"""Every exit code alr can return, with the result its run log records.

0 completed, 1 failed, 2 usage, 3 refused, 4 after_run failed, 130 and 143 stopped.
"""

import signal
from pathlib import Path

import pytest
from e2e_helpers import (
    ONE_TASK,
    assert_no_tmp_file,
    read_tasks_yaml,
    run_alr,
    run_log,
    run_args,
    shared_block,
    start_alr,
    task_states,
    wait_for_file,
    write_tasks_yaml,
)
from test_signal import stopped_at_exit

ONE_TASK_YAML = shared_block() + ONE_TASK

AFTER_RUN_FAILS = [{"action": "exit 7"}]
AFTER_RUN_FAILS_YAML = shared_block("Body.", [], AFTER_RUN_FAILS) + ONE_TASK
AFTER_RUN_FAILURE = 'action "exit 7" failed: exit status 7'

BLOCKED_TASK_YAML = (
    shared_block()
    + """tasks:
  - id: 1
    name: First task
    prompt: Block forever.
"""
)


def assert_run_finished(folder: Path, result: str, exit_code: int) -> None:
    last = run_log(folder)[-1]

    assert last["event"] == "run_finished"
    assert last["result"] == result
    assert last["exit"] == exit_code


def test_completed_exits_0(tmp_path: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(tmp_path, ONE_TASK_YAML)

    res = run_alr(*run_args(tasks_path))

    assert res.code == 0, res
    assert_run_finished(tmp_path, "completed", 0)


def test_failed_task_exits_1(tmp_path: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(tmp_path, ONE_TASK_YAML)

    res = run_alr(
        *run_args(tasks_path),
        env={"FAKECLAUDE_OUTPUT": '{"state":"failed","error":"boom"}\n'},
    )

    assert res.code == 1, res
    assert_run_finished(tmp_path, "failed", 1)


def test_bad_flag_exits_2(tmp_path: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(tmp_path, ONE_TASK_YAML)

    res = run_alr(*run_args(tasks_path), "--action-timeout", "soon")

    assert res.code == 2, res
    assert not (tmp_path / "logs").exists()


def test_refusal_exits_3_and_writes_no_log(tmp_path: Path, fake_claude: Path) -> None:
    failed_yaml = (
        shared_block()
        + "tasks:\n  - {id: 1, name: First, prompt: p1, state: failed, error: boom}\n"
    )
    tasks_path = write_tasks_yaml(tmp_path, failed_yaml)

    res = run_alr(*run_args(tasks_path))

    assert res.code == 3, res
    assert res.err.splitlines()[0].startswith("error: refused: failed-tasks: ")
    assert not (tmp_path / "logs").exists()


def test_reset_failed_with_an_unwritable_log_folder_leaves_the_file_alone(
    tmp_path: Path, fake_claude: Path
) -> None:
    failed_yaml = (
        shared_block()
        + "tasks:\n  - {id: 1, name: First, prompt: p1, state: failed, error: boom}\n"
    )
    tasks_path = write_tasks_yaml(tmp_path, failed_yaml)
    # A file where the log folder belongs fails for every user, root included.
    (tmp_path / "logs").write_text("a file, not a folder", encoding="utf-8")

    res = run_alr(*run_args(tasks_path), "--reset-failed")

    assert res.code == 3, res
    assert res.err.splitlines()[0].startswith("error: refused: log-unwritable: ")
    assert tasks_path.read_text(encoding="utf-8") == failed_yaml


def test_reset_failed_keeps_a_users_own_tmp_file(
    tmp_path: Path, fake_claude: Path
) -> None:
    failed_yaml = (
        shared_block()
        + "tasks:\n  - {id: 1, name: First, prompt: p1, state: failed, error: boom}\n"
    )
    tasks_path = write_tasks_yaml(tmp_path, failed_yaml)
    users = tmp_path / "tasks.yaml.tmp"
    users.write_text("precious", encoding="utf-8")

    res = run_alr(*run_args(tasks_path), "--reset-failed")

    assert res.code == 0, res
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}
    assert users.read_bytes() == b"precious"
    assert_no_tmp_file(tasks_path)


def test_after_run_failure_exits_4_and_leaves_the_tasks_completed(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, AFTER_RUN_FAILS_YAML)

    res = run_alr(*run_args(tasks_path))

    assert res.code == 4, res
    assert AFTER_RUN_FAILURE in res.err
    assert_run_finished(tmp_path, "run_failed", 4)
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}


@pytest.mark.parametrize(
    "sig", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"]
)
def test_a_signal_exits_128_plus_its_number(
    tmp_path: Path, fake_claude: Path, sig: signal.Signals
) -> None:
    """130 for SIGINT and 143 for SIGTERM, with the stop on the run log."""
    tasks_path = write_tasks_yaml(tmp_path, BLOCKED_TASK_YAML)
    ready_file = tmp_path / "ready"
    env = {"FAKECLAUDE_BLOCK": "1", "FAKECLAUDE_READY_FILE": str(ready_file)}

    with stopped_at_exit(
        start_alr(*run_args(tasks_path), env=env), ready_file
    ) as process:
        # The fake is blocking inside the session once this exists.
        wait_for_file(ready_file, 10)

        process.send_signal(sig)
        out, err = process.communicate(timeout=10)

        assert process.returncode == 128 + sig, (out, err)
        assert_run_finished(tmp_path, "stopped", 128 + sig)


def test_after_run_failure_has_no_task_in_the_run_finished_error(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, AFTER_RUN_FAILS_YAML)

    run_alr(*run_args(tasks_path))

    assert run_log(tmp_path)[-1]["error"] == AFTER_RUN_FAILURE


def test_an_after_task_failure_names_the_task_in_the_run_finished_error(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(
        tmp_path, shared_block("Body.", [{"action": "exit 7"}]) + ONE_TASK
    )

    res = run_alr(*run_args(tasks_path))

    assert res.code == 1, res
    assert (
        run_log(tmp_path)[-1]["error"]
        == f"task 1: First task failed: {AFTER_RUN_FAILURE}"
    )
    assert_run_finished(tmp_path, "failed", 1)


def test_the_ledger_says_completed_once_the_actions_passed(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(
        tmp_path, shared_block("Body.", [{"action": "true"}]) + ONE_TASK
    )

    res = run_alr(*run_args(tasks_path))

    assert res.code == 0, res
    finished = [e for e in run_log(tmp_path) if e["event"] == "task_finished"]
    assert [e["state"] for e in finished] == ["completed"]


def test_a_signal_during_an_after_task_action_exits_143(
    tmp_path: Path, fake_claude: Path
) -> None:
    ready_file = tmp_path / "ready"
    action = {"action": f"echo $$ > '{ready_file}'; exec sleep 60"}
    tasks_yaml = shared_block("Body.", [action]) + ONE_TASK
    tasks_path = write_tasks_yaml(tmp_path, tasks_yaml)

    with stopped_at_exit(start_alr(*run_args(tasks_path)), ready_file) as process:
        wait_for_file(ready_file, 10)

        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=15)

        assert process.returncode == 143, (out, err)
        assert_run_finished(tmp_path, "stopped", 143)
        assert tasks_path.read_text(encoding="utf-8") == tasks_yaml
