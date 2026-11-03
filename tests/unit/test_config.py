"""config.yaml: parse and validate."""

import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from alr.config import (
    Config,
    SandboxDefault,
    config_dir,
    config_folder_exists,
    create_folder,
    default_file_names,
    load,
    parse,
)
from alr.errors import (
    CONFIG_SETUP_MISSING,
    CONFIG_UNWRITABLE,
    INVALID_CONFIG,
    RefusedError,
)

DOCUMENTED = """\
version: 1.0.0
defaults:
  sandbox:
    permissiveness:
      default: tight
  prompts:
    task_review:
      prompt: ./prompts/task_review.md
      max_task_review_rounds: 1
    task_remediation:
      prompt: ./prompts/task_remediation.md
"""

NO_SANDBOX = """\
version: 1.0.0
defaults:
  prompts:
    task_review:
      prompt: ./prompts/task_review.md
      max_task_review_rounds: 1
    task_remediation:
      prompt: ./prompts/task_remediation.md
"""


def make_folder(tmp_path: Path) -> Path:
    """Create a config folder holding both prompt files."""
    folder = tmp_path / "alr"
    (folder / "prompts").mkdir(parents=True)
    (folder / "prompts" / "task_review.md").write_text("review", encoding="utf-8")
    (folder / "prompts" / "task_remediation.md").write_text("fix", encoding="utf-8")
    return folder


def replace(old: str, new: str) -> str:
    """The documented file with one piece changed."""
    assert old in DOCUMENTED
    return DOCUMENTED.replace(old, new)


def test_config_dir_is_under_the_home_folder(tmp_path: Path) -> None:
    assert config_dir(tmp_path) == tmp_path / ".local" / "alr"


def test_config_dir_defaults_to_the_real_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert config_dir() == tmp_path / ".local" / "alr"


def test_parse_reads_the_documented_file(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)

    config = parse(DOCUMENTED, folder)

    assert config == Config(
        sandbox=SandboxDefault(mode="tight", sandbox_file=""),
        task_review_prompt=str(folder / "prompts" / "task_review.md"),
        task_review_max_rounds=1,
        task_remediation_prompt=str(folder / "prompts" / "task_remediation.md"),
    )
    assert Path(config.task_review_prompt).is_absolute()
    assert config.task_review_max_rounds == 1


def test_a_missing_sandbox_section_gives_none(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)

    config = parse(NO_SANDBOX, folder)

    assert config.sandbox is None


@pytest.mark.parametrize("mode", ["tight", "large", "yolo"])
def test_each_preset_is_read(tmp_path: Path, mode: str) -> None:
    folder = make_folder(tmp_path)
    text = replace("default: tight", f"default: {mode}")

    config = parse(text, folder)

    assert config.sandbox == SandboxDefault(mode=mode, sandbox_file="")


