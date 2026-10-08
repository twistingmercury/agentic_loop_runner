"""AlrApp: the four states, Start, Stop, and the summary, over a real engine and the fake claude."""

import asyncio
import os
import signal
from pathlib import Path

import pytest
from helpers import (
    fake_claude_records,
    git,
    saved_tasks,
    until,
    wait_for_text,
    wait_until_gone,
    write_file,
    write_nested_file,
)
from textual.pilot import Pilot
from textual.screen import ModalScreen
from textual.coordinate import Coordinate
from textual.widgets import (
    Button,
    DataTable,
    Input,
    OptionList,
    Select,
    Static,
    TextArea,
)

from alr.errors import RunLogError
from alr.record.runlog import RunLog
from alr.engine.commit import CommitMode
from alr.tasks import Gate, parse_tasks
from alr.tui.app import AlrApp, AppState
from alr.record.events import Activity, TaskStarted
from alr.tasks import Task
from alr.tui.adapter import RunEvent
from alr.tui.panes import ActivityPane, TasksPane
from alr.tui.prepare import Prepared
from alr.tui import app as app_module
from alr.tui.dialogs import (
    FileViewer,
    FolderDialog,
    GatesDialog,
    PermissionsDialog,
    ResetDialog,
)
from alr.tui.settings import Permissions, RunSettings

TASKS = """shared:
  prompt: do it
  gates: []
tasks:
  - id: 1
    name: First
    prompt: p
  - id: 2
    name: Second
    prompt: p
"""


# The fake claude writes nothing, so a gate makes each task leave a file to commit.
WORKING_TASKS = TASKS.replace(
    "gates: []",
    "gates:\n"
    "    - cmd: n=1; while [ -e work-$n.txt ]; do n=$((n+1)); done; echo w > work-$n.txt",
)


def _settings(repo: Path, tasks: str = TASKS, **fields: object) -> RunSettings:
    tasks_file = write_file(repo / "run" / "tasks.yaml", tasks)
    return RunSettings(
        tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False, **fields
    )


def _visible_settings(repo: Path) -> RunSettings:
    """Settings whose log folder, repo/visible/logs, git can see; only the task file is ignored."""
    (repo / ".git" / "info" / "exclude").write_text(
        "visible/tasks.yaml\n", encoding="utf-8"
    )
    (repo / "visible").mkdir()
    tasks_file = write_file(repo / "visible" / "tasks.yaml", TASKS)
    return RunSettings(
        tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False
    )


async def _finished(app: AlrApp) -> None:
    await until(lambda: app.state is AppState.FINISHED)


async def test_setup_disables_start_and_names_the_reason() -> None:
    app = AlrApp(RunSettings())
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.state is AppState.SETUP
        assert app.query_one("#start").disabled
        assert "choose a run folder" in str(app.query_one("#reason").render())


async def test_ready_enables_start(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.state is AppState.READY
        assert not app.query_one("#start").disabled


@pytest.mark.usefixtures("fake_claude")
async def test_a_clean_run_finishes_done_with_a_summary(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo, WORKING_TASKS))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "done"
        assert git(git_repo, "log", "--format=%s").split("\n")[:2] == [
            "Second",
            "First",
        ]
        assert app.screen.query("#close")


