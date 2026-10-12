"""The full-screen view: one main window, its states, and the run worker."""

import asyncio
import signal
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Header, Select, Static
from textual.timer import Timer
from textual.worker import Worker, WorkerState

from alr.engine import proc
from alr.engine.loop import read_tasks, reset_failed, reset_in_memory, run
from alr.durations import format_elapsed
from alr.errors import AlrError
from alr.engine.stop import Stop
from alr.record.events import RunDone, RunResult, TaskStarted
from alr.stream.frontend import StreamFrontEnd
from alr.tui.adapter import RunEvent, make_report
from alr.tui.dialogs import (
    ErrorDialog,
    error_text,
    FileViewer,
    FolderDialog,
    ActionsDialog,
    PermissionsDialog,
    ResetDialog,
    SummaryDialog,
)
from alr.tui.panes import ActivityPane, ActionsPane, TasksPane
from alr.tui.prepare import Prepared, prepare
from alr.tui.progress import Progress
from alr.tasks import FAILED, PENDING, Task, TaskList
from alr.tui.settings import Permissions, RunSettings


_STOPPED = "stopped"
# How often a second Stop looks to see whether the first one has finished.
_STOP_POLL = 0.02
_NO_RESULT = "the run ended without a result"


class AppState(StrEnum):
    SETUP = "setup"
    READY = "ready"
    RUNNING = "running"
    FINISHED = "finished"


