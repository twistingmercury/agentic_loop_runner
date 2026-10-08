"""The main screen's labels, pane titles and proportions, and how long task names are cut."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import write_file
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
    "gates",
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
    return f"shared:\n  prompt: p\n  gates: []\ntasks:\n{rows}"


def _app(repo: Path, *names: str) -> AlrApp:
    tasks_file = write_file(repo / "run" / "tasks.yaml", _tasks(*(names or ("A",))))
    return AlrApp(
        RunSettings(tasks_file=tasks_file, permissions=Permissions.SKIP, logging=False)
    )


async def _until(condition: Callable[[], object]) -> None:
    for _ in range(300):
        if condition():
            return

        await asyncio.sleep(0.02)

    pytest.fail("timed out waiting for the condition")


async def _loaded(app: AlrApp) -> None:
    await _until(lambda: app.query_one(TasksPane).row_count > 0)
    await _until(lambda: app.query_one(TasksPane).size.width > 0)


def _name_cell(app: AlrApp, row: int = 0) -> str:
    return str(app.query_one(TasksPane).get_cell_at((row, 2)))


@pytest.mark.parametrize(
    ("widget", "text"), [("#commit-label", "Commit:"), ("#logging-label", "Logging:")]
)
async def test_the_settings_in_the_strip_are_labelled(
    git_repo: Path, widget: str, text: str
) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()

        assert str(app.query_one(widget, Static).render()).strip() == text


@pytest.mark.parametrize(
    ("pane", "title"),
    [
        ("#tasks", "Tasks"),
        ("#activity", "Task: 1 A"),
        ("#gates", "Gates & commits"),
    ],
)
async def test_the_panes_have_titles(git_repo: Path, pane: str, title: str) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()

        assert app.query_one(pane).border_title == title


async def test_a_label_lines_up_with_its_widget(git_repo: Path) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        label = app.query_one("#commit-label").region
        select = app.query_one("#commit", Select).region
        assert (label.y, label.height) == (select.y, select.height)


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_the_tasks_pane_is_wider_than_the_gates_pane(
    git_repo: Path, size: tuple[int, int]
) -> None:
    app = _app(git_repo)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()

        assert app.query_one("#tasks").region.width >= 28
        assert (
            app.query_one("#tasks").region.width > app.query_one("#gates").region.width
        )


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_tasks_and_gates_share_the_top_row_above_the_activity_pane(
    git_repo: Path, size: tuple[int, int]
) -> None:
    app = _app(git_repo)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()

        tasks = app.query_one("#tasks").region
        gates = app.query_one("#gates").region
        activity = app.query_one("#activity").region
        assert (tasks.y, tasks.height) == (gates.y, gates.height)
        assert tasks.right <= gates.x
        assert activity.y >= tasks.bottom


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_the_activity_pane_spans_the_width_and_the_panes_fit_the_screen(
    git_repo: Path, size: tuple[int, int]
) -> None:
    app = _app(git_repo)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()

        screen = app.screen.region
        activity = app.query_one("#activity").region
        assert activity.width == screen.width
        assert activity.height >= 6
        for pane in ("#tasks", "#activity", "#gates"):
            assert screen.contains_region(app.query_one(pane).region)


async def test_the_gates_title_is_fully_visible_at_80_columns(git_repo: Path) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        gates = app.query_one("#gates")
        assert gates.region.width >= len(gates.border_title) + 4


async def test_a_forty_character_name_is_whole_at_110_columns(git_repo: Path) -> None:
    app = _app(git_repo, FORTY)
    async with app.run_test(size=(110, 32)) as pilot:
        await _loaded(app)
        await pilot.pause()

        assert _name_cell(app) == FORTY


async def test_a_name_longer_than_the_column_ends_with_an_ellipsis(
    git_repo: Path,
) -> None:
    app = _app(git_repo, SIXTY)
    async with app.run_test(size=(110, 32)) as pilot:
        await _loaded(app)
        await pilot.pause()

        cell = _name_cell(app)
        assert cell.endswith("…")
        assert SIXTY.startswith(cell.removesuffix("…"))


async def test_the_cut_follows_a_narrower_terminal(git_repo: Path) -> None:
    app = _app(git_repo, FORTY)
    async with app.run_test(size=(80, 24)) as pilot:
        await _loaded(app)
        await pilot.pause()

        assert _name_cell(app).endswith("…")


async def test_the_summary_leaves_a_gap_above_its_button(git_repo: Path) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()
        app.push_screen(SummaryDialog("done", 1.0, None))
        await pilot.pause()

        message = app.screen.query_one("#message").region
        close = app.screen.query_one("#close").region
        assert close.y >= message.bottom + 1


async def test_the_menu_is_one_row_inside_an_80_column_screen(git_repo: Path) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        regions = [app.query_one(f"#menu-{name}", Button).region for name in MENU]
        assert {region.y for region in regions} == {regions[0].y}
        assert max(region.right for region in regions) <= 80


async def test_the_strip_keeps_start_on_an_80_column_screen(git_repo: Path) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        assert app.query_one("#start").region.right <= 80


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_the_columns_leave_room_for_the_scrollbar(
    git_repo: Path, size: tuple[int, int]
) -> None:
    app = _app(git_repo, *(f"{SIXTY} {n}" for n in range(30)))
    async with app.run_test(size=size) as pilot:
        await _loaded(app)
        await pilot.pause()

        table = app.query_one(TasksPane)
        used = sum(column.get_render_width(table) for column in table.columns.values())
        assert used <= table.size.width - 2


MENU_LABELS = {
    "folder": "(f)older",
    "permissions": "(p)ermissions",
    "gates": "(g)ates",
    "reset": "(r)eset",
    "tasks": "(t)asks",
    "sandbox": "sandbo(x)",
    "quit": "(q)uit",
}


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
async def test_the_menu_buttons_carry_their_hotkeys(
    git_repo: Path, size: tuple[int, int]
) -> None:
    app = _app(git_repo)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()

        for name, label in MENU_LABELS.items():
            assert str(app.query_one(f"#menu-{name}", Button).label) == label
        assert str(app.query_one("#start", Button).label) == "(s)tart"


async def test_there_is_no_footer(git_repo: Path) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        assert not app.query(Footer)


async def test_the_menu_row_and_start_fit_inside_80_columns(git_repo: Path) -> None:
    app = _app(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        buttons = [app.query_one(f"#menu-{name}", Button) for name in MENU]
        buttons.append(app.query_one("#start", Button))
        assert max(button.region.right for button in buttons) <= 80
