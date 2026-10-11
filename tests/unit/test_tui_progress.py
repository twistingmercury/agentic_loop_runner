"""The status line's progress: driven only by run events and a fake clock."""

import pytest

from alr.record.events import (
    Activity,
    CommitStarted,
    Committed,
    ActionFinished,
    ActionStarted,
    RunDone,
    SessionFinished,
    TaskFinished,
    TaskStarted,
)
from alr.tasks import Action, Task
from alr.tui.progress import Progress

FIRST = Task(id=3, name="First", prompt="p")
SECOND = Task(id=7, name="Second", prompt="p")
FORMAT = Action("uv run ruff format --check")
TESTS = Action("uv run pytest -n auto tests/unit")


class FakeClock:
    """A clock the test moves by hand, so nothing sleeps."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def progress(clock: FakeClock) -> Progress:
    return Progress(total_tasks=12, action_count=2, clock=clock)


def test_text_is_empty_before_any_event(progress: Progress) -> None:
    assert progress.text() == ""


def test_text_is_empty_until_the_first_task_starts(progress: Progress) -> None:
    progress.feed(Activity(FIRST, "warming up"))

    assert progress.text() == ""


def test_session_shows_the_task_number_and_session(
    progress: Progress, clock: FakeClock
) -> None:
    progress.feed(TaskStarted(FIRST))
    clock.advance(5)

    assert progress.text() == "task 1 of 12 · session · 5.0s · run 5.0s"


def test_activity_keeps_the_session_step(progress: Progress, clock: FakeClock) -> None:
    progress.feed(TaskStarted(FIRST))
    clock.advance(2)
    progress.feed(Activity(FIRST, "→ read a.py"))
    clock.advance(3)

    assert progress.text() == "task 1 of 12 · session · 5.0s · run 5.0s"


def test_action_shows_its_number_and_command(
    progress: Progress, clock: FakeClock
) -> None:
    progress.feed(TaskStarted(FIRST))
    clock.advance(10)
    progress.feed(SessionFinished(FIRST, 10.0))
    progress.feed(ActionStarted(FIRST, FORMAT))
    clock.advance(1)
    progress.feed(ActionFinished(FIRST, FORMAT, "", None, False, 1.0))
    progress.feed(ActionStarted(FIRST, TESTS))
    clock.advance(31)

    assert progress.text() == (
        "task 1 of 12 · action 2 of 2: uv run pytest -n auto tests/unit · 31.0s · run 42.0s"
    )


def test_action_with_several_lines_shows_only_the_first(progress: Progress) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(ActionStarted(FIRST, Action("make lint\nmake test\nmake docs")))

    assert (
        progress.text() == "task 1 of 12 · action 1 of 2: make lint · 0.0s · run 0.0s"
    )


def test_commit_shows_commit(progress: Progress, clock: FakeClock) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(ActionStarted(FIRST, FORMAT))
    clock.advance(4)
    progress.feed(CommitStarted(FIRST))
    clock.advance(1)

    assert progress.text() == "task 1 of 12 · commit · 1.0s · run 5.0s"


def test_step_timer_restarts_on_each_step(progress: Progress, clock: FakeClock) -> None:
    progress.feed(TaskStarted(FIRST))
    clock.advance(20)
    progress.feed(ActionStarted(FIRST, FORMAT))

    assert (
        progress.text()
        == "task 1 of 12 · action 1 of 2: uv run ruff format --check · 0.0s · run 20.0s"
    )


def test_next_task_counts_up_and_restarts_action_numbers(
    progress: Progress, clock: FakeClock
) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(ActionStarted(FIRST, FORMAT))
    progress.feed(ActionStarted(FIRST, TESTS))
    progress.feed(CommitStarted(FIRST))
    progress.feed(Committed(FIRST, "abc123"))
    progress.feed(TaskFinished(FIRST, None, 30.0))
    clock.advance(30)
    progress.feed(TaskStarted(SECOND))
    clock.advance(1)
    progress.feed(ActionStarted(SECOND, FORMAT))

    assert (
        progress.text()
        == "task 2 of 12 · action 1 of 2: uv run ruff format --check · 0.0s · run 31.0s"
    )


def test_run_timer_counts_from_the_first_event(
    progress: Progress, clock: FakeClock
) -> None:
    progress.feed(Activity(FIRST, "before the task"))
    clock.advance(40)
    progress.feed(TaskStarted(FIRST))
    clock.advance(720)

    assert progress.text() == "task 1 of 12 · session · 12m 0s · run 12m 40s"


def test_text_follows_the_clock_without_new_events(
    progress: Progress, clock: FakeClock
) -> None:
    progress.feed(TaskStarted(FIRST))
    clock.advance(1)
    first = progress.text()
    clock.advance(1)

    assert (first, progress.text()) == (
        "task 1 of 12 · session · 1.0s · run 1.0s",
        "task 1 of 12 · session · 2.0s · run 2.0s",
    )


def test_run_done_clears_the_line(progress: Progress, clock: FakeClock) -> None:
    progress.feed(TaskStarted(FIRST))
    clock.advance(5)
    progress.feed(RunDone(None, 5.0))

    assert progress.text() == ""


def test_default_clock_is_monotonic() -> None:
    progress = Progress(total_tasks=1, action_count=0)
    progress.feed(TaskStarted(FIRST))

    assert progress.text().startswith("task 1 of 1 · session · ")


def test_action_that_starts_with_a_newline_reads_like_the_action_marker(
    progress: Progress,
) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(ActionStarted(FIRST, Action("\nmake lint")))

    assert progress.text() == "task 1 of 12 · action 1 of 2:  · 0.0s · run 0.0s"


def test_long_action_is_cut_with_an_ellipsis_so_the_timers_fit(
    progress: Progress, clock: FakeClock
) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(
        ActionStarted(FIRST, Action("uv run pytest " + "-k very_long_name " * 8))
    )
    clock.advance(75)

    text = progress.text(width=60)

    assert len(text) == 60
    assert text.startswith("task 1 of 12 · action 1 of 2: uv run …")
    assert text.endswith("… · 1m 15s · run 1m 15s")


def test_action_that_fits_is_not_cut(progress: Progress) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(ActionStarted(FIRST, FORMAT))

    assert progress.text(width=200) == progress.text()


def test_line_exactly_as_wide_as_the_room_is_not_cut(progress: Progress) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(ActionStarted(FIRST, FORMAT))
    whole = progress.text()

    assert progress.text(width=len(whole)) == whole
    assert progress.text(width=len(whole) - 1).count("…") == 1


def test_task_finished_clears_the_step_and_its_timer(
    progress: Progress, clock: FakeClock
) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(ActionStarted(FIRST, FORMAT))
    clock.advance(9)
    progress.feed(TaskFinished(FIRST, None, 9.0))
    clock.advance(2)

    assert progress.text() == "task 1 of 12 · run 11.0s"


def test_next_task_start_brings_the_step_back(
    progress: Progress, clock: FakeClock
) -> None:
    progress.feed(TaskStarted(FIRST))
    progress.feed(TaskFinished(FIRST, None, 1.0))
    clock.advance(3)
    progress.feed(TaskStarted(SECOND))

    assert progress.text() == "task 2 of 12 · session · 0.0s · run 3.0s"


def test_after_all_commit_reads_commit_once_it_starts(
    clock: FakeClock,
) -> None:
    progress = Progress(total_tasks=1, action_count=1, clock=clock)
    progress.feed(TaskStarted(FIRST))
    progress.feed(TaskFinished(FIRST, None, 4.0))
    clock.advance(4)
    after_finish = progress.text()
    progress.feed(CommitStarted(FIRST))
    clock.advance(1)

    assert (after_finish, progress.text()) == (
        "task 1 of 1 · run 4.0s",
        "task 1 of 1 · commit · 1.0s · run 5.0s",
    )