class AlrApp(App[None]):
    """Setup, then one run, then the result on screen until the user quits."""

    TITLE = "alr"
    # Ctrl-P would open a palette of commands the buttons already offer.
    ENABLE_COMMAND_PALETTE = False
    # Horizontal fills the screen by default; the bars should take one row each.
    CSS = """
    #strip, #menu { height: auto; }
    /* Flat buttons: plain words, and only keyboard focus lights one up. */
    Button {
        border: none;
        height: 1;
        min-width: 0;
        width: auto;
        padding: 0;
        background: transparent;
        color: $text;
        text-style: none;
    }
    Button:focus { background: $accent; text-style: bold; }
    #start { margin: 1 0; }
    #logging-row { width: auto; height: auto; }
    .field { width: auto; height: 3; padding: 0 1; content-align: left middle; }
    #logging { width: 12; }
    #permissions { width: auto; padding: 1 2; }
    #reason { height: auto; padding: 0 1; color: $warning; }
    #panes { height: 1fr; }
    #top { height: 2fr; }
    #top > *, #activity { border: round $primary; }
    #top > * { height: 1fr; }
    #tasks { width: 3fr; min-width: 28; }
    #actions { width: 2fr; }
    #activity { width: 100%; height: 3fr; }
    #progress { height: 1; padding: 0 1; }
    """
    BINDINGS = [
        Binding("s", "start", "Start"),
        # Textual receives Ctrl-C as a key press, so it must be taken here or it quits.
        Binding("ctrl+c", "stop", "Stop", priority=True),
        Binding("q", "quit_if_idle", "Quit"),
        Binding("f", "menu('folder')", "Folder"),
        Binding("p", "menu('permissions')", "Permissions"),
        Binding("a", "menu('actions')", "Actions"),
        Binding("r", "menu('reset')", "Reset"),
        Binding("t", "menu('tasks')", "Tasks file"),
        Binding("x", "menu('sandbox')", "Sandbox file"),
    ]

    def __init__(
        self, settings: RunSettings, clock: Callable[[], float] = time.monotonic
    ) -> None:
        super().__init__()
        self.settings = settings
        self.state = AppState.SETUP
        self.outcome = ""
        self.exit_code = 0
        """What `run_app` returns: the final run's code, 0 when no run ever ended."""
        self._stopping = False
        self._quitting = False
        self._stop = Stop()
        """The one signal object: the SIGTERM handler fills it, the engine and the exit code read it."""
        self._worker: Worker[None] | None = None
        self._started = 0.0
        self._task_list: TaskList | None = None
        # Drives the status line and the task times; a test passes its own so nothing waits.
        self._clock = clock
        self._progress: Progress | None = None
        self._ticker: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="strip"):
            yield Static("", id="permissions")
            with Horizontal(id="logging-row"):
                yield Static("Logging:", id="logging-label", classes="field")
                yield Select(
                    [("on", True), ("off", False)],
                    value=self.settings.logging,
                    allow_blank=False,
                    id="logging",
                )
            yield Button("(s)tart", id="start")
        yield Static("", id="reason")
        with Horizontal(id="menu"):
            yield Button("(f)older", id="menu-folder")
            yield Button("(p)ermissions", id="menu-permissions")
            yield Button("(a)ctions", id="menu-actions")
            yield Button("(r)eset", id="menu-reset")
            yield Button("(t)asks", id="menu-tasks")
            yield Button("sandbo(x)", id="menu-sandbox")
            yield Button("(q)uit", id="menu-quit")
        with Vertical(id="panes"):
            with Horizontal(id="top"):
                yield TasksPane(id="tasks", clock=self._clock)
                yield ActionsPane(id="actions")
            yield Static("", id="progress")
            yield _activity_pane()
        yield Static("", id="status")

    def on_mount(self) -> None:
        # After the first refresh, so the table has its columns before it gets rows.
        self.call_after_refresh(self._show_tasks)
        self._refresh()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self._on_sigterm)
        self.run_worker(self.launch_sequence(), exit_on_error=False)

    def _read_tasks(self) -> None:
        """Load the task file; an unreadable one is reported by the dialogs."""
        if not self.settings.tasks_file:
            return

        try:
            self._task_list = self._open_file(self.settings.tasks_file)
        except AlrError:
            return

    def _open_file(self, tasks_file: str) -> TaskList:
        """Read a task file; --reset-failed resets the failed tasks in this copy only.

        Start saves the reset once its checks pass, so quitting before Start
        leaves the file as the user wrote it.
        """
        task_list = read_tasks(tasks_file)
        if self.settings.reset_failed:
            reset_in_memory(task_list)

        return task_list

    def _show_tasks(self) -> None:
        """Show the tasks before Start."""
        # Queued at mount, this can land after a quick Start; a reload then would wipe the run's rows.
        if self.state in (AppState.RUNNING, AppState.FINISHED):
            return

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
        # Read here, not after the first refresh: needs_actions must see the file.
        self._read_tasks()
        if not self.settings.tasks_file:
            await self.ask_folder()

        if not self.settings.tasks_file:
            return

        if self.settings.permissions is Permissions.UNSET:
            await self.ask_permissions()

        if self.needs_actions():
            await self.ask_actions()

    async def ask_folder(self) -> None:
        """Show the folder dialog and apply an accepted answer to the settings."""
        check = await self.push_screen_wait(FolderDialog(str(Path.cwd())))
        if check is None:
            return

        # Actions edited for another folder must never be saved into this one's file.
        if check.tasks_file != self.settings.tasks_file:
            self.settings.after_task = None

        self.settings.tasks_file = check.tasks_file
        try:
            # The check read the file before any reset; --reset-failed acts on the pick.
            self._task_list = (
                self._open_file(check.tasks_file)
                if self.settings.reset_failed
                else check.task_list
            )
        except AlrError as exc:
            self.push_screen(ErrorDialog(error_text(exc)))
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
            self.push_screen(ErrorDialog(error_text(exc)))
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

    def needs_actions(self) -> bool:
        """True when the loaded file leaves the actions undecided and the user has not decided."""
        if self._task_list is None or self.settings.after_task is not None:
            return False

        return self._task_list.shared.after_task is None

    async def ask_actions(self) -> None:
        """Show the actions dialog; nothing is saved until Start passes its checks."""
        current = self.settings.after_task
        if current is None and self._task_list is not None:
            current = self._task_list.after_task_list()

        actions = await self.push_screen_wait(ActionsDialog(current or []))
        if actions is not None:
            self.settings.after_task = actions

    def on_unmount(self) -> None:
        # The status line goes with the screen, so no tick may come after it.
        self._stop_ticking()
        asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)

    def _lock_dropdowns(self) -> None:
        """A run copies the settings at Start, so later changes would only mislead."""
        locked = self.state in (AppState.RUNNING, AppState.FINISHED)
        self.query_one("#logging", Select).disabled = locked

    def _refresh(self) -> None:
        """Bring the Start button and the reason line in line with the state."""
        self.query_one("#permissions", Static).update(
            _permissions_label(self.settings.permissions)
        )
        self._lock_dropdowns()
        self._grey_out_menu()
        self._refresh_start()
        if self.state in (AppState.RUNNING, AppState.FINISHED):
            return

        reason = self._blocker()
        self.state = AppState.SETUP if reason else AppState.READY
        self.query_one("#start", Button).disabled = reason is not None
        self.query_one("#reason", Static).update(reason or "")

    def _grey_out_menu(self) -> None:
        """Grey out what a run makes inert; Actions, Tasks and Sandbox are left as they are."""
        running = self.state is AppState.RUNNING
        spent = self.state is AppState.FINISHED
        # The setup dialogs stay shut once a run has started; Quit only waits for the run.
        for item in ("folder", "permissions", "reset"):
            self.query_one(f"#menu-{item}", Button).disabled = running or spent
        self.query_one("#menu-quit", Button).disabled = running

    def _refresh_start(self) -> None:
        """While a run goes the Start button is the Stop button; afterwards it is spent."""
        button = self.query_one("#start", Button)
        running = self.state is AppState.RUNNING
        button.label = "Stop (^c)" if running else "(s)tart"
        if running:
            button.disabled = False
        elif self.state is AppState.FINISHED:
            button.disabled = True

    def on_button_pressed(self, message: Button.Pressed) -> None:
        name = message.button.id or ""
        if name == "start":
            self._press_start()
            return

        if name.startswith("menu-"):
            self._menu(name.removeprefix("menu-"))

    def _press_start(self) -> None:
        """The one button starts a run when ready and stops it while it goes."""
        if self.state is AppState.RUNNING:
            # A double click must not throw away the clean shutdown; only Ctrl-C quits.
            if not self._stopping:
                self.action_stop()

            return

        self.action_start()

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
            "actions": self.ask_actions,
            "reset": self.ask_reset,
        }
        self.run_worker(action[item](), exit_on_error=False)

    def on_select_changed(self, message: Select.Changed) -> None:
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

    def _on_sigterm(self) -> None:
        self._stop.signum = signal.SIGTERM
        # A finished run keeps its own code; with no run going SIGTERM is the whole outcome.
        if self.state in (AppState.SETUP, AppState.READY):
            self.exit_code = self._stop.exit_code()

        self.action_stop()

    def action_stop(self) -> None:
        # A second Stop quits whatever the run is doing, so nobody is ever trapped.
        if self.state is AppState.RUNNING and self._stopping:
            # The run never reported, and a stopped run must not look like a success.
            self.exit_code = self._stop.exit_code()
            self._quit_after_stop()
            return

        if self.state is not AppState.RUNNING:
            self.exit()
            return

        self._stopping = True
        if self._worker is not None:
            self._worker.cancel()

    def _quit_after_stop(self) -> None:
        """Quit once the stop under way is done, so a hook that traps TERM does not outlive alr."""
        if self._quitting:
            return

        self._quitting = True
        self.run_worker(self._exit_when_stopped(), exit_on_error=False)

    async def _exit_when_stopped(self) -> None:
        """Wait for the run worker to end, for as long as a stop may take, then exit.

        Polled, not awaited with a timeout: cancelling the wait on the worker's
        task would cancel the task, and with it the stop it is carrying out.
        """
        deadline = time.monotonic() + proc.stop_deadline()
        while self._worker is not None and not self._worker.is_finished:
            if time.monotonic() > deadline:
                break

            await asyncio.sleep(_STOP_POLL)

        self.exit()

    def action_quit_if_idle(self) -> None:
        if self.state is not AppState.RUNNING:
            self.exit()

    async def _run(self) -> None:
        try:
            prepared = await prepare(self.settings, self._stop)
        except asyncio.CancelledError:
            # Stop before the engine started: it never posts a RunDone, so this does.
            self._finish_or_fail(_STOPPED)
            raise
        except Exception as exc:
            self._back_to_ready(exc)
            return

        self.query_one(TasksPane).load(prepared.task_list)
        self._start_ticking(prepared)
        front_end = StreamFrontEnd(make_report(self.post_message, prepared.record))
        try:
            await run(prepared.task_list, prepared.options, front_end)
        except AlrError:
            # The engine already reported RunDone with the reason.
            pass
        finally:
            prepared.close()

    def _start_ticking(self, prepared: Prepared) -> None:
        """Follow the run on the status line, which counts up every second."""
        pending = sum(task.state == PENDING for task in prepared.task_list.tasks)
        actions = len(prepared.task_list.after_task_list())
        self._progress = Progress(pending, actions, clock=self._clock)
        self._ticker = self.set_interval(1, self._tick)

    def _tick(self) -> None:
        """Redraw the status line and the running task's time, so they move between events."""
        if self._progress is None:
            return

        line = self.query_one("#progress", Static)
        # The line is one row tall; whatever wraps is hidden, and the timers come last.
        line.update(self._progress.text(width=line.content_size.width))
        self.query_one(TasksPane).tick()

    def _stop_ticking(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()

        self._ticker = None
        self._progress = None

    def _back_to_ready(self, exc: Exception) -> None:
        """A check failed before the run began; show why and let the user try again."""
        # A stop that landed on this refusal is spent; left set, the next run's first Stop
        # would count as the second and quit without the graceful cancel.
        self._stopping = False
        self._stop.signum = signal.SIGINT
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

        # Only the user's Stop is a stop; anything else here is a run that died without a result.
        if self._stopping:
            result, exit_code = RunResult.STOPPED, self._stop.exit_code()
        else:
            result, exit_code = RunResult.FAILED, 1

        seconds = time.monotonic() - self._started
        self._finish(RunDone(error, seconds, result, exit_code))

    def on_run_event(self, message: RunEvent) -> None:
        # Textual delivers the events queued before an exit after it has taken
        # the screens down, when there is nothing left to draw them on.
        if not self.is_running:
            return

        event = message.event
        if self._progress is not None:
            self._progress.feed(event)
            self._tick()

        self.query_one(TasksPane).apply(event)
        if isinstance(event, TaskStarted):
            # Not left to the highlight message: it is silent when the row was already highlighted.
            self.query_one(ActivityPane).show(event.task.id, event.task.name)

        self.query_one(ActivityPane).apply(event)
        self.query_one(ActionsPane).apply(event)
        if isinstance(event, RunDone):
            self._finish(event)

    def on_tasks_pane_selected(self, message: TasksPane.Selected) -> None:
        self.query_one(ActivityPane).show(message.task_id, message.name)

    def _finish(self, event: RunDone) -> None:
        self.state = AppState.FINISHED
        self.exit_code = event.exit_code
        self.outcome = _outcome(event, self._stopping)
        self._stop_ticking()
        self.query_one("#progress", Static).update("")
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
        return error_text(exc)

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
    """Run the view to the end; it exits with the final run's code, from the table plain mode uses."""
    app = AlrApp(settings)
    # A keyboard-driven view: no mouse reporting, so the terminal keeps its own.
    app.run(mouse=False)
    return app.exit_code
