"""The setup dialogs, each driven through a small host app."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from helpers import write_nested_file
from textual.app import App
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListView,
    RadioButton,
    RadioSet,
    TextArea,
)

from alr.sandboxes import Sandbox, list_sandboxes, sandboxes_dir
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

SKIP_LABEL = "Skip permissions (no sandbox)"


def settings_in(folder: Path, **fields: Any) -> RunSettings:
    """Settings for a run folder, with the task file named as the app would."""
    return RunSettings(tasks_file=str(folder / "tasks.yaml"), **fields)


def selected(screen: Screen[Any]) -> str | None:
    """The id of the pressed radio."""
    button = screen.query_one(RadioSet).pressed_button
    return button.id if button else None


def radios(screen: Screen[Any]) -> list[tuple[str, str]]:
    """Every radio as (id, label), in the order the dialog lists them."""
    return [
        (button.id or "", str(button.label)) for button in screen.query(RadioButton)
    ]


def preview(screen: Screen[Any]) -> str:
    return str(screen.query_one("#preview").render())


def error(screen: Screen[Any]) -> str:
    return str(screen.query_one("#error").render())


def ok_is_disabled(screen: Screen[Any]) -> bool:
    return screen.query_one("#ok", Button).disabled


def sandbox_path(name: str) -> Path:
    """Where the sandbox called name lives in the temporary home."""
    return sandboxes_dir() / f"{name}.json"


def add_sandbox(name: str, text: str) -> Path:
    """Put a sandbox file in the temporary home's sandboxes folder."""
    return write_nested_file(sandbox_path(name), text)


def shown_for(path: Path) -> str:
    """What the preview shows for a good file: its path, then the file exactly as it is."""
    return f"{path}\n{path.read_text(encoding='utf-8')}"


async def test_permissions_dialog_lists_every_sandbox_by_name_then_skip() -> None:
    add_sandbox("my_sandbox", "{}")
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()

        assert radios(host.screen) == [
            ("sandbox-0", "large"),
            ("sandbox-1", "my_sandbox"),
            ("sandbox-2", "tight"),
            ("skip", SKIP_LABEL),
        ]


async def test_permissions_dialog_puts_the_settings_file_first_when_one_was_given(
    tmp_path: Path,
) -> None:
    given = write_nested_file(tmp_path / "mine.json", '{"sandbox": {}}')
    settings = RunSettings(permissions=Permissions.SANDBOX, sandbox_file=str(given))
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings, list_sandboxes()), host.record)
        await pilot.pause()

        listed = radios(host.screen)
        assert [radio_id for radio_id, _ in listed] == [
            "file",
            "sandbox-0",
            "sandbox-1",
            "skip",
        ]
        assert listed[0][1] == f"Use the settings file {given}"
        assert selected(host.screen) == "file"
        assert preview(host.screen) == shown_for(given)


async def test_permissions_dialog_has_no_file_radio_without_a_settings_file(
    tmp_path: Path,
) -> None:
    # A sandbox.json in the run folder is no longer an answer, so it is not offered.
    write_nested_file(tmp_path / "sandbox.json", "{}")
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(settings_in(tmp_path), list_sandboxes()), host.record
        )
        await pilot.pause()

        assert not host.screen.query("#file")


async def test_permissions_dialog_preselects_tight_when_nothing_chose() -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()

        assert selected(host.screen) == "sandbox-1"
        assert preview(host.screen) == shown_for(sandbox_path("tight"))


@pytest.mark.parametrize(
    ("name", "radio_id"),
    [("large", "sandbox-0"), ("my_sandbox", "sandbox-1")],
    ids=["a shipped name", "a name the user added"],
)
async def test_permissions_dialog_preselects_the_sandbox_the_flag_named(
    name: str, radio_id: str
) -> None:
    add_sandbox("my_sandbox", "{}")
    settings = RunSettings(
        permissions=Permissions.SANDBOX,
        sandbox_file=str(sandbox_path(name)),
        sandbox_name=name,
    )
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings, list_sandboxes()), host.record)
        await pilot.pause()

        assert selected(host.screen) == radio_id
        assert preview(host.screen) == shown_for(sandbox_path(name))


