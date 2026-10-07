"""The three panes of the main window; each knows events, never the engine."""

from textual.message import Message
from textual.widgets import DataTable, Log

from alr.stream.events import (
    Activity,
    Committed,
    Event,
    GateFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import COMPLETED, FAILED, TaskList

_SYMBOLS = {COMPLETED: "✔", FAILED: "✖"}
_PENDING = "·"
_RUNNING = "▶"


class TasksPane(DataTable[str]):
    """One row per task: its state, id, and name."""

    class Selected(Message):
        """The user moved to a task; the activity pane follows."""

        def __init__(self, task_id: int) -> None:
            super().__init__()
            self.task_id = task_id

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_column("", key="state")
        self.add_column("ID", key="id")
        self.add_column("Task", key="name")

    def load(self, task_list: TaskList) -> None:
        """Fill the table from the file's tasks, as they stand now."""
        self.clear()
        for task in task_list.tasks:
            symbol = _SYMBOLS.get(task.state, _PENDING)
            self.add_row(symbol, str(task.id), task.name, key=str(task.id))

    def apply(self, event: Event) -> None:
        if isinstance(event, TaskStarted):
            self._set_state(event.task.id, _RUNNING)

        if isinstance(event, TaskFinished):
            self._set_state(event.task.id, _SYMBOLS.get(event.task.state, _PENDING))

    def on_data_table_row_highlighted(self, message: DataTable.RowHighlighted) -> None:
        if message.row_key.value is not None:
            self.post_message(self.Selected(int(message.row_key.value)))

    def _set_state(self, task_id: int, symbol: str) -> None:
        self.update_cell(str(task_id), "state", symbol)


class ActivityPane(Log):
    """The selected task's activity lines."""

    def __init__(self) -> None:
        super().__init__()
        self._by_task: dict[int, list[str]] = {}
        self._shown: int | None = None

    def show(self, task_id: int) -> None:
        """Switch to a task and redraw what it has said so far."""
        self._shown = task_id
        self.clear()
        self.write_lines(self._by_task.get(task_id, []))

    def apply(self, event: Event) -> None:
        if not isinstance(event, Activity):
            return

        self._by_task.setdefault(event.task.id, []).append(event.line)
        if event.task.id == self._shown:
            self.write_line(event.line)


class GatesPane(Log):
    """Gate results and commit hashes, kept apart so they never scroll away in the activity."""

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

    return f"{event.task.id}. {symbol} {event.gate.cmd} {event.seconds:.1f}s"
