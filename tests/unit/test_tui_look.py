"""How the view looks: dialogs float over the main window, and buttons are flat."""

from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import write_file
from textual.pilot import Pilot
from textual.screen import Screen
from textual.widgets import Button

from alr.tui.app import AlrApp
from alr.tui.dialogs import (
    ErrorDialog,
    FileViewer,
    FolderDialog,
    ActionsDialog,
    PermissionsDialog,
    ResetDialog,
    SummaryDialog,
)
from alr.tui.settings import Permissions, RunSettings

TASKS = "shared:\n  prompt: p\n  after_task: []\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"
SIZES = [(80, 24), (140, 40)]

DIALOGS: dict[str, Callable[[str], Screen[object]]] = {
    "folder": lambda tasks: FolderDialog(str(Path(tasks).parent)),
    "permissions": lambda tasks: PermissionsDialog(""),
    "actions": lambda tasks: ActionsDialog([]),
    "viewer": lambda tasks: FileViewer(tasks),
    "error": lambda tasks: ErrorDialog("something went wrong"),
    "summary": lambda tasks: SummaryDialog("done", 1.0, None),
    "reset": lambda tasks: ResetDialog([]),
}


@pytest.fixture
def app(work_dir: Path) -> AlrApp:
    tasks_file = write_file(work_dir / "run" / "tasks.yaml", TASKS)
    return AlrApp(
        RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False)
    )


@pytest.mark.parametrize("size", SIZES, ids=["80x24", "140x40"])
@pytest.mark.parametrize("name", list(DIALOGS))
async def test_a_dialog_is_a_centered_box_over_the_main_window(
    app: AlrApp, name: str, size: tuple[int, int]
) -> None:
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        main = app.screen
        app.push_screen(DIALOGS[name](app.settings.tasks_file))
        await pilot.pause()

        box = app.screen.query_one("#dialog").region
        width, height = size
        assert 0 < box.width < width
        assert 0 < box.height < height
        assert box.right <= width
        assert box.bottom <= height
        assert abs(box.x - (width - box.right)) <= 1
        assert abs(box.y - (height - box.bottom)) <= 1
        assert app.screen_stack[-2] is main


def _strip_button(app: AlrApp) -> Button:
    return app.query_one("#start", Button)


def _menu_button(app: AlrApp) -> Button:
    return app.query_one("#menu-folder", Button)


async def _dialog_button(app: AlrApp, pilot: Pilot) -> Button:
    app.push_screen(FolderDialog("."))
    await pilot.pause()
    return app.screen.query_one("#ok", Button)


@pytest.mark.parametrize("where", ["strip", "menu", "dialog"])
async def test_buttons_are_flat_one_row_and_only_focus_changes_them(
    app: AlrApp, where: str
) -> None:
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()
        if where == "dialog":
            button = await _dialog_button(app, pilot)
        else:
            button = {"strip": _strip_button, "menu": _menu_button}[where](app)
        await pilot.pause()

        assert all(edge[0] == "" for edge in button.styles.border)
        assert button.region.height == 1
        # The other button of the screen stays plain, whatever the focus did.
        other = [b for b in app.screen.query(Button) if b is not button][0]
        other.focus()
        await pilot.pause()
        plain = button.styles.background
        button.focus()
        await pilot.pause()

        assert button.styles.background != plain


async def test_a_greyed_out_button_looks_different_from_an_enabled_one(
    app: AlrApp,
) -> None:
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()
        button = _menu_button(app)
        enabled = button.styles.text_opacity

        button.disabled = True
        await pilot.pause()

        assert button.styles.text_opacity < enabled