@pytest.mark.usefixtures("fake_claude")
async def test_a_failed_task_finishes_failed(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", '{"state":"failed","error":"nope"}')
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "failed"


@pytest.mark.usefixtures("fake_claude")
async def test_start_twice_starts_one_run(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = tmp_path / "record.log"
    monkeypatch.setenv("FAKECLAUDE_RECORD_FILE", str(record))
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s", "s")
        await _finished(app)

    assert len(record.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.usefixtures("fake_claude")
async def test_stop_ends_a_blocked_run_as_stopped(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.outcome == "stopped"


@pytest.mark.usefixtures("fake_claude")
async def test_the_start_button_becomes_a_working_stop_button(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        button = app.query_one("#start", Button)
        await until(lambda: not button.disabled)
        assert str(button.label) == "(s)tart"

        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)
        assert str(button.label) == "Stop (^c)"
        assert not button.disabled

        button.focus()
        await pilot.press("enter")
        await _finished(app)

        assert app.outcome == "stopped"
        assert button.disabled
        assert str(button.label) == "(s)tart"


async def test_ctrl_p_opens_nothing(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)
        assert type(app.screen).__name__ != "CommandPalette"


@pytest.mark.usefixtures("fake_claude")
async def test_sigterm_stops_a_run_like_ctrl_c(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        os.kill(os.getpid(), signal.SIGTERM)
        await _finished(app)

        assert app.outcome == "stopped"


async def test_ctrl_c_when_nothing_runs_quits(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+c")
        await pilot.pause()

    assert not app.is_running


async def test_a_failed_check_on_start_shows_an_error_and_stays_ready(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo)
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.screen.query("#close"))

        assert app.state is AppState.READY
        assert "clean work tree" in str(app.screen.query_one("#message").render())


async def test_a_run_of_completed_tasks_finishes_done_at_once(git_repo: Path) -> None:
    done = TASKS.replace("prompt: p\n", "prompt: p\n    state: completed\n")
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", done)
    app = AlrApp(
        RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False)
    )
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "done"


async def test_permissions_label_shows_the_choice_and_applies_the_dialog(
    git_repo: Path,
) -> None:
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", TASKS)
    app = AlrApp(RunSettings(tasks_file=tasks_file))
    async with app.run_test() as pilot:
        await until(lambda: isinstance(app.screen, PermissionsDialog))
        label = app.query_one("#permissions")
        assert "not chosen" in str(label.render())

        await pilot.click("#skip")
        await pilot.click("#ok")
        await until(lambda: app.settings.permissions is Permissions.SKIP)

        assert "skip" in str(label.render())


@pytest.mark.parametrize(
    ("gates_block", "want"),
    [("", True), ("  gates: []\n", False), ("  gates:\n    - cmd: 'true'\n", False)],
    ids=["absent key", "decided none", "has gates"],
)
async def test_needs_gates_only_for_an_absent_key(
    git_repo: Path, gates_block: str, want: bool
) -> None:
    text = f"shared:\n  prompt: p\n{gates_block}tasks:\n  - id: 1\n    name: A\n    prompt: p\n"
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", text)
    app = AlrApp(RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP))
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.needs_gates() is want


@pytest.mark.usefixtures("fake_claude")
async def test_start_saves_the_edited_gates(git_repo: Path) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true")])
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.gate_list() == [Gate(cmd="true")]


async def test_launch_opens_the_folder_dialog_when_no_folder_is_known() -> None:
    app = AlrApp(RunSettings())
    async with app.run_test():
        await until(lambda: isinstance(app.screen, FolderDialog))


async def test_launch_skips_every_step_the_inputs_answer(git_repo: Path) -> None:
    text = "shared:\n  prompt: p\n  gates: []\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", text)
    app = AlrApp(RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP))
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.state is AppState.READY
        assert not isinstance(app.screen, ModalScreen)


async def test_launch_asks_for_permissions_then_gates_in_order(git_repo: Path) -> None:
    text = "shared:\n  prompt: p\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", text)
    app = AlrApp(RunSettings(tasks_file=tasks_file))
    async with app.run_test() as pilot:
        await until(lambda: isinstance(app.screen, PermissionsDialog))

        await pilot.click("#skip")
        await pilot.click("#ok")
        await until(lambda: isinstance(app.screen, GatesDialog))

        await pilot.click("#done")
        await until(lambda: app.state is AppState.READY)


