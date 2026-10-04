"""SIGINT and SIGTERM stop a run: the session's whole process group dies and nothing is saved."""

import contextlib
import os
import signal
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from e2e_helpers import (
    assert_no_tmp_file,
    run_args,
    start_alr,
    wait_for_file,
    wait_process_gone,
    write_prompt,
    write_tasks_yaml,
)

TASKS_YAML = """tasks:
  - id: 1
    name: First task
    prompt: Block forever.
  - id: 2
    name: Second task
    prompt: Should never start.
"""


@pytest.mark.parametrize(
    "sig", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"]
)
def test_loop_signal_kills_process_tree(
    tmp_path: Path, fake_claude: Path, sig: signal.Signals
) -> None:
    """Covers gralph's TestLoop_SIGINT_KillsProcessTree and TestLoop_SIGTERM_KillsProcessTree."""
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(tmp_path, TASKS_YAML)
    ready_file = tmp_path / "ready"
    descendant_pid_file = tmp_path / "descendant.pid"
    env = {
        "FAKECLAUDE_BLOCK": "1",
        "FAKECLAUDE_READY_FILE": str(ready_file),
        "FAKECLAUDE_DESCENDANT_PID_FILE": str(descendant_pid_file),
    }

    with stopped_at_exit(
        start_alr(*run_args(prompt_path, tasks_path), env=env),
        ready_file,
        descendant_pid_file,
    ) as process:
        # The fake has spawned its descendant and is blocking once this exists.
        claude_pid = int(wait_for_file(ready_file, 10))
        descendant_pid = int(wait_for_file(descendant_pid_file, 5))

        process.send_signal(sig)
        out, err = process.communicate(timeout=10)

        assert process.returncode == 1, (out, err)
        assert "error: loop error: task 1: First task: stopped\n" in err
        assert "Traceback" not in err
        assert tasks_path.read_text(encoding="utf-8") == TASKS_YAML
        assert_no_tmp_file(tasks_path)
        assert "Should never start." not in out
        # The descendant proves the whole group was killed, not just the direct child.
        wait_process_gone(claude_pid, 10)
        wait_process_gone(descendant_pid, 10)


def test_sigint_exits_while_an_escaped_descendant_holds_the_session_pipe(
    tmp_path: Path,
) -> None:
    prompt_path = write_prompt(tmp_path, "Body.\n")
    tasks_path = write_tasks_yaml(tmp_path, TASKS_YAML)
    ready_file = tmp_path / "ready"
    session_pid_file = tmp_path / "session.pid"
    sleeper_pid_file = tmp_path / "sleeper.pid"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # setsid takes the sleeper out of the group alr kills, and it keeps the
    # session's stdout open; its stderr goes away so it cannot also hold the
    # test's pipe. Short, so a failing run cleans up on its own.
    claude = bin_dir / "claude"
    claude.write_text(
        "#!/bin/sh\n"
        f"echo $$ > {session_pid_file}\n"
        "setsid sleep 5 2>/dev/null &\n"
        f"echo $! > {sleeper_pid_file}\n"
        f"echo ready > {ready_file}\n"
        "exec sleep 60\n",
        encoding="utf-8",
    )
    claude.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    with stopped_at_exit(
        start_alr(*run_args(prompt_path, tasks_path), env=env),
        session_pid_file,
        sleeper_pid_file,
    ) as process:
        wait_for_file(ready_file, 10)

        process.send_signal(signal.SIGINT)
        # Well before the sleeper exits, so a hang fails here.
        out, err = process.communicate(timeout=3)

        assert process.returncode == 1, (out, err)
        assert "error: loop error: task 1: First task: stopped\n" in err
        assert "Traceback" not in err
        assert tasks_path.read_text(encoding="utf-8") == TASKS_YAML
        assert_no_tmp_file(tasks_path)


@contextlib.contextmanager
def stopped_at_exit(
    process: subprocess.Popen[str], *pid_files: Path
) -> Iterator[subprocess.Popen[str]]:
    """Kill alr and every process named in pid_files on the way out, so a failing test leaves nothing running."""
    try:
        with process:
            try:
                yield process
            finally:
                process.kill()
    finally:
        for pid_file in pid_files:
            kill_pid_in(pid_file)


def kill_pid_in(pid_file: Path) -> None:
    try:
        os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
    except (OSError, ValueError):
        # No file, or the process is already gone: nothing is left to stop.
        pass
