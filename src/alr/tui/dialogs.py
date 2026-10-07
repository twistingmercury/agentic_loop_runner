"""The modal dialogs over the main window."""

from collections.abc import Iterable
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, Static

from alr.errors import AlrError
from alr.tui.checks import FolderCheck, check_folder


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


class _Directories(DirectoryTree):
    """The picker's tree: folders only, hidden ones shown, since run folders live under .local/."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if path.is_dir()]


class FolderDialog(ModalScreen[FolderCheck | None]):
    """Pick the run folder; the answer is checked here, so an error keeps the user on this step."""

    def __init__(self, start: str) -> None:
        super().__init__()
        self._start = start

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Run folder")
            yield Input(value=self._start, id="path")
            yield _Directories(self._start, id="tree")
            yield Static("", id="error")
            with Horizontal():
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_directory_tree_directory_selected(
        self, message: DirectoryTree.DirectorySelected
    ) -> None:
        self.query_one("#path", Input).value = str(message.path)

    async def on_button_pressed(self, message: Button.Pressed) -> None:
        if message.button.id == "cancel":
            self.dismiss(None)
            return

        await self._accept(self.query_one("#path", Input).value)

    async def _accept(self, folder: str) -> None:
        try:
            check = await check_folder(folder)
        except AlrError as exc:
            self.query_one("#error", Static).update(str(exc))
            return

        self.dismiss(check)