async def test_the_menu_reopens_a_dialog_before_start(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.click("#menu-gates")
        await until(lambda: isinstance(app.screen, GatesDialog))

        await pilot.click("#cancel")
        await until(lambda: not isinstance(app.screen, ModalScreen))
        assert app.state is AppState.READY


async def test_the_menu_opens_the_task_file_read_only(git_repo: Path) -> None:
    settings = _settings(git_repo)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.click("#menu-tasks")
        await until(lambda: isinstance(app.screen, ModalScreen))

        viewer = app.screen.query_one("#text", TextArea)
        assert viewer.read_only
        assert viewer.text == Path(settings.tasks_file).read_text(encoding="utf-8")


async def test_the_viewer_says_when_there_is_no_sandbox_file(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.click("#menu-sandbox")
        await until(lambda: isinstance(app.screen, ModalScreen))

        assert "no file chosen yet" in app.screen.query_one("#text", TextArea).text


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize("item", ["folder", "permissions", "gates"])
async def test_setup_dialogs_do_not_open_while_running(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, item: str
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        await pilot.click(f"#menu-{item}")
        await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_a_logged_run_leaves_a_ledger(git_repo: Path) -> None:
    settings = _settings(git_repo)
    settings.logging = True
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

    ledgers = list((git_repo / "run" / "logs").glob("*/run.jsonl"))
    assert len(ledgers) == 1
    # The engine's run_done hook is what the runlog writes as run_finished.
    ledger = ledgers[0].read_text(encoding="utf-8")
    assert '"event":"run_finished","result":"completed"' in ledger


async def test_logging_on_with_a_visible_log_folder_blocks_start(
    git_repo: Path,
) -> None:
    settings = _visible_settings(git_repo)
    settings.logging = True
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.screen.query("#close"))

        assert app.state is AppState.READY
        assert ".gitignore" in str(app.screen.query_one("#message").render())


@pytest.mark.usefixtures("fake_claude")
async def test_turning_logging_off_skips_the_log_folder(git_repo: Path) -> None:
    settings = _visible_settings(git_repo)
    settings.logging = True
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await _tab_to(app, pilot, "logging")
        await pilot.press("enter", "down", "enter")
        await until(lambda: app.settings.logging is False)
        await pilot.press("s")
        await _finished(app)

    assert not (git_repo / "visible" / "logs").exists()


async def test_logging_is_a_dropdown_with_on_and_off(git_repo: Path) -> None:
    settings = _settings(git_repo)
    settings.logging = True
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        dropdown = app.query_one("#logging", Select)

        assert dropdown.value is True
        await _tab_to(app, pilot, "logging")
        await pilot.press("enter")
        options = app.query_one("#logging SelectOverlay", OptionList)

        assert [str(options.get_option_at_index(i).prompt) for i in range(2)] == [
            "on",
            "off",
        ]
        assert options.option_count == 2


@pytest.fixture
def outside_settings(outside: Path, monkeypatch: pytest.MonkeyPatch) -> RunSettings:
    """Settings for a run in a folder with no git repository around it."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outside.parent))
    monkeypatch.chdir(outside)
    tasks_file = write_file(outside / "tasks.yaml", WORKING_TASKS)
    return RunSettings(
        tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False
    )


@pytest.mark.usefixtures("fake_claude")
async def test_a_run_outside_a_repository_finishes_done_without_commits(
    outside: Path, outside_settings: RunSettings
) -> None:
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.in_work_tree is not None)

        assert app.query_one("#commit", Select).disabled
        assert app.query_one("#commit-note", Static).display
        assert "no git repository: nothing will be committed" in str(
            app.query_one("#commit-note", Static).render()
        )

        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "done"
        assert not (outside / ".git").exists()


async def test_a_run_inside_a_repository_keeps_the_commit_choice(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.in_work_tree is not None)
        await pilot.pause()

        assert not app.query_one("#commit", Select).disabled
        assert not app.query_one("#commit-note", Static).display


def _hold_prepare(
    monkeypatch: pytest.MonkeyPatch,
    entered: asyncio.Event,
    *,
    refuse_first_cancel: bool,
) -> None:
    """Make prepare block, optionally ignoring the first cancel like a stuck check."""

    async def held(settings: RunSettings) -> Prepared:
        entered.set()
        refusals = 1 if refuse_first_cancel else 0
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if refusals == 0:
                    raise

                refusals -= 1

    monkeypatch.setattr(app_module, "prepare", held)


async def test_stop_during_prepare_finishes_stopped(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _hold_prepare(monkeypatch, entered, refuse_first_cancel=False)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)

        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.outcome == "stopped"


async def test_pressing_the_stop_button_twice_leaves_the_app_open(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_to_stop(settings: RunSettings) -> Prepared:
        entered.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await release.wait()
            raise

    monkeypatch.setattr(app_module, "prepare", slow_to_stop)
    app = AlrApp(_settings(git_repo))
    exits: list[object] = []
    # exit() only queues the shutdown, so record the call instead of racing it.
    monkeypatch.setattr(app, "exit", lambda *args, **kwargs: exits.append(args))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)
        button = app.query_one("#start", Button)
        button.focus()

        await pilot.press("enter")
        await until(lambda: app._stopping)
        # A button ignores presses while its press flash shows.
        await until(lambda: not button.has_class("-active"))
        await pilot.press("enter")
        await pilot.pause()

        assert not exits
        release.set()
        await _finished(app)

        assert app.outcome == "stopped"
        assert not exits


async def test_a_second_stop_while_stopping_quits(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _hold_prepare(monkeypatch, entered, refuse_first_cancel=True)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)

        await pilot.press("ctrl+c")
        await pilot.press("ctrl+c")
        await until(lambda: not app.is_running)


async def test_a_second_stop_button_press_while_stopping_does_nothing(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _hold_prepare(monkeypatch, entered, refuse_first_cancel=True)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)
        button = app.query_one("#start", Button)
        button.focus()

        await pilot.press("enter")
        await until(lambda: app._stopping)
        # A button ignores presses while its pressed flash shows, so wait that out.
        await until(lambda: not button.has_class("-active"))
        await pilot.press("enter")
        await pilot.pause()

        assert app.is_running
        assert app.state is AppState.RUNNING

        await pilot.press("ctrl+c")
        await until(lambda: not app.is_running)


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        (PermissionError("denied"), "PermissionError: denied"),
        (OSError(), "OSError"),
    ],
    ids=["with a message", "without a message"],
)
async def test_an_unexpected_error_in_prepare_returns_to_ready_with_a_dialog(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, error: OSError, shown: str
) -> None:
    async def failing(settings: RunSettings) -> Prepared:
        raise error

    monkeypatch.setattr(app_module, "prepare", failing)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.screen.query("#close"))

        assert app.state is AppState.READY
        assert str(app.screen.query_one("#message").render()) == shown


@pytest.mark.usefixtures("fake_claude")
async def test_a_run_log_that_cannot_write_still_ends_the_run(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: RunLog, event: object) -> None:
        raise RunLogError("disk full")

    monkeypatch.setattr(RunLog, "record", broken)
    settings = _settings(git_repo)
    settings.logging = True
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "failed"


async def test_launch_opens_the_gates_dialog_when_flags_answer_everything_else(
    git_repo: Path,
) -> None:
    text = "shared:\n  prompt: p\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", text)
    app = AlrApp(RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP))
    async with app.run_test() as pilot:
        await until(lambda: isinstance(app.screen, GatesDialog))
        # Let the table's queued first highlight land; leaving with it queued
        # makes it arrive in a closing app on a busy worker.
        await pilot.pause()


async def _pick_folder(app: AlrApp, pilot: Pilot, folder: Path) -> None:
    await pilot.click("#menu-folder")
    await until(lambda: isinstance(app.screen, FolderDialog))
    app.screen.query_one("#path", Input).value = str(folder)
    await pilot.click("#ok")
    await until(lambda: not isinstance(app.screen, ModalScreen))


async def test_picking_another_folder_drops_the_gates_edited_for_the_first(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true")])
    other = git_repo / "other"
    other.mkdir()
    write_file(other / "tasks.yaml", TASKS)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await _pick_folder(app, pilot, other)

        assert settings.tasks_file == str(other / "tasks.yaml")
        assert settings.gates is None


async def test_picking_the_same_folder_keeps_the_gates(git_repo: Path) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true")])
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await _pick_folder(app, pilot, git_repo / "run")

        assert settings.gates == [Gate(cmd="true")]


@pytest.mark.parametrize("size", [(80, 24), (140, 40)], ids=["80x24", "140x40"])
async def test_the_tasks_pane_keeps_room_at_common_terminal_sizes(
    git_repo: Path, size: tuple[int, int]
) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test(size=size) as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.pause()

        region = app.query_one("#tasks").region
        assert region.height > 3
        assert region.width >= 30


def _activity_lines(app: AlrApp) -> list[str]:
    return [strip.text.rstrip() for strip in app.query_one(ActivityPane).lines]


async def test_the_activity_pane_follows_the_running_task(git_repo: Path) -> None:
    first = Task(id=1, name="First", prompt="p")
    second = Task(id=2, name="Second", prompt="p")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        tasks = app.query_one(TasksPane)
        activity = app.query_one(ActivityPane)
        await until(lambda: tasks.row_count == 2)

        app.post_message(RunEvent(TaskStarted(first)))
        app.post_message(RunEvent(Activity(first, "one says hi")))
        await until(lambda: _activity_lines(app) == ["one says hi"])
        assert activity.border_title == "Task: 1 First"

        app.post_message(RunEvent(TaskStarted(second)))
        await until(lambda: tasks.cursor_row == 1)
        await until(lambda: _activity_lines(app) == [])
        assert activity.border_title == "Task: 2 Second"

        app.post_message(RunEvent(Activity(second, "two says hi")))
        await until(lambda: _activity_lines(app) == ["two says hi"])

        tasks.focus()
        await pilot.press("up")
        await until(lambda: _activity_lines(app) == ["one says hi"])
        assert activity.border_title == "Task: 1 First"

        app.post_message(RunEvent(Activity(second, "two again")))
        await pilot.pause()
        assert _activity_lines(app) == ["one says hi"]

        app.post_message(RunEvent(TaskStarted(second)))
        await until(lambda: _activity_lines(app) == ["two says hi", "two again"])
        assert tasks.cursor_row == 1


THREE_TASKS = (
    TASKS
    + """  - id: 3
    name: Third
    prompt: p
"""
)


async def test_browsing_another_task_gives_way_to_the_next_task_start(
    git_repo: Path,
) -> None:
    first = Task(id=1, name="First", prompt="p")
    second = Task(id=2, name="Second", prompt="p")
    third = Task(id=3, name="Third", prompt="p")
    app = AlrApp(_settings(git_repo, THREE_TASKS))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        tasks = app.query_one(TasksPane)
        activity = app.query_one(ActivityPane)
        await until(lambda: tasks.row_count == 3)

        app.post_message(RunEvent(TaskStarted(first)))
        app.post_message(RunEvent(Activity(first, "one says hi")))
        await until(lambda: _activity_lines(app) == ["one says hi"])

        tasks.focus()
        await pilot.press("down")
        await until(lambda: activity.border_title == "Task: 2 Second")
        app.post_message(RunEvent(Activity(second, "two says hi")))
        app.post_message(RunEvent(Activity(first, "one again")))
        await until(lambda: _activity_lines(app) == ["two says hi"])

        app.post_message(RunEvent(TaskStarted(third)))
        app.post_message(RunEvent(Activity(third, "three says hi")))
        await until(lambda: tasks.cursor_row == 2)
        await until(lambda: _activity_lines(app) == ["three says hi"])
        assert activity.border_title == "Task: 3 Third"

        tasks.move_cursor(row=1)
        await until(lambda: _activity_lines(app) == ["two says hi"])


async def test_a_late_row_zero_highlight_does_not_pull_the_activity_back(
    git_repo: Path,
) -> None:
    second = Task(id=2, name="Second", prompt="p")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        tasks = app.query_one(TasksPane)
        activity = app.query_one(ActivityPane)
        await until(lambda: tasks.row_count == 2)
        await pilot.pause()

        app.post_message(RunEvent(TaskStarted(second)))
        app.post_message(RunEvent(Activity(second, "two says hi")))
        await until(lambda: tasks.cursor_row == 1)
        await until(lambda: _activity_lines(app) == ["two says hi"])

        # The table's first highlight message, delivered after the start moved the cursor.
        row_key = tasks.coordinate_to_cell_key(Coordinate(0, 0)).row_key
        tasks.post_message(DataTable.RowHighlighted(tasks, 0, row_key))
        await pilot.pause()

        assert tasks.cursor_row == 1
        assert activity.border_title == "Task: 2 Second"
        assert _activity_lines(app) == ["two says hi"]


@pytest.fixture
def closed_logs(monkeypatch: pytest.MonkeyPatch) -> list[RunLog]:
    """Every run log closed so far, so a test can tell Prepared.close ran."""
    closed: list[RunLog] = []
    close = RunLog.close

    def spy(self: RunLog) -> None:
        closed.append(self)
        close(self)

    monkeypatch.setattr(RunLog, "close", spy)
    return closed


def _logged_settings(repo: Path, tasks: str) -> RunSettings:
    settings = _settings(repo, tasks)
    settings.logging = True
    return settings


def _ledger(repo: Path) -> str:
    (path,) = (repo / "run" / "logs").glob("*/run.jsonl")
    return path.read_text(encoding="utf-8")


def _gated(cmd: str) -> str:
    return TASKS.replace("gates: []", f"gates:\n    - cmd: '{cmd}'")


@pytest.mark.usefixtures("fake_claude")
async def test_stop_during_a_gate_kills_the_gate_and_ends_stopped(
    git_repo: Path, tmp_path: Path, closed_logs: list[RunLog]
) -> None:
    pid_file = tmp_path / "gate.pid"
    settings = _logged_settings(git_repo, _gated(f"echo $$ > {pid_file}; sleep 30"))
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        pid = int(await wait_for_text(pid_file))

        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.outcome == "stopped"
        await wait_until_gone(pid)
        assert len(closed_logs) == 1
        assert '"event":"run_finished","result":"stopped"' in _ledger(git_repo)


@pytest.mark.usefixtures("fake_claude")
async def test_stop_during_the_commit_ends_stopped_with_nothing_committed(
    git_repo: Path, tmp_path: Path, closed_logs: list[RunLog]
) -> None:
    ready = tmp_path / "hook.ready"
    hook = write_nested_file(
        git_repo / ".git" / "hooks" / "pre-commit",
        f"#!/bin/sh\necho ready > {ready}\nsleep 60\n",
    )
    hook.chmod(0o700)
    app = AlrApp(_logged_settings(git_repo, WORKING_TASKS))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await wait_for_text(ready)

        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.outcome == "stopped"
        assert git(git_repo, "log", "--format=%s").split() == ["init"]
        assert len(closed_logs) == 1


@pytest.mark.usefixtures("fake_claude")
async def test_stop_in_the_second_task_keeps_the_first_completed(
    git_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    closed_logs: list[RunLog],
) -> None:
    record = tmp_path / "record.log"
    monkeypatch.setenv("FAKECLAUDE_RECORD_FILE", str(record))
    monkeypatch.setenv("FAKECLAUDE_BLOCK_ON", "Second")
    settings = _logged_settings(git_repo, WORKING_TASKS)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: len(fake_claude_records(record)) == 2)

        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.outcome == "stopped"
        states = [task.state for task in saved_tasks(Path(settings.tasks_file)).tasks]
        assert states == ["completed", "pending"]
        assert len(closed_logs) == 1


def test_run_app_turns_the_mouse_off(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    seen: dict[str, object] = {}

    def record(self: AlrApp, **kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(AlrApp, "run", record)

    app_module.run_app(_settings(git_repo))

    assert seen == {"mouse": False}


@pytest.mark.parametrize(
    ("key", "dialog"),
    [("f", FolderDialog), ("p", PermissionsDialog), ("g", GatesDialog)],
    ids=["folder", "permissions", "gates"],
)
async def test_hotkeys_open_the_setup_dialogs_before_start(
    git_repo: Path, key: str, dialog: type[ModalScreen[object]]
) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.press(key)

        await until(lambda: isinstance(app.screen, dialog))


@pytest.mark.parametrize("key", ["t", "x"])
async def test_viewer_hotkeys_open_a_viewer(git_repo: Path, key: str) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.press(key)

        await until(lambda: isinstance(app.screen, FileViewer))


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize("key", ["f", "p", "g"])
async def test_setup_hotkeys_do_nothing_while_running(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        await pilot.press(key)
        await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_viewer_hotkeys_work_while_running(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        await pilot.press("t")
        await until(lambda: isinstance(app.screen, FileViewer))

        await pilot.press("escape")
        await until(lambda: not isinstance(app.screen, ModalScreen))
        await pilot.press("ctrl+c")
        await _finished(app)


async def test_hotkeys_do_not_fire_inside_a_dialog(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.press("f")
        await until(lambda: isinstance(app.screen, FolderDialog))
        dialog = app.screen

        await pilot.press("g", "tab")
        await pilot.pause()

        assert app.screen is dialog


NO_GATES_TASKS = TASKS.replace("  gates: []\n", "")


@pytest.mark.usefixtures("fake_claude")
async def test_the_whole_setup_runs_from_the_keyboard_alone(git_repo: Path) -> None:
    write_file(git_repo / "run" / "tasks.yaml", NO_GATES_TASKS)
    settings = RunSettings(logging=False)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        # The folder dialog starts on the working directory; add the folder to it.
        await until(lambda: isinstance(app.screen, FolderDialog))
        await pilot.press("end", *"/run", "enter")

        # Down to "skip", Enter picks it, Tab goes through the file field to OK.
        await until(lambda: isinstance(app.screen, PermissionsDialog))
        await pilot.press("down", "enter", "tab", "tab", "enter")

        # The command field has focus; Enter adds, then Tab to Done.
        await until(lambda: isinstance(app.screen, GatesDialog))
        await pilot.press(*"true", "enter")
        await pilot.press("tab", "tab", "tab", "tab", "enter")

        await until(lambda: app.state is AppState.READY)
        assert settings.gates == [Gate(cmd="true")]

        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "done"


@pytest.mark.usefixtures("fake_claude")
async def test_the_main_screen_is_operable_by_focus_and_keys(git_repo: Path) -> None:
    settings = _settings(git_repo)
    settings.logging = True
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await _tab_to(app, pilot, "commit")
        await pilot.press("enter", "down", "enter")
        await until(lambda: app.settings.commit_mode is CommitMode.AFTER_ALL)

        await _tab_to(app, pilot, "logging")
        await pilot.press("enter", "down", "enter")
        await until(lambda: app.settings.logging is False)

        await _tab_to(app, pilot, "start")
        await pilot.press("enter")
        await until(lambda: app.state is not AppState.READY)


async def _tab_to(app: AlrApp, pilot: Pilot, widget_id: str) -> None:
    """Tab along the real focus chain until the widget has focus; fail if it never does."""
    for _ in range(20):
        if app.focused is not None and app.focused.id == widget_id:
            return

        await pilot.press("tab")

    pytest.fail(f"Tab never reached #{widget_id}")


FAILED_TASKS = TASKS.replace(
    "    name: First\n    prompt: p\n",
    "    name: First\n    prompt: p\n    state: failed\n    error: boom\n",
)


async def test_reset_failed_flag_resets_the_file_at_launch(git_repo: Path) -> None:
    settings = _settings(git_repo, FAILED_TASKS, reset_failed=True)
    app = AlrApp(settings)
    async with app.run_test():
        await until(lambda: app.state is AppState.READY)
        await until(
            lambda: saved_tasks(Path(settings.tasks_file)).tasks[0].state == "pending"
        )

        assert not isinstance(app.screen, ModalScreen)
        assert saved_tasks(Path(settings.tasks_file)).tasks[0].error == ""


DONE_AND_FAILED = FAILED_TASKS.replace(
    "    name: Second\n    prompt: p\n",
    "    name: Second\n    prompt: p\n    state: completed\n",
)


def _states(settings: RunSettings) -> list[str]:
    return [task.state for task in saved_tasks(Path(settings.tasks_file)).tasks]


async def test_a_file_with_a_failed_task_opens_with_start_blocked(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo, FAILED_TASKS))
    async with app.run_test() as pilot:
        await until(lambda: app.query_one(TasksPane).row_count == 2)
        await pilot.pause()

        assert app.query_one(TasksPane).get_row("1")[0] == "✖"
        assert app.state is AppState.SETUP
        assert app.query_one("#start").disabled
        assert "1 failed task: press r to reset" in str(
            app.query_one("#reason").render()
        )


async def test_the_reason_counts_several_failed_tasks(git_repo: Path) -> None:
    both = FAILED_TASKS.replace(
        "    name: Second\n    prompt: p\n",
        "    name: Second\n    prompt: p\n    state: failed\n",
    )
    app = AlrApp(_settings(git_repo, both))
    async with app.run_test() as pilot:
        await until(lambda: app.query_one(TasksPane).row_count == 2)
        await pilot.pause()

        assert "2 failed tasks: press r to reset" in str(
            app.query_one("#reason").render()
        )


async def test_r_lists_the_failed_tasks(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo, FAILED_TASKS))
    async with app.run_test() as pilot:
        await until(lambda: app.query_one(TasksPane).row_count == 2)

        await pilot.press("r")
        await until(lambda: isinstance(app.screen, ResetDialog))

        assert "1: First: boom" in str(app.screen.query_one("#message").render())


async def test_escape_in_the_reset_dialog_changes_nothing(git_repo: Path) -> None:
    settings = _settings(git_repo, FAILED_TASKS)
    before = Path(settings.tasks_file).read_bytes()
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.query_one(TasksPane).row_count == 2)
        await pilot.press("r")
        await until(lambda: isinstance(app.screen, ResetDialog))

        await pilot.press("escape")
        await until(lambda: not isinstance(app.screen, ModalScreen))

        assert Path(settings.tasks_file).read_bytes() == before
        assert app.query_one("#start").disabled


async def test_enter_in_the_reset_dialog_resets_and_enables_start(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, DONE_AND_FAILED, gates=[Gate(cmd="true")])
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.query_one(TasksPane).row_count == 2)
        await pilot.press("r")
        await until(lambda: isinstance(app.screen, ResetDialog))

        await pilot.press("enter")
        await until(lambda: app.state is AppState.READY)

        saved = saved_tasks(Path(settings.tasks_file))
        assert [task.state for task in saved.tasks] == ["pending", "completed"]
        assert [task.error for task in saved.tasks] == ["", ""]
        # The edit waits for Start; a reset saves the file as it was loaded.
        assert saved.gate_list() == []
        assert not app.query_one("#start").disabled
        assert app.query_one(TasksPane).get_row("1")[0] != "✖"


async def test_r_does_nothing_when_no_task_failed(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.press("r")
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)


async def test_r_does_nothing_when_no_file_is_loaded() -> None:
    app = AlrApp(RunSettings())
    async with app.run_test() as pilot:
        await until(lambda: isinstance(app.screen, FolderDialog))
        await pilot.press("escape")
        await until(lambda: not isinstance(app.screen, ModalScreen))

        await pilot.press("r")
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)


@pytest.mark.usefixtures("fake_claude")
async def test_r_does_nothing_while_running(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        await pilot.press("r")
        await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_r_does_nothing_once_finished(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)
        await pilot.press("escape")
        await until(lambda: not isinstance(app.screen, ModalScreen))

        await pilot.press("r")
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)


async def test_the_flag_resets_a_file_picked_in_the_folder_dialog(
    git_repo: Path,
) -> None:
    write_file(git_repo / "run" / "tasks.yaml", FAILED_TASKS)
    settings = RunSettings(
        permissions=Permissions.SKIP, logging=False, reset_failed=True
    )
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: isinstance(app.screen, FolderDialog))

        await pilot.press("end", *"/run", "enter")
        await until(lambda: app.state is AppState.READY)

        assert _states(settings) == ["pending", "pending"]
        assert app.query_one(TasksPane).get_row("1")[0] != "✖"


def _dropdowns_disabled(app: AlrApp) -> tuple[bool, bool]:
    return (
        app.query_one("#commit", Select).disabled,
        app.query_one("#logging", Select).disabled,
    )


@pytest.mark.usefixtures("fake_claude")
async def test_the_dropdowns_lock_from_start_on(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.in_work_tree is not None)
        await pilot.pause()

        assert _dropdowns_disabled(app) == (False, False)

        await pilot.press("s")
        await until(lambda: _dropdowns_disabled(app) == (True, True))
        await _finished(app)

        assert _dropdowns_disabled(app) == (True, True)


@pytest.mark.usefixtures("fake_claude")
async def test_the_commit_dropdown_stays_disabled_outside_a_repository(
    outside_settings: RunSettings,
) -> None:
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.in_work_tree is not None)
        await until(lambda: app.state is AppState.READY)

        assert _dropdowns_disabled(app) == (True, False)

        await pilot.press("s")
        await _finished(app)

        assert _dropdowns_disabled(app) == (True, True)


MENU = ["folder", "permissions", "gates", "reset", "tasks", "sandbox", "quit"]
# What each state greys out; gates, tasks and sandbox are never touched.
GREYED = {
    "ready": {"folder": False, "permissions": False, "reset": False, "quit": False},
    "running": {"folder": True, "permissions": True, "reset": True, "quit": True},
    "finished": {"folder": True, "permissions": True, "reset": True, "quit": False},
}


def _menu_disabled(app: AlrApp) -> dict[str, bool]:
    return {item: app.query_one(f"#menu-{item}", Button).disabled for item in MENU}


def _expected_menu(state: str) -> dict[str, bool]:
    return {item: GREYED[state].get(item, False) for item in MENU}


async def test_nothing_is_greyed_out_before_start() -> None:
    app = AlrApp(RunSettings())
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.state is AppState.SETUP
        assert _menu_disabled(app) == _expected_menu("ready")


@pytest.mark.usefixtures("fake_claude")
async def test_nothing_is_greyed_out_when_ready(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.pause()

        assert _menu_disabled(app) == _expected_menu("ready")


@pytest.mark.usefixtures("fake_claude")
async def test_a_run_greys_out_the_inert_buttons_and_quit_returns_after(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)
        assert _menu_disabled(app) == _expected_menu("running")

        await pilot.press("ctrl+c")
        await _finished(app)
        assert _menu_disabled(app) == _expected_menu("finished")


@pytest.mark.usefixtures("fake_claude")
async def test_tab_skips_the_greyed_out_buttons(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        reached: set[str] = set()
        for _ in range(20):
            await pilot.press("tab")
            reached.add(getattr(app.focused, "id", None) or "")

        assert {"menu-gates", "menu-tasks", "menu-sandbox"} <= reached
        assert not reached & {
            "menu-folder",
            "menu-permissions",
            "menu-reset",
            "menu-quit",
        }

        await pilot.press("ctrl+c")
        await _finished(app)


class _Clock:
    """A clock the test moves by hand, so the status line's timers need no waiting."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _status_line(app: AlrApp) -> str:
    return str(app.query_one("#progress", Static).render())


async def test_the_status_line_is_empty_before_start(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.pause()

        assert _status_line(app) == ""


@pytest.mark.usefixtures("fake_claude")
async def test_the_status_line_shows_the_task_and_session(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo), clock=_Clock())
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: "session" in _status_line(app))

        assert _status_line(app) == "task 1 of 2 · session · 0.0s · run 0.0s"

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_the_status_line_shows_the_running_gate(
    git_repo: Path, tmp_path: Path
) -> None:
    pid_file = tmp_path / "gate.pid"
    app = AlrApp(_settings(git_repo, _gated(f"echo $$ > {pid_file}; sleep 30")))
    async with app.run_test() as pilot:
        await pilot.press("s")
        pid = int(await wait_for_text(pid_file))
        await until(lambda: "gate 1 of 1: echo $$" in _status_line(app))

        assert _status_line(app).startswith("task 1 of 2 · gate 1 of 1: ")

        await pilot.press("ctrl+c")
        await _finished(app)
        await wait_until_gone(pid)


@pytest.mark.usefixtures("fake_claude")
async def test_the_status_line_ticks_with_the_clock(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    clock = _Clock()
    app = AlrApp(_settings(git_repo), clock=clock)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: "session" in _status_line(app))

        # No event comes while the session blocks, so only the timer can redraw.
        clock.now += 65
        await until(lambda: _status_line(app).endswith("1m 5s · run 1m 5s"))

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_the_status_line_is_cleared_when_the_summary_appears(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo, WORKING_TASKS))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.screen.query("#close"))

        assert _status_line(app) == ""


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize("size", [(80, 24), (110, 32)], ids=["80x24", "110x32"])
async def test_the_status_line_sits_whole_between_the_panes(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, size: tuple[int, int]
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo), clock=_Clock())
    async with app.run_test(size=size) as pilot:
        await pilot.press("s")
        await until(lambda: "session" in _status_line(app))
        await pilot.pause()

        line = app.query_one("#progress").region
        assert line.height == 1
        assert app.query_one("#tasks").region.bottom <= line.y
        assert line.bottom <= app.query_one("#activity").region.y
        assert len(_status_line(app)) <= line.width - 2
        assert app.query_one("#tasks").region.height > 3

        await pilot.press("ctrl+c")
        await _finished(app)
