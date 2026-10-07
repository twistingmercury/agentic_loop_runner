"""The full-screen view: one main window, its states, and the run worker."""

import asyncio
import signal
from enum import StrEnum
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Footer, Header, Select, Static, Switch
from textual.worker import Worker

from alr.engine.commit import CommitMode
from alr.engine.loop import load_tasks, run
from alr.errors import AlrError
from alr.stream.events import RunDone
from alr.stream.frontend import StreamFrontEnd
from alr.tui.adapter import RunEvent, make_report
from alr.tui.dialogs import (
    ErrorDialog,
    FolderDialog,
    GatesDialog,
    PermissionsDialog,
    SummaryDialog,
)
from alr.tui.panes import ActivityPane, GatesPane, TasksPane
from alr.tui.prepare import prepare
from alr.tasks import TaskList
from alr.tui.settings import Permissions, RunSettings


class AppState(StrEnum):
    SETUP = "setup"
    READY = "ready"
    RUNNING = "running"
    FINISHED = "finished"


class AlrApp(App[None]):
    """Setup, then one run, then the result on screen until the user quits."""

    TITLE = "alr"
    BINDINGS = [
        Binding("s", "start", "Start"),
        # Textual receives Ctrl-C as a key press, so it must be taken here or it quits.
        Binding("ctrl+c", "stop", "Stop", priority=True),
        Binding("q", "quit_if_idle", "Quit"),
    ]

    def __init__(self, settings: RunSettings) -> None:
        super().__init__()
        self.settings = settings
        self.state = AppState.SETUP
        self.outcome = ""
        self._stopping = False
        self._worker: Worker[None] | None = None
        self._task_list: TaskList | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="strip"):
            yield Select(
                [
                    ("per task", CommitMode.PER_TASK),
                    ("after all", CommitMode.AFTER_ALL),
                ],
                value=self.settings.commit_mode,
                allow_blank=False,
                id="commit",
            )
            yield Button("", id="permissions")
            yield Switch(value=self.settings.logging, id="logging")
            yield Button("Start", id="start", variant="success")
            yield Static("", id="reason")
        with Horizontal(id="panes"):
            yield TasksPane(id="tasks")
            yield _activity_pane()
            yield GatesPane(id="gates")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        # After the first refresh, so the table has its columns before it gets rows.
        self.call_after_refresh(self._load_tasks)
        self._refresh()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self.action_stop)

    def _load_tasks(self) -> None:
        """Show the tasks before Start; an unreadable file is reported by the dialogs."""
        if not self.settings.tasks_file:
            return

        try:
            self._task_list = load_tasks(self.settings.tasks_file)
        except AlrError:
            return

        self.query_one(TasksPane).load(self._task_list)

    async def ask_folder(self) -> None:
        """Show the folder dialog and apply an accepted answer to the settings."""
        check = await self.push_screen_wait(FolderDialog(str(Path.cwd())))
        if check is None:
            return

        self.settings.tasks_file = check.tasks_file
        self._task_list = check.task_list
        if check.sandbox_file and self.settings.permissions is Permissions.UNSET:
            self.settings.permissions = Permissions.SANDBOX
            self.settings.sandbox_file = check.sandbox_file

        self.query_one(TasksPane).load(check.task_list)
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
        self.query_one("#permissions", Button).label = _permissions_label(
            self.settings.permissions
        )
        if self.state in (AppState.RUNNING, AppState.FINISHED):
            self.query_one("#start", Button).disabled = True
            return

        reason = self.settings.missing()
        self.state = AppState.SETUP if reason else AppState.READY
        self.query_one("#start", Button).disabled = reason is not None
        self.query_one("#reason", Static).update(reason or "")

    def on_button_pressed(self, message: Button.Pressed) -> None:
        if message.button.id == "start":
            self.action_start()
            return

        if message.button.id == "permissions" and self.state is not AppState.RUNNING:
            self.run_worker(self.ask_permissions())

    def on_select_changed(self, message: Select.Changed) -> None:
        if message.select.id == "commit" and isinstance(message.value, CommitMode):
            self.settings.commit_mode = message.value

    def on_switch_changed(self, message: Switch.Changed) -> None:
        if message.switch.id == "logging":
            self.settings.logging = message.value

    def action_start(self) -> None:
        if self.state is not AppState.READY:
            return

        # RUNNING at once, so a second press before prepare returns starts nothing.
        self.state = AppState.RUNNING
        self._refresh()
        self._worker = self.run_worker(self._run(), name="run", exit_on_error=False)

    def action_stop(self) -> None:
        if self.state is not AppState.RUNNING:
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
        except AlrError as exc:
            # READY lets the refresh recompute the state from the settings.
            self.state = AppState.READY
            self._refresh()
            self.push_screen(ErrorDialog(str(exc)))
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
            f"{self.outcome} in {event.seconds:.1f}s"
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
    app.run()
    return 0 if app.outcome == "done" else 1
