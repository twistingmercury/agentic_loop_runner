"""The modal dialogs over the main window."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DirectoryTree,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
)

from alr.errors import AlrError
from alr.tui.checks import FolderCheck, check_folder, check_sandbox
from alr.tui.settings import Permissions


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


@dataclass(frozen=True)
class PermissionChoice:
    """How sessions run, and the settings file when sandboxed."""

    permissions: Permissions
    sandbox_file: str


class PermissionsDialog(ModalScreen[PermissionChoice | None]):
    """Sandbox settings file, or skip permissions; nothing is pre-selected without a folder file."""

    def __init__(self, default_file: str) -> None:
        super().__init__()
        self._default_file = default_file

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("How should sessions run?")
            with RadioSet():
                yield RadioButton(
                    "Sandbox, with a settings file",
                    id="sandbox",
                    value=bool(self._default_file),
                )
                yield RadioButton("Skip permissions (no sandbox)", id="skip")
            yield Input(value=self._default_file, placeholder="sandbox.json", id="file")
            yield Static("", id="error")
            with Horizontal():
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, message: Button.Pressed) -> None:
        if message.button.id == "cancel":
            self.dismiss(None)
            return

        self._accept()

    def _accept(self) -> None:
        if self.query_one("#skip", RadioButton).value:
            self.dismiss(PermissionChoice(Permissions.SKIP, ""))
            return

        if not self.query_one("#sandbox", RadioButton).value:
            self.query_one("#error", Static).update("choose sandbox or skip")
            return

        path = self.query_one("#file", Input).value
        try:
            check_sandbox(path)
        except AlrError as exc:
            self.query_one("#error", Static).update(str(exc))
            return

        self.dismiss(PermissionChoice(Permissions.SANDBOX, path))
