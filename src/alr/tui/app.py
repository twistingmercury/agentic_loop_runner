"""The full-screen view: one main window, its states, and the run worker."""

import asyncio
import signal
import time
from enum import StrEnum
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Select, Static
from textual.worker import Worker, WorkerState

from alr.engine.commit import CommitMode, work_tree_root
from alr.engine.loop import read_tasks, reset_failed, run
from alr.durations import format_elapsed
from alr.errors import AlrError
from alr.record.events import RunDone
from alr.stream.frontend import StreamFrontEnd
from alr.tui.adapter import RunEvent, make_report
from alr.tui.dialogs import (
    ErrorDialog,
    FileViewer,
    FolderDialog,
    GatesDialog,
    PermissionsDialog,
    ResetDialog,
    SummaryDialog,
)
from alr.tui.panes import ActivityPane, GatesPane, TasksPane
from alr.tui.prepare import prepare
from alr.tasks import FAILED, Task, TaskList
from alr.tui.settings import Permissions, RunSettings


_STOPPED = "stopped"
_NO_RESULT = "the run ended without a result"


class AppState(StrEnum):
    SETUP = "setup"
    READY = "ready"
    RUNNING = "running"
    FINISHED = "finished"


class AlrApp(App[None]):
    """Setup, then one run, then the result on screen until the user quits."""

    TITLE = "alr"
    # Horizontal fills the screen by default; the bars should take one row each.
    CSS = """
    #strip, #menu { height: auto; }
    /* Flat buttons: plain words, and only keyboard focus lights one up. */
    Button {
        border: none;
        height: 1;
        min-width: 0;
        width: auto;
        padding: 0 1;
        background: transparent;
        color: $text;
        text-style: none;
    }
    Button:focus { background: $accent; text-style: bold; }
    #start { margin: 1 0; }
    #commit-box { width: 27; height: auto; }
    #commit-row, #logging-row { width: auto; height: auto; }
    .field { width: auto; height: 3; padding: 0 1; content-align: left middle; }
    #commit { width: 18; }
    #logging { width: 12; }
    #commit-note { height: auto; padding: 0 1; }
    #permissions { width: auto; padding: 1 2; }
    #reason { height: auto; padding: 0 1; color: $warning; }
    #panes { height: 1fr; }
    #panes > * { height: 1fr; border: round $primary; }
    #tasks { width: 5fr; min-width: 28; }
    #activity { width: 3fr; }
    #gates { width: 2fr; }
    """
    BINDINGS = [
        Binding("s", "start", "Start"),
        # Textual receives Ctrl-C as a key press, so it must be taken here or it quits.
        Binding("ctrl+c", "stop", "Stop", priority=True),
        Binding("q", "quit_if_idle", "Quit"),
        Binding("f", "menu('folder')", "Folder"),
        Binding("p", "menu('permissions')", "Permissions"),
        Binding("g", "menu('gates')", "Gates"),
        Binding("r", "menu('reset')", "Reset"),
        Binding("t", "menu('tasks')", "Tasks file"),
        Binding("x", "menu('sandbox')", "Sandbox file"),
    ]

    def __init__(self, settings: RunSettings) -> None:
        super().__init__()
        self.settings = settings
        self.state = AppState.SETUP
        self.outcome = ""
        self._stopping = False
        self._worker: Worker[None] | None = None
        self._started = 0.0
        self._task_list: TaskList | None = None
        self.in_work_tree: bool | None = None
        """None until the launch check has answered."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="strip"):
            with Vertical(id="commit-box"):
                with Horizontal(id="commit-row"):
                    yield Static("Commit:", id="commit-label", classes="field")
                    yield Select(
                        [
                            ("per task", CommitMode.PER_TASK),
                            ("after all", CommitMode.AFTER_ALL),
                        ],
                        value=self.settings.commit_mode,
                        allow_blank=False,
                        id="commit",
                    )
                note = Static(
                    "no git repository: nothing will be committed", id="commit-note"
                )
                note.display = False
                yield note
            yield Static("", id="permissions")
            with Horizontal(id="logging-row"):
                yield Static("Logging:", id="logging-label", classes="field")
                yield Select(
                    [("on", True), ("off", False)],
                    value=self.settings.logging,
                    allow_blank=False,
                    id="logging",
                )
            yield Button("Start", id="start")
        yield Static("", id="reason")
        with Horizontal(id="menu"):
            yield Button("Folder", id="menu-folder")
            yield Button("Permissions", id="menu-permissions")
            yield Button("Gates", id="menu-gates")
            yield Button("Reset", id="menu-reset")
            yield Button("Tasks", id="menu-tasks")
            yield Button("Sandbox", id="menu-sandbox")
            yield Button("Quit", id="menu-quit")
        with Horizontal(id="panes"):
            yield TasksPane(id="tasks")
            yield _activity_pane()
            yield GatesPane(id="gates")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        # After the first refresh, so the table has its columns before it gets rows.
        self.call_after_refresh(self._show_tasks)
        self._refresh()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self.action_stop)
        self.run_worker(self._check_work_tree(), exit_on_error=False)
        self.run_worker(self.launch_sequence(), exit_on_error=False)

    async def _check_work_tree(self) -> None:
        """Without a work tree nothing can be committed, so the commit choice is moot."""
        self.in_work_tree = await work_tree_root() is not None
        if self.in_work_tree:
            return

        self.query_one("#commit", Select).disabled = True
        self.query_one("#commit-note", Static).display = True

    def _read_tasks(self) -> None:
        """Load the task file; an unreadable one is reported by the dialogs."""
        if not self.settings.tasks_file:
            return

        try:
            self._task_list = self._open_file(self.settings.tasks_file)
        except AlrError:
            return

    def _open_file(self, tasks_file: str) -> TaskList:
        """Read a task file, first resetting its failed tasks when --reset-failed asked."""
        if self.settings.reset_failed:
            reset_failed(tasks_file)

        return read_tasks(tasks_file)

    def _show_tasks(self) -> None:
        """Show the tasks before Start."""
        # The launch worker may not have read the file yet; whichever runs first reads it.
        if self._task_list is None:
            self._read_tasks()
        # The file may hold failed tasks, which hold Start back.
        self._refresh()

        if self._task_list is not None:
            self.query_one(TasksPane).load(self._task_list)
            self._refresh()

    async def launch_sequence(self) -> None:
        """Ask, in order, only for what the flags and the task file leave open."""
        # Read here, not after the first refresh: needs_gates must see the file.
        self._read_tasks()
        if not self.settings.tasks_file:
            await self.ask_folder()

        if not self.settings.tasks_file:
            return

        if self.settings.permissions is Permissions.UNSET:
            await self.ask_permissions()

        if self.needs_gates():
            await self.ask_gates()

    async def ask_folder(self) -> None:
        """Show the folder dialog and apply an accepted answer to the settings."""
        check = await self.push_screen_wait(FolderDialog(str(Path.cwd())))
        if check is None:
            return

        # Gates edited for another folder must never be saved into this one's file.
        if check.tasks_file != self.settings.tasks_file:
            self.settings.gates = None

        self.settings.tasks_file = check.tasks_file
        try:
            # The check read the file before any reset; --reset-failed acts on the pick.
            self._task_list = (
                self._open_file(check.tasks_file)
                if self.settings.reset_failed
                else check.task_list
            )
        except AlrError as exc:
            self.push_screen(ErrorDialog(str(exc)))
            return

        if check.sandbox_file and self.settings.permissions is Permissions.UNSET:
            self.settings.permissions = Permissions.SANDBOX
            self.settings.sandbox_file = check.sandbox_file

        self.query_one(TasksPane).load(self._task_list)
        self._refresh()

    async def ask_permissions(self) -> None:
        """Show the permissions dialog and apply an accepted answer to the settings."""
        choice = await self.push_screen_wait(
            PermissionsDialog(self.settings.sandbox_file)
        )
        if choice is None:
            return

        self.settings.permissions = choice.permissions
        self.settings.sandbox_file = choice.sandbox_file
        self._refresh()

    async def ask_reset(self) -> None:
        """Offer to put the failed tasks back to pending; this saves the file at once."""
        failed = self._failed_tasks()
        if not failed:
            return

        if await self.push_screen_wait(ResetDialog(failed)) is None:
            return

        try:
            reset_failed(self.settings.tasks_file)
            self._task_list = read_tasks(self.settings.tasks_file)
        except AlrError as exc:
            self.push_screen(ErrorDialog(str(exc)))
            return

        self.query_one(TasksPane).load(self._task_list)
        self._refresh()

    def _failed_tasks(self) -> list[Task]:
        if self._task_list is None:
            return []

        return [task for task in self._task_list.tasks if task.state == FAILED]

    def _blocker(self) -> str | None:
        """Say what stops Start: no folder, then failed tasks, then the settings."""
        failed = len(self._failed_tasks())
        if self.settings.tasks_file and failed:
            noun = "task" if failed == 1 else "tasks"
            return f"{failed} failed {noun}: press r to reset"

        return self.settings.missing()

    def needs_gates(self) -> bool:
        """True when the loaded file leaves the gates undecided and the user has not decided."""
        if self._task_list is None or self.settings.gates is not None:
            return False

        return self._task_list.shared.gates is None

    async def ask_gates(self) -> None:
        """Show the gates dialog; nothing is saved until Start passes its checks."""
        current = self.settings.gates
        if current is None and self._task_list is not None:
            current = self._task_list.gate_list()

        gates = await self.push_screen_wait(GatesDialog(current or []))
        if gates is not None:
            self.settings.gates = gates

    def on_unmount(self) -> None:
        asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)

    def _refresh(self) -> None:
        """Bring the Start button and the reason line in line with the state."""
        self.query_one("#permissions", Static).update(
            _permissions_label(self.settings.permissions)
        )
        if self.state in (AppState.RUNNING, AppState.FINISHED):
            self.query_one("#start", Button).disabled = True
            return

        reason = self._blocker()
        self.state = AppState.SETUP if reason else AppState.READY
        self.query_one("#start", Button).disabled = reason is not None
        self.query_one("#reason", Static).update(reason or "")

    def on_button_pressed(self, message: Button.Pressed) -> None:
        name = message.button.id or ""
        if name == "start":
            self.action_start()
            return

        if name.startswith("menu-"):
            self._menu(name.removeprefix("menu-"))

    def action_menu(self, item: str) -> None:
        self._menu(item)

    def _menu(self, item: str) -> None:
        """Route a menu button; the viewers work any time, the setup dialogs only before Start."""
        if item == "quit":
            self.action_quit_if_idle()
            return

        if item == "tasks":
            self.push_screen(FileViewer(self.settings.tasks_file))
            return

        if item == "sandbox":
            self.push_screen(FileViewer(self.settings.sandbox_file))
            return

        # The setup dialogs change the settings, which are locked once a run starts.
        if self.state in (AppState.RUNNING, AppState.FINISHED):
            return

        action = {
            "folder": self.ask_folder,
            "permissions": self.ask_permissions,
            "gates": self.ask_gates,
            "reset": self.ask_reset,
        }
        self.run_worker(action[item](), exit_on_error=False)

    def on_select_changed(self, message: Select.Changed) -> None:
        if message.select.id == "commit" and isinstance(message.value, CommitMode):
            self.settings.commit_mode = message.value

        if message.select.id == "logging" and isinstance(message.value, bool):
            self.settings.logging = message.value

    def action_start(self) -> None:
        if self.state is not AppState.READY:
            return

        # RUNNING at once, so a second press before prepare returns starts nothing.
        self.state = AppState.RUNNING
        self._started = time.monotonic()
        self._refresh()
        self._worker = self.run_worker(self._run(), name="run", exit_on_error=False)

    def action_stop(self) -> None:
        # A second Stop quits whatever the run is doing, so nobody is ever trapped.
        if self.state is not AppState.RUNNING or self._stopping:
            self.exit()
            return

        self._stopping = True
        if self._worker is not None:
            self._worker.cancel()

    def action_quit_if_idle(self) -> None:
        if self.state is not AppState.RUNNING:
            self.exit()

    async def _run(self) -> None:
        try:
            prepared = await prepare(self.settings)
        except asyncio.CancelledError:
            # Stop before the engine started: it never posts a RunDone, so this does.
            self._finish_or_fail(_STOPPED)
            raise
        except Exception as exc:
            self._back_to_ready(exc)
            return

        self.query_one(TasksPane).load(prepared.task_list)
        front_end = StreamFrontEnd(make_report(self.post_message, prepared.record))
        try:
            await run(prepared.task_list, prepared.options, front_end)
        except AlrError:
            # The engine already reported RunDone with the reason.
            pass
        finally:
            prepared.close()

    def _back_to_ready(self, exc: Exception) -> None:
        """A check failed before the run began; show why and let the user try again."""
        # READY lets the refresh recompute the state from the settings.
        self.state = AppState.READY
        self._refresh()
        self.push_screen(ErrorDialog(_error_text(exc)))

    def on_worker_state_changed(self, message: Worker.StateChanged) -> None:
        """The safety net: a run worker that ends without a RunDone must not leave RUNNING."""
        if message.worker is not self._worker:
            return

        if message.state not in (
            WorkerState.SUCCESS,
            WorkerState.ERROR,
            WorkerState.CANCELLED,
        ):
            return

        error = message.worker.error
        if error is None:
            self._finish_or_fail(_STOPPED if self._stopping else _NO_RESULT)
            return

        self._finish_or_fail(_error_text(error))

    def _finish_or_fail(self, error: str) -> None:
        """The one way out of RUNNING when the engine's RunDone did not arrive."""
        if self.state is not AppState.RUNNING:
            return

        self._finish(RunDone(error, time.monotonic() - self._started))

    def on_run_event(self, message: RunEvent) -> None:
        event = message.event
        self.query_one(TasksPane).apply(event)
        self.query_one(ActivityPane).apply(event)
        self.query_one(GatesPane).apply(event)
        if isinstance(event, RunDone):
            self._finish(event)

    def on_tasks_pane_selected(self, message: TasksPane.Selected) -> None:
        self.query_one(ActivityPane).show(message.task_id)

    def _finish(self, event: RunDone) -> None:
        self.state = AppState.FINISHED
        self.outcome = _outcome(event, self._stopping)
        self.query_one("#status", Static).update(
            f"{self.outcome} in {format_elapsed(event.seconds)}"
        )
        self._refresh()
        self.push_screen(SummaryDialog(self.outcome, event.seconds, event.error))


