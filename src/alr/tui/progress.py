"""The run's progress, followed from its events, as the text of a status line."""

import time
from collections.abc import Callable

from alr.durations import format_elapsed
from alr.engine.gates import first_line
from alr.record.events import (
    CommitStarted,
    Event,
    GateStarted,
    RunDone,
    TaskFinished,
    TaskStarted,
)

SEPARATOR = " · "
ELLIPSIS = "…"


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
                label = f"gate {self._gate_number} of {self._gate_count}: {first_line(gate.cmd)}"
                self._start_step(label, now)
            case CommitStarted():
                self._start_step("commit", now)
            case TaskFinished():
                # The engine now stages and diffs; no step is running, so none is shown.
                self._step = ""
            case RunDone():
                self._done = True

    def text(self, width: int | None = None) -> str:
        """The status line for the current moment, or "" before the first task and after the run.

        With a width, the step is cut with an ellipsis so the whole line fits and the
        timers always show; None leaves it whole.
        """
        if self._done or self._run_start is None or not self._task_number:
            return ""

        now = self._clock()
        task = f"task {self._task_number} of {self._total_tasks}"
        run_time = f"run {format_elapsed(now - self._run_start)}"
        if not self._step:
            return SEPARATOR.join([task, run_time])

        step_time = format_elapsed(now - self._step_start)
        step = self._step
        if width is not None:
            # Three separators sit between the four parts, the step among them.
            others = len(task) + len(step_time) + len(run_time) + 3 * len(SEPARATOR)
            step = _fit_step(step, width - others)

        return SEPARATOR.join([task, step, step_time, run_time])

    def _start_step(self, step: str, now: float) -> None:
        self._step = step
        self._step_start = now


def _fit_step(step: str, room: int) -> str:
    """The step, ending in an ellipsis when it is wider than the room it has."""
    if len(step) <= room:
        return step

    return step[: max(room - 1, 0)] + ELLIPSIS
