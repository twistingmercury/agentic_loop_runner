"""The main screen's labels, pane titles and proportions, and how long task names are cut."""

from pathlib import Path

import pytest
from helpers import until, write_file
from textual.widgets import Button, Footer, Select, Static

from alr.tui.app import AlrApp
from alr.tui.dialogs import SummaryDialog
from alr.tui.panes import TasksPane
from alr.tui.settings import Permissions, RunSettings

SIZES = [(80, 24), (110, 32)]
SIZE_IDS = ["80x24", "110x32"]
FORTY = "Add the after-all commit mode to the eng"
SIXTY = "Add the after-all commit mode to the engine and then some more words"
MENU = [
    "folder",
    "permissions",
    "actions",
    "reset",
    "tasks",
    "sandbox",
    "quit",
]


def _tasks(*names: str) -> str:
    rows = "".join(
        f"  - id: {number}\n    name: {name}\n    prompt: p\n"
        for number, name in enumerate(names, 1)
    )
    return f"shared:\n  prompt: p\n  after_task: []\ntasks:\n{rows}"


def _app(folder: Path, *names: str) -> AlrApp:
    tasks_file = write_file(folder / "run" / "tasks.yaml", _tasks(*(names or ("A",))))
    return AlrApp(
        RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False)
    )


async def _loaded(app: AlrApp) -> None:
    await until(lambda: app.query_one(TasksPane).row_count > 0)
    await until(lambda: app.query_one(TasksPane).size.width > 0)


def _name_cell(app: AlrApp, row: int = 0) -> str:
    return str(app.query_one(TasksPane).get_cell_at((row, 2)))


async def test_the_logging_setting_in_the_strip_is_labelled(work_dir: Path) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()

        label = app.query_one("#logging-label", Static)
        assert str(label.render()).strip() == "Logging:"


@pytest.mark.parametrize(
    ("pane", "title"),
    [
        ("#tasks", "Tasks"),
        ("#activity", "Task: 1 A"),
        ("#actions", "Actions"),
    ],
)
async def test_the_panes_have_titles(work_dir: Path, pane: str, title: str) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()

        assert app.query_one(pane).border_title == title


async def test_a_label_lines_up_with_its_widget(work_dir: Path) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        label = app.query_one("#logging-label").region
        select = app.query_one("#logging", Select).region
        assert (label.y, label.height) == (select.y, select.height)


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_the_tasks_pane_is_wider_than_the_actions_pane(
    work_dir: Path, size: tuple[int, int]
) -> None:
    app = _app(work_dir)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()

        assert app.query_one("#tasks").region.width >= 28
        assert (
            app.query_one("#tasks").region.width
            > app.query_one("#actions").region.width
        )


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_tasks_and_actions_share_the_top_row_above_the_activity_pane(
    work_dir: Path, size: tuple[int, int]
) -> None:
    app = _app(work_dir)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()

        tasks = app.query_one("#tasks").region
        actions = app.query_one("#actions").region
        activity = app.query_one("#activity").region
        assert (tasks.y, tasks.height) == (actions.y, actions.height)
        assert tasks.right <= actions.x
        assert activity.y >= tasks.bottom


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_the_activity_pane_spans_the_width_and_the_panes_fit_the_screen(
    work_dir: Path, size: tuple[int, int]
) -> None:
    app = _app(work_dir)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()

        screen = app.screen.region
        activity = app.query_one("#activity").region
        assert activity.width == screen.width
        assert activity.height >= 6
        for pane in ("#tasks", "#activity", "#actions"):
            assert screen.contains_region(app.query_one(pane).region)


async def test_the_actions_title_is_fully_visible_at_80_columns(work_dir: Path) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        actions = app.query_one("#actions")
        assert actions.region.width >= len(actions.border_title) + 4


async def test_a_forty_character_name_is_whole_at_110_columns(work_dir: Path) -> None:
    app = _app(work_dir, FORTY)
    async with app.run_test(size=(110, 32)) as pilot:
        await _loaded(app)
        await pilot.pause()

        assert _name_cell(app) == FORTY


async def test_a_name_longer_than_the_column_ends_with_an_ellipsis(
    work_dir: Path,
) -> None:
    app = _app(work_dir, SIXTY)
    async with app.run_test(size=(110, 32)) as pilot:
        await _loaded(app)
        await pilot.pause()

        cell = _name_cell(app)
        assert cell.endswith("…")
        assert SIXTY.startswith(cell.removesuffix("…"))


async def test_the_cut_follows_a_narrower_terminal(work_dir: Path) -> None:
    app = _app(work_dir, FORTY)
    async with app.run_test(size=(80, 24)) as pilot:
        await _loaded(app)
        await pilot.pause()

        assert _name_cell(app).endswith("…")


async def test_the_summary_leaves_a_gap_above_its_button(work_dir: Path) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()
        app.push_screen(SummaryDialog("done", 1.0, None))
        await pilot.pause()

        message = app.screen.query_one("#message").region
        close = app.screen.query_one("#close").region
        assert close.y >= message.bottom + 1


async def test_the_menu_is_one_row_inside_an_80_column_screen(work_dir: Path) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        regions = [app.query_one(f"#menu-{name}", Button).region for name in MENU]
        assert {region.y for region in regions} == {regions[0].y}
        assert max(region.right for region in regions) <= 80


async def test_the_strip_keeps_start_on_an_80_column_screen(work_dir: Path) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        assert app.query_one("#start").region.right <= 80


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_the_columns_leave_room_for_the_scrollbar(
    work_dir: Path, size: tuple[int, int]
) -> None:
    app = _app(work_dir, *(f"{SIXTY} {n}" for n in range(30)))
    async with app.run_test(size=size) as pilot:
        await _loaded(app)
        await pilot.pause()

        table = app.query_one(TasksPane)
        used = sum(column.get_render_width(table) for column in table.columns.values())
        assert used <= table.size.width - 2


MENU_LABELS = {
    "folder": "(f)older",
    "permissions": "(p)ermissions",
    "actions": "(a)ctions",
    "reset": "(r)eset",
    "tasks": "(t)asks",
    "sandbox": "sandbo(x)",
    "quit": "(q)uit",
}


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_the_menu_buttons_carry_their_hotkeys(
    work_dir: Path, size: tuple[int, int]
) -> None:
    app = _app(work_dir)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()

        for name, label in MENU_LABELS.items():
            assert str(app.query_one(f"#menu-{name}", Button).label) == label
        assert str(app.query_one("#start", Button).label) == "(s)tart"


async def test_there_is_no_footer(work_dir: Path) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        assert not app.query(Footer)


async def test_the_menu_row_and_start_fit_inside_80_columns(work_dir: Path) -> None:
    app = _app(work_dir)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        buttons = [app.query_one(f"#menu-{name}", Button) for name in MENU]
        buttons.append(app.query_one("#start", Button))
        assert max(button.region.right for button in buttons) <= 80