def test_custom_reads_its_sandbox_file_relative_to_the_folder(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    (folder / "mine.json").write_text("{}", encoding="utf-8")
    text = replace("default: tight", "default: custom\n      sandbox_file: ./mine.json")

    config = parse(text, folder)

    assert config.sandbox == SandboxDefault(
        mode="custom", sandbox_file=str(folder / "mine.json")
    )


def test_an_absolute_path_stays_as_written(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("mine", encoding="utf-8")
    text = replace("./prompts/task_review.md", str(elsewhere))

    config = parse(text, folder)

    assert config.task_review_prompt == str(elsewhere)


def test_a_larger_cap_is_read(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    text = replace("max_task_review_rounds: 1", "max_task_review_rounds: 3")

    config = parse(text, folder)

    assert config.task_review_max_rounds == 3


REFUSALS = {
    "bad yaml": ("version: [1.0.0\n", "not valid YAML"),
    "empty file": ("", "must be a mapping"),
    "a list": ("- version\n", "must be a mapping"),
    "a number": ("3\n", "must be a mapping"),
    "unknown top key": (
        replace("version: 1.0.0", "version: 1.0.0\ncolour: red"),
        '"colour"',
    ),
    "unknown defaults key": (
        replace("defaults:", "defaults:\n  colour: red"),
        '"colour"',
    ),
    "unknown sandbox key": (
        replace("  sandbox:", "  sandbox:\n    colour: red"),
        '"colour"',
    ),
    "unknown permissiveness key": (
        replace("      default: tight", "      default: tight\n      colour: red"),
        '"colour"',
    ),
    "unknown prompts key": (
        replace("  prompts:", "  prompts:\n    colour: red"),
        '"colour"',
    ),
    "unknown task_review key": (
        replace("    task_review:", "    task_review:\n      colour: red"),
        '"colour"',
    ),
    "unknown task_remediation key": (
        replace("    task_remediation:", "    task_remediation:\n      colour: red"),
        '"colour"',
    ),
    "unknown mode": (replace("default: tight", "default: loose"), '"loose"'),
    "custom without a file": (
        replace("default: tight", "default: custom"),
        "sandbox_file",
    ),
    "a file beside tight": (
        replace("default: tight", "default: tight\n      sandbox_file: ./a.json"),
        "sandbox_file",
    ),
    "a file beside yolo": (
        replace("default: tight", "default: yolo\n      sandbox_file: ./a.json"),
        "sandbox_file",
    ),
    "empty sandbox_file": (
        replace("default: tight", "default: custom\n      sandbox_file: ''"),
        "sandbox_file",
    ),
    "zero rounds": (
        replace("max_task_review_rounds: 1", "max_task_review_rounds: 0"),
        "max_task_review_rounds",
    ),
    "negative rounds": (
        replace("max_task_review_rounds: 1", "max_task_review_rounds: -2"),
        "max_task_review_rounds",
    ),
    "text rounds": (
        replace("max_task_review_rounds: 1", "max_task_review_rounds: two"),
        "max_task_review_rounds",
    ),
    "true rounds": (
        replace("max_task_review_rounds: 1", "max_task_review_rounds: true"),
        "max_task_review_rounds",
    ),
    "missing review prompt": (
        replace("./prompts/task_review.md", "./prompts/nope.md"),
        "nope.md",
    ),
    "missing remediation prompt": (
        replace("./prompts/task_remediation.md", "./prompts/nope.md"),
        "nope.md",
    ),
    "prompt is a folder": (
        replace("./prompts/task_review.md", "./prompts"),
        "prompts",
    ),
    "version 2": (replace("version: 1.0.0", "version: 2.0.0"), "2.0.0"),
    "version 10": (replace("version: 1.0.0", "version: 10.0.0"), "10.0.0"),
    "missing version": (DOCUMENTED.split("\n", 1)[1], "version"),
    "version not text": (replace("version: 1.0.0", "version: 1"), "version"),
    "version garbage": (replace("version: 1.0.0", "version: abc"), "abc"),
    "missing defaults": ("version: 1.0.0\n", "defaults"),
    "defaults not a mapping": ("version: 1.0.0\ndefaults: 3\n", "defaults"),
    "missing prompts": (
        "version: 1.0.0\ndefaults:\n  sandbox:\n"
        "    permissiveness:\n      default: tight\n",
        "prompts",
    ),
    "missing review section": (
        replace(
            "    task_review:\n"
            "      prompt: ./prompts/task_review.md\n"
            "      max_task_review_rounds: 1\n",
            "",
        ),
        "task_review",
    ),
    "missing review prompt key": (
        replace("      prompt: ./prompts/task_review.md\n", ""),
        "prompt",
    ),
    "missing rounds": (
        replace("      max_task_review_rounds: 1\n", ""),
        "max_task_review_rounds",
    ),
    "missing remediation section": (
        replace(
            "    task_remediation:\n      prompt: ./prompts/task_remediation.md\n", ""
        ),
        "task_remediation",
    ),
    "missing mode": (
        replace("      default: tight\n", "      sandbox_file: ./a.json\n"),
        "default",
    ),
    "sandbox without permissiveness": (
        replace(
            "  sandbox:\n    permissiveness:\n      default: tight\n", "  sandbox: {}\n"
        ),
        "permissiveness",
    ),
    "sandbox not a mapping": (
        replace(
            "  sandbox:\n    permissiveness:\n      default: tight\n", "  sandbox:\n"
        ),
        "sandbox",
    ),
}


@pytest.mark.parametrize(("text", "problem"), REFUSALS.values(), ids=REFUSALS.keys())
def test_parse_refuses(tmp_path: Path, text: str, problem: str) -> None:
    folder = make_folder(tmp_path)

    with pytest.raises(RefusedError) as caught:
        parse(text, folder)

    assert caught.value.reason == INVALID_CONFIG
    assert str(folder / "config.yaml") in caught.value.message
    assert problem in caught.value.message


DEFAULT_FILES = ("config.yaml", "prompts/task_review.md", "prompts/task_remediation.md")


def test_create_folder_writes_the_default_files(tmp_path: Path) -> None:
    create_folder(tmp_path)

    folder = config_dir(tmp_path)
    for name in DEFAULT_FILES:
        assert (folder / name).is_file()
    config = load(tmp_path)
    assert config.sandbox is None
    assert config.task_review_max_rounds == 1
    assert config.task_review_prompt == str(folder / "prompts" / "task_review.md")


def test_create_folder_creates_a_missing_dot_local(tmp_path: Path) -> None:
    assert not (tmp_path / ".local").exists()

    create_folder(tmp_path)

    assert (tmp_path / ".local" / "alr" / "config.yaml").is_file()


def test_create_folder_leaves_no_staging_files_behind(tmp_path: Path) -> None:
    create_folder(tmp_path)

    folder = config_dir(tmp_path)
    left = sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))
    assert left == [
        "config.yaml",
        "prompts",
        "prompts/task_remediation.md",
        "prompts/task_review.md",
    ]


def test_create_folder_prints_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create_folder(tmp_path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_creating_again_keeps_the_users_edits(tmp_path: Path) -> None:
    create_folder(tmp_path)
    prompt = config_dir(tmp_path) / "prompts" / "task_review.md"
    prompt.write_text("my own review prompt", encoding="utf-8")

    create_folder(tmp_path)

    assert prompt.read_text(encoding="utf-8") == "my own review prompt"


def test_default_file_names_lists_what_create_folder_writes(tmp_path: Path) -> None:
    create_folder(tmp_path)

    folder = config_dir(tmp_path)
    written = sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.is_file()
    )
    assert sorted(default_file_names()) == written


def test_config_folder_exists_follows_the_folder(tmp_path: Path) -> None:
    assert config_folder_exists(tmp_path) is False

    create_folder(tmp_path)

    assert config_folder_exists(tmp_path) is True


def test_load_reads_an_existing_folder(tmp_path: Path) -> None:
    create_folder(tmp_path)
    config_file = config_dir(tmp_path) / "config.yaml"
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace(
            "max_task_review_rounds: 1", "max_task_review_rounds: 4"
        ),
        encoding="utf-8",
    )

    config = load(tmp_path)

    assert config.task_review_max_rounds == 4


