"""The plain real run: load the inputs, check the repository, then run every pending task."""

import sys
from collections.abc import Sequence
from datetime import datetime
from importlib import metadata
from pathlib import Path

from alr.engine.commit import CommitMode, Repo
from alr.engine.loop import RunOptions, reset_failed, run
from alr.errors import (
    RunCancelledError,
    RunError,
    TaskFileError,
)
from alr.plain.frontend import PlainFrontEnd
from alr.plain.report import load_tasks_report, repo_for
from alr.record.runlog import Info, RunLog, run_dir
from alr.tasks import TaskList


async def start(
    tasks_file: str,
    gate_timeout: str,
    session_args: Sequence[str],
    commit: CommitMode,
    reset: bool = False,
    sandbox_settings: str = "",
) -> None:
    """Run the loop in plain mode: load, check, then run every pending task.

    With reset, failed tasks go back to pending first, without a word. Every
    run is logged in a new folder under logs/ beside the task file.
    """
    try:
        if reset:
            reset_failed(tasks_file)

        task_list = load_tasks_report(sys.stdout, tasks_file)
    except TaskFileError as exc:
        # A FailedTasksError is not one, so it passes through without the prefix.
        raise type(exc)(f"failed to start loop runner: {exc}") from exc

    commits = commit is not CommitMode.NONE
    path = run_dir(Path(tasks_file).parent / "logs", datetime.now().astimezone())
    repo = await repo_for(sys.stdout, tasks_file, commits, str(path))
    info = _info(tasks_file, gate_timeout, sandbox_settings, commits)
    log = RunLog.open(path, info)
    try:
        await _run_logged(
            task_list, tasks_file, gate_timeout, session_args, repo, commit, log
        )
    finally:
        log.close()


def _info(
    tasks_file: str, gate_timeout: str, sandbox_settings: str, commits: bool
) -> Info:
    return Info(
        version=metadata.version("alr"),
        tasks_file=tasks_file,
        permissions="sandbox" if sandbox_settings else "skip",
        sandbox_settings=sandbox_settings,
        gate_timeout=gate_timeout,
        commit=commits,
    )


async def _run_logged(
    task_list: TaskList,
    tasks_file: str,
    gate_timeout: str,
    session_args: Sequence[str],
    repo: Repo | None,
    commit: CommitMode,
    log: RunLog,
) -> None:
    options = RunOptions(
        tasks_file=tasks_file,
        gate_timeout=gate_timeout,
        session_args=session_args,
        repo=repo,
        commit_mode=commit,
    )
    try:
        await run(task_list, options, PlainFrontEnd(record=log.record))
    except RunCancelledError as exc:
        raise RunCancelledError(f"loop error: {exc}") from exc
    except RunError as exc:
        raise RunError(f"loop error: {exc}") from exc
