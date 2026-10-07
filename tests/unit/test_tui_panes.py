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

        assert shown_text(pane) == ["→ read a.py"]

        pane.show(2, "Second")

        assert shown_text(pane) == ["→ edit b.py"]


def shown_text(pane: ActivityPane) -> list[str]:
    """The rows on screen, without the padding that fills each to the pane's width."""
    return [strip.text.rstrip() for strip in pane.lines]


async def test_activity_pane_wraps_a_long_line_without_losing_text() -> None:
    pane = ActivityPane()
    line = " ".join(f"word{i}" for i in range(40))
    async with Host(pane).run_test(size=(40, 20)) as pilot:
        pane.show(1, "First")
        pane.apply(Activity(FIRST, line))
        await pilot.pause()

        rows = shown_text(pane)

        assert len(rows) > 1
        assert " ".join(rows).split() == line.split()


async def test_activity_pane_prints_square_brackets_raw() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test() as pilot:
        pane.show(1, "First")
        pane.apply(Activity(FIRST, "[tool] [x]"))
        await pilot.pause()

        assert shown_text(pane) == ["[tool] [x]"]


async def test_activity_pane_rewraps_when_it_is_resized() -> None:
    pane = ActivityPane()
    line = " ".join(f"word{i}" for i in range(40))
    async with Host(pane).run_test(size=(40, 20)) as pilot:
        pane.show(1, "First")
        pane.apply(Activity(FIRST, line))
        await pilot.pause()
        narrow = len(shown_text(pane))

        await pilot.resize_terminal(120, 20)
        await pilot.pause()

        assert len(shown_text(pane)) < narrow
        assert " ".join(shown_text(pane)).split() == line.split()


def fill(pane: ActivityPane, count: int) -> None:
    for i in range(count):
        pane.apply(Activity(FIRST, f"line {i}"))


async def test_activity_pane_stays_put_when_scrolled_up() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test(size=(40, 10)) as pilot:
        pane.show(1, "First")
        fill(pane, 50)
        await pilot.pause()
        pane.scroll_to(y=0, animate=False)
        await pilot.pause()

        pane.apply(Activity(FIRST, "one more"))
        await pilot.pause()

        assert pane.scroll_y == 0


async def test_activity_pane_follows_new_lines_at_the_bottom() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test(size=(40, 10)) as pilot:
        pane.show(1, "First")
        fill(pane, 50)
        await pilot.pause()
        assert pane.is_vertical_scroll_end

        pane.apply(Activity(FIRST, "one more"))
        await pilot.pause()

        assert pane.is_vertical_scroll_end
        assert pane.scroll_y > 0


async def test_activity_pane_lands_at_the_bottom_on_a_task_switch() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test(size=(40, 10)) as pilot:
        pane.show(1, "First")
        fill(pane, 50)
        for i in range(50):
            pane.apply(Activity(SECOND, f"other {i}"))
        await pilot.pause()
        pane.scroll_to(y=0, animate=False)
        await pilot.pause()

        pane.show(2, "Second")
        await pilot.pause()

        assert pane.is_vertical_scroll_end
        assert pane.scroll_y > 0


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
