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
    Checkbox,
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
from alr.tasks import Action, Task, parse_timeout
from alr.tui.checks import FolderCheck, check_folder, check_sandbox
from alr.engine.presets import Preset, Switches, preset_path, render, switches_for
from alr.engine.sandbox import folder_sandbox
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


class SummaryDialog(_Dialog[None]):
    """How the run ended; the app stays open behind it."""

    def __init__(self, outcome: str, seconds: float, error: str | None) -> None:
        super().__init__()
        self._text = f"Run {outcome} in {format_elapsed(seconds)}"
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
            self.query_one("#error", Static).update(error_text(exc))
            return

        self.dismiss(check)


@dataclass(frozen=True)
class PermissionChoice:
    """How sessions run: a preset with its switches, a settings file as it is, or skip."""

    permissions: Permissions
    sandbox_file: str
    preset: Preset | None
    switches: Switches | None


class PermissionsDialog(_Dialog[PermissionChoice]):
    """Pick Tight or Large and adjust the three switches, use a settings file, or skip permissions."""

    DEFAULT_CSS = """
    PermissionsDialog #dialog { width: 90%; max-width: 110; height: 90%; }
    /* The preview takes what is left, so the buttons stay on screen however long the document is. */
    PermissionsDialog #preview { margin-top: 1; height: 1fr; min-height: 3; overflow-y: auto; }
    PermissionsDialog #note { margin-bottom: 1; }
    PermissionsDialog Checkbox { border: none; height: 1; padding: 0; }
    """

    def __init__(self, settings: RunSettings) -> None:
        super().__init__()
        self._settings = settings
        self._folder_file = _folder_file(settings)
        self._answered = _answered_file(settings)
        self._file = self._answered or self._folder_file or ""
        self._start = self._first_radio()

    def _first_radio(self) -> str:
        """The id of the radio that starts pressed."""
        if self._answered:
            return "file"

        if self._settings.preset is not None:
            return self._settings.preset.value

        if self._settings.permissions is Permissions.SKIP:
            return "skip"

        return Preset.TIGHT.value

    def _first_switches(self) -> Switches:
        """The switch positions that start shown: the ones already chosen, else Tight's."""
        if self._settings.switches is not None:
            return self._settings.switches

        return switches_for(self._settings.preset or Preset.TIGHT)

    def compose(self) -> ComposeResult:
        switches = self._first_switches()
        off = self._start not in _PRESET_IDS
        with Vertical(id="dialog"):
            yield Label("How should sessions run?")
            with RadioSet():
                if self._file:
                    yield self._radio("file", f"Use the settings file {self._file}")

                yield self._radio(
                    "tight", "Tight sandbox   no web, no Docker, keys hidden"
                )
                yield self._radio("large", "Large sandbox   web and Docker allowed")
                yield self._radio("skip", "Skip permissions (no sandbox)")

            yield Checkbox(
                "Sessions may reach the web", switches.web, id="web", disabled=off
            )
            yield Checkbox(
                "Sessions may run Docker  (runs outside the sandbox with your full access)",
                switches.docker,
                id="docker",
                disabled=off,
            )
            yield Checkbox(
                "Hide credential folders (~/.ssh, ~/.aws, ~/.gnupg, ~/.config/gh, ~/.netrc, ~/.claude)",
                switches.hide_credentials,
                id="hide",
                disabled=off,
            )
            yield Static("", id="preview", markup=False)
            yield Static("", id="note", markup=False)
            yield Static("", id="error")
            with Horizontal():
                yield Button("OK", id="ok")
                yield Button("Cancel", id="cancel")

    def _radio(self, radio_id: str, label: str) -> RadioButton:
        return RadioButton(label, id=radio_id, value=radio_id == self._start)

    def on_mount(self) -> None:
        self._refresh_preview()

    def on_radio_set_changed(self, message: RadioSet.Changed) -> None:
        radio_id = message.pressed.id or ""
        for box in self.query(Checkbox):
            box.disabled = radio_id not in _PRESET_IDS

        if radio_id in _PRESET_IDS:
            self._show_switches(switches_for(Preset(radio_id)))

        self._refresh_preview()

    def on_checkbox_changed(self) -> None:
        self._refresh_preview()

    def _show_switches(self, switches: Switches) -> None:
        """Set the three checkboxes; the caller refreshes the preview once."""
        with self.prevent(Checkbox.Changed):
            self.query_one("#web", Checkbox).value = switches.web
            self.query_one("#docker", Checkbox).value = switches.docker
            self.query_one("#hide", Checkbox).value = switches.hide_credentials

    def _pressed(self) -> str:
        """The id of the pressed radio."""
        button = self.query_one(RadioSet).pressed_button
        return button.id or "" if button else ""

    def _switches(self) -> Switches:
        """The switch positions the checkboxes show now."""
        return Switches(
            web=self.query_one("#web", Checkbox).value,
            docker=self.query_one("#docker", Checkbox).value,
            hide_credentials=self.query_one("#hide", Checkbox).value,
        )

    def _refresh_preview(self) -> None:
        """Show the document the chosen radio stands for, and what Start does with it."""
        pressed = self._pressed()
        preview = ""
        note = ""
        if pressed in _PRESET_IDS:
            preview = render(self._switches())
            note = self._written_note()
        elif pressed == "file":
            preview = _file_preview(self._file)
            note = "Nothing is written. Pick a preset to replace it."

        self.query_one("#preview", Static).update(preview)
        self.query_one("#note", Static).update(note)

    def _written_note(self) -> str:
        """Say where a preset is written, and that a folder file is replaced."""
        note = (
            f"Written to {self._preset_file()} when you press Start."
            " Cancel or Quit discards it."
        )
        if self._folder_file:
            note += " Replaces the file there."

        return note

    def _preset_file(self) -> str:
        """Where a preset goes: beside the task file, or a stand-in before a folder is chosen."""
        if self._settings.tasks_file:
            return preset_path(self._settings.tasks_file)

        return "<folder>/sandbox.json"

    def on_button_pressed(self, message: Button.Pressed) -> None:
        if message.button.id == "cancel":
            self.dismiss(None)
            return

        self._accept()

    def _accept(self) -> None:
        pressed = self._pressed()
        if pressed == "skip":
            self.dismiss(PermissionChoice(Permissions.SKIP, "", None, None))
            return

        if pressed == "file":
            self._accept_file()
            return

        self.dismiss(
            PermissionChoice(
                Permissions.SANDBOX,
                preset_path(self._settings.tasks_file)
                if self._settings.tasks_file
                else "",
                Preset(pressed),
                self._switches(),
            )
        )

    def _accept_file(self) -> None:
        """Check the settings file as the run will read it; an error keeps the user here."""
        try:
            check_sandbox(self._file)
        except AlrError as exc:
            self.query_one("#error", Static).update(error_text(exc))
            return

        self.dismiss(PermissionChoice(Permissions.SANDBOX, self._file, None, None))


_PRESET_IDS = frozenset(preset.value for preset in Preset)


def _answered_file(settings: RunSettings) -> str:
    """The settings file already answering the question, or an empty string."""
    if settings.switches is None:
        return settings.sandbox_file

    return ""


def _folder_file(settings: RunSettings) -> str | None:
    """The run folder's sandbox.json when it has one."""
    if not settings.tasks_file:
        return None

    return folder_sandbox(str(Path(settings.tasks_file).parent))


def _file_preview(path: str) -> str:
    """The settings file as it is on disk under a header, or why it cannot be read."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"{path} is not accessible: {exc.strerror}"

    return f"{path} (as it is)\n{text}"


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
