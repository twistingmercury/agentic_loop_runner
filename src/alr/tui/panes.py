"""The three panes of the main window; each knows events, never the engine."""

import time
from collections import deque
from collections.abc import Callable

from textual.events import Resize
from textual.message import Message
from textual.timer import Timer
from textual.widgets import DataTable, RichLog
from textual.widgets.data_table import ColumnKey

from alr.record.events import (
    Activity,
    Committed,
    Event,
    GateFinished,
    RunDone,
    RunResult,
    TaskFinished,
    TaskStarted,
)
from alr.durations import format_elapsed
from alr.tasks import COMPLETED, FAILED, PASSED, TaskList

_SYMBOLS = {COMPLETED: "✔", PASSED: "●", FAILED: "✖"}
_PENDING = "·"
_RUNNING = "▶"
_MIN_NAME = 10
# Room for any time under a hundred hours, such as "10h 59m 59s".
_TIME_WIDTH = 11
# Always kept free: the bar appears once the rows outgrow the pane, after the cut was made.
_SCROLLBAR = 2


def _cut(name: str, width: int) -> str:
    """The name, ending in an ellipsis when it does not fit the column."""
    if len(name) <= width:
        return name

    return name[: width - 1] + "…"


class TasksPane(DataTable[str]):
    """One row per task: its state, id, name, and how long it took."""

    class Selected(Message):
        """The user moved to a task; the activity pane follows."""

        def __init__(self, task_id: int, name: str) -> None:
            super().__init__()
            self.task_id = task_id
            self.name = name

    def __init__(
        self, *, id: str | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        super().__init__(id=id)
        self._names: dict[str, str] = {}
        """The whole name of each task, for the cut to start from."""
        # A test passes its own clock so the running time moves without waiting.
        self._clock = clock
        self._passed: list[str] = []
        """The row keys of the tasks that passed and wait for the commit, in order."""
        self._committed: list[str] = []
        """The row keys a landed commit covers, in order."""
        self._running_task: tuple[str, float] | None = None
        """The running task's row key and when it started."""

    def on_mount(self) -> None:
        self.border_title = "Tasks"
        self.cursor_type = "row"
        # Fixed widths, so the room left for the name is known before any row is drawn.
        self.add_column("", key="state", width=1)
        self.add_column("ID", key="id", width=2)
        self.add_column("Task", key="name", width=_MIN_NAME)
        self.add_column("Time", key="time", width=_TIME_WIDTH)

    def load(self, task_list: TaskList) -> None:
        """Fill the table from the file's tasks, as they stand now."""
        self.clear()
        self._running_task = None
        self._passed = []
        self._committed = []
        self._names = {str(task.id): task.name for task in task_list.tasks}
        longest = max((len(key) for key in self._names), default=2)
        self.columns[ColumnKey("id")].width = max(2, longest)
        width = self._name_width()
        for task in task_list.tasks:
            symbol = _SYMBOLS.get(task.state, _PENDING)
            name = _cut(task.name, width)
            self.add_row(symbol, str(task.id), name, "", key=str(task.id))

        self._fit(width)

    def on_resize(self) -> None:
        self._fit(self._name_width())

    def _name_width(self) -> int:
        """What is left of the pane for the name once the other columns and padding are placed."""
        others = sum(
            column.get_render_width(self)
            for key, column in self.columns.items()
            if key.value != "name"
        )
        padding = 2 * self.cell_padding
        room = self.size.width - _SCROLLBAR - others - padding
        return max(_MIN_NAME, room)

    def _fit(self, width: int) -> None:
        """Make the name column that wide and cut every name to it, so the table never scrolls sideways."""
        column = self.columns[ColumnKey("name")]
        if column.width == width and not self._names:
            return

        column.width = width
        for key, name in self._names.items():
            self.update_cell(key, "name", _cut(name, width), update_width=True)

    def apply(self, event: Event) -> None:
        if isinstance(event, TaskStarted):
            self._set_state(event.task.id, _RUNNING)
            self._running_task = (str(event.task.id), self._clock())
            self.tick()
            self._follow(event.task.id)

        if isinstance(event, TaskFinished):
            self._set_state(event.task.id, _SYMBOLS.get(event.task.state, _PENDING))
            self._running_task = None
            self._set_time(str(event.task.id), event.seconds)
            self._note_passed(event)

        if isinstance(event, Committed):
            self._note_committed()

        if isinstance(event, RunDone):
            self._settle(event)

    def _note_passed(self, event: TaskFinished) -> None:
        if event.task.state == PASSED:
            self._passed.append(str(event.task.id))

    def _note_committed(self) -> None:
        """Remember which rows the landed commit covers.

        In `run` mode that is every passed row; in `task` mode it is the
        running task, which has no TaskFinished yet when a stop arrives.
        """
        covered = list(self._passed)
        if self._running_task is not None:
            covered.append(self._running_task[0])

        self._committed.extend(covered)

    def _settle(self, event: RunDone) -> None:
        """Make the rows that are still provisional show what tasks.yaml holds.

        ADR-025: a `run`-mode run saves once, so a passed row is not complete
        until the commit lands, and a stop after the commit landed saves the
        covered tasks `completed` (as does a `task`-mode stop after its
        commit). A failed commit is saved on the last task that ran. Any other
        ending leaves unsaved rows pending, as the file does.
        """
        passed, self._passed = self._passed, []
        committed, self._committed = self._committed, []
        running, self._running_task = self._running_task, None
        if event.result is RunResult.COMPLETED:
            return

        # Only a stop saves the covered tasks after the commit; a failure there
        # is a failed save, so the file still says pending.
        landed = committed if event.result is RunResult.STOPPED else []
        for key in [*passed, *([running[0]] if running else [])]:
            symbol = _SYMBOLS[COMPLETED] if key in landed else _PENDING
            self._set_state(int(key), symbol)

        # A landed commit whose save failed is exit 4 too, but nothing failed:
        # the file says pending and no task carries the failure.
        if event.result is RunResult.COMMIT_FAILED and passed and not committed:
            self._set_state(int(passed[-1]), _SYMBOLS[FAILED])

    def tick(self) -> None:
        """Count the running task's time up; the view calls this once a second."""
        if self._running_task is None:
            return

        key, started = self._running_task
        self._set_time(key, self._clock() - started)

    def on_data_table_row_highlighted(self, message: DataTable.RowHighlighted) -> None:
        key = message.row_key.value
        if key is None:
            return

        # The message queue can hold a highlight the cursor has since left, such as the
        # table's first one arriving after a task start moved it; the cursor is the truth.
        if message.cursor_row != self.cursor_row:
            return

        self.post_message(self.Selected(int(key), self._names.get(key, "")))

    def _follow(self, task_id: int) -> None:
        """Move the highlight to the running task, which also keeps its row on screen."""
        self.move_cursor(row=self.get_row_index(str(task_id)))

    def _set_state(self, task_id: int, symbol: str) -> None:
        self.update_cell(str(task_id), "state", symbol)

    def _set_time(self, key: str, seconds: float) -> None:
        self.update_cell(key, "time", format_elapsed(seconds))


# A task that prints tens of thousands of lines would freeze every redraw; the run log keeps them all.
ACTIVITY_LINE_CAP = 5_000

# Quiet time after the last resize before the lines are written again at the new width.
RESIZE_SETTLE_SECONDS = 0.05


class ActivityPane(RichLog):
    """The selected task's activity lines, wrapped to the pane's width."""

    def on_mount(self) -> None:
        self.border_title = "Task"

    def __init__(self) -> None:
        # Markup and highlight off, so a line with square brackets prints raw; the default
        # minimum width of 78 would keep a narrow pane from wrapping at its own edge.
        super().__init__(
            wrap=True, markup=False, highlight=False, auto_scroll=False, min_width=10
        )
        self._by_task: dict[int, deque[str]] = {}
        self._shown: int | None = None
        self._width = 0
        self._settle_timer: Timer | None = None

    def show(self, task_id: int, name: str) -> None:
        """Switch to a task and redraw what it has said so far."""
        if task_id == self._shown:
            return

        self._shown = task_id
        self.border_title = f"Task: {task_id} {name}"
        self._redraw()

    def on_resize(self, event: Resize) -> None:
        """Wrapping is fixed when a line is written, so a new width needs the lines written again."""
        if event.size.width == self._width:
            return

        first_width = self._width == 0
        self._width = event.size.width
        if first_width:
            self._redraw()
            return

        # A drag fires a resize per column; redraw once the width has stopped changing.
        if self._settle_timer is not None:
            self._settle_timer.stop()

        self._settle_timer = self.set_timer(RESIZE_SETTLE_SECONDS, self._redraw)

    def _redraw(self) -> None:
        self.clear()
        for line in self._by_task.get(self._shown, []):
            self.write(line, scroll_end=True)

    def apply(self, event: Event) -> None:
        if not isinstance(event, Activity):
            return

        self._by_task.setdefault(event.task.id, deque(maxlen=ACTIVITY_LINE_CAP)).append(
            event.line
        )
        if event.task.id == self._shown:
            # Decided before the write: a line that makes the pane taller must not
            # count as the reader having scrolled up.
            self.write(event.line, scroll_end=self.is_vertical_scroll_end)


class GatesPane(RichLog):
    """Gate results and commit hashes, kept apart so they never scroll away in the activity."""

    def on_mount(self) -> None:
        self.border_title = "Gates & commits"

    def __init__(self, id: str | None = None) -> None:
        # Same settings as the activity pane: raw text, and wrapping at the pane's own edge.
        super().__init__(
            wrap=True,
            markup=False,
            highlight=False,
            auto_scroll=True,
            min_width=10,
            id=id,
        )
        self._lines: list[str] = []
        self._width = 0

    def on_resize(self, event: Resize) -> None:
        """Wrapping is fixed when a line is written, so a new width needs the lines written again."""
        if event.size.width == self._width:
            return

        # The first resize comes at width 0, before there is anything to wrap against.
        self._width = event.size.width
        self._redraw()

    def _redraw(self) -> None:
        self.clear()
        for line in self._lines:
            self.write(line)

    def _add(self, line: str) -> None:
        self._lines.append(line)
        self.write(line)

    def apply(self, event: Event) -> None:
        if isinstance(event, GateFinished):
            self._add(_gate_line(event))

        if isinstance(event, Committed):
            self._add(f"{event.task.id}. commit {event.commit_hash[:7]}")


def _gate_line(event: GateFinished) -> str:
    symbol = "✔"
    if event.timed_out:
        symbol = "⏱"
    elif event.error is not None:
        symbol = "✖"

    return f"{event.task.id}. {symbol} {event.gate.cmd} {format_elapsed(event.seconds)}"
