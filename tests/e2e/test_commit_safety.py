"""The commit-safety guards: one committing run per repository, ignore rules re-checked, git tied to alr's life.

The fake claude changes no files, so an action does the "work" a commit picks up.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from e2e_helpers import (
    ONE_TASK,
    fake_claude_records,
    read_tasks_yaml,
    run_alr,
    run_args,
    shared_block,
    start_alr,
    task_states,
    wait_for_file,
    wait_process_gone,
    write_tasks_yaml,
)
from test_commit_run import git
from test_outcomes import (
    ONE_WORK_TASK_YAML,
    assert_run_finished,
    install_pre_commit_hook,
)
from test_signal import stopped_at_exit

MODES = ["task", "run"]


def start_blocked_run(tasks_path: Path, ready_file: Path) -> subprocess.Popen[str]:
    """Start a committing run whose session blocks, so it holds the lock until it is stopped."""
    env = {"FAKECLAUDE_BLOCK": "1", "FAKECLAUDE_READY_FILE": str(ready_file)}
    return start_alr(*run_args(tasks_path), "--commit", "run", env=env)


def test_second_committing_run_is_refused(git_repo: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)
    ready_file = tasks_path.parent / "ready"
    second_record = tasks_path.parent / "second.ndjson"

    with stopped_at_exit(start_blocked_run(tasks_path, ready_file), ready_file):
        # The session is blocking, so the first run is past its start and holds the lock.
        wait_for_file(ready_file, 10)

        res = run_alr(
            *run_args(tasks_path),
            "--commit",
            "run",
            env={"FAKECLAUDE_RECORD_FILE": str(second_record)},
        )

    assert res.code == 3, res
    assert res.err.splitlines()[0].startswith("error: refused: locked: "), res
    assert fake_claude_records(second_record) == [], "claude must not run"
    # A refusal writes nothing, so the only run folder is the first run's.
    assert len(list((tasks_path.parent / "logs").iterdir())) == 1


def test_lock_is_free_after_a_kill(git_repo: Path, fake_claude: Path) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)
    ready_file = tasks_path.parent / "ready"

    with stopped_at_exit(
        start_blocked_run(tasks_path, ready_file), ready_file
    ) as process:
        wait_for_file(ready_file, 10)

        # SIGKILL runs none of alr's own cleanup: only the kernel can free the lock.
        process.kill()
        process.wait(timeout=10)

        res = run_alr(*run_args(tasks_path), "--commit", "run")

    assert res.code == 0, res
    assert git(git_repo, "log", "--format=%s").splitlines() == [
        "alr: 1 task completed",
        "init",
    ]


@pytest.mark.parametrize("mode", MODES)
def test_unignored_task_file_fails_the_commit(
    git_repo: Path, fake_claude: Path, mode: str
) -> None:
    # Emptying .gitignore un-ignores run/, and the task file with it.
    action = f"echo one > {git_repo}/one.txt; : > {git_repo}/.gitignore"
    tasks_yaml = shared_block("Body.", [{"action": action}]) + ONE_TASK
    tasks_path = write_tasks_yaml(git_repo / "run", tasks_yaml)

    res = run_alr(*run_args(tasks_path), "--commit", mode)

    assert res.code == 4, res
    assert "task file visible: run/tasks.yaml" in res.err
    assert git(git_repo, "log", "--format=%s").splitlines() == ["init"]
    assert git(git_repo, "diff", "--cached", "--name-only") == "", "nothing staged"
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "failed"}
    assert_run_finished(tasks_path.parent, "commit_failed", 4)


@pytest.mark.skipif(
    sys.platform != "linux", reason="only Linux has PR_SET_PDEATHSIG, which stops git"
)
@pytest.mark.parametrize("mode", MODES)
def test_sigkill_during_a_commit_hook_lands_no_commit(
    git_repo: Path, fake_claude: Path, mode: str
) -> None:
    tasks_path = write_tasks_yaml(git_repo / "run", ONE_WORK_TASK_YAML)
    run_dir = tasks_path.parent
    git_pid_file = run_dir / "git.pid"
    hook_pid_file = run_dir / "hook.pid"
    release_file = run_dir / "release"
    # The hook is what keeps git busy while alr dies, and it ends only when the
    # test says so, after alr is gone, so no timing decides the outcome. A git
    # that outlives alr finishes the commit once the hook ends.
    install_pre_commit_hook(
        git_repo,
        f"echo $PPID > {git_pid_file}\n"
        f"echo $$ > {hook_pid_file}\n"
        f"while [ ! -e {release_file} ]; do sleep 0.05; done",
    )

    with stopped_at_exit(
        start_alr(*run_args(tasks_path), "--commit", mode), hook_pid_file
    ) as process:
        wait_for_file(hook_pid_file, 10)
        git_pid = int(wait_for_file(git_pid_file, 10))

        process.kill()
        process.wait(timeout=10)
        release_file.write_text("release", encoding="utf-8")

        # Gone either way: stopped with alr, or done once the hook let it commit.
        wait_process_gone(git_pid, 10)

    assert git(git_repo, "log", "--format=%s").splitlines() == ["init"]