def test_load_names_a_missing_config_folder(tmp_path: Path) -> None:
    with pytest.raises(RefusedError) as caught:
        load(tmp_path)

    assert caught.value.reason == CONFIG_SETUP_MISSING
    assert (
        caught.value.message == f"the config folder is missing ({config_dir(tmp_path)})"
    )


# Both look at the folder first, and a run asks config_folder_exists before it loads.
LOOKS_AT_THE_FOLDER = [load, config_folder_exists]


@pytest.mark.parametrize("look", LOOKS_AT_THE_FOLDER)
def test_a_file_at_the_folder_is_refused_and_kept(
    tmp_path: Path, look: Callable[[Path], object]
) -> None:
    folder = config_dir(tmp_path)
    folder.parent.mkdir()
    folder.write_text("precious", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        look(tmp_path)

    assert caught.value.reason == INVALID_CONFIG
    assert str(folder) in caught.value.message
    assert "is a file; remove it yourself" in caught.value.message
    assert folder.read_text(encoding="utf-8") == "precious"


@pytest.mark.parametrize("look", LOOKS_AT_THE_FOLDER)
def test_a_symlink_at_the_folder_is_refused_and_kept(
    tmp_path: Path, look: Callable[[Path], object]
) -> None:
    # A complete config folder, so only the link itself can cause the refusal.
    elsewhere = tmp_path / "elsewhere"
    create_folder(elsewhere)
    real = config_dir(elsewhere)
    home = tmp_path / "home"
    folder = config_dir(home)
    folder.parent.mkdir(parents=True)
    folder.symlink_to(real)
    before = sorted(path.name for path in real.iterdir())

    with pytest.raises(RefusedError) as caught:
        look(home)

    assert caught.value.reason == INVALID_CONFIG
    assert "is a symlink; remove it yourself" in caught.value.message
    assert folder.is_symlink()
    assert sorted(path.name for path in real.iterdir()) == before


def test_an_unwritable_home_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".local").write_text("in the way", encoding="utf-8")

    with pytest.raises(RefusedError) as caught:
        create_folder(tmp_path)

    assert caught.value.reason == CONFIG_UNWRITABLE
    assert str(config_dir(tmp_path)) in caught.value.message
    assert (tmp_path / ".local").read_text(encoding="utf-8") == "in the way"


