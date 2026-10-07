"""The modal dialogs over the main window."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ErrorDialog(ModalScreen[None]):
    """A problem found on Start, or while opening a file."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Cannot continue")
            yield Static(self._text, id="message")
            yield Button("Close", id="close")

    def on_button_pressed(self) -> None:
        self.dismiss(None)


class SummaryDialog(ModalScreen[None]):
    """How the run ended; the app stays open behind it."""

    def __init__(self, outcome: str, seconds: float, error: str | None) -> None:
        super().__init__()
        self._text = f"Run {outcome} in {seconds:.1f}s"
        if error and outcome != "done":
            self._text += f"\n{error}"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Run finished")
            yield Static(self._text, id="message")
            yield Button("Close", id="close")

    def on_button_pressed(self) -> None:
        self.dismiss(None)
