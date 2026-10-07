"""The three panes, each fed events directly."""

from textual.app import App, ComposeResult

from alr.record.events import (
    Activity,
    Committed,
    GateFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import COMPLETED, FAILED, Gate, Shared, Task, TaskList
from alr.tui.panes import ActivityPane, GatesPane, TasksPane

FIRST = Task(id=1, name="First", prompt="p")
SECOND = Task(id=2, name="Second", prompt="p")
TASK_LIST = TaskList(shared=Shared(prompt="s"), tasks=[FIRST, SECOND])


class Host(App[None]):
    def __init__(self, pane: TasksPane | ActivityPane | GatesPane) -> None:
        super().__init__()
        self._pane = pane

    def compose(self) -> ComposeResult:
        yield self._pane


async def test_tasks_pane_shows_each_task_and_its_state() -> None:
    pane = TasksPane()
    async with Host(pane).run_test():
        pane.load(TASK_LIST)
        pane.apply(TaskStarted(FIRST))
        pane.apply(TaskFinished(Task(1, "First", "p", state=COMPLETED), None, 1.0))

        assert [pane.get_row_at(i)[1:] for i in range(2)] == [
            ["1", "First"],
            ["2", "Second"],
        ]
        assert pane.get_row_at(0)[0] == "✔"
        assert pane.get_row_at(1)[0] == "·"


async def test_tasks_pane_marks_a_running_task() -> None:
    pane = TasksPane()
    async with Host(pane).run_test():
        pane.load(TASK_LIST)

        pane.apply(TaskStarted(SECOND))

        assert pane.get_row_at(1)[0] == "▶"


async def test_tasks_pane_marks_a_failed_task() -> None:
    pane = TasksPane()
    async with Host(pane).run_test():
        pane.load(TASK_LIST)

        pane.apply(TaskFinished(Task(2, "Second", "p", state=FAILED), "boom", 1.0))

        assert pane.get_row_at(1)[0] == "✖"


async def test_activity_pane_shows_only_the_selected_task() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test():
        pane.show(1, "First")
        pane.apply(Activity(FIRST, "→ read a.py"))
        pane.apply(Activity(SECOND, "→ edit b.py"))

        assert list(pane.lines) == ["→ read a.py"]

        pane.show(2, "Second")

        assert list(pane.lines) == ["→ edit b.py"]


async def test_activity_pane_title_names_the_task_it_shows() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test():
        assert pane.border_title == "Activity"

        pane.show(2, "Second")

        assert pane.border_title == "Activity: 2 Second"


async def test_tasks_pane_moves_its_highlight_to_the_started_task() -> None:
    pane = TasksPane()
    async with Host(pane).run_test() as pilot:
        pane.load(TASK_LIST)
        await pilot.pause()
        assert pane.cursor_row == 0

        pane.apply(TaskStarted(SECOND))
        await pilot.pause()

        assert pane.cursor_row == 1


async def test_tasks_pane_announces_the_highlighted_task_with_its_name() -> None:
    pane = TasksPane()
    seen: list[tuple[int, str]] = []

    class Listening(Host):
        def on_tasks_pane_selected(self, message: TasksPane.Selected) -> None:
            seen.append((message.task_id, message.name))

    async with Listening(pane).run_test() as pilot:
        pane.load(TASK_LIST)
        await pilot.pause()

        pane.apply(TaskStarted(SECOND))
        await pilot.pause()

        assert seen[-1] == (2, "Second")


async def test_gates_pane_lists_gate_results_and_commit_hashes() -> None:
    pane = GatesPane()
    gate = Gate(cmd="ruff check")
    async with Host(pane).run_test():
        pane.apply(GateFinished(FIRST, gate, "10m", None, False, 0.4))
        pane.apply(GateFinished(FIRST, gate, "10m", "exit status 1", False, 0.2))
        pane.apply(GateFinished(FIRST, gate, "1s", None, True, 1.0))
        pane.apply(Committed(FIRST, "a1b2c3d4e5f6"))

        assert list(pane.lines) == [
            "1. ✔ ruff check 0.4s",
            "1. ✖ ruff check 0.2s",
            "1. ⏱ ruff check 1.0s",
            "1. commit a1b2c3d",
        ]


async def test_gates_pane_shows_long_gate_times_in_hours_minutes_seconds() -> None:
    pane = GatesPane()
    gate = Gate(cmd="make test")
    async with Host(pane).run_test():
        pane.apply(GateFinished(FIRST, gate, "2h", None, False, 3725.5))

        assert list(pane.lines) == ["1. ✔ make test 1h 2m 6s"]
