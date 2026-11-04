"""The modal dialogs over the main window."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import DescendantFocus
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

from alr.durations import format_elapsed
from alr.errors import AlrError, RefusedError
from alr.sandboxes import Sandbox
from alr.tasks import Action, Task, parse_timeout
from alr.tui.checks import FolderCheck, check_folder, check_sandbox
from alr.tui.settings import Permissions, RunSettings


def error_text(exc: AlrError) -> str:
    """Say what a domain error says; a refusal's reason is for the command line, so only its message shows."""
    if isinstance(exc, RefusedError):
        return exc.message

    return str(exc)


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
    _Dialog #message { margin-bottom: 1; }
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


class ResetDialog(_Dialog[bool]):
    """Ask before failed tasks go back to pending; Enter confirms, Escape cancels."""

    AUTO_FOCUS = "#reset"

    def __init__(self, failed: list[Task]) -> None:
        super().__init__()
        noun = "task" if len(failed) == 1 else "tasks"
        self._title = f"Reset {len(failed)} failed {noun} to pending?"
        self._lines = "\n".join(_failed_line(task) for task in failed)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, id="title")
            yield Static(self._lines, id="message")
            with Horizontal():
                yield Button("Reset", id="reset")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, message: Button.Pressed) -> None:
        self.dismiss(True if message.button.id == "reset" else None)


def _failed_line(task: Task) -> str:
    """One task as `id: name`, with the first line of its error when it has one."""
    line = f"{task.id}: {task.name}"
    if not task.error:
        return line

    return f"{line}: {task.error.splitlines()[0]}"


class SummaryDialog(_Dialog[bool]):
    """How the run ended. After a clean run its button is Exit and answers True; otherwise Close keeps the app open."""

    # Enter acts at once, so a clean run needs no tab to reach Exit.
    AUTO_FOCUS = "#close"

    def __init__(self, outcome: str, seconds: float, error: str | None) -> None:
        super().__init__()
        self._exits = outcome == "done"
        self._text = f"Run {outcome} in {format_elapsed(seconds)}"
        if error and outcome != "done":
            self._text += f"\n{error}"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Run finished")
            yield Static(self._text, id="message")
            yield Button("Exit" if self._exits else "Close", id="close")

    def on_button_pressed(self) -> None:
        self.dismiss(self._exits)


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
            yield Label(self._path or "no file", id="path")
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
            self.query_one("#error", Static).update(error_text(exc))
            return

        self.dismiss(check)


@dataclass(frozen=True)
class PermissionChoice:
    """How sessions run: a sandbox picked by name, a settings file as it is, or skip."""

    permissions: Permissions
    sandbox_file: str
    sandbox_name: str


