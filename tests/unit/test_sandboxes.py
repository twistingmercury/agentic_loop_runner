"""The sandboxes folder: shipped files added when missing, listed, and found by name."""

import os
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import pytest
from helpers import chmodded

from alr.config import config_dir, create_folder
from alr.errors import (
    CONFIG_UNWRITABLE,
    INVALID_CONFIG,
    RefusedError,
    UsageError,
)
from alr.sandboxes import (
    Sandbox,
    add_missing,
    check_can_add_missing,
    find,
    list_sandboxes,
    names_in,
    names_text,
    read_text,
    sandboxes_dir,
)

SHIPPED = ("large.json", "tight.json")


def package_copy(name: str) -> bytes:
    return (files("alr") / "defaults" / "sandboxes" / name).read_bytes()


@pytest.fixture
def set_up_home(tmp_path: Path) -> Path:
    """A home whose config folder exists, as it does after the first-run setup."""
    config_dir(tmp_path).mkdir(parents=True)
    return tmp_path


def test_sandboxes_dir_is_inside_the_config_folder(tmp_path: Path) -> None:
    assert sandboxes_dir(tmp_path) == config_dir(tmp_path) / "sandboxes"


def test_first_run_folder_has_both_sandboxes(tmp_path: Path) -> None:
    create_folder(tmp_path)

    for name in SHIPPED:
        assert (sandboxes_dir(tmp_path) / name).read_bytes() == package_copy(name)


def test_add_missing_adds_a_deleted_file(set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)
    add_missing(set_up_home)
    (folder / "large.json").unlink()

    added = add_missing(set_up_home)

    assert added == [folder / "large.json"]
    assert (folder / "large.json").read_bytes() == package_copy("large.json")


def test_add_missing_never_touches_an_edited_file(set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)
    add_missing(set_up_home)
    large = folder / "large.json"
    large.write_text('{"sandbox": {"network": {}}}\n', encoding="utf-8")
    edited = large.read_bytes()

    first = add_missing(set_up_home)
    second = add_missing(set_up_home)

    assert first == []
    assert second == []
    assert large.read_bytes() == edited


