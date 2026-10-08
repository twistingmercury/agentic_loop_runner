"""Helpers for the unit tests.

Plain functions live here and fixtures in conftest.py. pytest puts this folder
on sys.path for every run, so any test module can `from helpers import ...`.
Test modules never import each other.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from alr import tasks
from alr.engine import loop
from alr.engine.commit import CommitMode, Repo
from alr.engine.frontend import FrontEnd
from alr.engine.sandbox import bypass_args
from alr.errors import RunCancelledError
from alr.plain.frontend import PlainFrontEnd
from alr.tasks import Shared, TaskList

SHARED = Shared(prompt="shared")
ALR = Path(sys.executable).parent / "alr"


def write_file(path: Path, content: str) -> str:
    """Write content to path and return the path as the text the command line takes."""
    path.write_text(content, encoding="utf-8")
    return str(path)


def write_nested_file(path: Path, content: str) -> Path:
    """Write content to path, making its folders first, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def saved_tasks(path: Path) -> TaskList:
    return tasks.parse_tasks(path.read_text(encoding="utf-8"))


def fake_claude_records(path: Path) -> list[dict[str, Any]]:
    """Every fake claude call so far, in order; none when it was never started."""
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return done.stdout


def commit_subjects(repo: Path) -> list[str]:
    """The commit subjects, newest first."""
    return git(repo, "log", "--format=%s").strip().split("\n")


async def run_loop(
    task_list: TaskList,
    tasks_file: Path,
    *,
    gate_timeout: str = "",
    session_args: list[str] | None = None,
    repo: Repo | None = None,
    front_end: FrontEnd | None = None,
    commit_mode: CommitMode = CommitMode.TASK,
) -> None:
    """Run the loop on task_list, with the plain front end unless front_end is given."""
    options = loop.RunOptions(
        tasks_file=str(tasks_file),
        gate_timeout=gate_timeout,
        session_args=bypass_args() if session_args is None else session_args,
        repo=repo,
        commit_mode=commit_mode,
    )
    await loop.run(
        task_list, options, PlainFrontEnd() if front_end is None else front_end
    )


async def cancel_run(run: asyncio.Future[None]) -> RunCancelledError:
    """Cancel a run and return the error it ends with."""
    run.cancel()
    with pytest.raises(RunCancelledError) as caught:
        await asyncio.wait_for(run, 5)

    return caught.value


def _written_text(path: Path) -> str:
    """What path holds, or nothing; an empty file is one its writer has not yet filled."""
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def wait_for_file(path: Path, timeout: float = 10) -> str:
    """Poll until path holds something, for tests with no event loop, and return it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if text := _written_text(path):
            return text

        time.sleep(0.01)

    pytest.fail(f"timed out after {timeout}s waiting for {path} to appear")


async def wait_for_text(path: Path, timeout: float = 5) -> str:
    """Wait for a child to write something to path and return it."""
    # Always yield once: when the file is already there, a task the caller just
    # created would otherwise never get to run before the caller cancels it,
    # and cancelling a task that has not started stops nothing it would start.
    await asyncio.sleep(0)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if text := _written_text(path):
            return text

        await asyncio.sleep(0.01)

    pytest.fail(f"timed out after {timeout}s waiting for {path} to appear")


def process_is_running(pid: int) -> bool:
    """Report whether pid is a live process that is not a zombie.

    A signal 0 alone is not enough on Linux: it still succeeds for a process
    that has exited but is not reaped yet, which an orphaned descendant can
    briefly be.
    """
    if not _probe(pid):
        return False

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        # No /proc (macOS), or the process was reaped between the signal probe
        # and this read; probe again to tell the two apart.
        return _probe(pid)

    # The state follows the command name, which is in parentheses and may hold spaces.
    fields = stat.rpartition(")")[2].split()
    return not fields or fields[0] not in ("Z", "X")


def _probe(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists; it just belongs to someone else.
        pass

    return True


def gone_within(pid: int, timeout: float) -> bool:
    """Poll until pid is no longer running; False if it still is after timeout seconds."""
    deadline = time.monotonic() + timeout
    while process_is_running(pid):
        if time.monotonic() > deadline:
            return False

        time.sleep(0.01)

    return True


def wait_process_gone(pid: int, timeout: float) -> None:
    assert gone_within(pid, timeout), f"process {pid} is still alive"


async def wait_until_gone(pid: int, timeout: float = 2) -> None:
    """The same wait for a test that runs an event loop, which a sleep must not block."""
    deadline = asyncio.get_running_loop().time() + timeout
    while process_is_running(pid):
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail(f"process {pid} remained running")

        await asyncio.sleep(0.01)


async def kill_holder_and_let_pipes_close(pid_file: Path) -> None:
    """Kill the background process whose pid a child wrote to pid_file.

    The pid file may appear a moment late, so it is waited for first.
    """
    await wait_for_text(pid_file)
    os.kill(int(pid_file.read_text()), signal.SIGKILL)
    # Closing the pipes lets asyncio finish with the transport; one left open
    # would only be cleaned up after the loop is gone, with a warning.
    await asyncio.sleep(0.05)


async def until(condition: Callable[[], object]) -> None:
    """Poll until the condition is truthy, failing the test after a few seconds.

    The wait yields to the event loop in small steps, so the app under test keeps
    running, and the bound means a condition that never holds fails with a message
    rather than hanging the run.
    """
    for _ in range(300):
        if condition():
            return

        await asyncio.sleep(0.02)

    pytest.fail("timed out waiting for the condition")


def start_alr(*args: str, env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Start the real alr command without waiting for it, for the signal tests."""
    return subprocess.Popen(
        [str(ALR), *args],
        env={**os.environ, **(env or {})},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class HandClock:
    """A clock the test moves by hand, so the view's timers need no waiting."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now
