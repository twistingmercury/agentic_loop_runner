"""The three panes, each fed events directly."""

import pytest
from helpers import HandClock, until
from textual.app import App, ComposeResult
from textual.events import Resize
from textual.geometry import Size

from alr.record.events import (
    Activity,
    Committed,
    GateFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import COMPLETED, FAILED, Gate, Shared, Task, TaskList
from alr.tui.panes import ACTIVITY_LINE_CAP, ActivityPane, GatesPane, TasksPane

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

        assert [pane.get_row_at(i)[1:3] for i in range(2)] == [
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


def _time_cell(pane: TasksPane, row: int) -> str:
    return str(pane.get_cell_at((row, 3)))


async def test_tasks_pane_has_a_time_column_last() -> None:
    pane = TasksPane()
    async with Host(pane).run_test():
        labels = [str(column.label) for column in pane.columns.values()]

        assert labels[-1] == "Time"


async def test_tasks_pane_shows_how_long_a_finished_task_took() -> None:
    pane = TasksPane()
    async with Host(pane).run_test():
        pane.load(TASK_LIST)
        pane.apply(TaskStarted(FIRST))

        pane.apply(TaskFinished(Task(1, "First", "p", state=COMPLETED), None, 65.0))

        assert _time_cell(pane, 0) == "1m 5s"


async def test_tasks_pane_shows_how_long_a_failed_task_took() -> None:
    pane = TasksPane()
    async with Host(pane).run_test():
        pane.load(TASK_LIST)
        pane.apply(TaskStarted(SECOND))

        pane.apply(TaskFinished(Task(2, "Second", "p", state=FAILED), "boom", 12.3))

        assert _time_cell(pane, 1) == "12.3s"


async def test_tasks_pane_counts_up_the_running_task_on_each_tick() -> None:
    clock = HandClock()
    pane = TasksPane(clock=clock)
    async with Host(pane).run_test():
        pane.load(TASK_LIST)
        pane.apply(TaskStarted(FIRST))
        assert _time_cell(pane, 0) == "0.0s"

        clock.now += 7
        pane.tick()

        assert _time_cell(pane, 0) == "7.0s"


async def test_tasks_pane_stops_counting_once_the_task_finishes() -> None:
    clock = HandClock()
    pane = TasksPane(clock=clock)
    async with Host(pane).run_test():
        pane.load(TASK_LIST)
        pane.apply(TaskStarted(FIRST))
        pane.apply(TaskFinished(Task(1, "First", "p", state=COMPLETED), None, 3.0))

        clock.now += 50
        pane.tick()

        assert _time_cell(pane, 0) == "3.0s"


async def test_tasks_pane_leaves_pending_and_already_done_times_empty() -> None:
    done = Task(id=1, name="First", prompt="p", state=COMPLETED)
    pane = TasksPane()
    async with Host(pane).run_test():
        pane.load(TaskList(shared=Shared(prompt="s"), tasks=[done, SECOND]))

        pane.tick()

        assert [_time_cell(pane, row) for row in range(2)] == ["", ""]


async def test_activity_pane_shows_only_the_selected_task() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test():
        pane.show(1, "First")
        pane.apply(Activity(FIRST, "→ read a.py"))
        pane.apply(Activity(SECOND, "→ edit b.py"))

        assert shown_text(pane) == ["→ read a.py"]

        pane.show(2, "Second")

        assert shown_text(pane) == ["→ edit b.py"]


def shown_text(pane: ActivityPane | GatesPane) -> list[str]:
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
        await until(lambda: len(shown_text(pane)) < narrow)

        assert len(shown_text(pane)) < narrow
        assert " ".join(shown_text(pane)).split() == line.split()


def fill(pane: ActivityPane, count: int) -> None:
    for i in range(count):
        pane.apply(Activity(FIRST, f"line {i}"))


async def test_activity_pane_keeps_only_the_last_lines_of_a_task() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test(size=(40, 10)) as pilot:
        fill(pane, 20_000)
        pane.show(1, "First")
        await pilot.pause()

        stored = pane._by_task[1]
        assert len(stored) == ACTIVITY_LINE_CAP == 5_000
        assert stored[0] == "line 15000"
        assert stored[-1] == "line 19999"
        assert len(pane.lines) == ACTIVITY_LINE_CAP


async def test_activity_pane_caps_each_task_separately() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test(size=(40, 10)):
        fill(pane, ACTIVITY_LINE_CAP + 10)
        for i in range(3):
            pane.apply(Activity(SECOND, f"other {i}"))

        assert len(pane._by_task[1]) == ACTIVITY_LINE_CAP
        assert list(pane._by_task[2]) == ["other 0", "other 1", "other 2"]


async def test_activity_pane_shows_only_the_capped_lines_after_a_redraw() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test(size=(40, 10)) as pilot:
        fill(pane, ACTIVITY_LINE_CAP + 100)
        pane.show(1, "First")
        await pilot.pause()

        assert len(pane.lines) == ACTIVITY_LINE_CAP
        assert shown_text(pane)[0] == "line 100"


async def test_activity_pane_leaves_a_short_task_untouched() -> None:
    pane = ActivityPane()
    async with Host(pane).run_test(size=(40, 10)):
        fill(pane, 30)

        assert list(pane._by_task[1]) == [f"line {i}" for i in range(30)]


async def test_activity_pane_redraws_once_for_a_burst_of_resizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pane = ActivityPane()
    redraws: list[int] = []
    original = ActivityPane._redraw

    def counting(self: ActivityPane) -> None:
        redraws.append(1)
        original(self)

    async with Host(pane).run_test(size=(40, 20)) as pilot:
        pane.show(1, "First")
        await pilot.pause()
        monkeypatch.setattr(ActivityPane, "_redraw", counting)

        # Posted in one go: awaiting between events would let the timer fire mid-burst.
        for width in (50, 60, 70, 80):
            size = Size(width, 20)
            pane.on_resize(Resize(size, size))
        await pilot.pause()
        await until(lambda: redraws)

        assert redraws == [1]
        assert pane._width == 80


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
        assert pane.border_title == "Task"

        pane.show(2, "Second")

        assert pane.border_title == "Task: 2 Second"


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
    async with Host(pane).run_test() as pilot:
        pane.apply(GateFinished(FIRST, gate, "10m", None, False, 0.4))
        pane.apply(GateFinished(FIRST, gate, "10m", "exit status 1", False, 0.2))
        pane.apply(GateFinished(FIRST, gate, "1s", None, True, 1.0))
        pane.apply(Committed(FIRST, "a1b2c3d4e5f6"))
        await pilot.pause()

        assert shown_text(pane) == [
            "1. ✔ ruff check 0.4s",
            "1. ✖ ruff check 0.2s",
            "1. ⏱ ruff check 1.0s",
            "1. commit a1b2c3d",
        ]


async def test_gates_pane_shows_long_gate_times_in_hours_minutes_seconds() -> None:
    pane = GatesPane()
    gate = Gate(cmd="make test")
    async with Host(pane).run_test() as pilot:
        pane.apply(GateFinished(FIRST, gate, "2h", None, False, 3725.5))
        await pilot.pause()

        assert shown_text(pane) == ["1. ✔ make test 1h 2m 6s"]


LONG_GATE = Gate(
    cmd="uv run pytest " + " ".join(f"tests/unit/dir{i}/test_x.py" for i in range(8))
)


async def test_gates_pane_wraps_a_long_gate_line_without_losing_text() -> None:
    pane = GatesPane()
    event = GateFinished(FIRST, LONG_GATE, "10m", None, False, 0.4)
    async with Host(pane).run_test(size=(40, 20)) as pilot:
        pane.apply(event)
        await pilot.pause()

        rows = shown_text(pane)

        assert len(rows) > 1
        assert " ".join(rows).split() == f"1. ✔ {LONG_GATE.cmd} 0.4s".split()
        assert pane.max_scroll_x == 0


async def test_gates_pane_prints_square_brackets_raw() -> None:
    pane = GatesPane()
    gate = Gate(cmd="pytest [x]")
    async with Host(pane).run_test() as pilot:
        pane.apply(GateFinished(FIRST, gate, "10m", None, False, 0.4))
        await pilot.pause()

        assert shown_text(pane) == ["1. ✔ pytest [x] 0.4s"]


async def test_gates_pane_rewraps_when_it_is_resized() -> None:
    pane = GatesPane()
    event = GateFinished(FIRST, LONG_GATE, "10m", None, False, 0.4)
    async with Host(pane).run_test(size=(40, 20)) as pilot:
        pane.apply(event)
        await pilot.pause()
        narrow = len(shown_text(pane))

        await pilot.resize_terminal(200, 20)
        await until(lambda: len(shown_text(pane)) < narrow)

        assert len(shown_text(pane)) < narrow
        assert (
            " ".join(shown_text(pane)).split() == f"1. ✔ {LONG_GATE.cmd} 0.4s".split()
        )