class PermissionsDialog(_Dialog[PermissionChoice]):
    """Pick a sandbox by name, use the --sandbox-settings file, or skip permissions.

    The preview beside the list shows the selected file as it is on disk. A
    file that cannot be used is listed with its problem, and OK stays off
    while it is selected, so the refusal shows here instead of ending the app.
    """

    DEFAULT_CSS = """
    PermissionsDialog #dialog { width: 90%; max-width: 110; height: 96%; max-height: 96%; padding: 0 2; }
    PermissionsDialog Button { border: none; height: 1; min-height: 1; }
    PermissionsDialog #ok, PermissionsDialog #cancel { border: none; height: 1; }
    /* The preview takes what is left, so the buttons stay on screen however long the file is. */
    PermissionsDialog #preview { height: 1fr; min-height: 6; overflow-y: auto; }
    /* Borderless radios and a one-row button bar keep OK on an 80x24 screen; a long list scrolls instead of pushing OK off. */
    PermissionsDialog RadioSet, PermissionsDialog RadioSet:focus { border: none; padding: 0; }
    PermissionsDialog RadioSet { max-height: 10; }
    PermissionsDialog Horizontal { height: 1; }
    """

    def __init__(self, settings: RunSettings, sandboxes: list[Sandbox]) -> None:
        super().__init__()
        self._settings = settings
        self._sandboxes = sandboxes
        self._file = _answered_file(settings)
        self._start = self._first_radio()

    def _first_radio(self) -> str:
        """The id of the radio that starts pressed; empty when there is nothing safe to press."""
        if self._file:
            return "file"

        if self._settings.permissions is Permissions.SKIP:
            return "skip"

        index = self._first_sandbox()
        return "" if index is None else _sandbox_id(index)

    def _first_sandbox(self) -> int | None:
        """The index of the sandbox that starts pressed: the one chosen already, else tight, else the first."""
        names = [sandbox.name for sandbox in self._sandboxes]
        for wanted in (self._settings.sandbox_name, "tight"):
            if wanted in names:
                return names.index(wanted)

        return 0 if names else None

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("How should sessions run?")
            with RadioSet():
                if self._file:
                    yield self._radio("file", f"Use the settings file {self._file}")

                for index, sandbox in enumerate(self._sandboxes):
                    yield self._radio(_sandbox_id(index), _sandbox_label(sandbox))

                yield self._radio("skip", "Skip permissions (no sandbox)")

            yield Static("", id="preview", markup=False)
            yield Static("", id="error")
            with Horizontal():
                yield Button("OK", id="ok")
                yield Button("Cancel", id="cancel")

    def _radio(self, radio_id: str, label: str) -> RadioButton:
        return RadioButton(label, id=radio_id, value=radio_id == self._start)

    def on_mount(self) -> None:
        self._show_selection()

    def on_radio_set_changed(self) -> None:
        self._show_selection()

    def _pressed(self) -> str:
        """The id of the pressed radio."""
        button = self.query_one(RadioSet).pressed_button
        return button.id or "" if button else ""

    def _pressed_sandbox(self) -> Sandbox | None:
        """The sandbox whose radio is pressed; None for the file, skip, or no radio."""
        pressed = self._pressed()
        if not pressed.startswith(_SANDBOX_ID):
            return None

        return self._sandboxes[int(pressed.removeprefix(_SANDBOX_ID))]

    def _show_selection(self) -> None:
        """Show the pressed radio's file and drop the last error; OK is on only for a choice the run can use."""
        pressed = self._pressed()
        sandbox = self._pressed_sandbox()
        self.query_one("#preview", Static).update(self._preview(pressed, sandbox))
        self.query_one("#error", Static).update("")
        unusable = pressed == "" or (sandbox is not None and sandbox.problem != "")
        self.query_one("#ok", Button).disabled = unusable

    def _preview(self, pressed: str, sandbox: Sandbox | None) -> str:
        """The text for the pressed radio: its file under its path, or nothing for skip."""
        if sandbox is not None:
            return _sandbox_preview(sandbox)

        if pressed == "file":
            return _file_preview(self._file)

        return ""

    def on_button_pressed(self, message: Button.Pressed) -> None:
        if message.button.id == "cancel":
            self.dismiss(None)
            return

        self._accept()

    def _accept(self) -> None:
        pressed = self._pressed()
        if pressed == "skip":
            self.dismiss(PermissionChoice(Permissions.SKIP, "", ""))
            return

        if pressed == "file":
            self._accept_settings(self._file, "")
            return

        sandbox = self._pressed_sandbox()
        # OK is off while nothing is pressed, so a press with no sandbox picked has nothing to answer.
        if sandbox is None:
            return

        self._accept_settings(str(sandbox.path), sandbox.name)

    def _accept_settings(self, path: str, name: str) -> None:
        """Check the settings file as the run will read it; an error keeps the user here."""
        try:
            check_sandbox(path, name)
        except AlrError as exc:
            self.query_one("#error", Static).update(error_text(exc))
            return

        self.dismiss(PermissionChoice(Permissions.SANDBOX, path, name))


# Radio ids cannot be arbitrary sandbox names, so a sandbox is "sandbox-" and its place in the list.
_SANDBOX_ID = "sandbox-"


def _sandbox_id(index: int) -> str:
    return f"{_SANDBOX_ID}{index}"


def _sandbox_label(sandbox: Sandbox) -> str:
    """The name, with the reason after it when the file cannot be used."""
    if sandbox.problem:
        return f"{sandbox.name}   ({sandbox.problem})"

    return sandbox.name