async def test_permissions_dialog_ignores_a_named_sandbox_that_is_gone() -> None:
    settings = RunSettings(
        permissions=Permissions.SANDBOX,
        sandbox_file=str(sandbox_path("vanished")),
        sandbox_name="vanished",
    )
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings, list_sandboxes()), host.record)
        await pilot.pause()

        assert selected(host.screen) == "sandbox-1"


async def test_permissions_dialog_preselects_the_first_name_without_tight() -> None:
    add_sandbox("alpha", "{}")
    sandbox_path("tight").unlink()
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()

        assert radios(host.screen)[0] == ("sandbox-0", "alpha")
        assert selected(host.screen) == "sandbox-0"


async def test_permissions_dialog_preselects_skip_after_yolo() -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(
                RunSettings(permissions=Permissions.SKIP), list_sandboxes()
            ),
            host.record,
        )
        await pilot.pause()

        assert selected(host.screen) == "skip"
        assert preview(host.screen) == ""
        assert not ok_is_disabled(host.screen)


async def test_permissions_dialog_with_no_sandboxes_presses_nothing_until_skip_is_chosen() -> (
    None
):
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(RunSettings(), []), host.record)
        await pilot.pause()

        assert radios(host.screen) == [("skip", SKIP_LABEL)]
        assert selected(host.screen) is None
        assert ok_is_disabled(host.screen)

        await pilot.click("#skip")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(Permissions.SKIP, "", "")


async def test_the_preview_shows_the_selected_file_exactly_as_it_is() -> None:
    # Odd spacing and key order: the preview must not reformat what is on disk.
    odd = add_sandbox("odd", '{"b": 1,\n   "a": 2}   \n')
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()

        await pilot.click("#sandbox-1")

        assert preview(host.screen) == f'{odd}\n{{"b": 1,\n   "a": 2}}   \n'


async def test_choosing_another_sandbox_changes_the_preview() -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()

        await pilot.click("#sandbox-0")
        assert preview(host.screen) == shown_for(sandbox_path("large"))

        await pilot.click("#skip")
        assert preview(host.screen) == ""


async def test_a_broken_sandbox_is_listed_with_its_problem_and_cannot_be_accepted() -> (
    None
):
    broken = add_sandbox("broken", "not json")
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()
        assert radios(host.screen)[0] == ("sandbox-0", "broken   (not valid JSON)")
        assert not ok_is_disabled(host.screen), "tight is preselected and works"

        await pilot.click("#sandbox-0")

        assert preview(host.screen) == f"{broken}\nnot valid JSON"
        assert ok_is_disabled(host.screen)
        await pilot.click("#ok")
        await pilot.pause()
        assert host.result == "unset"
        assert isinstance(host.screen, PermissionsDialog)

        # The broken file must not trap the user: Cancel still closes the dialog.
        await pilot.click("#cancel")
        await pilot.pause()
        assert host.result is None
        assert not isinstance(host.screen, PermissionsDialog)


async def test_a_broken_sandbox_the_flag_named_starts_with_ok_off_until_a_working_one_is_chosen() -> (
    None
):
    add_sandbox("broken", "[1, 2]")
    settings = RunSettings(
        permissions=Permissions.SANDBOX,
        sandbox_file=str(sandbox_path("broken")),
        sandbox_name="broken",
    )
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings, list_sandboxes()), host.record)
        await pilot.pause()

        assert selected(host.screen) == "sandbox-0"
        assert ok_is_disabled(host.screen)

        await pilot.click("#sandbox-2")

        assert not ok_is_disabled(host.screen)


async def test_a_sandbox_that_cannot_be_read_shows_that_as_the_reason(
    tmp_path: Path,
) -> None:
    locked = Sandbox("locked", tmp_path / "locked.json", "cannot be read")
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(RunSettings(), [locked]), host.record)
        await pilot.pause()

        # No tight, so the first name is preselected, and it is the unreadable one.
        assert radios(host.screen)[0] == ("sandbox-0", "locked   (cannot be read)")
        assert preview(host.screen) == f"{locked.path}\ncannot be read"
        assert ok_is_disabled(host.screen)


async def test_ok_returns_the_picked_sandbox_by_name_and_path() -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()

        await pilot.click("#sandbox-0")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(
            Permissions.SANDBOX, str(sandbox_path("large")), "large"
        )