@pytest.fixture
def locked_folder(tmp_path: Path) -> Iterator[Path]:
    """A folder with no permissions, restored afterwards so pytest can clean up."""
    if os.geteuid() == 0:
        pytest.skip("root can search any folder")

    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    yield locked
    locked.chmod(0o700)


@pytest.mark.parametrize("look", LOOKS_AT_THE_FOLDER)
def test_a_local_folder_that_cannot_be_searched_is_refused(
    locked_folder: Path, look: Callable[[Path], object]
) -> None:
    # locked/.local/alr: the check of alr cannot get past locked/.
    with pytest.raises(RefusedError) as caught:
        look(locked_folder)

    assert caught.value.reason == CONFIG_UNWRITABLE
    assert str(config_dir(locked_folder)) in caught.value.message


@pytest.mark.parametrize("look", LOOKS_AT_THE_FOLDER)
def test_a_home_that_cannot_be_found_is_refused(
    monkeypatch: pytest.MonkeyPatch, look: Callable[[Path | None], object]
) -> None:
    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", no_home)

    with pytest.raises(RefusedError) as caught:
        look(None)

    assert caught.value.reason == CONFIG_UNWRITABLE
    assert "cannot find the home folder" in caught.value.message


def test_a_prompt_that_cannot_be_checked_is_refused(
    tmp_path: Path, locked_folder: Path
) -> None:
    text = replace("./prompts/task_review.md", f"{locked_folder}/review.md")

    with pytest.raises(RefusedError) as caught:
        parse(text, make_folder(tmp_path))

    assert caught.value.reason == INVALID_CONFIG
    assert f"{locked_folder}/review.md" in caught.value.message


def test_an_existing_folder_without_config_is_refused(tmp_path: Path) -> None:
    folder = config_dir(tmp_path)
    folder.mkdir(parents=True)

    with pytest.raises(RefusedError) as caught:
        load(tmp_path)

    assert caught.value.reason == INVALID_CONFIG
    assert str(folder / "config.yaml") in caught.value.message
    assert list(folder.iterdir()) == []


def test_a_config_that_is_not_text_is_refused(tmp_path: Path) -> None:
    folder = config_dir(tmp_path)
    folder.mkdir(parents=True)
    (folder / "config.yaml").write_bytes(b"\xff\xfe")

    with pytest.raises(RefusedError) as caught:
        load(tmp_path)

    assert caught.value.reason == INVALID_CONFIG
    assert "cannot be read" in caught.value.message


