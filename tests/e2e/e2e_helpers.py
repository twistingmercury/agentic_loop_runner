"""Helpers for the black-box tests: they run the installed alr command against the fake claude.

Plain functions live here and fixtures in conftest.py. The suite never imports
alr; the command line is its only way in.

Ported from gralph's tests/e2e. Not ported: skill_check_test.go and
wizard_signal_linux_test.go. --install-skill and the full-screen view are
covered by test_install_skill.py and test_tui.py.
"""

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

SHARED_PROMPT = "Follow the runbook."

ONE_TASK = """tasks:
  - id: 1
    name: First task
    prompt: Do the thing.
"""

TWO_TASKS = """tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
  - id: 2
    name: Second task
    prompt: Do the second thing.
"""

ONE_TASK_TABLE = (
    "   ID  STATE      NAME\n   --  ---------  ----\n    1  PENDING    First task\n"
)


def find_alr() -> str:
    """The alr command on PATH; the suite has nothing to test without it."""
    found = shutil.which("alr")
    if found is None:
        raise pytest.UsageError(
            "the alr command is not on PATH: install the alr wheel before running these tests"
        )

    return found


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str


def run_alr(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float = 60.0,
) -> Result:
    """Run the real alr command, with no terminal, and return what it printed."""
    done = subprocess.run(
        [find_alr(), *args],
        env={**os.environ, **(env or {})},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return Result(done.returncode, done.stdout, done.stderr)


def write_tasks_yaml(folder: Path, content: str) -> Path:
    path = folder / "tasks.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def shared_block(
    prompt: str = SHARED_PROMPT,
    after_task: list[dict[str, str]] | None = None,
    after_run: list[dict[str, str]] | None = None,
) -> str:
    """The shared: block that opens a task file; put the tasks after it."""
    shared: dict[str, Any] = {"prompt": prompt}
    if after_task is not None:
        shared["after_task"] = after_task

    if after_run is not None:
        shared["after_run"] = after_run

    return yaml.safe_dump({"shared": shared}, sort_keys=False)


def run_args(tasks_path: Path) -> list[str]:
    """The flags of an ordinary unsandboxed run; the task file is the folder's tasks.yaml."""
    return ["--yolo", f"--dir={tasks_path.parent}"]


VALID_TASKS_YAML = shared_block() + ONE_TASK
TWO_TASKS_YAML = shared_block() + TWO_TASKS


def expected_stdin(shared_prompt: str, id: int, name: str, prompt: str) -> str:
    """The exact text a session gets on stdin: the trimmed shared prompt, then the task."""
    task_text = f"{id}: {name}\n\n{prompt}".strip()
    return f"{shared_prompt}\n\n{task_text}\n"


def read_tasks_yaml(path: Path) -> dict[str, Any]:
    """Parse the task file as it is on disk now."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def task_states(task_list: dict[str, Any]) -> dict[int, str]:
    return {task["id"]: task["state"] for task in task_list["tasks"]}


def task_errors(task_list: dict[str, Any]) -> dict[int, str]:
    return {task["id"]: task.get("error", "") for task in task_list["tasks"]}


def read_task_log(tasks_path: Path, task_id: int) -> str:
    """The text of a task's log from the run's one folder under <dir>/logs."""
    (folder,) = (tasks_path.parent / "logs").iterdir()
    return (folder / f"task-{task_id}.log").read_text(encoding="utf-8")


def run_log(folder: Path) -> list[dict[str, Any]]:
    """The run's ledger: the one run.jsonl under <folder>/logs, one entry per line."""
    (run_folder,) = (folder / "logs").iterdir()
    lines = (run_folder / "run.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def assert_no_tmp_file(tasks_path: Path) -> None:
    """A save either finished or never started: its temporary file is gone.

    A save names its temporary file <name>.<random>.tmp; the user's own
    <name>.tmp is not one of them.
    """
    left = sorted(tasks_path.parent.glob(f"{tasks_path.name}.*.tmp"))
    assert not left, f"leftover temp files {left}"


def run_dry_run(tmp_path: Path, tasks_yaml: str, *flags: str) -> tuple[Result, Path]:
    """Dry-run tasks_yaml; assert claude never ran and the task file is untouched."""
    tasks_path = write_tasks_yaml(tmp_path, tasks_yaml)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        "-d",
        str(tasks_path.parent),
        "--dry-run",
        *flags,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert not record_file.exists(), "claude must not run"
    assert tasks_path.read_text(encoding="utf-8") == tasks_yaml
    assert_no_tmp_file(tasks_path)
    return res, tasks_path


def fake_claude_records(path: Path) -> list[dict[str, Any]]:
    """Every fake claude call so far, in order; none when it was never started."""
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def wait_for_file(path: Path, timeout: float = 10) -> str:
    """Poll until path holds something and return it; an empty file is not yet filled."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and (text := path.read_text(encoding="utf-8").strip()):
            return text

        time.sleep(0.01)

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


def wait_process_gone(pid: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while process_is_running(pid):
        assert time.monotonic() <= deadline, f"process {pid} is still alive"
        time.sleep(0.01)


def start_alr(*args: str, env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Start the real alr command without waiting for it, for the signal tests."""
    return subprocess.Popen(
        [find_alr(), *args],
        env={**os.environ, **(env or {})},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
