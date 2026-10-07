"""The setup dialogs, each driven through a small host app."""

from pathlib import Path

from helpers import write_nested_file
from textual.app import App
from textual.widgets import Input

from alr.tui.checks import FolderCheck
from alr.tui.dialogs import FolderDialog

TASKS = "shared:\n  prompt: p\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"


class Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.result: object = "unset"

    def record(self, result: object) -> None:
        self.result = result


async def test_folder_dialog_accepts_a_valid_folder(git_repo: Path) -> None:
    write_nested_file(git_repo / "run" / "tasks.yaml", TASKS)
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(git_repo)), host.record)
        await pilot.pause()
        host.screen.query_one("#path", Input).value = str(git_repo / "run")

        await pilot.click("#ok")
        await pilot.pause()

        assert isinstance(host.result, FolderCheck)
        assert host.result.tasks_file == str(git_repo / "run" / "tasks.yaml")


async def test_folder_dialog_keeps_the_user_on_an_error(git_repo: Path) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(git_repo)), host.record)
        await pilot.pause()
        host.screen.query_one("#path", Input).value = str(git_repo / "run")

        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == "unset"
        assert "no tasks.yaml" in str(host.screen.query_one("#error").render())


async def test_folder_dialog_cancel_returns_none(git_repo: Path) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(git_repo)), host.record)
        await pilot.pause()

        await pilot.click("#cancel")
        await pilot.pause()

        assert host.result is None
