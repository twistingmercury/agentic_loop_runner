"""The adapter: each engine event becomes one Textual message."""

from alr.stream.events import Activity, RunDone
from alr.tasks import Task
from alr.tui.adapter import RunEvent, make_report

TASK = Task(id=1, name="First", prompt="p")


def test_report_posts_one_run_event_per_event() -> None:
    posted: list[RunEvent] = []
    report = make_report(lambda message: bool(posted.append(message)) or True)
    event = Activity(TASK, "→ read a.py")

    report(event)

    assert [message.event for message in posted] == [event]


def test_report_records_before_it_posts() -> None:
    order: list[str] = []
    report = make_report(
        lambda message: bool(order.append("post")) or True,
        record=lambda event: order.append("record"),
    )

    report(RunDone(None, 1.0))

    assert order == ["record", "post"]
