"""The plain real run: load the inputs, check the repository, then run every pending task."""

import sys
from collections.abc import Sequence

from alr.engine.loop import RunOptions, run
from alr.errors import (
    RunCancelledError,
    RunError,
    TaskFileError,
)
from alr.plain.frontend import PlainFrontEnd
from alr.plain.report import load_tasks_report, repo_for


async def start(
    tasks_file: str,
    gate_timeout: str,
    session_args: Sequence[str],
    commit: bool,
) -> None:
    """Run the loop in plain mode: load, check, then run every pending task."""
    try:
        task_list = load_tasks_report(sys.stdout, tasks_file)
    except TaskFileError as exc:
        # A FailedTasksError is not one, so it passes through without the prefix.
        raise type(exc)(f"failed to start loop runner: {exc}") from exc

    repo = await repo_for(sys.stdout, tasks_file, commit)
    options = RunOptions(
        tasks_file=tasks_file,
        gate_timeout=gate_timeout,
        session_args=session_args,
        repo=repo,
    )
    try:
        await run(task_list, options, PlainFrontEnd())
    except RunCancelledError as exc:
        raise RunCancelledError(f"loop error: {exc}") from exc
    except RunError as exc:
        raise RunError(f"loop error: {exc}") from exc
