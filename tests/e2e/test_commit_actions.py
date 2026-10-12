"""A commit is an action: real `git commit` actions run through the installed alr.

alr has no commit concept, so each test sets up a git repository, lists the
actions in the task file, runs alr in the repository, and then looks at what
git, the task file, and the run log say.
"""

import signal
import subprocess
from pathlib import Path

import pytest
import yaml
from e2e_helpers import (
    ONE_TASK,
    fake_claude_records,
    read_tasks_yaml,
    run_alr,
    run_args,
    run_log,
    shared_block,
    start_alr,
    task_states,
    wait_for_file,
    write_tasks_yaml,
)
from test_signal import stopped_at_exit

# The fake claude changes no files, so this action does each task's "work".
WORK = 'echo "$ALR_TASK_NAME" > "task-$ALR_TASK_ID.txt"'

# Copied verbatim from the README and the task file template, so the suite tests
# the action users copy. The long `:(exclude)` form is needed: with an absolute
# path, git reads the short `:!` form as a path from the top of the work tree and
# silently excludes nothing. The diff guard lets a task that changed nothing pass.
COMMIT = (
    'git add -A -- . ":(exclude)$ALR_TASK_FILE" ":(exclude)$ALR_LOG_DIR" '
    '&& (git diff --cached --quiet || git commit -s -m "$ALR_TASK_NAME")'
)

# Like WORK, but the second task changes nothing.
WORK_EXCEPT_TASK_2 = '[ "$ALR_TASK_ID" = 2 ] || ' + WORK


def actions(*commands: str) -> list[dict[str, str]]:
    return [{"action": command} for command in commands]


def tasks_block(*names: str) -> str:
    tasks = [
        {"id": number, "name": name, "prompt": "Do it."}
        for number, name in enumerate(names, start=1)
    ]
    return yaml.safe_dump({"tasks": tasks}, sort_keys=False)


def make_run(
    repo: Path,
    names: list[str],
    after_task: list[str],
    after_run: list[str] | None = None,
) -> Path:
    """Write a run folder inside the repository, where git can see it, and return its task file."""
    folder = repo / "run"
    folder.mkdir()
    shared = shared_block(
        "Body.",
        actions(*after_task),
        None if after_run is None else actions(*after_run),
    )
    return write_tasks_yaml(folder, shared + tasks_block(*names))


def git_output(repo: Path, *args: str) -> list[str]:
    done = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return done.stdout.splitlines()


def git_subjects(repo: Path) -> list[str]:
    """The commit subjects, newest first; the seed commit is left out."""
    return git_output(repo, "log", "--format=%s")[:-1]


def git_committed_files(repo: Path) -> list[str]:
    """Every file any commit touched, so a file added and later removed still shows."""
    return git_output(repo, "log", "--name-only", "--format=")


def test_a_commit_action_commits_each_task(git_repo: Path, fake_claude: Path) -> None:
    tasks_path = make_run(git_repo, ["one", "two"], [WORK, COMMIT])

    res = run_alr(*run_args(tasks_path), cwd=git_repo)

    assert res.code == 0, res
    assert git_subjects(git_repo) == ["two", "one"]
    files = git_committed_files(git_repo)
    assert "task-1.txt" in files
    assert "task-2.txt" in files
    assert "run/tasks.yaml" not in files
    assert not any(name.startswith("run/logs/") for name in files)


def test_a_task_that_changes_nothing_still_completes(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_path = make_run(git_repo, ["one", "two"], [WORK_EXCEPT_TASK_2, COMMIT])

    res = run_alr(*run_args(tasks_path), cwd=git_repo)

    assert res.code == 0, res
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed", 2: "completed"}
    assert git_subjects(git_repo) == ["one"]
    files = git_committed_files(git_repo)
    assert "run/tasks.yaml" not in files
    assert not any(name.startswith("run/logs/") for name in files)


def test_a_hostile_task_name_is_data(git_repo: Path, fake_claude: Path) -> None:
    name = 'fix "auth"; $(touch pwned) `touch pwned2`'
    tasks_path = make_run(git_repo, [name], [WORK, COMMIT])

    res = run_alr(*run_args(tasks_path), cwd=git_repo)

    assert res.code == 0, res
    assert git_subjects(git_repo) == [name]
    assert not (git_repo / "pwned").exists()
    assert not (git_repo / "pwned2").exists()


def test_a_failing_action_fails_the_task_and_stops(
    git_repo: Path, fake_claude: Path
) -> None:
    tasks_path = make_run(git_repo, ["one", "two"], [WORK, "false", COMMIT])

    res = run_alr(*run_args(tasks_path), cwd=git_repo)

    assert res.code == 1, res
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "failed", 2: "pending"}
    # The commit action sits after the failing one, so it never ran.
    assert git_subjects(git_repo) == []


def test_after_run_failure_exits_4_and_tasks_stay_completed(
    tmp_path: Path, fake_claude: Path
) -> None:
    tasks_path = write_tasks_yaml(
        tmp_path, shared_block("Body.", [], actions("exit 9")) + ONE_TASK
    )

    res = run_alr(*run_args(tasks_path), cwd=tmp_path)

    assert res.code == 4, res
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}
    last = run_log(tmp_path)[-1]
    assert last["event"] == "run_finished"
    assert last["result"] == "run_failed"
    assert last["exit"] == 4