def test_two_creations_racing_both_succeed_and_keep_the_winners_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = config_dir(tmp_path)
    review = folder / "prompts" / "task_review.md"
    real_copyfile = shutil.copyfile
    other_alr_ran: list[bool] = []

    def other_alr_wins_and_the_user_edits(source: Path, target: Path) -> None:
        if not other_alr_ran:
            other_alr_ran.append(True)
            # The other process finishes its whole creation between our staging and our rename.
            create_folder(tmp_path)
            review.write_text("edited after the other one's creation", encoding="utf-8")

        real_copyfile(source, target)

    monkeypatch.setattr("alr.config.shutil.copyfile", other_alr_wins_and_the_user_edits)

    create_folder(tmp_path)

    assert review.read_text(encoding="utf-8") == "edited after the other one's creation"
    assert load(tmp_path).task_review_prompt == str(review)
    assert sorted(path.name for path in folder.parent.iterdir()) == ["alr"]


def copy_files_then(
    monkeypatch: pytest.MonkeyPatch, copies_allowed: int, then: Callable[[], None]
) -> None:
    """Make the copy helper call `then` once `copies_allowed` copies are done."""
    real_copyfile = shutil.copyfile
    copies: list[str] = []

    def copy_then_act(source: Path, target: Path) -> None:
        if len(copies) == copies_allowed:
            then()

        copies.append(Path(target).name)
        real_copyfile(source, target)

    monkeypatch.setattr("alr.config.shutil.copyfile", copy_then_act)


def raise_disk_full() -> None:
    raise OSError(28, "No space left on device")


def raise_interrupt() -> None:
    raise KeyboardInterrupt


def test_a_failed_copy_leaves_no_folder_and_no_staging_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy_files_then(monkeypatch, 2, raise_disk_full)

    with pytest.raises(RefusedError) as caught:
        create_folder(tmp_path)

    assert caught.value.reason == CONFIG_UNWRITABLE
    assert "No space left" in caught.value.message
    assert list((tmp_path / ".local").iterdir()) == []


def test_an_interrupt_mid_copy_leaves_no_folder_and_no_staging_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy_files_then(monkeypatch, 2, raise_interrupt)

    with pytest.raises(KeyboardInterrupt):
        create_folder(tmp_path)

    assert list((tmp_path / ".local").iterdir()) == []


def test_a_reader_never_sees_the_folder_before_it_is_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = config_dir(tmp_path)
    real_copyfile = shutil.copyfile
    seen: list[bool] = []

    def look_before_each_copy(source: Path, target: Path) -> None:
        seen.append(folder.exists())
        real_copyfile(source, target)

    monkeypatch.setattr("alr.config.shutil.copyfile", look_before_each_copy)

    create_folder(tmp_path)

    assert seen == [False] * len(DEFAULT_FILES)
    assert (folder / "config.yaml").is_file()


def test_a_missing_custom_sandbox_file_names_the_config_the_key_and_the_path(
    tmp_path: Path,
) -> None:
    folder = make_folder(tmp_path)
    text = replace("default: tight", "default: custom\n      sandbox_file: ./mine.json")

    with pytest.raises(RefusedError) as caught:
        parse(text, folder)

    message = str(caught.value)
    assert INVALID_CONFIG in message
    assert "config.yaml" in message
    assert "defaults.sandbox.permissiveness.sandbox_file" in message
    assert str(folder / "mine.json") in message
    assert "--sandbox-settings" not in message


def test_an_unreadable_prompt_file_is_refused_at_startup(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    prompt = folder / "prompts" / "task_review.md"
    # A link to itself cannot be opened, even by root, who ignores file modes.
    prompt.unlink()
    prompt.symlink_to(prompt)

    with pytest.raises(RefusedError) as caught:
        parse(DOCUMENTED, folder)

    message = str(caught.value)
    assert "config.yaml" in message
    assert "defaults.prompts.task_review.prompt" in message
    assert str(prompt) in message