def test_add_missing_losing_a_race_is_not_an_error_and_keeps_the_winner(
    set_up_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = sandboxes_dir(set_up_home)
    real_link = os.link

    def other_alr_wins_the_first_file(source: str, target: Path) -> None:
        # Another alr (or the user) writes the file after our "is it there?" check.
        if Path(target).name == "large.json":
            Path(target).write_text("the winner\n", encoding="utf-8")

        real_link(source, target)

    monkeypatch.setattr("alr.sandboxes.os.link", other_alr_wins_the_first_file)

    added = add_missing(set_up_home)

    assert added == [folder / "tight.json"]
    assert (folder / "large.json").read_text(encoding="utf-8") == "the winner\n"
    assert sorted(path.name for path in folder.iterdir()) == list(SHIPPED)


def test_add_missing_makes_the_folder(set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)
    assert not folder.exists()

    added = add_missing(set_up_home)

    assert added == [folder / "large.json", folder / "tight.json"]
    for name in SHIPPED:
        assert (folder / name).read_bytes() == package_copy(name)


def test_add_missing_leaves_no_temporary_file_behind(set_up_home: Path) -> None:
    add_missing(set_up_home)

    left = sorted(path.name for path in sandboxes_dir(set_up_home).iterdir())
    assert left == list(SHIPPED)


def test_add_missing_leaves_other_files_alone(set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.mkdir()
    mine = folder / "mine.json"
    mine.write_text("{}\n", encoding="utf-8")

    add_missing(set_up_home)

    assert mine.read_text(encoding="utf-8") == "{}\n"


def test_sandboxes_path_that_is_a_file_is_invalid_config(set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.write_text("in the way", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        add_missing(set_up_home)

    assert caught.value.reason == INVALID_CONFIG
    assert str(folder) in caught.value.message
    assert folder.read_text(encoding="utf-8") == "in the way"


def test_unwritable_folder_is_config_unwritable(
    set_up_home: Path, locked_folder: None
) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.mkdir()
    folder.chmod(0o500)

    try:
        with pytest.raises(RefusedError) as caught:
            add_missing(set_up_home)
    finally:
        folder.chmod(0o700)

    assert caught.value.reason == CONFIG_UNWRITABLE
    assert str(folder) in caught.value.message
    assert list(folder.iterdir()) == []


def test_unwritable_folder_that_holds_every_file_is_fine(
    set_up_home: Path, locked_folder: None
) -> None:
    add_missing(set_up_home)
    folder = sandboxes_dir(set_up_home)
    folder.chmod(0o500)

    try:
        added = add_missing(set_up_home)
    finally:
        folder.chmod(0o700)

    assert added == []


def refusal_of(call: Callable[[Path], object], home: Path) -> RefusedError:
    """Call with the home and return the refusal it raises."""
    with pytest.raises(RefusedError) as caught:
        call(home)

    return caught.value


def test_check_refuses_a_sandboxes_path_that_is_a_file(set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.write_text("in the way", encoding="utf-8")

    refusal = refusal_of(check_can_add_missing, set_up_home)

    assert refusal.reason == INVALID_CONFIG
    assert refusal.message == f"{folder} is not a folder; remove it yourself"
    assert refusal.message == refusal_of(add_missing, set_up_home).message
    assert folder.read_text(encoding="utf-8") == "in the way"


def test_check_refuses_a_read_only_folder_that_lacks_a_file_as_the_real_run_does(
    set_up_home: Path, locked_folder: None
) -> None:
    add_missing(set_up_home)
    folder = sandboxes_dir(set_up_home)
    (folder / "tight.json").unlink()

    with chmodded(folder, 0o500):
        dry = refusal_of(check_can_add_missing, set_up_home)
        real = refusal_of(add_missing, set_up_home)
        assert sorted(path.name for path in folder.iterdir()) == ["large.json"]

    assert dry.reason == CONFIG_UNWRITABLE
    assert dry.message == f"cannot add the sandboxes to {folder}: Permission denied"
    assert dry.message == real.message


def test_check_refuses_a_missing_folder_in_a_read_only_config_folder_as_the_real_run_does(
    set_up_home: Path, locked_folder: None
) -> None:
    folder = sandboxes_dir(set_up_home)

    with chmodded(config_dir(set_up_home), 0o500):
        dry = refusal_of(check_can_add_missing, set_up_home)
        real = refusal_of(add_missing, set_up_home)
        assert not folder.exists()

    assert dry.reason == CONFIG_UNWRITABLE
    assert dry.message == f"cannot add the sandboxes to {folder}: Permission denied"
    assert dry.message == real.message


def test_check_refuses_a_folder_alr_cannot_search_as_the_real_run_does(
    set_up_home: Path, locked_folder: None
) -> None:
    add_missing(set_up_home)
    folder = sandboxes_dir(set_up_home)

    with chmodded(folder, 0o000):
        dry = refusal_of(check_can_add_missing, set_up_home)
        real = refusal_of(add_missing, set_up_home)

    assert dry.reason == CONFIG_UNWRITABLE
    assert dry.message == f"cannot add the sandboxes to {folder}: Permission denied"
    assert dry.message == real.message


def test_check_accepts_a_read_only_folder_that_holds_every_file(
    set_up_home: Path, locked_folder: None
) -> None:
    add_missing(set_up_home)

    with chmodded(sandboxes_dir(set_up_home), 0o500):
        check_can_add_missing(set_up_home)


def test_check_accepts_a_read_only_config_folder_when_the_sandboxes_are_all_there(
    set_up_home: Path, locked_folder: None
) -> None:
    add_missing(set_up_home)

    with chmodded(config_dir(set_up_home), 0o500):
        check_can_add_missing(set_up_home)


def test_check_accepts_a_missing_folder_and_a_missing_file_and_writes_nothing(
    set_up_home: Path,
) -> None:
    folder = sandboxes_dir(set_up_home)

    check_can_add_missing(set_up_home)

    assert not folder.exists()

    folder.mkdir()
    check_can_add_missing(set_up_home)

    assert list(folder.iterdir()) == []


def test_list_skips_subfolders_dotfiles_bad_names_and_reserved_names(
    set_up_home: Path,
) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.mkdir()
    for name in ("b.json", "a_1.json", "Z-9.json"):
        (folder / name).write_text("{}\n", encoding="utf-8")
    for name in (
        ".hidden.json",
        "has space.json",
        "dotted.name.json",
        "yolo.json",
        "custom.json",
        "notes.txt",
        "noext",
        ".json",
    ):
        (folder / name).write_text("{}\n", encoding="utf-8")
    (folder / "subfolder.json").mkdir()
    (folder / "nested").mkdir()
    (folder / "nested" / "inner.json").write_text("{}\n", encoding="utf-8")

    listed = list_sandboxes(set_up_home)

    assert [sandbox.name for sandbox in listed] == ["Z-9", "a_1", "b"]
    assert listed[2] == Sandbox("b", folder / "b.json", "")


def test_list_sorts_by_name_not_by_file_name(set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.mkdir()
    # "a-b.json" sorts before "a.json" as a file name but after "a" as a name.
    for name in ("a-b.json", "a.json"):
        (folder / name).write_text("{}\n", encoding="utf-8")

    assert [sandbox.name for sandbox in list_sandboxes(set_up_home)] == ["a", "a-b"]


def test_list_with_no_folder_is_empty(set_up_home: Path) -> None:
    assert list_sandboxes(set_up_home) == []


def test_list_of_a_sandboxes_path_that_is_a_file_is_invalid_config(
    set_up_home: Path,
) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.write_text("in the way", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        list_sandboxes(set_up_home)

    assert caught.value.reason == INVALID_CONFIG
    assert caught.value.message == f"{folder} is not a folder; remove it yourself"


def test_list_of_a_sandboxes_path_that_is_a_dangling_link_is_invalid_config(
    set_up_home: Path,
) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.symlink_to(set_up_home / "nowhere")

    with pytest.raises(RefusedError) as caught:
        list_sandboxes(set_up_home)

    assert caught.value.reason == INVALID_CONFIG
    assert caught.value.message == f"{folder} is not a folder; remove it yourself"


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(0o000, id="no access at all"),
        pytest.param(0o311, id="searchable but not readable"),
        pytest.param(0o400, id="readable but not searchable"),
    ],
)
def test_list_of_a_folder_alr_cannot_list_is_config_unwritable(
    mode: int, set_up_home: Path, locked_folder: None
) -> None:
    add_missing(set_up_home)
    folder = sandboxes_dir(set_up_home)

    with chmodded(folder, mode), pytest.raises(RefusedError) as caught:
        list_sandboxes(set_up_home)

    assert caught.value.reason == CONFIG_UNWRITABLE
    assert caught.value.message == (
        f"cannot list the sandboxes in {folder}: Permission denied"
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_the_names_a_run_could_pick_from_follow_the_same_refusals(
    dry_run: bool, set_up_home: Path
) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.write_text("in the way", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        names_in(folder, dry_run)

    assert caught.value.reason == INVALID_CONFIG


def test_list_marks_invalid_json_and_unreadable(
    set_up_home: Path, locked_folder: None
) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.mkdir()
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    (folder / "list.json").write_text("[]\n", encoding="utf-8")
    (folder / "nan.json").write_text('{"a": NaN}\n', encoding="utf-8")
    (folder / "infinity.json").write_text('{"a": Infinity}\n', encoding="utf-8")
    (folder / "five.json").write_text('{"sandbox": 5}\n', encoding="utf-8")
    (folder / "null.json").write_text('{"sandbox": null}\n', encoding="utf-8")
    (folder / "good.json").write_text("{}\n", encoding="utf-8")
    secret = folder / "secret.json"
    secret.write_text("{}\n", encoding="utf-8")
    secret.chmod(0)

    try:
        problems = {
            sandbox.name: sandbox.problem for sandbox in list_sandboxes(set_up_home)
        }
    finally:
        secret.chmod(0o600)

    assert problems == {
        "broken": "not valid JSON",
        "five": "sandbox is not an object",
        "good": "",
        "infinity": "not valid JSON",
        "list": "not valid JSON",
        "nan": "not valid JSON",
        "null": "sandbox is not an object",
        "secret": "cannot be read",
    }


def test_find_returns_the_path_of_a_listed_name(set_up_home: Path) -> None:
    add_missing(set_up_home)

    assert find("tight", set_up_home) == sandboxes_dir(set_up_home) / "tight.json"


def test_find_unknown_name_lists_names(set_up_home: Path) -> None:
    add_missing(set_up_home)
    (sandboxes_dir(set_up_home) / "mine.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(UsageError) as caught:
        find("typo", set_up_home)

    assert str(caught.value) == (
        "--sandbox: no sandbox named typo; the sandboxes are: large, mine, tight"
    )


def test_find_unknown_name_with_no_sandboxes_says_none(set_up_home: Path) -> None:
    with pytest.raises(UsageError) as caught:
        find("tight", set_up_home)

    assert str(caught.value) == (
        "--sandbox: no sandbox named tight; the sandboxes are: (none)"
    )


@pytest.mark.parametrize("name", ["yolo", "custom", "../tight", "has space", ""])
def test_find_refuses_names_that_are_never_listed(name: str, set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)
    folder.mkdir()
    (folder / "yolo.json").write_text("{}\n", encoding="utf-8")
    (folder / "custom.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(UsageError):
        find(name, set_up_home)


def test_find_dry_run_falls_back_to_package_copy(set_up_home: Path) -> None:
    folder = sandboxes_dir(set_up_home)

    path = find("tight", set_up_home, dry_run=True)

    assert path == folder / "tight.json"
    assert not folder.exists()
    assert read_text(path).encode("utf-8") == package_copy("tight.json")


def test_find_dry_run_error_names_what_a_real_run_would_add(
    set_up_home: Path,
) -> None:
    with pytest.raises(UsageError) as caught:
        find("typo", set_up_home, dry_run=True)

    assert str(caught.value).endswith("the sandboxes are: large, tight")
    assert not sandboxes_dir(set_up_home).exists()


def test_find_dry_run_does_not_invent_a_name_that_is_not_shipped(
    set_up_home: Path,
) -> None:
    with pytest.raises(UsageError):
        find("mine", set_up_home, dry_run=True)


def test_find_dry_run_prefers_the_file_on_disk(set_up_home: Path) -> None:
    add_missing(set_up_home)
    large = sandboxes_dir(set_up_home) / "large.json"
    large.write_text('{"mine": true}\n', encoding="utf-8")

    path = find("large", set_up_home, dry_run=True)

    assert read_text(path) == '{"mine": true}\n'


def test_read_text_of_a_missing_file_that_is_not_shipped_raises(
    set_up_home: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        read_text(sandboxes_dir(set_up_home) / "mine.json")


def test_names_text_joins_names_or_says_none(set_up_home: Path) -> None:
    assert names_text(set_up_home) == "(none)"

    add_missing(set_up_home)

    assert names_text(set_up_home) == "large, tight"