def _activity_pane() -> ActivityPane:
    """Build the activity pane; its constructor takes no id, so it is set before mounting."""
    pane = ActivityPane()
    pane.id = "activity"
    return pane


def _permissions_label(permissions: Permissions) -> str:
    """Name the current permission choice for the strip's button."""
    if permissions is Permissions.UNSET:
        return "Permissions: not chosen"

    return f"Permissions: {permissions.value}"


def _error_text(exc: BaseException) -> str:
    """Say what went wrong; only a domain error's own text is enough without its type."""
    if isinstance(exc, AlrError):
        return str(exc)

    name = type(exc).__name__
    return f"{name}: {exc}" if str(exc) else name


def _outcome(event: RunDone, stopping: bool) -> str:
    """Name how the run ended.

    Only the user's own Stop is a stop: the text of a cancelled run varies
    ("stopped", or "task 1: name: stopped"), and a run that finished clean
    stays done even if Stop raced it.
    """
    if event.error is None:
        return "done"

    return "stopped" if stopping else "failed"


def run_app(settings: RunSettings) -> int:
    """Run the view to the end; 0 only when the run finished clean."""
    app = AlrApp(settings)
    # A keyboard-driven view: no mouse reporting, so the terminal keeps its own.
    app.run(mouse=False)
    return 0 if app.outcome == "done" else 1
