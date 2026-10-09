"""The dry run: validate the task file and report on it, without starting Claude or writing a file."""

import io
from datetime import datetime
from pathlib import Path
from typing import TextIO

from alr.engine.gates import first_line, gate_limit
from alr.engine.loop import read_tasks
from alr.errors import INVALID_TASK_FILE, RefusedError, TaskFileError
from alr.plain.report import print_tasks, refuse_failed_report, repo_for
from alr.record.runlog import check_writable, run_dir
from alr.tasks import TaskList


async def dry_run(
    out: TextIO, tasks_file: str, gate_timeout: str, sandbox_file: str, commit: bool
) -> None:
    """Apply the checks a real run starts with, in its order, and write what it would run under."""
    task_list = _read_for_dry_run(tasks_file)
    path = run_dir(Path(tasks_file).parent / "logs", datetime.now().astimezone())
    # Held back so its note lands after the gate lines, where it always was.
    repo_notes = io.StringIO()
    repo = await repo_for(repo_notes, tasks_file, commit, str(path))
    refuse_failed_report(out, task_list)
    check_writable(path)
    print_tasks(out, task_list)
    _print_gate_limits(out, task_list, gate_timeout)
    if sandbox_file:
        out.write(f"sandbox settings: {sandbox_file}\n")

    out.write(repo_notes.getvalue())
    if repo is not None:
        out.write(f"commit: {repo.root}\n")

    out.write(f"{tasks_file} is valid\n")


def _read_for_dry_run(tasks_file: str) -> TaskList:
    """Read the task file, failed tasks and all, so a bad file is refused before the repository is looked at."""
    try:
        return read_tasks(tasks_file)
    except TaskFileError as exc:
        raise RefusedError(INVALID_TASK_FILE, str(exc)) from exc


def _print_gate_limits(out: TextIO, task_list: TaskList, override: str) -> None:
    """Write one line per gate: the limit it would run under and where the limit came from."""
    for gate in task_list.gate_list():
        limit, source = gate_limit(gate, override)
        out.write(f"gate: {first_line(gate.cmd)}: {limit} ({source})\n")
