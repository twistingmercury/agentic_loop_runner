"""The record of a run: a ledger of what ran and when, and each task's activity.

It only listens to the run's events; the loop knows nothing about it.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from alr.durations import format_duration
from alr.engine.gates import first_line
from alr.errors import RunLogError
from alr.stream.events import (
    Activity,
    Committed,
    Event,
    GateFinished,
    RunDone,
    SessionFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import FAILED, Task

# The record holds Claude's text and whatever a gate or git printed, so it is
# private to the user.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

_LEDGER_NAME = "run.jsonl"
_APPEND = os.O_CREAT | os.O_WRONLY | os.O_APPEND


@dataclass(frozen=True)
class Info:
    """What the run_started line says about the run."""

    version: str
    tasks_file: str
    permissions: str  # "sandbox" or "skip"
    sandbox_settings: str = ""  # the settings file's path, "" when not passed
    gate_timeout: str = ""  # the --gate-timeout value, "" when not passed
    commit: bool = False


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
        raise RunLogError(f"{path}: {exc.strerror}") from exc

    # errors="replace" because Claude's text can hold a lone surrogate, which
    # UTF-8 cannot encode; it is written as "?" rather than failing the run.
    return os.fdopen(fd, "a", encoding="utf-8", errors="replace", newline="\n")


def _close(file: TextIO | None, path: Path) -> None:
    if file is None:
        return

    try:
        file.close()
    except OSError as exc:
        raise RunLogError(f"{path}: {exc.strerror}") from exc


def _rfc3339(stamp: datetime) -> str:
    # Written like Go's RFC 3339: the clock's own offset, and Z for UTC.
    text = stamp.isoformat(timespec="seconds")
    return text.removesuffix("+00:00") + "Z" if text.endswith("+00:00") else text


class RunLog:
    """One run's record, written as the run's events arrive."""

    def __init__(self, path: Path, now: Callable[[], datetime]) -> None:
        self._path = path
        self._now = now
        self._ledger: TextIO | None = None
        self._detail: TextIO | None = None
        self._detail_path = path
        # Says the last finished task failed, which is what tells a failed run
        # from one stopped for any other reason.
        self._failed = False

    @classmethod
    def open(
        cls,
        path: Path,
        info: Info,
        now: Callable[[], datetime] = _local_now,
    ) -> "RunLog":
        """Create path, which must not exist, and start the ledger with the
        run_started line."""
        try:
            path.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
            # Not exist_ok: an existing run folder holds another run's record.
            path.mkdir(mode=_DIR_MODE)
        except OSError as exc:
            raise RunLogError(f"{path}: {exc.strerror}") from exc

        log = cls(path, now)
        try:
            log._start(info)
        except RunLogError:
            log.close()
            raise

        return log

    def _start(self, info: Info) -> None:
        self._ledger = _open_append(self._path / _LEDGER_NAME)
        fields: dict[str, Any] = {
            "version": info.version,
            "tasks_file": info.tasks_file,
            "permissions": info.permissions,
        }
        if info.sandbox_settings:
            fields["sandbox_settings"] = info.sandbox_settings

        if info.gate_timeout:
            fields["gate_timeout"] = info.gate_timeout

        fields["commit"] = info.commit
        self._write_ledger("run_started", fields)

    def record(self, event: Event) -> None:
        """Write what event adds to the record. An error means the record is no
        longer complete; the caller stops the run."""
        self._dispatch(event)

    def close(self) -> None:
        """Close the record's files. Safe to call twice."""
        detail, ledger = self._detail, self._ledger
        self._detail = self._ledger = None
        # The ledger closes even when the detail file's close fails.
        try:
            _close(detail, self._detail_path)
        finally:
            _close(ledger, self._path / _LEDGER_NAME)

    def _dispatch(self, event: Event) -> None:
        match event:
            case TaskStarted():
                self._task_started(event)
            case Activity():
                self._activity(event)
            case SessionFinished():
                self._step_finished("session_finished", event.task, event.seconds)
            case GateFinished():
                self._gate_finished(event)
            case Committed():
                self._write_ledger(
                    "committed", {"task": event.task.id, "hash": event.commit_hash}
                )
            case TaskFinished():
                self._task_finished(event)
            case RunDone():
                self._run_done(event)

    def _write(self, file: TextIO | None, path: Path, text: str) -> None:
        if file is None:
            raise RunLogError(f"{path}: the run log is not open")

        try:
            file.write(text)
            file.flush()
        except OSError as exc:
            raise RunLogError(f"{path}: {exc.strerror}") from exc

    def _write_ledger(self, event: str, fields: dict[str, Any]) -> None:
        line = {"time": _rfc3339(self._now()), "event": event, **fields}
        text = json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n"
        self._write(self._ledger, self._path / _LEDGER_NAME, text)

    def _task_started(self, event: TaskStarted) -> None:
        # Checked first so a closed log does not open a detail file it would leak.
        if self._ledger is None:
            raise RunLogError(f"{self._path / _LEDGER_NAME}: the run log is not open")

        self._detail_path = self._path / f"task-{event.task.id}.log"
        self._detail = _open_append(self._detail_path)
        self._failed = False
        self._write_ledger(
            "task_started", {"task": event.task.id, "name": event.task.name}
        )

    def _activity(self, event: Activity) -> None:
        clock = self._now().strftime("%H:%M:%S")
        self._write(self._detail, self._detail_path, f"{clock} {event.line}\n")

    def _step_finished(self, event: str, task: Task, seconds: float) -> None:
        fields: dict[str, Any] = {"task": task.id, "state": task.state}
        if task.error:
            fields["error"] = task.error

        fields["duration"] = format_duration(seconds)
        self._write_ledger(event, fields)

    def _gate_finished(self, event: GateFinished) -> None:
        self._write_ledger(
            "gate_finished",
            {
                "task": event.task.id,
                "cmd": first_line(event.gate.cmd),
                "result": _gate_result(event),
                "timeout": event.limit,
                "duration": format_duration(event.seconds),
            },
        )

    def _task_finished(self, event: TaskFinished) -> None:
        self._failed = event.task.state == FAILED
        self._step_finished("task_finished", event.task, event.seconds)
        detail, self._detail = self._detail, None
        _close(detail, self._detail_path)

    def _run_done(self, event: RunDone) -> None:
        fields: dict[str, Any] = {"result": "completed"}
        if event.error is not None:
            fields["result"] = "failed" if self._failed else "stopped"
            if event.error:
                fields["error"] = event.error

        fields["duration"] = format_duration(event.seconds)
        self._write_ledger("run_finished", fields)


def _gate_result(event: GateFinished) -> str:
    if event.timed_out:
        return "timed_out"

    if event.error is not None:
        return "failed"

    return "passed"
