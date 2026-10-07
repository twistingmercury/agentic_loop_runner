"""AlrApp: the four states, Start, Stop, and the summary, over a real engine and the fake claude."""

import asyncio
import os
import signal
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import git, write_file
from textual.pilot import Pilot
from textual.screen import ModalScreen
from textual.widgets import Input, Select, Static, TextArea

from alr.errors import RunLogError
from alr.stream.runlog import RunLog
from alr.tasks import Gate, parse_tasks
from alr.tui.app import AlrApp, AppState
from alr.tui.prepare import Prepared
from alr.tui import app as app_module
from alr.tui.dialogs import FolderDialog, GatesDialog, PermissionsDialog
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


async def _until(condition: Callable[[], object]) -> None:
    for _ in range(300):
        if condition():
            return

        await asyncio.sleep(0.02)

    pytest.fail("timed out waiting for the condition")


async def _finished(app: AlrApp) -> None:
    await _until(lambda: app.state is AppState.FINISHED)


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
        await _until(lambda: app.state is AppState.RUNNING)

        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.outcome == "stopped"


@pytest.mark.usefixtures("fake_claude")
async def test_sigterm_stops_a_run_like_ctrl_c(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _until(lambda: app.state is AppState.RUNNING)

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
        await _until(lambda: app.screen.query("#close"))

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
        await _until(lambda: isinstance(app.screen, PermissionsDialog))
        label = app.query_one("#permissions")
        assert "not chosen" in str(label.render())

        await pilot.click("#skip")
        await pilot.click("#ok")
        await _until(lambda: app.settings.permissions is Permissions.SKIP)

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
        await _until(lambda: isinstance(app.screen, FolderDialog))


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
        await _until(lambda: isinstance(app.screen, PermissionsDialog))

        await pilot.click("#skip")
        await pilot.click("#ok")
        await _until(lambda: isinstance(app.screen, GatesDialog))

        await pilot.click("#done")
        await _until(lambda: app.state is AppState.READY)


async def test_the_menu_reopens_a_dialog_before_start(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await _until(lambda: app.state is AppState.READY)

        await pilot.click("#menu-gates")
        await _until(lambda: isinstance(app.screen, GatesDialog))

        await pilot.click("#cancel")
        await _until(lambda: not isinstance(app.screen, ModalScreen))
        assert app.state is AppState.READY


async def test_the_menu_opens_the_task_file_read_only(git_repo: Path) -> None:
    settings = _settings(git_repo)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await _until(lambda: app.state is AppState.READY)

        await pilot.click("#menu-tasks")
        await _until(lambda: isinstance(app.screen, ModalScreen))

        viewer = app.screen.query_one("#text", TextArea)
        assert viewer.read_only
        assert viewer.text == Path(settings.tasks_file).read_text(encoding="utf-8")


async def test_the_viewer_says_when_there_is_no_sandbox_file(git_repo: Path) -> None:
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await _until(lambda: app.state is AppState.READY)

        await pilot.click("#menu-sandbox")
        await _until(lambda: isinstance(app.screen, ModalScreen))

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
        await _until(lambda: app.state is AppState.RUNNING)

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
    settings = _settings(git_repo, log_dir=str(git_repo / "visible"))
    settings.logging = True
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _until(lambda: app.screen.query("#close"))

        assert app.state is AppState.READY
        assert ".gitignore" in str(app.screen.query_one("#message").render())


@pytest.mark.usefixtures("fake_claude")
async def test_turning_logging_off_skips_the_log_folder(git_repo: Path) -> None:
    settings = _settings(git_repo, log_dir=str(git_repo / "visible"))
    settings.logging = True
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await pilot.click("#logging")
        await pilot.press("s")
        await _finished(app)

    assert not (git_repo / "visible").exists()


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
        await _until(lambda: app.in_work_tree is not None)

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
        await _until(lambda: app.in_work_tree is not None)
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
        await _until(entered.is_set)

        await pilot.press("ctrl+c")
        await _finished(app)

        assert app.outcome == "stopped"


async def test_a_second_stop_while_stopping_quits(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    _hold_prepare(monkeypatch, entered, refuse_first_cancel=True)
    app = AlrApp(_settings(git_repo))
    async with app.run_test() as pilot:
        await pilot.press("s")
        await _until(entered.is_set)

        await pilot.press("ctrl+c")
        await pilot.press("ctrl+c")
        await _until(lambda: not app.is_running)


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
        await _until(lambda: app.screen.query("#close"))

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
    async with app.run_test():
        await _until(lambda: isinstance(app.screen, GatesDialog))


async def _pick_folder(app: AlrApp, pilot: Pilot, folder: Path) -> None:
    await pilot.click("#menu-folder")
    await _until(lambda: isinstance(app.screen, FolderDialog))
    app.screen.query_one("#path", Input).value = str(folder)
    await pilot.click("#ok")
    await _until(lambda: not isinstance(app.screen, ModalScreen))


async def test_picking_another_folder_drops_the_gates_edited_for_the_first(
    git_repo: Path,
) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true")])
    other = git_repo / "other"
    other.mkdir()
    write_file(other / "tasks.yaml", TASKS)
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await _until(lambda: app.state is AppState.READY)

        await _pick_folder(app, pilot, other)

        assert settings.tasks_file == str(other / "tasks.yaml")
        assert settings.gates is None


async def test_picking_the_same_folder_keeps_the_gates(git_repo: Path) -> None:
    settings = _settings(git_repo, gates=[Gate(cmd="true")])
    app = AlrApp(settings)
    async with app.run_test() as pilot:
        await _until(lambda: app.state is AppState.READY)

        await _pick_folder(app, pilot, git_repo / "run")

        assert settings.gates == [Gate(cmd="true")]
