"""The setup dialogs, each driven through a small host app."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from helpers import write_nested_file
from textual.app import App
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Checkbox,
    Input,
    Label,
    ListView,
    RadioButton,
    RadioSet,
    TextArea,
)

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
from alr.engine.presets import Preset, Switches, render, switches_for
from alr.tui.settings import Permissions, RunSettings

TASKS = "shared:\n  prompt: p\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"


class Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.result: object = "unset"

    def record(self, result: object) -> None:
        self.result = result


async def test_folder_dialog_accepts_a_valid_folder(work_dir: Path) -> None:
    write_nested_file(work_dir / "run" / "tasks.yaml", TASKS)
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(work_dir)), host.record)
        await pilot.pause()
        host.screen.query_one("#path", Input).value = str(work_dir / "run")

        await pilot.click("#ok")
        await pilot.pause()

        assert isinstance(host.result, FolderCheck)
        assert host.result.tasks_file == str(work_dir / "run" / "tasks.yaml")


async def test_folder_dialog_keeps_the_user_on_an_error(work_dir: Path) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(work_dir)), host.record)
        await pilot.pause()
        host.screen.query_one("#path", Input).value = str(work_dir / "run")

        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == "unset"
        assert "no tasks.yaml" in str(host.screen.query_one("#error").render())


async def test_folder_dialog_cancel_returns_none(work_dir: Path) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(work_dir)), host.record)
        await pilot.pause()

        await pilot.click("#cancel")
        await pilot.pause()

        assert host.result is None


BIG = (120, 60)
"""A terminal tall enough that every control in the permissions dialog can be clicked."""


def settings_in(folder: Path, **fields: Any) -> RunSettings:
    """Settings for a run folder, with the task file named as the app would."""
    return RunSettings(tasks_file=str(folder / "tasks.yaml"), **fields)


def selected(screen: Screen[Any]) -> str | None:
    """The id of the pressed radio."""
    button = screen.query_one(RadioSet).pressed_button
    return button.id if button else None


def preview(screen: Screen[Any]) -> str:
    return str(screen.query_one("#preview").render())


def note(screen: Screen[Any]) -> str:
    return str(screen.query_one("#note").render())


def switches_shown(screen: Screen[Any]) -> Switches:
    return Switches(
        web=screen.query_one("#web", Checkbox).value,
        docker=screen.query_one("#docker", Checkbox).value,
        hide_credentials=screen.query_one("#hide", Checkbox).value,
    )


async def test_permissions_dialog_preselects_tight_without_a_file(
    tmp_path: Path,
) -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings_in(tmp_path)), host.record)
        await pilot.pause()

        assert selected(host.screen) == "tight"
        assert switches_shown(host.screen) == switches_for(Preset.TIGHT)
        assert preview(host.screen) == render(switches_for(Preset.TIGHT))


async def test_permissions_dialog_preselects_the_file_when_the_folder_has_one(
    tmp_path: Path,
) -> None:
    sandbox = write_nested_file(tmp_path / "sandbox.json", '{"sandbox": {}}')
    settings = settings_in(
        tmp_path, permissions=Permissions.SANDBOX, sandbox_file=str(sandbox)
    )
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings), host.record)
        await pilot.pause()

        assert selected(host.screen) == "file"
        assert str(sandbox) in str(host.screen.query_one("#file", RadioButton).label)
        assert all(
            host.screen.query_one(f"#{name}", Checkbox).disabled
            for name in ("web", "docker", "hide")
        )
        assert f"{sandbox} (as it is)" in preview(host.screen)
        assert '{"sandbox": {}}' in preview(host.screen)


async def test_permissions_dialog_hides_the_file_radio_without_a_file(
    tmp_path: Path,
) -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings_in(tmp_path)), host.record)
        await pilot.pause()

        assert not host.screen.query("#file")


async def test_permissions_dialog_preselects_the_flags_preset(tmp_path: Path) -> None:
    settings = settings_in(
        tmp_path,
        permissions=Permissions.SANDBOX,
        preset=Preset.LARGE,
        switches=switches_for(Preset.LARGE),
    )
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings), host.record)
        await pilot.pause()

        assert selected(host.screen) == "large"
        assert switches_shown(host.screen) == Switches(True, True, True)


async def test_permissions_dialog_preselects_skip_after_yolo(tmp_path: Path) -> None:
    settings = settings_in(tmp_path, permissions=Permissions.SKIP)
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings), host.record)
        await pilot.pause()

        assert selected(host.screen) == "skip"
        assert preview(host.screen) == ""
        assert note(host.screen) == ""
        assert host.screen.query_one("#web", Checkbox).disabled


async def test_choosing_large_sets_every_switch_on_and_shows_its_document(
    tmp_path: Path,
) -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings_in(tmp_path)), host.record)
        await pilot.pause()

        await pilot.click("#large")
        await pilot.pause()

        assert switches_shown(host.screen) == Switches(True, True, True)
        assert preview(host.screen) == render(switches_for(Preset.LARGE))


async def test_moving_a_switch_changes_the_preview_and_the_result(
    tmp_path: Path,
) -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings_in(tmp_path)), host.record)
        await pilot.pause()

        await pilot.click("#web")
        await pilot.pause()
        assert '"allowedDomains"' in preview(host.screen)
        await pilot.click("#ok")
        await pilot.pause()

        assert isinstance(host.result, PermissionChoice)
        assert host.result.preset is Preset.TIGHT
        assert host.result.switches == Switches(True, False, True)


async def test_ok_with_a_preset_returns_the_folder_path_and_switches(
    tmp_path: Path,
) -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings_in(tmp_path)), host.record)
        await pilot.pause()

        await pilot.click("#large")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(
            Permissions.SANDBOX,
            str(tmp_path / "sandbox.json"),
            Preset.LARGE,
            switches_for(Preset.LARGE),
        )


async def test_ok_with_a_preset_and_no_folder_returns_no_path() -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(RunSettings()), host.record)
        await pilot.pause()

        assert "<folder>/sandbox.json" in note(host.screen)
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(
            Permissions.SANDBOX, "", Preset.TIGHT, switches_for(Preset.TIGHT)
        )


async def test_ok_with_the_file_returns_the_file_and_no_switches(
    tmp_path: Path,
) -> None:
    sandbox = write_nested_file(tmp_path / "sandbox.json", "{}")
    settings = settings_in(
        tmp_path, permissions=Permissions.SANDBOX, sandbox_file=str(sandbox)
    )
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings), host.record)
        await pilot.pause()

        assert note(host.screen) == "Nothing is written. Pick a preset to replace it."
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(
            Permissions.SANDBOX, str(sandbox), None, None
        )


async def test_ok_with_the_file_keeps_the_user_on_the_step_when_it_is_invalid(
    tmp_path: Path,
) -> None:
    bad = write_nested_file(tmp_path / "sandbox.json", "not json")
    settings = settings_in(
        tmp_path, permissions=Permissions.SANDBOX, sandbox_file=str(bad)
    )
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings), host.record)
        await pilot.pause()

        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == "unset"
        shown = str(host.screen.query_one("#error").render())
        assert shown.startswith("--sandbox-settings: must be a JSON object")
        assert "refused" not in shown, "the dialog shows the message, not the reason"


async def test_the_note_says_the_file_is_written_on_start_and_replaced(
    tmp_path: Path,
) -> None:
    sandbox = write_nested_file(tmp_path / "sandbox.json", "{}")
    settings = settings_in(
        tmp_path,
        permissions=Permissions.SANDBOX,
        preset=Preset.LARGE,
        switches=switches_for(Preset.LARGE),
    )
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings), host.record)
        await pilot.pause()

        assert selected(host.screen) == "large"
        assert note(host.screen) == (
            f"Written to {sandbox} when you press Start. Cancel or Quit discards it."
            " Replaces the file there."
        )
        assert str(sandbox) in str(host.screen.query_one("#file", RadioButton).label)


async def test_the_note_has_no_replace_sentence_without_a_folder_file(
    tmp_path: Path,
) -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings_in(tmp_path)), host.record)
        await pilot.pause()

        assert note(host.screen) == (
            f"Written to {tmp_path / 'sandbox.json'} when you press Start."
            " Cancel or Quit discards it."
        )


async def test_permissions_dialog_skip_returns_skip(tmp_path: Path) -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings_in(tmp_path)), host.record)
        await pilot.pause()

        await pilot.click("#skip")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(Permissions.SKIP, "", None, None)


async def test_actions_dialog_adds_a_action_and_returns_the_list() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([Action(command="ruff check")], []), host.record)
        await pilot.pause()
        host.screen.query_one("#cmd", Input).value = "pytest"
        host.screen.query_one("#timeout", Input).value = "5m"

        await pilot.click("#add")
        await pilot.click("#done")
        await pilot.pause()

        assert host.result == (
            [Action(command="ruff check"), Action(command="pytest", timeout="5m")],
            [],
        )


async def test_actions_dialog_rejects_a_bad_timeout() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([], []), host.record)
        await pilot.pause()
        host.screen.query_one("#cmd", Input).value = "pytest"
        host.screen.query_one("#timeout", Input).value = "soon"

        await pilot.click("#add")
        assert str(host.screen.query_one("#error").render()) != ""

        await pilot.click("#done")
        await pilot.pause()

        assert host.result == ([], [])


async def test_actions_dialog_rejects_an_empty_command() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([], []), host.record)
        await pilot.pause()

        await pilot.click("#add")
        assert "needs a command" in str(host.screen.query_one("#error").render())

        await pilot.click("#done")
        await pilot.pause()

        assert host.result == ([], [])


async def test_actions_dialog_removes_the_highlighted_action() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(
            ActionsDialog([Action(command="a"), Action(command="b")], []), host.record
        )
        await pilot.pause()
        host.screen.query_one("#after-task", ListView).index = 0

        await pilot.click("#remove")
        await pilot.click("#done")
        await pilot.pause()

        assert host.result == ([Action(command="b")], [])


async def test_actions_dialog_shows_both_lists_prefilled() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(
            ActionsDialog([Action(command="a")], [Action(command="b")]), host.record
        )
        await pilot.pause()

        labels = [str(label.render()) for label in host.screen.query(Label)]
        assert "After each task" in labels
        assert "After the run" in labels
        assert len(host.screen.query_one("#after-task", ListView)) == 1
        assert len(host.screen.query_one("#after-run", ListView)) == 1


async def test_actions_dialog_adds_to_the_focused_list() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([], []), host.record)
        await pilot.pause()
        host.screen.query_one("#after-run", ListView).focus()
        host.screen.query_one("#cmd", Input).value = "make build"

        await pilot.click("#add")
        await pilot.click("#done")
        await pilot.pause()

        assert host.result == ([], [Action(command="make build")])


async def test_actions_dialog_removes_from_the_focused_list() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(
            ActionsDialog([Action(command="a")], [Action(command="b")]), host.record
        )
        await pilot.pause()
        host.screen.query_one("#after-run", ListView).index = 0
        host.screen.query_one("#after-run", ListView).focus()

        await pilot.click("#remove")
        await pilot.click("#done")
        await pilot.pause()

        assert host.result == ([Action(command="a")], [])


async def test_actions_dialog_done_with_no_actions_returns_two_empty_lists() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([], []), host.record)
        await pilot.pause()

        await pilot.click("#done")
        await pilot.pause()

        assert host.result == ([], [])


async def test_actions_dialog_cancel_returns_none() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([Action(command="a")], []), host.record)
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
        lambda: PermissionsDialog(RunSettings()),
        lambda: ActionsDialog([], []),
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


async def test_enter_in_the_folder_field_submits(work_dir: Path) -> None:
    write_nested_file(work_dir / "run" / "tasks.yaml", TASKS)
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(work_dir / "run")), host.record)
        await pilot.pause()
        host.screen.query_one("#path", Input).focus()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(host.result, FolderCheck)


@pytest.mark.parametrize("field", ["cmd", "timeout"])
async def test_enter_in_a_action_field_adds_the_action(field: str) -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([], []), host.record)
        await pilot.pause()
        host.screen.query_one("#cmd", Input).value = "pytest"
        host.screen.query_one(f"#{field}", Input).focus()

        await pilot.press("enter")
        await pilot.pause()

        assert len(host.screen.query_one("#after-task", ListView).children) == 1


async def test_the_actions_dialog_starts_in_the_command_field() -> None:
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(ActionsDialog([Action(command="x")], []), host.record)
        await pilot.pause()

        assert host.screen.focused is host.screen.query_one("#cmd", Input)


async def test_the_folder_tree_picks_a_folder_with_arrows_and_enter(
    work_dir: Path,
) -> None:
    (work_dir / "run" / "feat").mkdir()
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(FolderDialog(str(work_dir / "run")), host.record)
        await pilot.pause(0.5)

        await pilot.press("tab", "down", "enter")
        await pilot.pause()

        assert host.screen.query_one("#path", Input).value == str(
            work_dir / "run" / "feat"
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


async def test_permissions_dialog_fits_an_80_by_24_terminal(tmp_path: Path) -> None:
    sandbox = write_nested_file(tmp_path / "sandbox.json", "{}")
    settings = settings_in(
        tmp_path, permissions=Permissions.SANDBOX, sandbox_file=str(sandbox)
    )
    host = Host()
    async with host.run_test(size=(80, 24)) as pilot:
        host.push_screen(PermissionsDialog(settings), host.record)
        await pilot.pause()

        box = host.screen.query_one("#dialog").region
        inner = host.screen.query_one("#dialog").content_region
        ok = host.screen.query_one("#ok").region
        assert box.contains_region(ok), "the buttons lie inside the dialog"
        for name in ("web", "docker", "hide"):
            checkbox = host.screen.query_one(f"#{name}").region
            assert checkbox.right <= inner.right, f"#{name} is wider than the dialog"
        assert host.screen.query_one("#preview").region.height >= 6


async def test_permissions_dialog_preselects_skip_over_a_stale_settings_file() -> None:
    settings = RunSettings(permissions=Permissions.SKIP, sandbox_file="x.json")
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings), host.record)
        await pilot.pause()

        assert selected(host.screen) == "skip"
