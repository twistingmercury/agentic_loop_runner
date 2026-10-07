"""The modal dialogs over the main window."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DirectoryTree,
    Input,
    Label,
    ListItem,
    ListView,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
)

from alr.errors import AlrError
from alr.tasks import Gate, parse_timeout
from alr.tui.checks import FolderCheck, check_folder, check_sandbox
from alr.tui.settings import Permissions


class _Dialog[R](ModalScreen[R | None]):
    """A dialog that Escape closes with no answer, so every one can be left by keyboard."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    # A box floating over the main window, which shows through the modal's dimmed background.
    DEFAULT_CSS = """
    _Dialog { align: center middle; }
    _Dialog #dialog {
        width: 60;
        height: auto;
        max-height: 90%;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    _Dialog Horizontal { height: auto; }
    _Dialog #error { height: auto; }
    """

    def action_cancel(self) -> None:
        self.dismiss(None)


class ErrorDialog(_Dialog[None]):
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


class SummaryDialog(_Dialog[None]):
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


class FileViewer(_Dialog[None]):
    """A file shown as it is on disk, read-only."""

    DEFAULT_CSS = """
    FileViewer #dialog { width: 80%; height: 80%; }
    FileViewer #text { height: 1fr; }
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._path or "no file")
            yield TextArea(_read(self._path), read_only=True, id="text")
            yield Button("Close", id="close")

    def on_button_pressed(self) -> None:
        self.dismiss(None)


def _read(path: str) -> str:
    """The file's text, or a line saying why there is none."""
    if not path:
        return "(no file chosen yet)"

    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"({path} is not accessible: {exc.strerror})"


class _Directories(DirectoryTree):
    """The picker's tree: folders only, hidden ones shown, since run folders live under .local/."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if path.is_dir()]


class FolderDialog(_Dialog[FolderCheck]):
    """Pick the run folder; the answer is checked here, so an error keeps the user on this step."""

    DEFAULT_CSS = """
    FolderDialog #dialog { width: 80%; max-width: 90; }
    FolderDialog #tree { height: 10; }
    """

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
                yield Button("OK", id="ok")
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

    async def on_input_submitted(self) -> None:
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


class PermissionsDialog(_Dialog[PermissionChoice]):
    """Sandbox settings file, or skip permissions; nothing is pre-selected without a folder file."""

    DEFAULT_CSS = """
    PermissionsDialog #dialog { width: 80%; max-width: 90; }
    """

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
                yield Button("OK", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, message: Button.Pressed) -> None:
        if message.button.id == "cancel":
            self.dismiss(None)
            return

        self._accept()

    def on_input_submitted(self) -> None:
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


class GatesDialog(_Dialog[list[Gate]]):
    """Add and remove the file's shared gates; Done with none is a decision that there are none."""

    DEFAULT_CSS = """
    GatesDialog #dialog { width: 80%; max-width: 90; }
    GatesDialog #list { height: auto; max-height: 6; }
    """

    # Typing a gate is the first thing to do; the list is reached with Tab.
    AUTO_FOCUS = "#cmd"

    def __init__(self, gates: list[Gate]) -> None:
        super().__init__()
        self._gates = list(gates)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Gates, run after each task's session")
            yield ListView(*(self._item(gate) for gate in self._gates), id="list")
            yield Input(placeholder="command", id="cmd")
            yield Input(placeholder="timeout, such as 5m (optional)", id="timeout")
            yield Static("", id="error")
            with Horizontal():
                yield Button("Add", id="add")
                yield Button("Remove", id="remove")
                yield Button("Done", id="done")
                yield Button("Cancel", id="cancel")

    async def on_button_pressed(self, message: Button.Pressed) -> None:
        match message.button.id:
            case "add":
                await self._add()
            case "remove":
                await self._remove()
            case "done":
                self.dismiss(self._gates)
            case "cancel":
                self.dismiss(None)

    async def on_input_submitted(self) -> None:
        await self._add()

    async def _add(self) -> None:
        cmd = self.query_one("#cmd", Input).value.strip()
        timeout = self.query_one("#timeout", Input).value.strip()
        error = _gate_error(cmd, timeout)
        self.query_one("#error", Static).update(error or "")
        if error:
            return

        gate = Gate(cmd=cmd, timeout=timeout)
        self._gates.append(gate)
        await self.query_one("#list", ListView).append(self._item(gate))
        self.query_one("#cmd", Input).value = ""
        self.query_one("#timeout", Input).value = ""

    async def _remove(self) -> None:
        index = self.query_one("#list", ListView).index
        if index is None:
            return

        del self._gates[index]
        await self.query_one("#list", ListView).pop(index)

    @staticmethod
    def _item(gate: Gate) -> ListItem:
        suffix = f"  ({gate.timeout})" if gate.timeout else ""
        return ListItem(Label(gate.cmd + suffix))


def _gate_error(cmd: str, timeout: str) -> str | None:
    """Why this gate cannot be added, or None when it can."""
    if not cmd:
        return "a gate needs a command"

    if not timeout:
        return None

    try:
        parse_timeout(timeout)
    except ValueError as exc:
        return f"timeout: {exc}"

    return None
