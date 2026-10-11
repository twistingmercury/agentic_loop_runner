"""The setup dialogs, each driven through a small host app."""

from collections.abc import Callable
from pathlib import Path

import pytest

from helpers import write_nested_file
from textual.app import App
from textual.screen import ModalScreen
from textual.widgets import Input, ListView, TextArea

from alr.tui.checks import FolderCheck
from alr.tasks import FAILED, Action, Task
from alr.tui.dialogs import (
    ErrorDialog,
    ResetDialog,
    SummaryDialog,
    FileViewer,
    FolderDialog,
    ActionsDialog,
    PermissionChoice,
    PermissionsDialog,
)
from alr.tui.settings import Permissions

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


async def test_permissions_dialog_skip_returns_skip() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(PermissionsDialog(""), host.record)
        await pilot.pause()

        await pilot.click("#skip")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(Permissions.SKIP, "")


async def test_permissions_dialog_sandbox_returns_the_file(tmp_path: Path) -> None:
    sandbox = write_nested_file(tmp_path / "sandbox.json", "{}")
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(PermissionsDialog(""), host.record)
        await pilot.pause()
        host.screen.query_one("#file", Input).value = str(sandbox)

        await pilot.click("#sandbox")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(Permissions.SANDBOX, str(sandbox))


async def test_permissions_dialog_bad_settings_file_keeps_the_user_on_the_step(
    tmp_path: Path,
) -> None:
    bad = write_nested_file(tmp_path / "sandbox.json", "not json")
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(PermissionsDialog(""), host.record)
        await pilot.pause()
        host.screen.query_one("#file", Input).value = str(bad)

        await pilot.click("#sandbox")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == "unset"
        shown = str(host.screen.query_one("#error").render())
        assert shown.startswith("--sandbox-settings: must be a JSON object")
        assert "refused" not in shown, "the dialog shows the message, not the reason"


async def test_permissions_dialog_preselects_nothing_without_a_default() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(PermissionsDialog(""), host.record)
        await pilot.pause()

        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == "unset"
        assert str(host.screen.query_one("#error").render()) != ""


async def test_actions_dialog_adds_a_action_and_returns_the_list() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([Action(command="ruff check")]), host.record)
        await pilot.pause()
        host.screen.query_one("#cmd", Input).value = "pytest"
        host.screen.query_one("#timeout", Input).value = "5m"

        await pilot.click("#add")
        await pilot.click("#done")
        await pilot.pause()

        assert host.result == [
            Action(command="ruff check"),
            Action(command="pytest", timeout="5m"),
        ]


async def test_actions_dialog_rejects_a_bad_timeout() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([]), host.record)
        await pilot.pause()
        host.screen.query_one("#cmd", Input).value = "pytest"
        host.screen.query_one("#timeout", Input).value = "soon"

        await pilot.click("#add")
        assert str(host.screen.query_one("#error").render()) != ""

        await pilot.click("#done")
        await pilot.pause()

        assert host.result == []


async def test_actions_dialog_rejects_an_empty_command() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([]), host.record)
        await pilot.pause()

        await pilot.click("#add")
        assert "needs a command" in str(host.screen.query_one("#error").render())

        await pilot.click("#done")
        await pilot.pause()

        assert host.result == []


async def test_actions_dialog_removes_the_highlighted_action() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(
            ActionsDialog([Action(command="a"), Action(command="b")]), host.record
        )
        await pilot.pause()
        host.screen.query_one("#list", ListView).index = 0

        await pilot.click("#remove")
        await pilot.click("#done")
        await pilot.pause()

        assert host.result == [Action(command="b")]


async def test_actions_dialog_done_with_no_actions_returns_an_empty_list() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([]), host.record)
        await pilot.pause()

        await pilot.click("#done")
        await pilot.pause()

        assert host.result == []


async def test_actions_dialog_cancel_returns_none() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([Action(command="a")]), host.record)
        await pilot.pause()

        await pilot.click("#cancel")
        await pilot.pause()

        assert host.result is None


async def test_file_viewer_shows_the_file_read_only(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FileViewer(str(path)))
        await pilot.pause()

        text = host.screen.query_one("#text", TextArea)
        assert text.read_only
        assert text.text == "a: 1\n"


