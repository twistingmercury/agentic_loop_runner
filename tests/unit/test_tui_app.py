"""AlrApp: the four states, Start, Stop, and the summary, over a real engine and the fake claude."""

import asyncio
import os
import signal
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from helpers import (
    HandClock,
    fake_claude_records,
    git,
    process_is_running,
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

from alr.engine import proc
from alr.engine.stop import Stop
from alr.errors import RefusedError, RunLogError
from alr.record.runlog import RunLog
from alr.engine.commit import CommitMode
from alr.tasks import Action, parse_tasks
from alr.tui.app import AlrApp, AppState
from alr.record.events import Activity, CommitStarted, TaskStarted
from alr.tasks import Task
from alr.tui.adapter import RunEvent
from alr.tui.panes import ActivityPane, TasksPane
from alr.tui.prepare import Prepared
from alr.tui import app as app_module
from alr.tui.dialogs import (
    FileViewer,
    FolderDialog,
    ActionsDialog,
    PermissionsDialog,
    ResetDialog,
)
from alr.tui.settings import Permissions, RunSettings

TASKS = """shared:
  prompt: do it
  after_task: []
tasks:
  - id: 1
    name: First
    prompt: p
  - id: 2
    name: Second
    prompt: p
"""


# The fake claude writes nothing, so an action makes each task leave a file to commit.
WORKING_TASKS = TASKS.replace(
    "after_task: []",
    "after_task:\n"
    "    - action: n=1; while [ -e work-$n.txt ]; do n=$((n+1)); done; echo w > work-$n.txt",
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
        tasks_file=tasks_file,
        permissions=Permissions.SKIP,
        logging=False,
        commit_mode=CommitMode.TASK,
    )


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second Stop waits out a stop under way, which for a stuck one takes the grace period."""
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.2)


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
    app = AlrApp(_settings(git_repo, WORKING_TASKS, commit_mode=CommitMode.TASK))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "done"
        assert git(git_repo, "log", "--format=%s").split("\n")[:2] == [
            "Second",
            "First",
        ]
        await until(lambda: app.screen.query("#close"))


@pytest.mark.usefixtures("fake_claude")
async def test_a_failed_run_commit_leaves_no_check_mark_for_unfinished_tasks(
    git_repo: Path,
) -> None:
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    settings = _settings(git_repo, WORKING_TASKS, commit_mode=CommitMode.RUN)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        rows = [app.query_one(TasksPane).get_row(key)[0] for key in ("1", "2")]

    assert rows == ["·", "✖"]
    assert _states(settings) == ["pending", "failed"]


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
    settings = _settings(git_repo, commit_mode=CommitMode.TASK)
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
    ("actions_block", "want"),
    [
        ("", True),
        ("  after_task: []\n", False),
        ("  after_task:\n    - action: 'true'\n", False),
    ],
    ids=["absent key", "decided none", "has actions"],
)
async def test_needs_actions_only_for_an_absent_key(
    git_repo: Path, actions_block: str, want: bool
) -> None:
    text = f"shared:\n  prompt: p\n{actions_block}tasks:\n  - id: 1\n    name: A\n    prompt: p\n"
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", text)
    app = AlrApp(RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP))
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.needs_actions() is want


@pytest.mark.usefixtures("fake_claude")
async def test_start_saves_the_edited_actions(git_repo: Path) -> None:
    settings = _settings(git_repo, after_task=[Action(command="true")])
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

    saved = parse_tasks(Path(settings.tasks_file).read_text(encoding="utf-8"))
    assert saved.after_task_list() == [Action(command="true")]


async def test_launch_opens_the_folder_dialog_when_no_folder_is_known() -> None:
    app = AlrApp(RunSettings())
    async with app.run_test():
        await until(lambda: isinstance(app.screen, FolderDialog))


async def test_launch_skips_every_step_the_inputs_answer(git_repo: Path) -> None:
    text = "shared:\n  prompt: p\n  after_task: []\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", text)
    app = AlrApp(RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP))
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.state is AppState.READY
        assert not isinstance(app.screen, ModalScreen)


async def test_launch_asks_for_permissions_then_actions_in_order(
    git_repo: Path,
) -> None:
    text = "shared:\n  prompt: p\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", text)
    app = AlrApp(RunSettings(tasks_file=tasks_file))
    async with app.run_test() as pilot:
        await until(lambda: isinstance(app.screen, PermissionsDialog))

        await pilot.click("#skip")
        await pilot.click("#ok")
        await until(lambda: isinstance(app.screen, ActionsDialog))

        await pilot.click("#done")
        await until(lambda: app.state is AppState.READY)


async def test_the_menu_reopens_a_dialog_before_start(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.click("#menu-actions")
        await until(lambda: isinstance(app.screen, ActionsDialog))

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
@pytest.mark.parametrize("item", ["folder", "permissions", "actions"])
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


async def test_commit_is_a_dropdown_with_none_per_task_and_after_all(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        assert app.query_one("#commit", Select).value is CommitMode.NONE
        await _tab_to(app, pilot, "commit")
        await pilot.press("enter")
        options = app.query_one("#commit SelectOverlay", OptionList)

        assert [str(options.get_option_at_index(i).prompt) for i in range(3)] == [
            "none",
            "per task",
            "after all",
        ]
        assert options.option_count == 3


async def test_the_commit_dropdown_starts_from_the_flag(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo, commit_mode=CommitMode.TASK))
    async with app.run_test():
        await until(lambda: app.state is AppState.READY)

        assert app.query_one("#commit", Select).value is CommitMode.TASK


@pytest.mark.usefixtures("fake_claude")
async def test_a_none_run_in_a_dirty_repository_starts_and_commits_nothing(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo, WORKING_TASKS))
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "done"
        assert git(git_repo, "log", "--format=%s").split() == ["init"]


async def test_switching_to_per_task_in_a_dirty_repository_is_refused(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo))
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await _tab_to(app, pilot, "commit")
        await pilot.press("enter", "down", "enter")
        await until(lambda: app.settings.commit_mode is CommitMode.TASK)
        await pilot.press("s")
        await until(lambda: app.screen.query("#close"))

        assert app.state is AppState.READY
        assert "clean work tree" in str(app.screen.query_one("#message").render())


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
    outside_settings.commit_mode = CommitMode.TASK
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.in_work_tree is not None)

        assert app.query_one("#commit-note", Static).display
        assert "no git repository: nothing will be committed" in str(
            app.query_one("#commit-note", Static).render()
        )

        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "done"
        assert not (outside / ".git").exists()


@pytest.mark.usefixtures("fake_claude")
async def test_the_note_shows_for_after_all_outside_a_repository(
    outside_settings: RunSettings,
) -> None:
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await _tab_to(app, pilot, "commit")
        await pilot.press("enter", "down", "down", "enter")
        await until(lambda: app.settings.commit_mode is CommitMode.RUN)

        assert _note_shown(app)


@pytest.mark.usefixtures("fake_claude")
async def test_the_note_shows_when_the_settings_start_on_after_all(
    outside_settings: RunSettings,
) -> None:
    outside_settings.commit_mode = CommitMode.RUN
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.in_work_tree is not None)
        await pilot.pause()

        assert _note_shown(app)


@pytest.mark.usefixtures("fake_claude")
async def test_the_note_shows_for_a_task_run_started_outside_a_repository(
    outside_settings: RunSettings,
) -> None:
    # Waits on the note itself, not on the lookup, so it holds whichever code path asks.
    outside_settings.commit_mode = CommitMode.TASK
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await pilot.pause()
        await until(lambda: _note_shown(app))

        assert _note_shown(app)


async def test_a_run_inside_a_repository_keeps_the_commit_choice(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo, commit_mode=CommitMode.TASK))
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

    async def held(settings: RunSettings, stop: Stop) -> Prepared:
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

    async def slow_to_stop(settings: RunSettings, stop: Stop) -> Prepared:
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


@pytest.mark.usefixtures("fake_claude")
async def test_a_second_stop_waits_for_a_commit_that_traps_sigterm(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_GENTLE_GRACE", 0.5)
    pid_file = tmp_path / "hook.pid"
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    # The pid comes after the trap, so a stop that sees it meets a hook that ignores TERM.
    hook.write_text(
        f"#!/bin/sh\ntrap '' TERM\necho $$ > {pid_file}\nsleep 30\n", encoding="utf-8"
    )
    hook.chmod(0o755)
    app = AlrApp(_settings(git_repo, WORKING_TASKS, commit_mode=CommitMode.TASK))
    async with app.run_test() as pilot:
        await pilot.press("s")
        hook_pid = int(await wait_for_text(pid_file))
        await pilot.press("ctrl+c")
        await until(lambda: app._stopping)

        second_stop = time.monotonic()
        await pilot.press("ctrl+c")
        await until(lambda: not app.is_running)

        # Quitting must not leave the hook behind; it ignores TERM, so only the kill
        # after the grace period removes it, and the app waited for that.
        assert not process_is_running(hook_pid)
        assert time.monotonic() - second_stop < 3

    assert app.exit_code == 130


async def test_a_second_stop_while_stopping_leaves_exit_code_130(
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

    assert app.exit_code == 130


async def test_sigterm_during_prepare_finishes_stopped_with_exit_code_143(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _hold_prepare(monkeypatch, entered, refuse_first_cancel=False)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)

        os.kill(os.getpid(), signal.SIGTERM)
        await _finished(app)

        assert app.outcome == "stopped"
        assert app.exit_code == 143


async def test_a_ctrl_c_after_sigterm_during_prepare_keeps_exit_code_143(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _hold_prepare(monkeypatch, entered, refuse_first_cancel=True)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)

        os.kill(os.getpid(), signal.SIGTERM)
        await until(lambda: app._stopping)
        await pilot.press("ctrl+c")
        await until(lambda: not app.is_running)

    assert app.exit_code == 143


async def test_a_second_sigterm_while_stopping_quits_with_exit_code_143(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _hold_prepare(monkeypatch, entered, refuse_first_cancel=True)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)

        os.kill(os.getpid(), signal.SIGTERM)
        await until(lambda: app._stopping)
        os.kill(os.getpid(), signal.SIGTERM)
        await until(lambda: not app.is_running)

    assert app.exit_code == 143


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


def _refuse_on_the_first_cancel_then_hold(
    monkeypatch: pytest.MonkeyPatch, entered: asyncio.Event
) -> None:
    """The first prepare fails when Stop cancels it; later ones block and cancel cleanly."""
    calls = 0

    async def prepare_refusing_once(settings: RunSettings, stop: Stop) -> Prepared:
        nonlocal calls
        calls += 1
        first = calls == 1
        entered.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            if first:
                raise OSError("refused while stopping") from None

            raise

        raise AssertionError("the sleep ended")

    monkeypatch.setattr(app_module, "prepare", prepare_refusing_once)


async def test_a_refused_start_during_a_stop_clears_the_stop_state(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _refuse_on_the_first_cancel_then_hold(monkeypatch, entered)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)

        await pilot.press("ctrl+c")
        await until(lambda: app.state is AppState.READY)

        assert not app._stopping


async def test_a_refused_start_during_a_sigterm_stop_forgets_the_signal(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _refuse_on_the_first_cancel_then_hold(monkeypatch, entered)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)

        os.kill(os.getpid(), signal.SIGTERM)
        await until(lambda: app.state is AppState.READY)

        # Left at SIGTERM, a later Ctrl-C stop would end 143 instead of 130.
        assert app._stop.signum == signal.SIGINT


async def test_the_first_stop_after_a_refused_start_is_graceful(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _refuse_on_the_first_cancel_then_hold(monkeypatch, entered)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)
        await pilot.press("ctrl+c")
        await until(lambda: app.state is AppState.READY)
        await pilot.click("#close")
        entered.clear()

        await pilot.press("s")
        await until(entered.is_set)
        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.is_running
        assert app.outcome == "stopped"
        assert app.exit_code == 130


async def test_a_failure_after_a_refused_stop_is_not_called_stopped(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    real_prepare = app_module.prepare
    _refuse_on_the_first_cancel_then_hold(monkeypatch, entered)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(entered.is_set)
        await pilot.press("ctrl+c")
        await until(lambda: app.state is AppState.READY)
        await pilot.click("#close")
        monkeypatch.setattr(app_module, "run", _crashing_run)
        monkeypatch.setattr(app_module, "prepare", real_prepare)

        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "failed"
        assert app.exit_code == 1


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
    async def failing(settings: RunSettings, stop: Stop) -> Prepared:
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


async def test_launch_opens_the_actions_dialog_when_flags_answer_everything_else(
    git_repo: Path,
) -> None:
    text = "shared:\n  prompt: p\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"
    tasks_file = write_file(git_repo / "run" / "tasks.yaml", text)
    app = AlrApp(RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP))
    async with app.run_test() as pilot:
        await until(lambda: isinstance(app.screen, ActionsDialog))
        # Let the table's queued first highlight land; leaving with it queued
        # makes it arrive in a closing app on a busy worker.
        await pilot.pause()


async def _pick_folder(app: AlrApp, pilot: Pilot, folder: Path) -> None:
    await pilot.click("#menu-folder")
    await until(lambda: isinstance(app.screen, FolderDialog))
    app.screen.query_one("#path", Input).value = str(folder)
    await pilot.click("#ok")
    await until(lambda: not isinstance(app.screen, ModalScreen))


async def test_picking_another_folder_drops_the_actions_edited_for_the_first(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, after_task=[Action(command="true")])
    other = git_repo / "other"
    other.mkdir()
    write_file(other / "tasks.yaml", TASKS)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await _pick_folder(app, pilot, other)

        assert settings.tasks_file == str(other / "tasks.yaml")
        assert settings.after_task is None


async def test_picking_the_same_folder_keeps_the_actions(git_repo: Path) -> None:
    settings = _settings(git_repo, after_task=[Action(command="true")])
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await _pick_folder(app, pilot, git_repo / "run")

        assert settings.after_task == [Action(command="true")]


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
    settings = _settings(repo, tasks, commit_mode=CommitMode.TASK)
    settings.logging = True
    return settings


def _ledger(repo: Path) -> str:
    (path,) = (repo / "run" / "logs").glob("*/run.jsonl")
    return path.read_text(encoding="utf-8")


def _with_actions(action: str) -> str:
    return TASKS.replace("after_task: []", f"after_task:\n    - action: '{action}'")


@pytest.mark.usefixtures("fake_claude")
async def test_stop_during_a_action_kills_the_action_and_ends_stopped(
    git_repo: Path, tmp_path: Path, closed_logs: list[RunLog]
) -> None:
    pid_file = tmp_path / "action.pid"
    settings = _logged_settings(
        git_repo, _with_actions(f"echo $$ > {pid_file}; sleep 30")
    )
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


@pytest.mark.usefixtures("fake_claude")
async def test_a_clean_run_leaves_exit_code_0(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.exit_code == 0


@pytest.mark.usefixtures("fake_claude")
async def test_a_failed_task_leaves_exit_code_1(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", '{"state":"failed","error":"nope"}')
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.exit_code == 1


@pytest.mark.usefixtures("fake_claude")
async def test_a_failed_commit_leaves_exit_code_4(git_repo: Path) -> None:
    hook = write_nested_file(
        git_repo / ".git" / "hooks" / "pre-commit", "#!/bin/sh\necho no\nexit 1\n"
    )
    hook.chmod(0o700)
    app = AlrApp(_settings(git_repo, WORKING_TASKS, commit_mode=CommitMode.TASK))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "failed"
        assert app.exit_code == 4


@pytest.mark.usefixtures("fake_claude")
async def test_the_users_stop_leaves_exit_code_130(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.exit_code == 130


@pytest.mark.usefixtures("fake_claude")
async def test_sigterm_during_a_run_leaves_exit_code_143_in_the_app_and_the_ledger(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_logged_settings(git_repo, TASKS))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.state is AppState.RUNNING)

        os.kill(os.getpid(), signal.SIGTERM)
        await _finished(app)

        assert app.exit_code == 143
        assert '"exit":143' in _ledger(git_repo)


async def test_sigterm_with_no_run_leaves_exit_code_143(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.pause()

        os.kill(os.getpid(), signal.SIGTERM)
        await until(lambda: not app.is_running)

    assert app.exit_code == 143


def _press_ctrl_c(pilot: Pilot) -> Awaitable[None]:
    return pilot.press("ctrl+c")


def _send_sigterm(pilot: Pilot) -> Awaitable[None]:
    os.kill(os.getpid(), signal.SIGTERM)
    return pilot.pause()


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("first_stop", "code"),
    [(_press_ctrl_c, 130), (_send_sigterm, 143)],
    ids=["ctrl-c", "sigterm"],
)
async def test_a_second_stop_during_a_stuck_commit_exits_with_the_stops_code(
    git_repo: Path,
    tmp_path: Path,
    first_stop: Callable[[Pilot], Awaitable[None]],
    code: int,
) -> None:
    ready = tmp_path / "hook.ready"
    # Ignoring TERM keeps git alive after the first stop, so the engine is still stopping.
    hook = write_nested_file(
        git_repo / ".git" / "hooks" / "pre-commit",
        f"#!/bin/sh\ntrap '' TERM\necho ready > {ready}\nsleep 1\n",
    )
    hook.chmod(0o700)
    app = AlrApp(_logged_settings(git_repo, WORKING_TASKS))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await wait_for_text(ready)

        await first_stop(pilot)
        await until(lambda: app._stopping)
        await pilot.press("ctrl+c")
        await until(lambda: not app.is_running)
        # Let the hook end, so no subprocess outlives the event loop.
        await asyncio.sleep(1.2)

    assert app.exit_code == code


async def _no_run_done(*args: object) -> None:
    """A run that returns without ever posting a RunDone."""


async def _crashing_run(*args: object) -> None:
    raise RuntimeError("boom")


@pytest.mark.parametrize(
    "fake_run", [_no_run_done, _crashing_run], ids=["no result", "crash"]
)
async def test_a_run_that_dies_without_a_result_leaves_exit_code_1(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_run: Callable[..., Awaitable[None]],
) -> None:
    monkeypatch.setattr(app_module, "run", fake_run)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _finished(app)

        assert app.outcome == "failed"
        assert app.exit_code == 1


async def test_stop_during_prepare_leaves_exit_code_130(
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

        assert app.exit_code == 130


async def test_quitting_without_a_run_leaves_exit_code_0(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("q")

    assert app.exit_code == 0


def test_run_app_returns_the_apps_exit_code(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    def finish_with_4(self: AlrApp, **kwargs: object) -> None:
        self.exit_code = 4

    monkeypatch.setattr(AlrApp, "run", finish_with_4)

    assert app_module.run_app(_settings(git_repo)) == 4


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
    [("f", FolderDialog), ("p", PermissionsDialog), ("a", ActionsDialog)],
    ids=["folder", "permissions", "actions"],
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
@pytest.mark.parametrize("key", ["f", "p", "a"])
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

        await pilot.press("a", "tab")
        await pilot.pause()

        assert app.screen is dialog


NO_ACTIONS_TASKS = TASKS.replace("  after_task: []\n", "")


@pytest.mark.usefixtures("fake_claude")
async def test_the_whole_setup_runs_from_the_keyboard_alone(git_repo: Path) -> None:
    write_file(git_repo / "run" / "tasks.yaml", NO_ACTIONS_TASKS)
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
        await until(lambda: isinstance(app.screen, ActionsDialog))
        await pilot.press(*"true", "enter")
        await pilot.press("tab", "tab", "tab", "tab", "enter")

        await until(lambda: app.state is AppState.READY)
        assert settings.after_task == [Action(command="true")]

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
        await pilot.press("enter", "down", "down", "enter")
        await until(lambda: app.settings.commit_mode is CommitMode.RUN)

        await _tab_to(app, pilot, "logging")
        await pilot.press("enter", "down", "enter")
        await until(lambda: app.settings.logging is False)

        await _tab_to(app, pilot, "start")
        await pilot.press("enter")
        await until(lambda: app.state is not AppState.READY)


async def test_a_run_event_that_arrives_after_the_app_stopped_is_ignored(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test():
        await until(lambda: app.state is AppState.READY)

    # Textual still delivers the messages queued before the exit, after it has
    # taken the screens down; a run that was mid-flight posts them up to the end.
    first = Task(id=1, name="First", prompt="p")
    app.on_run_event(RunEvent(Activity(first, "late")))


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


async def test_reset_failed_flag_leaves_the_file_alone_at_launch(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, FAILED_TASKS, reset_failed=True)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.pause()

        assert not isinstance(app.screen, ModalScreen)
        assert not app.query_one("#start").disabled
        assert app.query_one(TasksPane).get_row("1")[0] != "✖"

    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS


async def test_a_refused_start_with_reset_failed_leaves_the_file_and_logs_alone(
    git_repo: Path,
) -> None:
    settings = _settings(
        git_repo, FAILED_TASKS, reset_failed=True, commit_mode=CommitMode.TASK
    )
    settings.logging = True
    (git_repo / "stray.txt").write_text("x", encoding="utf-8")
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.press("s")
        await until(lambda: app.screen.query("#close"))

        assert app.state is AppState.READY
        assert "clean work tree" in str(app.screen.query_one("#message").render())

    assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS
    assert not (git_repo / "run" / "logs").exists()


@pytest.mark.usefixtures("fake_claude")
async def test_start_with_reset_failed_runs_the_reset_task_and_completes_it(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, FAILED_TASKS, reset_failed=True)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        await pilot.press("s")
        await _finished(app)

    assert _states(settings) == ["completed", "completed"]
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
    settings = _settings(git_repo, DONE_AND_FAILED, after_task=[Action(command="true")])
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
        assert saved.after_task_list() == []
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


async def test_the_flag_resets_a_file_picked_in_the_folder_dialog_in_memory_only(
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

        assert Path(settings.tasks_file).read_text(encoding="utf-8") == FAILED_TASKS
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
        await until(lambda: app.state is AppState.READY)
        await pilot.pause()

        assert _dropdowns_disabled(app) == (False, False)

        await pilot.press("s")
        await until(lambda: _dropdowns_disabled(app) == (True, True))
        await _finished(app)

        assert _dropdowns_disabled(app) == (True, True)


async def test_the_dropdowns_are_disabled_while_running_and_enabled_after_a_failed_start(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held_then_failing(settings: RunSettings, stop: Stop) -> Prepared:
        entered.set()
        await release.wait()
        raise OSError("gone")

    monkeypatch.setattr(app_module, "prepare", held_then_failing)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.press("s")
        await until(entered.is_set)

        assert app.state is AppState.RUNNING
        assert _dropdowns_disabled(app) == (True, True)

        release.set()
        await until(lambda: app.state is AppState.READY)

        assert _dropdowns_disabled(app) == (False, False)


def _note_shown(app: AlrApp) -> bool:
    return app.query_one("#commit-note", Static).display


@pytest.mark.usefixtures("fake_claude")
async def test_the_commit_note_follows_the_dropdown_outside_a_repository(
    outside_settings: RunSettings,
) -> None:
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        assert not _note_shown(app)

        await _tab_to(app, pilot, "commit")
        await pilot.press("enter", "down", "enter")
        await until(lambda: _note_shown(app))

        await pilot.press("enter", "up", "enter")
        await until(lambda: not _note_shown(app))

        await pilot.press("s")
        await _finished(app)

        assert _dropdowns_disabled(app) == (True, True)


class _Lookups:
    """A stand-in for the work-tree lookup that counts its calls and can be held open."""

    def __init__(self, answer: str | None) -> None:
        self.calls = 0
        self.answer = answer
        self.release = asyncio.Event()

    async def hold(self) -> str | None:
        self.calls += 1
        await self.release.wait()
        return self.answer

    async def __call__(self) -> str | None:
        self.calls += 1
        return self.answer


def _patch_lookup(monkeypatch: pytest.MonkeyPatch, answer: str | None) -> _Lookups:
    lookups = _Lookups(answer)
    monkeypatch.setattr(app_module, "work_tree_root", lookups)
    return lookups


async def _pick_commit(app: AlrApp, pilot: Pilot, *moves: str) -> None:
    """Open the Commit dropdown, move from the current option by the given keys, then choose."""
    await _tab_to(app, pilot, "commit")
    await pilot.press("enter", *moves, "enter")


async def test_none_never_looks_at_the_repository_inside_one(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = _patch_lookup(monkeypatch, str(git_repo))
    app = AlrApp(_settings(git_repo, commit_mode=CommitMode.NONE))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.pause()

        assert lookups.calls == 0
        assert app.in_work_tree is None
        assert not _note_shown(app)


async def test_none_never_looks_at_the_repository_outside_one(
    outside_settings: RunSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = _patch_lookup(monkeypatch, None)
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.pause()

        assert lookups.calls == 0
        assert not _note_shown(app)


async def test_the_launch_lookup_runs_for_per_task_and_shows_the_note_outside(
    outside_settings: RunSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = _patch_lookup(monkeypatch, None)
    outside_settings.commit_mode = CommitMode.TASK
    app = AlrApp(outside_settings)
    async with app.run_test():
        await until(lambda: app.in_work_tree is not None)

        assert lookups.calls == 1
        assert _note_shown(app)


async def test_picking_per_task_outside_a_repository_looks_it_up_and_shows_the_note(
    outside_settings: RunSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = _patch_lookup(monkeypatch, None)
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)

        assert lookups.calls == 0

        await _pick_commit(app, pilot, "down")
        await until(lambda: _note_shown(app))

        assert lookups.calls == 1

        await _pick_commit(app, pilot, "up")
        await until(lambda: app.settings.commit_mode is CommitMode.NONE)

        assert not _note_shown(app)


async def test_a_lookup_finishing_after_the_switch_back_to_none_shows_no_note(
    outside_settings: RunSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = _patch_lookup(monkeypatch, None)
    monkeypatch.setattr(app_module, "work_tree_root", lookups.hold)
    app = AlrApp(outside_settings)
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await _pick_commit(app, pilot, "down")
        await until(lambda: lookups.calls == 1)
        await _pick_commit(app, pilot, "up")
        await until(lambda: app.settings.commit_mode is CommitMode.NONE)

        lookups.release.set()
        await until(lambda: app.in_work_tree is not None)
        await pilot.pause()

        assert not _note_shown(app)


def test_a_refusal_shows_its_message_without_the_reason_prefix() -> None:
    error = RefusedError("dirty-tree", "--commit needs a clean work tree")

    assert app_module._error_text(error) == "--commit needs a clean work tree"


def test_another_domain_error_shows_its_own_text() -> None:
    assert app_module._error_text(RunLogError("disk full")) == "disk full"


async def test_a_failing_lookup_does_not_crash_the_view(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken() -> str | None:
        raise RefusedError("git-error", "--commit: git rev-parse: boom")

    monkeypatch.setattr(app_module, "work_tree_root", broken)
    app = AlrApp(_settings(git_repo, commit_mode=CommitMode.TASK))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await pilot.pause()

        assert app.in_work_tree is None
        assert not _note_shown(app)


async def test_the_commit_note_stays_hidden_inside_a_repository_with_per_task(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo, commit_mode=CommitMode.TASK))
    async with app.run_test() as pilot:
        await until(lambda: app.in_work_tree is not None)
        await pilot.pause()

        assert app.in_work_tree is True
        assert not _note_shown(app)


async def test_the_commit_note_stays_hidden_inside_a_repository_after_picking_after_all(
    git_repo: Path,
) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await until(lambda: app.state is AppState.READY)
        await _pick_commit(app, pilot, "down", "down")
        await until(lambda: app.in_work_tree is not None)
        await pilot.pause()

        assert app.settings.commit_mode is CommitMode.RUN
        assert not _note_shown(app)


MENU = ["folder", "permissions", "actions", "reset", "tasks", "sandbox", "quit"]
# What each state greys out; actions, tasks and sandbox are never touched.
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

        assert {"menu-actions", "menu-tasks", "menu-sandbox"} <= reached
        assert not reached & {
            "menu-folder",
            "menu-permissions",
            "menu-reset",
            "menu-quit",
        }

        await pilot.press("ctrl+c")
        await _finished(app)


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
    app = AlrApp(_settings(git_repo), clock=HandClock())
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: "session" in _status_line(app))

        assert _status_line(app) == "task 1 of 2 · session · 0.0s · run 0.0s"

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_the_status_line_shows_the_running_action(
    git_repo: Path, tmp_path: Path
) -> None:
    pid_file = tmp_path / "action.pid"
    app = AlrApp(_settings(git_repo, _with_actions(f"echo $$ > {pid_file}; sleep 30")))
    async with app.run_test() as pilot:
        await pilot.press("s")
        pid = int(await wait_for_text(pid_file))
        await until(lambda: "action 1 of 1: echo $$" in _status_line(app))

        assert _status_line(app).startswith("task 1 of 2 · action 1 of 1: ")

        await pilot.press("ctrl+c")
        await _finished(app)
        await wait_until_gone(pid)


@pytest.mark.usefixtures("fake_claude")
async def test_the_status_line_ticks_with_the_clock(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    clock = HandClock()
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
    app = AlrApp(_settings(git_repo), clock=HandClock())
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


def _time_cell(app: AlrApp, row: int) -> str:
    return str(app.query_one(TasksPane).get_cell_at((row, 3)))


@pytest.mark.usefixtures("fake_claude")
async def test_the_running_task_time_ticks_with_the_clock(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    clock = HandClock()
    app = AlrApp(_settings(git_repo), clock=clock)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: _time_cell(app, 0) == "0.0s")

        # No event comes while the session blocks, so only the timer can redraw.
        clock.now += 65
        await until(lambda: _time_cell(app, 0) == "1m 5s")
        assert _time_cell(app, 1) == ""

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_the_status_line_counts_only_the_pending_tasks(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    three = (TASKS + "  - id: 3\n    name: Third\n    prompt: p\n").replace(
        "prompt: p\n", "prompt: p\n    state: completed\n", 1
    )
    app = AlrApp(_settings(git_repo, three), clock=HandClock())
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: "session" in _status_line(app))

        assert _status_line(app) == "task 1 of 2 · session · 0.0s · run 0.0s"

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_the_status_line_updates_when_an_event_arrives(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo), clock=HandClock())
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: "session" in _status_line(app))

        # The clock does not move and no second passes: only the event can redraw.
        app.post_message(RunEvent(CommitStarted(Task(1, "First", "p"))))
        await pilot.pause()

        assert _status_line(app) == "task 1 of 2 · commit · 0.0s · run 0.0s"

        await pilot.press("ctrl+c")
        await _finished(app)


@pytest.mark.usefixtures("fake_claude")
async def test_the_ticker_stops_when_the_run_ends(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo, WORKING_TASKS), clock=HandClock())
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app.screen.query("#close"))

        assert app._ticker is None


@pytest.mark.usefixtures("fake_claude")
async def test_the_ticker_stops_when_the_app_unmounts(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo), clock=HandClock())
    async with app.run_test() as pilot:
        await pilot.press("s")
        await until(lambda: app._ticker is not None)
        ticker = app._ticker
        app.exit()
        await until(lambda: not app.is_running)

    assert (app._ticker, ticker is not None) == (None, True)


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize("size", [(80, 24), (110, 32)], ids=["80x24", "110x32"])
async def test_a_long_action_is_cut_so_the_timers_stay_on_the_line(
    git_repo: Path, tmp_path: Path, size: tuple[int, int]
) -> None:
    pid_file = tmp_path / "action.pid"
    padding = "x" * 120
    clock = HandClock()
    app = AlrApp(
        _settings(
            git_repo, _with_actions(f"echo $$ > {pid_file}; sleep 30 # {padding}")
        ),
        clock=clock,
    )
    async with app.run_test(size=size) as pilot:
        await pilot.press("s")
        pid = int(await wait_for_text(pid_file))
        await until(lambda: "action 1 of 1: echo $$" in _status_line(app))
        clock.now += 65
        await until(lambda: _status_line(app).endswith("1m 5s · run 1m 5s"))

        line = _status_line(app)
        assert len(line) <= app.query_one("#progress").content_size.width
        assert "… · 1m 5s · run 1m 5s" in line

        await pilot.press("ctrl+c")
        await _finished(app)
        await wait_until_gone(pid)
