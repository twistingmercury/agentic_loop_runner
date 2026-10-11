"""The run's progress, followed from its events, as the text of a status line."""

import time
from collections.abc import Callable

from alr.durations import format_elapsed
from alr.engine.actions import first_line
from alr.record.events import (
    CommitStarted,
    Event,
    ActionStarted,
    RunDone,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import RUN_SCOPE

SEPARATOR = " · "
ELLIPSIS = "…"


class Progress:
    """Follows a run's events and says where the run is and how long it has taken.

    The view only feeds it events and asks for the text; all the logic lives here.
    """

    def __init__(
        self,
        total_tasks: int,
        action_count: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._total_tasks = total_tasks
        self._action_count = action_count
        self._clock = clock
        self._run_start: float | None = None
        self._step_start = 0.0
        self._task_number = 0
        self._action_number = 0
        self._step = ""
        self._done = False
        self._in_after_run = False

    def feed(self, event: Event) -> None:
        """Take any run event and move the progress along."""
        now = self._clock()
        if self._run_start is None:
            self._run_start = now

        match event:
            case TaskStarted():
                self._task_number += 1
                self._action_number = 0
                self._start_step("session", now)
            case ActionStarted(task=task, action=action):
                self._start_step(self._action_label(task.id, action.command), now)
            case CommitStarted():
                self._start_step("commit", now)
            case TaskFinished():
                # The engine now stages and diffs; no step is running, so none is shown.
                self._step = ""
            case RunDone():
                self._done = True

    def _action_label(self, task_id: int, command: str) -> str:
        """Name an action's step; the run's own actions are not counted against a task's."""
        if task_id == RUN_SCOPE.id:
            self._in_after_run = True
            return f"{RUN_SCOPE.name}: {first_line(command)}"

        self._action_number += 1
        return f"action {self._action_number} of {self._action_count}: {first_line(command)}"

    def text(self, width: int | None = None) -> str:
        """The status line for the current moment, or "" before the first task and after the run.

        With a width, the step is cut with an ellipsis so the whole line fits and the
        timers always show; None leaves it whole.
        """
        if self._done or self._run_start is None:
            return ""

        # A rerun with every task completed starts no task but still runs the after_run actions.
        if not self._task_number and not self._in_after_run:
            return ""

        now = self._clock()
        run_time = f"run {format_elapsed(now - self._run_start)}"
        if not self._task_number:
            return self._after_run_text(now, run_time, width)

        task = f"task {self._task_number} of {self._total_tasks}"
        if not self._step:
            return SEPARATOR.join([task, run_time])

        step_time = format_elapsed(now - self._step_start)
        step = self._step
        if width is not None:
            # Three separators sit between the four parts, the step among them.
            others = len(task) + len(step_time) + len(run_time) + 3 * len(SEPARATOR)
            step = _fit_step(step, width - others)

        return SEPARATOR.join([task, step, step_time, run_time])

    def _after_run_text(self, now: float, run_time: str, width: int | None) -> str:
        """The status line when no task ran: only the after_run step and the timers."""
        step_time = format_elapsed(now - self._step_start)
        step = self._step
        if width is not None:
            others = len(step_time) + len(run_time) + 2 * len(SEPARATOR)
            step = _fit_step(step, width - others)

        return SEPARATOR.join([step, step_time, run_time])

    def _start_step(self, step: str, now: float) -> None:
        self._step = step
        self._step_start = now


def _fit_step(step: str, room: int) -> str:
    """The step, ending in an ellipsis when it is wider than the room it has."""
    if len(step) <= room:
        return step

    return step[: max(room - 1, 0)] + ELLIPSIS
