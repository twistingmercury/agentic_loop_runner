"""The plain real run: load the inputs, check the repository, then run every pending task."""

import sys
from collections.abc import Sequence
from datetime import datetime
from importlib import metadata
from pathlib import Path

from alr.engine.commit import CommitMode, Repo
from alr.engine.loop import RunOptions, read_tasks, reset_failed, run
from alr.engine.stop import Stop
from alr.errors import (
    INVALID_TASK_FILE,
    CommitFailedError,
    RefusedError,
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
    stop: Stop | None = None,
) -> None:
    """Run the loop in plain mode: load, check, then run every pending task.

    With reset, failed tasks go back to pending, without a word, once the
    repository checks have passed: a refused run changes nothing on disk.
    Every run is logged in a new folder under logs/ beside the task file.
    """
    _read_for_start(tasks_file)
    commits = commit is not CommitMode.NONE
    path = run_dir(Path(tasks_file).parent / "logs", datetime.now().astimezone())
    repo = await repo_for(sys.stdout, tasks_file, commits, str(path))
    if reset:
        reset_failed(tasks_file)

    task_list = load_tasks_report(sys.stdout, tasks_file)
    info = _info(tasks_file, gate_timeout, sandbox_settings, commit)
    log = RunLog.open(path, info)
    try:
        await _run_logged(
            task_list,
            tasks_file,
            gate_timeout,
            session_args,
            repo,
            commit,
            log,
            stop or Stop(),
        )
    finally:
        log.close()


def _read_for_start(tasks_file: str) -> TaskList:
    """Read the task file, failed tasks and all, so a bad file is refused before the repository is looked at."""
    try:
        return read_tasks(tasks_file)
    except TaskFileError as exc:
        raise RefusedError(
            INVALID_TASK_FILE, f"failed to start loop runner: {exc}"
        ) from exc


def _info(
    tasks_file: str, gate_timeout: str, sandbox_settings: str, commit: CommitMode
) -> Info:
    return Info(
        version=metadata.version("alr"),
        tasks_file=tasks_file,
        permissions="sandbox" if sandbox_settings else "skip",
        sandbox_settings=sandbox_settings,
        gate_timeout=gate_timeout,
        commit=commit.value,
    )


async def _run_logged(
    task_list: TaskList,
    tasks_file: str,
    gate_timeout: str,
    session_args: Sequence[str],
    repo: Repo | None,
    commit: CommitMode,
    log: RunLog,
    stop: Stop,
) -> None:
    options = RunOptions(
        tasks_file=tasks_file,
        gate_timeout=gate_timeout,
        session_args=session_args,
        repo=repo,
        commit_mode=commit,
        stop=stop,
    )
    try:
        await run(task_list, options, PlainFrontEnd(record=log.record))
    except RunCancelledError as exc:
        raise RunCancelledError(f"loop error: {exc}") from exc
    except CommitFailedError as exc:
        # Its own class, so the exit code 4 survives the prefix.
        raise CommitFailedError(f"loop error: {exc}") from exc
    except RunError as exc:
        raise RunError(f"loop error: {exc}") from exc