async def test_ok_with_the_settings_file_returns_the_file_and_no_name(
    tmp_path: Path,
) -> None:
    given = write_nested_file(tmp_path / "mine.json", "{}")
    settings = RunSettings(permissions=Permissions.SANDBOX, sandbox_file=str(given))
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings, list_sandboxes()), host.record)
        await pilot.pause()

        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(Permissions.SANDBOX, str(given), "")


async def test_ok_with_the_settings_file_keeps_the_user_on_the_step_when_it_is_invalid(
    tmp_path: Path,
) -> None:
    bad = write_nested_file(tmp_path / "mine.json", "not json")
    settings = RunSettings(permissions=Permissions.SANDBOX, sandbox_file=str(bad))
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings, list_sandboxes()), host.record)
        await pilot.pause()

        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == "unset"
        shown = error(host.screen)
        assert shown.startswith(f"--sandbox-settings {bad}: must be a JSON object")
        assert "refused" not in shown, "the dialog shows the message, not the reason"


async def test_ok_on_a_sandbox_the_run_would_refuse_keeps_the_user_here() -> None:
    # A JSON object, so it is listed without a problem; its sandbox key is what the run refuses.
    odd = add_sandbox("odd", '{"sandbox": 3}')
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()

        await pilot.click("#sandbox-1")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == "unset"
        assert error(host.screen) == (
            f"--sandbox odd: {odd}: sandbox: must be a JSON object"
        )

        await pilot.click("#sandbox-2")

        assert error(host.screen) == ""


async def test_permissions_dialog_skip_returns_skip() -> None:
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(
            PermissionsDialog(RunSettings(), list_sandboxes()), host.record
        )
        await pilot.pause()

        await pilot.click("#skip")
        await pilot.click("#ok")
        await pilot.pause()

        assert host.result == PermissionChoice(Permissions.SKIP, "", "")


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
        lambda: PermissionsDialog(RunSettings(), list_sandboxes()),
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


async def _press_enter_on_summary(outcome: str) -> tuple[str, bool, object]:
    """Show a summary, press Enter once; return the button's label, whether it had focus, and the answer."""
    host = Host()
    async with host.run_test() as pilot:
        host.push_screen(SummaryDialog(outcome, 1.0, "boom"), host.record)
        await pilot.pause()
        label = str(host.screen.query_one("#close", Button).label)
        focused = host.focused is not None and host.focused.id == "close"
        await pilot.press("enter")
        await pilot.pause()

    return label, focused, host.result


async def test_a_clean_run_summary_focuses_exit_and_enter_answers_true() -> None:
    assert await _press_enter_on_summary("done") == ("Exit", True, True)


@pytest.mark.parametrize("outcome", ["failed", "stopped"])
async def test_a_failed_or_stopped_summary_focuses_close_and_enter_answers_false(
    outcome: str,
) -> None:
    assert await _press_enter_on_summary(outcome) == ("Close", True, False)


def test_summary_dialog_shows_the_run_time_in_minutes_and_seconds() -> None:
    assert SummaryDialog("done", 1230.0, None)._text == "Run done in 20m 30s"


@pytest.mark.parametrize("radio", [None, "sandbox-1", "sandbox-0"])
async def test_permissions_dialog_fits_an_80_by_24_terminal(
    tmp_path: Path, radio: str | None
) -> None:
    sandbox = write_nested_file(tmp_path / "sandbox.json", "{}")
    settings = settings_in(
        tmp_path, permissions=Permissions.SANDBOX, sandbox_file=str(sandbox)
    )
    host = Host()
    async with host.run_test(size=(80, 24)) as pilot:
        host.push_screen(PermissionsDialog(settings, list_sandboxes()), host.record)
        await pilot.pause()
        if radio:
            await pilot.click(f"#{radio}")
            await pilot.pause()

        box = host.screen.query_one("#dialog").region
        ok = host.screen.query_one("#ok").region
        assert box.contains_region(ok), "the buttons lie inside the dialog"
        assert host.screen.region.contains_region(ok), "the buttons lie on screen"
        assert host.screen.query_one("#preview").region.height >= 6


async def test_permissions_dialog_preselects_skip_over_a_stale_settings_file() -> None:
    settings = RunSettings(permissions=Permissions.SKIP, sandbox_file="x.json")
    host = Host()
    async with host.run_test(size=BIG) as pilot:
        host.push_screen(PermissionsDialog(settings, list_sandboxes()), host.record)
        await pilot.pause()

        assert selected(host.screen) == "skip"
