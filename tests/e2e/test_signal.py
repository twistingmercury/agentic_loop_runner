"""SIGINT and SIGTERM stop a run: the session's whole process group dies and nothing is saved."""

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from e2e.conftest import (
    assert_no_tmp_file,
    run_args,
    start_alr,
    wait_for_file,
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


def wait_process_gone(pid: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while process_alive(pid):
        assert time.monotonic() < deadline, f"process {pid} is still alive"
        time.sleep(0.05)


def process_alive(pid: int) -> bool:
    """Report whether pid is a live process that is not a zombie.

    A signal 0 alone is not enough on Linux: it still succeeds for a process
    that has exited but is not reaped yet, which an orphaned descendant can
    briefly be.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass

    if sys.platform != "linux":
        return True

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False

    # The state follows the command name, which is in parentheses and may hold spaces.
    fields = stat.rpartition(")")[2].split()
    return not fields or fields[0] not in ("Z", "X")
