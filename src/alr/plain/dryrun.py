"""The dry run: validate the task file and report on it, without starting Claude or writing a file."""

from typing import TextIO

from alr.engine.gates import first_line, gate_limit
from alr.errors import FailedTasksError
from alr.plain.report import load_tasks_report, print_tasks, repo_for
from alr.tasks import TaskList


async def dry_run(
    out: TextIO, tasks_file: str, gate_timeout: str, sandbox_file: str, commit: bool
) -> None:
    """Apply the checks a real run starts with and write what it would run under."""
    try:
        task_list = load_tasks_report(out, tasks_file)
    except FailedTasksError:
        # The notice and the table are the report; the file itself is valid.
        return

    print_tasks(out, task_list)
    _print_gate_limits(out, task_list, gate_timeout)
    if sandbox_file:
        out.write(f"sandbox settings: {sandbox_file}\n")

    repo = await repo_for(out, tasks_file, commit)
    if repo is not None:
        out.write(f"commit: {repo.root}\n")

    out.write(f"{tasks_file} is valid\n")


def _print_gate_limits(out: TextIO, task_list: TaskList, override: str) -> None:
    """Write one line per gate: the limit it would run under and where the limit came from."""
    for gate in task_list.gate_list():
        limit, source = gate_limit(gate, override)
        out.write(f"gate: {first_line(gate.cmd)}: {limit} ({source})\n")
