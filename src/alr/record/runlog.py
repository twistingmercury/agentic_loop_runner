"""The record of a run: a ledger of what ran and when, and each task's activity.

It only listens to the run's events; the loop knows nothing about it.
"""

import contextlib
import errno
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from alr.durations import format_duration
from alr.errors import LOG_UNWRITABLE, RefusedError, RunLogError
from alr.record.events import (
    Activity,
    Event,
    ActionFinished,
    RunDone,
    SessionFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import RUN_SCOPE, Task

# The record holds Claude's text and whatever an action printed, so it is
# private to the user.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

_LEDGER_NAME = "run.jsonl"
_APPEND = os.O_CREAT | os.O_WRONLY | os.O_APPEND


@dataclass(frozen=True)
class Info:
    version: str
    tasks_file: str
    permissions: str  # "sandbox" or "skip"
    sandbox_settings: str = ""  # the settings file's path, "" when not passed
    action_timeout: str = ""  # the --action-timeout value, "" when not passed


def run_dir(log_dir: Path, start: datetime) -> Path:
    """Name the folder for a run that started at start: its local time, so the
    name matches the clock the user reads."""
    return log_dir / start.strftime("%Y%m%dT%H%M%S")


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _open_append(path: Path) -> TextIO:
    try:
        fd = os.open(path, _APPEND, _FILE_MODE)
    except OSError as exc:
        raise _io_error(path, exc) from exc

    # errors="replace" is the last resort for text that never passed through the
    # stream path's scrub (task names, Info fields): a lone surrogate cannot be
    # encoded as UTF-8, and it is written as "?" rather than failing the run.
    return os.fdopen(fd, "a", encoding="utf-8", errors="replace", newline="\n")


def _io_error(path: Path, exc: OSError) -> RunLogError:
    return RunLogError(f"{path}: {exc.strerror}")


def _missing_parents(path: Path) -> list[Path]:
    """The folders above path that do not exist yet, nearest first."""
    missing: list[Path] = []
    for parent in path.parents:
        if parent.exists():
            break

        missing.append(parent)

    return missing


def _create_run_dir(path: Path) -> tuple[Path, list[Path]]:
    """Make the run's folder, taking the first free suffixed name when path
    exists: an existing run folder holds another run's record.

    Returns the folder and the parents this call had to create, nearest first,
    so a discarded log can take them away again.
    """
    made = _missing_parents(path)
    try:
        path.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        raise _create_error(path, exc) from exc

    candidate = path
    attempt = 0
    while True:
        try:
            candidate.mkdir(mode=_DIR_MODE)
        except FileExistsError:
            attempt += 1
            candidate = path.with_name(f"{path.name}-{attempt}")
            continue
        except OSError as exc:
            raise _create_error(candidate, exc) from exc

        return candidate, made


def check_writable(path: Path) -> None:
    """Refuse, as opening the log would, a path whose run folder cannot be made,
    creating nothing: the dry run must not write, yet must agree with a real run."""
    ancestor = path.parent
    while not _present(ancestor) and ancestor != ancestor.parent:
        ancestor = ancestor.parent

    # A symlink that leads nowhere is there, so mkdir cannot make it a folder.
    if ancestor.is_symlink() and not ancestor.exists():
        raise _refused_unwritable(path, errno.EEXIST)

    if not ancestor.is_dir():
        raise _refused_unwritable(path, errno.ENOTDIR)

    if not os.access(ancestor, os.W_OK | os.X_OK):
        raise _refused_unwritable(path, errno.EACCES)


def _present(path: Path) -> bool:
    """Report whether anything is at path, a symlink that leads nowhere included."""
    return path.is_symlink() or path.exists()


def _refused_unwritable(path: Path, code: int) -> RefusedError:
    return RefusedError(
        LOG_UNWRITABLE, str(_create_error(path, OSError(code, os.strerror(code))))
    )


def _create_error(path: Path, exc: OSError) -> RunLogError:
    return RunLogError(f"cannot create the run log folder {path}: {exc.strerror}")


def _not_open(path: Path) -> RunLogError:
    return RunLogError(f"{path}: the run log is not open")


def _close(file: TextIO | None, path: Path) -> None:
    if file is None:
        return

    try:
        file.close()
    except OSError as exc:
        raise _io_error(path, exc) from exc


def _write(file: TextIO | None, path: Path, text: str) -> None:
    if file is None:
        raise _not_open(path)

    try:
        file.write(text)
        file.flush()
    except OSError as exc:
        raise _io_error(path, exc) from exc


def _rfc3339(stamp: datetime) -> str:
    # Written like Go's RFC 3339: the clock's own offset, and Z for UTC.
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")


class RunLog:
    def __init__(
        self,
        path: Path,
        now: Callable[[], datetime],
        made_parents: Sequence[Path] = (),
    ) -> None:
        self._path = path
        self._made_parents = tuple(made_parents)
        self._ledger_path = path / _LEDGER_NAME
        self._now = now
        self._ledger: TextIO | None = None
        self._detail: TextIO | None = None
        self._detail_path: Path | None = None

    @property
    def path(self) -> Path:
        """The run's folder: the name asked for, or its suffixed one when that was taken."""
        return self._path

    @classmethod
    def open(
        cls,
        path: Path,
        info: Info,
        now: Callable[[], datetime] = _local_now,
    ) -> "RunLog":
        """Create path, or path-1, path-2 and so on when it exists, and start the
        ledger with the run_started line."""
        try:
            path, made_parents = _create_run_dir(path)
        except RunLogError as exc:
            raise RefusedError(LOG_UNWRITABLE, str(exc)) from exc

        log = cls(path, now, made_parents)
        try:
            log._start(info)
        except RunLogError as exc:
            log.discard()
            raise RefusedError(LOG_UNWRITABLE, str(exc)) from exc

        return log

    def _start(self, info: Info) -> None:
        self._ledger = _open_append(self._ledger_path)
        fields: dict[str, Any] = {
            "version": info.version,
            "tasks_file": info.tasks_file,
            "permissions": info.permissions,
        }
        if info.sandbox_settings:
            fields["sandbox_settings"] = info.sandbox_settings

        if info.action_timeout:
            fields["action_timeout"] = info.action_timeout

        self._write_ledger("run_started", fields)

    def record(self, event: Event) -> None:
        """Write what event adds to the record. An error means the record is no
        longer complete; the caller stops the run."""
        match event:
            case TaskStarted():
                self._task_started(event)
            case Activity():
                self._activity(event)
            case SessionFinished():
                self._step_finished("session_finished", event.task, event.seconds)
            case ActionFinished():
                self._action_finished(event)
            case TaskFinished():
                self._task_finished(event)
            case RunDone():
                self._run_done(event)

    def close(self) -> None:
        """Close the record's files. Safe to call twice."""
        detail, ledger = self._detail, self._ledger
        self._detail = self._ledger = None
        # The ledger closes even when the detail file's close fails.
        try:
            _close(detail, self._detail_path or self._path)
        finally:
            _close(ledger, self._ledger_path)

    def discard(self) -> None:
        """Close the record and remove the run folder this log made, and the
        parent folders it had to create for it.

        For a run that was refused after the log opened. Only this log's own
        ledger is deleted, and each folder only if nothing else is in it.
        """
        # The record is being thrown away, so a failing close has nothing to lose.
        with contextlib.suppress(RunLogError):
            self.close()

        # rmdir refuses a folder that holds anything, so it cannot delete more
        # than this log made. Cleanup is best effort: the caller is already
        # raising the real error, and a leftover must not replace it.
        with contextlib.suppress(OSError):
            self._ledger_path.unlink(missing_ok=True)
            self._path.rmdir()
            for parent in self._made_parents:
                parent.rmdir()

    def _write_ledger(self, event: str, fields: dict[str, Any]) -> None:
        line = {"time": _rfc3339(self._now()), "event": event, **fields}
        text = json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n"
        _write(self._ledger, self._ledger_path, text)

    def _task_started(self, event: TaskStarted) -> None:
        # Checked first so a closed log does not open a detail file it would leak.
        if self._ledger is None:
            raise _not_open(self._ledger_path)

        detail_path = self._path / f"task-{event.task.id}.log"
        self._detail = _open_append(detail_path)
        self._detail_path = detail_path
        self._write_ledger(
            "task_started", {"task": event.task.id, "name": event.task.name}
        )

    def _activity(self, event: Activity) -> None:
        clock = self._now().strftime("%H:%M:%S")
        text = f"{clock} {event.line}\n"
        if event.task.id == RUN_SCOPE.id:
            # The after_run actions belong to no task, and may run when no task did.
            self._append_after_finish(self._path / f"task-{RUN_SCOPE.id}.log", text)
            return

        if self._detail is not None or self._detail_path is None:
            _write(self._detail, self._detail_path or self._path, text)
            return

        self._append_after_finish(self._detail_path, text)

    def _append_after_finish(self, detail_path: Path, text: str) -> None:
        """Add a line to a detail file that has no open handle.

        The after_run actions report under the run scope, which no task
        owns; a late line for a task that is done lands in its own file.
        """
        if self._ledger is None:
            raise _not_open(self._ledger_path)

        file = _open_append(detail_path)
        try:
            _write(file, detail_path, text)
        finally:
            _close(file, detail_path)

    def _step_finished(self, event: str, task: Task, seconds: float) -> None:
        fields: dict[str, Any] = {"task": task.id, "state": task.state}
        if task.error:
            fields["error"] = task.error

        fields["duration"] = format_duration(seconds)
        self._write_ledger(event, fields)

    def _action_finished(self, event: ActionFinished) -> None:
        self._write_ledger(
            "action_finished",
            {
                "task": event.task.id,
                # First line only, as the engine quotes it; record cannot import the engine.
                "cmd": event.action.command.split("\n", 1)[0],
                "result": _action_result(event),
                "timeout": event.limit,
                "duration": format_duration(event.seconds),
            },
        )

    def _task_finished(self, event: TaskFinished) -> None:
        self._step_finished("task_finished", event.task, event.seconds)
        detail, self._detail = self._detail, None
        _close(detail, self._detail_path or self._path)

    def _run_done(self, event: RunDone) -> None:
        fields: dict[str, Any] = {"result": event.result.value}
        if event.error:
            fields["error"] = event.error

        fields["exit"] = event.exit_code
        fields["duration"] = format_duration(event.seconds)
        self._write_ledger("run_finished", fields)


def _action_result(event: ActionFinished) -> str:
    if event.timed_out:
        return "timed_out"

    if event.error is not None:
        return "failed"

    return "passed"
