"""The three panes of the main window; each knows events, never the engine."""

from textual.events import Resize
from textual.message import Message
from textual.widgets import DataTable, Log, RichLog
from textual.widgets.data_table import ColumnKey

from alr.record.events import (
    Activity,
    Committed,
    Event,
    GateFinished,
    TaskFinished,
    TaskStarted,
)
from alr.durations import format_elapsed
from alr.tasks import COMPLETED, FAILED, TaskList

_SYMBOLS = {COMPLETED: "✔", FAILED: "✖"}
_PENDING = "·"
_RUNNING = "▶"
_MIN_NAME = 10
# Always kept free: the bar appears once the rows outgrow the pane, after the cut was made.
_SCROLLBAR = 2


def _cut(name: str, width: int) -> str:
    """The name, ending in an ellipsis when it does not fit the column."""
    if len(name) <= width:
        return name

    return name[: width - 1] + "…"


class TasksPane(DataTable[str]):
    """One row per task: its state, id, and name."""

    class Selected(Message):
        """The user moved to a task; the activity pane follows."""

        def __init__(self, task_id: int, name: str) -> None:
            super().__init__()
            self.task_id = task_id
            self.name = name

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._names: dict[str, str] = {}
        """The whole name of each task, for the cut to start from."""

    def on_mount(self) -> None:
        self.border_title = "Tasks"
        self.cursor_type = "row"
        # Fixed widths, so the room left for the name is known before any row is drawn.
        self.add_column("", key="state", width=1)
        self.add_column("ID", key="id", width=2)
        self.add_column("Task", key="name", width=_MIN_NAME)

    def load(self, task_list: TaskList) -> None:
        """Fill the table from the file's tasks, as they stand now."""
        self.clear()
        self._names = {str(task.id): task.name for task in task_list.tasks}
        longest = max((len(key) for key in self._names), default=2)
        self.columns[ColumnKey("id")].width = max(2, longest)
        width = self._name_width()
        for task in task_list.tasks:
            symbol = _SYMBOLS.get(task.state, _PENDING)
            self.add_row(symbol, str(task.id), _cut(task.name, width), key=str(task.id))

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
            self._follow(event.task.id)

        if isinstance(event, TaskFinished):
            self._set_state(event.task.id, _SYMBOLS.get(event.task.state, _PENDING))

    def on_data_table_row_highlighted(self, message: DataTable.RowHighlighted) -> None:
        key = message.row_key.value
        if key is None:
            return

        self.post_message(self.Selected(int(key), self._names.get(key, "")))

    def _follow(self, task_id: int) -> None:
        """Move the highlight to the running task, which also keeps its row on screen."""
        self.move_cursor(row=self.get_row_index(str(task_id)))

    def _set_state(self, task_id: int, symbol: str) -> None:
        self.update_cell(str(task_id), "state", symbol)


class ActivityPane(RichLog):
    """The selected task's activity lines, wrapped to the pane's width."""

    def on_mount(self) -> None:
        self.border_title = "Activity"

    def __init__(self) -> None:
        # Markup and highlight off, so a line with square brackets prints raw; the default
        # minimum width of 78 would keep a narrow pane from wrapping at its own edge.
        super().__init__(
            wrap=True, markup=False, highlight=False, auto_scroll=False, min_width=10
        )
        self._by_task: dict[int, list[str]] = {}
        self._shown: int | None = None
        self._width = 0

    def show(self, task_id: int, name: str) -> None:
        """Switch to a task and redraw what it has said so far."""
        if task_id == self._shown:
            return

        self._shown = task_id
        self.border_title = f"Activity: {task_id} {name}"
        self._redraw()

    def on_resize(self, event: Resize) -> None:
        """Wrapping is fixed when a line is written, so a new width needs the lines written again."""
        if event.size.width == self._width:
            return

        self._width = event.size.width
        self._redraw()

    def _redraw(self) -> None:
        self.clear()
        for line in self._by_task.get(self._shown, []):
            self.write(line, scroll_end=True)

    def apply(self, event: Event) -> None:
        if not isinstance(event, Activity):
            return

        self._by_task.setdefault(event.task.id, []).append(event.line)
        if event.task.id == self._shown:
            # Decided before the write: a line that makes the pane taller must not
            # count as the reader having scrolled up.
            self.write(event.line, scroll_end=self.is_vertical_scroll_end)


class GatesPane(Log):
    """Gate results and commit hashes, kept apart so they never scroll away in the activity."""

    def on_mount(self) -> None:
        self.border_title = "Gates & commits"

    def apply(self, event: Event) -> None:
        if isinstance(event, GateFinished):
            self.write_line(_gate_line(event))

        if isinstance(event, Committed):
            self.write_line(f"{event.task.id}. commit {event.commit_hash[:7]}")


def _gate_line(event: GateFinished) -> str:
    symbol = "✔"
    if event.timed_out:
        symbol = "⏱"
    elif event.error is not None:
        symbol = "✖"

    return f"{event.task.id}. {symbol} {event.gate.cmd} {format_elapsed(event.seconds)}"
