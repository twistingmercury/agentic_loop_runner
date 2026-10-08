"""The run's progress, followed from its events, as the text of a status line."""

import time
from collections.abc import Callable

from alr.durations import format_elapsed
from alr.record.events import CommitStarted, Event, GateStarted, RunDone, TaskStarted
from alr.tasks import Gate

SEPARATOR = " · "


def _gate_label(gate: Gate) -> str:
    """The gate's command as the line shows it: only its first line fits."""
    lines = gate.cmd.strip().splitlines()
    return lines[0] if lines else ""


class Progress:
    """Follows a run's events and says where the run is and how long it has taken.

    The view only feeds it events and asks for the text; all the logic lives here.
    """

    def __init__(
        self,
        total_tasks: int,
        gate_count: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._total_tasks = total_tasks
        self._gate_count = gate_count
        self._clock = clock
        self._run_start: float | None = None
        self._step_start = 0.0
        self._task_number = 0
        self._gate_number = 0
        self._step = ""
        self._done = False

    def feed(self, event: Event) -> None:
        """Take any run event and move the progress along."""
        now = self._clock()
        if self._run_start is None:
            self._run_start = now

        match event:
            case TaskStarted():
                self._task_number += 1
                self._gate_number = 0
                self._start_step("session", now)
            case GateStarted(gate=gate):
                self._gate_number += 1
                label = f"gate {self._gate_number} of {self._gate_count}: {_gate_label(gate)}"
                self._start_step(label, now)
            case CommitStarted():
                self._start_step("commit", now)
            case RunDone():
                self._done = True

    def text(self) -> str:
        """The status line for the current moment, or "" before the first task and after the run."""
        if self._done or self._run_start is None or not self._task_number:
            return ""

        now = self._clock()
        parts = [
            f"task {self._task_number} of {self._total_tasks}",
            self._step,
            format_elapsed(now - self._step_start),
            f"run {format_elapsed(now - self._run_start)}",
        ]
        return SEPARATOR.join(parts)

    def _start_step(self, step: str, now: float) -> None:
        self._step = step
        self._step_start = now
