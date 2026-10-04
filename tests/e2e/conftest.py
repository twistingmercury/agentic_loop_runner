"""Helpers for the black-box tests: they run the installed alr command against the fake claude.

Ported from gralph's tests/e2e. Not ported: install_skill_test.go,
skill_check_test.go, and wizard_signal_linux_test.go. The skill, the wizard,
and the full-screen view are out of scope until the TUI design.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from helpers import ALR

VALID_TASKS_YAML = """tasks:
  - id: 1
    name: First task
    prompt: Do the thing.
"""

TWO_TASKS_YAML = """tasks:
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
        [str(ALR), *args],
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


def write_prompt(folder: Path, body: str) -> Path:
    path = folder / "prompt.md"
    path.write_text(body, encoding="utf-8")
    return path


def run_args(prompt_path: Path, tasks_path: Path) -> list[str]:
    """The flags of an ordinary unsandboxed run."""
    return ["--skip-permissions", f"--prompt={prompt_path}", f"--tasks={tasks_path}"]


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


def assert_no_tmp_file(tasks_path: Path) -> None:
    """A save either finished or never started: its temporary file is gone."""
    tmp = tasks_path.with_name(tasks_path.name + ".tmp")
    assert not tmp.exists(), f"leftover temp file {tmp}"


def run_dry_run(tmp_path: Path, tasks_yaml: str, *flags: str) -> tuple[Result, Path]:
    """Dry-run tasks_yaml; assert claude never ran and the task file is untouched."""
    tasks_path = write_tasks_yaml(tmp_path, tasks_yaml)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        "-t",
        str(tasks_path),
        "--dry-run",
        *flags,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )

    assert not record_file.exists(), "claude must not run"
    assert tasks_path.read_text(encoding="utf-8") == tasks_yaml
    assert_no_tmp_file(tasks_path)
    return res, tasks_path