async def test_file_viewer_says_when_the_file_is_missing(tmp_path: Path) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FileViewer(str(tmp_path / "gone.json")))
        await pilot.pause()

        assert "not accessible" in host.screen.query_one("#text", TextArea).text


async def test_file_viewer_closes(tmp_path: Path) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FileViewer(""), host.record)
        await pilot.pause()

        await pilot.click("#close")
        await pilot.pause()

        assert host.result is None


@pytest.mark.parametrize(
    "make",
    [
        lambda: FolderDialog("."),
        lambda: PermissionsDialog(""),
        lambda: ActionsDialog([]),
        lambda: FileViewer(""),
        lambda: ErrorDialog("x"),
        lambda: SummaryDialog("done", 1.0, None),
        lambda: ResetDialog([]),
    ],
    ids=["folder", "permissions", "actions", "viewer", "error", "summary", "reset"],
)
async def test_escape_closes_every_dialog_with_none(
    make: Callable[[], ModalScreen[object]],
) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(make(), host.record)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert host.result is None


async def test_enter_in_the_folder_field_submits(git_repo: Path) -> None:
    write_nested_file(git_repo / "run" / "tasks.yaml", TASKS)
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(git_repo / "run")), host.record)
        await pilot.pause()
        host.screen.query_one("#path", Input).focus()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(host.result, FolderCheck)


async def test_enter_in_the_sandbox_field_submits(tmp_path: Path) -> None:
    sandbox = write_nested_file(tmp_path / "sandbox.json", "{}")
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(PermissionsDialog(str(sandbox)), host.record)
        await pilot.pause()
        host.screen.query_one("#file", Input).focus()

        await pilot.press("enter")
        await pilot.pause()

        assert host.result == PermissionChoice(Permissions.SANDBOX, str(sandbox))


@pytest.mark.parametrize("field", ["cmd", "timeout"])
async def test_enter_in_a_action_field_adds_the_action(field: str) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([]), host.record)
        await pilot.pause()
        host.screen.query_one("#cmd", Input).value = "pytest"
        host.screen.query_one(f"#{field}", Input).focus()

        await pilot.press("enter")
        await pilot.pause()

        assert len(host.screen.query_one("#list", ListView).children) == 1


async def test_the_actions_dialog_starts_in_the_command_field() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([Action(command="x")]), host.record)
        await pilot.pause()

        assert host.screen.focused is host.screen.query_one("#cmd", Input)


async def test_the_folder_tree_picks_a_folder_with_arrows_and_enter(
    git_repo: Path,
) -> None:
    (git_repo / "run" / "feat").mkdir()
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(git_repo / "run")), host.record)
        await pilot.pause(0.5)

        await pilot.press("tab", "down", "enter")
        await pilot.pause()

        assert host.screen.query_one("#path", Input).value == str(
            git_repo / "run" / "feat"
        )


FAILED_TASKS = [
    Task(id=3, name="Add the thing", prompt="p", state=FAILED, error="boom\nmore"),
    Task(id=5, name="No reason", prompt="p", state=FAILED),
]


async def test_reset_dialog_names_the_failed_tasks_and_their_first_error_line() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ResetDialog(FAILED_TASKS), host.record)
        await pilot.pause()

        text = str(host.screen.query_one("#message").render())
        title = str(host.screen.query_one("#title").render())
        assert title == "Reset 2 failed tasks to pending?"
        assert "3: Add the thing: boom" in text
        assert "more" not in text
        assert "5: No reason" in text


async def test_reset_dialog_titles_one_task_in_the_singular() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ResetDialog(FAILED_TASKS[:1]), host.record)
        await pilot.pause()

        title = str(host.screen.query_one("#title").render())
        assert title == "Reset 1 failed task to pending?"


async def test_enter_confirms_the_reset() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ResetDialog(FAILED_TASKS), host.record)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert host.result is True


def test_summary_dialog_shows_the_run_time_in_minutes_and_seconds() -> None:
    assert SummaryDialog("done", 1230.0, None)._text == "Run done in 20m 30s"
