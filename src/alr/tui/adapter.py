"""The bridge from the engine's events to Textual messages."""

from collections.abc import Callable

from textual.message import Message

from alr.stream.events import Event


class RunEvent(Message):
    """One engine event, delivered to the app."""

    def __init__(self, event: Event) -> None:
        super().__init__()
        self.event = event


def make_report(
    post: Callable[[Message], bool],
    record: Callable[[Event], None] | None = None,
) -> Callable[[Event], None]:
    """Build the stream front end's report callback.

    It holds only the function that posts messages, never the app or a widget,
    so the front end cannot pin them. The run log's writer, when there is one,
    sees each event first, and a record that failed to write stops the run. The
    event is posted either way: the final one must always reach the app, or the
    app would wait for a run that is already over.
    """

    def report(event: Event) -> None:
        try:
            if record is not None:
                record(event)
        finally:
            post(RunEvent(event))

    return report
