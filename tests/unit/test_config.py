"""config.yaml: parse and validate."""

from pathlib import Path

import pytest

from alr.config import Config, SandboxDefault, config_dir, parse
from alr.errors import INVALID_CONFIG, RefusedError

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