def _answered_file(settings: RunSettings) -> str:
    """The --sandbox-settings file already answering the question, or an empty string."""
    if settings.permissions is Permissions.SANDBOX and not settings.sandbox_name:
        return settings.sandbox_file

    return ""


def _sandbox_preview(sandbox: Sandbox) -> str:
    """The sandbox's file under its path, or under it why the file cannot be used."""
    if sandbox.problem:
        return f"{sandbox.path}\n{sandbox.problem}"

    return _file_preview(str(sandbox.path))


def _file_preview(path: str) -> str:
    """The settings file exactly as it is on disk under its path, or a line saying why there is none."""
    return f"{path}\n{_read(path)}"


class ActionsDialog(_Dialog[tuple[list[Action], list[Action]]]):
    """Add and remove the file's after_task and after_run actions in two lists.

    Done with both empty is a decision that there are none.
    """

    DEFAULT_CSS = """
    ActionsDialog #dialog { width: 80%; max-width: 90; }
    ActionsDialog ListView { height: auto; max-height: 6; }
    """

    # Typing an action is the first thing to do; the lists are reached with Tab.
    AUTO_FOCUS = "#cmd"

    def __init__(self, after_task: list[Action], after_run: list[Action]) -> None:
        super().__init__()
        self._lists: dict[str, list[Action]] = {
            "after-task": list(after_task),
            "after-run": list(after_run),
        }
        # Add and Remove act on the list last focused, since pressing a button moves focus off it.
        self._focused_list = "after-task"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("After each task")
            yield self._list_view("after-task")
            yield Label("After the run")
            yield self._list_view("after-run")
            yield Input(placeholder="command", id="cmd")
            yield Input(placeholder="timeout, such as 5m (optional)", id="timeout")
            yield Static("", id="error")
            with Horizontal():
                yield Button("Add", id="add")
                yield Button("Remove", id="remove")
                yield Button("Done", id="done")
                yield Button("Cancel", id="cancel")

    def _list_view(self, list_id: str) -> ListView:
        items = (self._item(action) for action in self._lists[list_id])
        return ListView(*items, id=list_id)

    def on_descendant_focus(self, event: DescendantFocus) -> None:
        """Remember the last list that had focus: a button click moves focus off it, and Add/Remove need it."""
        if not isinstance(event.widget, ListView):
            return

        widget_id = event.widget.id
        if widget_id is not None and widget_id in self._lists:
            self._focused_list = widget_id

    async def on_button_pressed(self, message: Button.Pressed) -> None:
        match message.button.id:
            case "add":
                await self._add()
            case "remove":
                await self._remove()
            case "done":
                self.dismiss((self._lists["after-task"], self._lists["after-run"]))
            case "cancel":
                self.dismiss(None)

    async def on_input_submitted(self) -> None:
        await self._add()

    async def _add(self) -> None:
        cmd = self.query_one("#cmd", Input).value.strip()
        timeout = self.query_one("#timeout", Input).value.strip()
        error = _action_error(cmd, timeout)
        self.query_one("#error", Static).update(error or "")
        if error:
            return

        action = Action(command=cmd, timeout=timeout)
        self._lists[self._focused_list].append(action)
        await self.query_one(f"#{self._focused_list}", ListView).append(
            self._item(action)
        )
        self.query_one("#cmd", Input).value = ""
        self.query_one("#timeout", Input).value = ""

    async def _remove(self) -> None:
        view = self.query_one(f"#{self._focused_list}", ListView)
        index = view.index
        if index is None:
            return

        del self._lists[self._focused_list][index]
        await view.pop(index)

    @staticmethod
    def _item(action: Action) -> ListItem:
        suffix = f"  ({action.timeout})" if action.timeout else ""
        return ListItem(Label(action.command + suffix))


def _action_error(cmd: str, timeout: str) -> str | None:
    """Why this action cannot be added, or None when it can."""
    if not cmd:
        return "an action needs a command"

    if not timeout:
        return None

    try:
        parse_timeout(timeout)
    except ValueError as exc:
        return f"timeout: {exc}"

    return None
