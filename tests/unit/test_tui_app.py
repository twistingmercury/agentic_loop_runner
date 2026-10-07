"""AlrApp: the four states, Start, Stop, and the summary, over a real engine and the fake claude."""

import asyncio
import os
import signal
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import git, write_file
from textual.widgets import Button

from alr.tui.app import AlrApp, AppState
from alr.tui.dialogs import PermissionsDialog
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


async def test_permissions_button_shows_the_choice_and_applies_the_dialog() -> None:
    app = AlrApp(RunSettings())
    async with app.run_test() as pilot:
        await pilot.pause()
        button = app.query_one("#permissions", Button)
        assert "not chosen" in str(button.label)

        await pilot.click("#permissions")
        await _until(lambda: isinstance(app.screen, PermissionsDialog))
        await pilot.click("#skip")
        await pilot.click("#ok")
        await _until(lambda: app.settings.permissions is Permissions.SKIP)

        assert "skip" in str(button.label)