def test_a_rerun_retries_after_run_when_every_task_is_completed(
    tmp_path: Path, fake_claude: Path
) -> None:
    # The append comes first, so every execution leaves a line, failing or not.
    action = "echo ran >> ran.txt; test -e marker"
    tasks_path = write_tasks_yaml(
        tmp_path, shared_block("Body.", [], actions(action)) + ONE_TASK
    )
    ran = tmp_path / "ran.txt"
    record_file = tmp_path / "record.ndjson"
    env = {"FAKECLAUDE_RECORD_FILE": str(record_file)}

    first = run_alr(*run_args(tasks_path), cwd=tmp_path, env=env)

    assert first.code == 4, first
    assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}
    assert ran.read_text(encoding="utf-8").splitlines() == ["ran"]
    # The first run started one session, so a missing record file cannot pass for "none".
    sessions_before = len(fake_claude_records(record_file))
    assert sessions_before == 1

    (tmp_path / "marker").touch()

    second = run_alr(*run_args(tasks_path), cwd=tmp_path, env=env)

    assert second.code == 0, second
    assert ran.read_text(encoding="utf-8").splitlines() == ["ran", "ran"]
    # Every task was already completed, so no new session started.
    assert len(fake_claude_records(record_file)) == sessions_before


OLD_SHARED_GATES = "shared:\n  prompt: Body.\n  gates:\n    - cmd: 'true'\n" + ONE_TASK
OLD_CMD_KEY = "shared:\n  prompt: Body.\n  after_task:\n    - cmd: 'true'\n" + ONE_TASK


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        pytest.param(
            ["--commit", "task"],
            2,
            "error: --commit: removed; a commit is an action now: "
            "put it in shared.after_task, see the HOWTO\n",
            id="commit flag",
        ),
        pytest.param(
            ["--gate-timeout", "5s"],
            2,
            "error: --gate-timeout: renamed to --action-timeout\n",
            id="gate-timeout flag",
        ),
    ],
)
def test_old_flags_are_refused_naming_the_replacement(
    tmp_path: Path, fake_claude: Path, args: list[str], code: int, message: str
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, shared_block() + ONE_TASK)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(tasks_path), *args, env={"FAKECLAUDE_RECORD_FILE": str(record_file)}
    )

    assert res.code == code, res
    assert message in res.err
    assert fake_claude_records(record_file) == []
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize(
    ("tasks_yaml", "message"),
    [
        pytest.param(
            OLD_SHARED_GATES,
            "shared.gates: renamed to shared.after_task; see the HOWTO",
            id="shared.gates",
        ),
        pytest.param(
            OLD_CMD_KEY,
            "shared.after_task[0]: cmd: renamed to action; see the HOWTO",
            id="cmd key",
        ),
    ],
)
def test_old_task_file_keys_are_refused_naming_the_replacement(
    tmp_path: Path, fake_claude: Path, tasks_yaml: str, message: str
) -> None:
    tasks_path = write_tasks_yaml(tmp_path, tasks_yaml)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        *run_args(tasks_path), env={"FAKECLAUDE_RECORD_FILE": str(record_file)}
    )

    assert res.code == 3, res
    assert message in res.err
    assert fake_claude_records(record_file) == []
    assert tasks_path.read_text(encoding="utf-8") == tasks_yaml


def test_sigint_during_after_run_exits_130_and_tasks_stay_completed(
    tmp_path: Path, fake_claude: Path
) -> None:
    ready_file = tmp_path / "ready"
    # exec, so the pid in the ready file is the sleep that stopped_at_exit cleans up.
    after_run = actions(f"echo $$ > '{ready_file}'; exec sleep 30")
    tasks_path = write_tasks_yaml(
        tmp_path, shared_block("Body.", [], after_run) + ONE_TASK
    )

    with stopped_at_exit(start_alr(*run_args(tasks_path)), ready_file) as process:
        wait_for_file(ready_file, 10)

        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=15)

        assert process.returncode == 130, (out, err)
        assert process.returncode != 4
        assert task_states(read_tasks_yaml(tasks_path)) == {1: "completed"}
        last = run_log(tmp_path)[-1]
        assert last["result"] == "stopped"
        assert last["exit"] == 130


def test_after_run_actions_get_no_task_variables(
    tmp_path: Path, fake_claude: Path
) -> None:
    folder = tmp_path.resolve()
    seen = folder / "seen.txt"
    report = (
        'printf "%s|%s|%s|%s" "${ALR_TASK_ID-unset}" "${ALR_TASK_NAME-unset}" '
        f'"$ALR_TASK_FILE" "$ALR_LOG_DIR" > "{seen}"'
    )
    tasks_path = write_tasks_yaml(
        folder, shared_block("Body.", [], actions(report)) + ONE_TASK
    )

    res = run_alr(*run_args(tasks_path), cwd=folder)

    assert res.code == 0, res
    assert seen.read_text(encoding="utf-8") == (
        f"unset|unset|{tasks_path}|{folder / 'logs'}"
    )
