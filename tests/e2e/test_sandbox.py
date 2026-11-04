"""--sandbox, --sandbox-settings, and --yolo: a run must choose one, and the sandbox keys are forced.

--sandbox <name> reads ~/.local/alr/sandboxes/<name>.json; a run folder's own
sandbox.json is never read.
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from e2e_helpers import (
    ONE_TASK_TABLE,
    SHARED_PROMPT,
    TWO_TASKS_YAML,
    VALID_TASKS_YAML,
    Result,
    expected_stdin,
    fake_claude_records,
    read_tasks_yaml,
    run_alr,
    run_dry_run,
    run_log,
    shared_block,
    task_states,
    write_tasks_yaml,
)

NEITHER_FLAG_ERROR = (
    "error: pass --sandbox <name>, --sandbox-settings <path>, or --yolo "
    "to run without a sandbox; the sandboxes are: large, tight"
)
BOTH_FLAGS_ERROR = "error: --sandbox-settings and --yolo cannot be used together"
FORCED_SETTINGS = {
    "sandbox": {
        "enabled": True,
        "allowUnsandboxedCommands": False,
        "failIfUnavailable": True,
        "filesystem": {"denyRead": ["~/"]},
    }
}
PRESET_CONFLICT_ERROR = (
    "error: --sandbox cannot be used with --sandbox-settings or --yolo"
)
TIGHT_DOC = {
    "sandbox": {
        "allowUnsandboxedCommands": False,
        "enabled": True,
        "failIfUnavailable": True,
        "filesystem": {
            "denyRead": [
                "~/.ssh",
                "~/.aws",
                "~/.gnupg",
                "~/.config/gh",
                "~/.netrc",
                "~/.claude",
            ]
        },
    }
}
LARGE_DOC = {
    "sandbox": {
        **TIGHT_DOC["sandbox"],
        "excludedCommands": ["docker *", "./build/build.sh", "./build/build.sh *"],
        "network": {
            "allowedDomains": [
                "github.com",
                "*.githubusercontent.com",
                "crates.io",
                "*.crates.io",
                "pypi.org",
                "files.pythonhosted.org",
                "registry.npmjs.org",
                "proxy.golang.org",
                "sum.golang.org",
                "storage.googleapis.com",
            ]
        },
    },
    "permissions": {
        "allow": [
            "Bash(docker *)",
            "Bash(./build/build.sh)",
            "Bash(./build/build.sh *)",
        ]
    },
}
FAILED_TASKS_YAML = (
    shared_block()
    + "tasks:\n  - {id: 1, name: First, prompt: p1, state: failed, error: boom}\n"
)


@dataclass(frozen=True)
class SandboxRun:
    res: Result
    records: list[dict[str, Any]]
    tasks_path: Path


def write_sandbox_settings(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "sandbox.json"
    path.write_text(content, encoding="utf-8")
    return path


def run_with_flags(
    tmp_path: Path, *flags: str, tasks_yaml: str = VALID_TASKS_YAML
) -> SandboxRun:
    """Run tasks_yaml with the given permission flags, the fake claude recording its argv.

    The task file lives in a subfolder, apart from any settings file written
    into tmp_path.
    """
    folder = tmp_path / "run"
    folder.mkdir(exist_ok=True)
    tasks_path = write_tasks_yaml(folder, tasks_yaml)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        f"--dir={tasks_path.parent}",
        *flags,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )
    return SandboxRun(res, fake_claude_records(record_file), tasks_path)


def assert_sandboxed(argv: list[str]) -> None:
    """The settings travel inline; their key order is not part of the contract, so they are compared parsed."""
    assert argv[:4] == ["--print", "--permission-mode", "acceptEdits", "--settings"]
    assert len(argv) == 5
    assert json.loads(argv[4]) == FORCED_SETTINGS


def test_sandbox_argv_carries_forced_settings(
    tmp_path: Path, fake_claude: Path
) -> None:
    content = '{"sandbox":{"enabled":false,"filesystem":{"denyRead":["~/"]}}}'
    settings = write_sandbox_settings(tmp_path, content)

    run = run_with_flags(tmp_path, f"--sandbox-settings={settings}")

    assert run.res.code == 0, run.res
    assert len(run.records) == 1
    assert_sandboxed(run.records[0]["argv"])
    # The stdin contract does not change.
    assert run.records[0]["stdin"] == expected_stdin(
        SHARED_PROMPT, 1, "First task", "Do the thing."
    )
    assert settings.read_text(encoding="utf-8") == content
    assert task_states(read_tasks_yaml(run.tasks_path))[1] == "completed"


def test_sandbox_every_task_gets_the_same_settings(
    tmp_path: Path, fake_claude: Path
) -> None:
    """A second task must not run with weaker flags than the first."""
    settings = write_sandbox_settings(
        tmp_path, '{"sandbox":{"filesystem":{"denyRead":["~/"]}}}'
    )

    run = run_with_flags(
        tmp_path, f"--sandbox-settings={settings}", tasks_yaml=TWO_TASKS_YAML
    )

    assert run.res.code == 0, run.res
    assert len(run.records) == 2
    assert_sandboxed(run.records[0]["argv"])
    assert run.records[1]["argv"] == run.records[0]["argv"]
    assert [record["stdin"] for record in run.records] == [
        expected_stdin(SHARED_PROMPT, 1, "First task", "Do the first thing."),
        expected_stdin(SHARED_PROMPT, 2, "Second task", "Do the second thing."),
    ]
    assert task_states(read_tasks_yaml(run.tasks_path)) == {
        1: "completed",
        2: "completed",
    }


@pytest.mark.parametrize(
    ("flags", "want"),
    [
        pytest.param([], NEITHER_FLAG_ERROR, id="neither"),
        pytest.param(
            ["--sandbox-settings="], NEITHER_FLAG_ERROR, id="empty sandbox path"
        ),
        pytest.param(
            ["--sandbox-settings={settings}", "--yolo"],
            BOTH_FLAGS_ERROR,
            id="both",
        ),
        pytest.param(
            ["--sandbox-settings={settings}", "--yolo", "--dry-run"],
            BOTH_FLAGS_ERROR,
            id="both on a dry run",
        ),
    ],
)
def test_run_needs_one_permission_flag(
    tmp_path: Path, fake_claude: Path, flags: list[str], want: str
) -> None:
    settings = write_sandbox_settings(tmp_path, "{}")

    run = run_with_flags(tmp_path, *(flag.format(settings=settings) for flag in flags))

    assert run.res.code == 2, run.res
    assert want in run.res.err
    assert run.records == [], "claude must not run"
    assert run.tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML


@pytest.mark.parametrize("mode", [[], ["--dry-run"]], ids=["run", "dry run"])
@pytest.mark.parametrize(
    ("content", "want"),
    [
        pytest.param(
            None, "is not accessible: No such file or directory", id="missing"
        ),
        pytest.param("", "is not accessible: Is a directory", id="directory"),
        pytest.param("null", "must be a JSON object", id="not an object"),
        pytest.param(
            '{"sandbox":true}',
            "sandbox: must be a JSON object",
            id="sandbox not an object",
        ),
    ],
)
def test_sandbox_bad_settings_file(
    tmp_path: Path, fake_claude: Path, content: str | None, want: str, mode: list[str]
) -> None:
    """content is the file's text; None leaves the file out, and "" passes a directory instead."""
    path = tmp_path / "missing.json"
    if content == "":
        path = tmp_path

    if content:
        path = write_sandbox_settings(tmp_path, content)

    run = run_with_flags(tmp_path, f"--sandbox-settings={path}", *mode)

    assert run.res.code == 3, run.res
    assert (
        "error: refused: invalid-sandbox-settings: --sandbox-settings: " in run.res.err
    )
    assert want in run.res.err
    assert run.records == [], "claude must not run"


def test_dry_run_names_sandbox_settings(tmp_path: Path, fake_claude: Path) -> None:
    # Outside the run folder, whose own sandbox.json would be noticed.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    settings = write_sandbox_settings(elsewhere, "{}")

    res, tasks_path = run_dry_run(
        tmp_path, VALID_TASKS_YAML, f"--sandbox-settings={settings}"
    )

    assert res.code == 0, res
    assert res.out == (
        f"{ONE_TASK_TABLE}sandbox settings: {settings}\n{tasks_path} is valid\n"
    )


def sandboxes_folder(home: Path) -> Path:
    """Where the sandbox files live in the test's home."""
    return home / ".local" / "alr" / "sandboxes"


def run_folder(
    tmp_path: Path, sandbox_json: str, *flags: str
) -> tuple[SandboxRun, Path]:
    """Run a folder holding tasks.yaml and a sandbox.json, with only --dir to name it."""
    folder = tmp_path / "run"
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, VALID_TASKS_YAML)
    write_sandbox_settings(folder, sandbox_json)
    record_file = tmp_path / "record.ndjson"
    res = run_alr(
        "-d",
        str(folder),
        *flags,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )
    return SandboxRun(res, fake_claude_records(record_file), tasks_path), folder


def folder_notice(folder: Path) -> str:
    """The line alr prints for a run folder that still holds a sandbox.json."""
    return f"{folder}/sandbox.json is no longer read; pick a sandbox with --sandbox <name>\n"


@pytest.mark.parametrize(("name", "doc"), [("tight", TIGHT_DOC), ("large", LARGE_DOC)])
def test_sandbox_name_passes_the_shipped_file_to_claude(
    name: str, doc: dict[str, Any], tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    path = sandboxes_folder(home) / f"{name}.json"

    run = run_with_flags(tmp_path, "--sandbox", name)

    assert run.res.code == 0, run.res
    # The document is written out by hand above, so this checks what the wheel ships.
    assert json.loads(path.read_text(encoding="utf-8")) == doc
    argv = run.records[0]["argv"]
    assert argv[:4] == ["--print", "--permission-mode", "acceptEdits", "--settings"]
    assert json.loads(argv[4]) == doc
    assert not (run.tasks_path.parent / "sandbox.json").exists()
    logged = run_log(run.tasks_path.parent)[0]["sandbox_settings"]
    assert logged == str(path)


def test_a_sandbox_of_the_users_own_runs_with_its_settings_and_the_forced_keys(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    """The file weakens every forced key; claude still gets them forced."""
    weak = {
        "permissions": {"deny": ["WebFetch"]},
        "sandbox": {
            "enabled": False,
            "allowUnsandboxedCommands": True,
            "failIfUnavailable": False,
            "filesystem": {"denyRead": ["~/"]},
        },
    }
    mine = sandboxes_folder(home) / "my_sandbox.json"
    mine.write_text(json.dumps(weak), encoding="utf-8")
    before = mine.read_bytes()

    run = run_with_flags(tmp_path, "--sandbox", "my_sandbox")

    assert run.res.code == 0, run.res
    argv = run.records[0]["argv"]
    assert argv[:4] == ["--print", "--permission-mode", "acceptEdits", "--settings"]
    assert len(argv) == 5
    assert json.loads(argv[4]) == {
        "permissions": {"deny": ["WebFetch"]},
        "sandbox": {**FORCED_SETTINGS["sandbox"]},
    }
    assert mine.read_bytes() == before
    logged = run_log(run.tasks_path.parent)[0]["sandbox_settings"]
    assert logged == str(mine)


def test_an_unknown_sandbox_name_exits_2_listing_the_names(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    (sandboxes_folder(home) / "my_sandbox.json").write_text("{}", encoding="utf-8")

    run = run_with_flags(tmp_path, "--sandbox", "nope")

    assert run.res.code == 2, run.res
    assert run.res.err == (
        "error: --sandbox: no sandbox named nope; "
        "the sandboxes are: large, my_sandbox, tight\n"
    )
    assert run.records == [], "claude must not run"
    assert run.tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML
    assert not (run.tasks_path.parent / "logs").exists()


@pytest.mark.parametrize("mode", [[], ["--dry-run"]], ids=["run", "dry run"])
def test_a_bad_named_sandbox_is_refused_and_starts_nothing(
    tmp_path: Path, home: Path, fake_claude: Path, mode: list[str]
) -> None:
    (sandboxes_folder(home) / "my_sandbox.json").write_text("null", encoding="utf-8")

    run = run_with_flags(tmp_path, "--sandbox", "my_sandbox", *mode)

    assert run.res.code == 3, run.res
    assert "error: refused: invalid-sandbox-settings: " in run.res.err
    assert "must be a JSON object" in run.res.err
    assert run.records == [], "claude must not run"


@pytest.mark.parametrize(
    "other",
    [["--yolo"], ["--sandbox-settings=unused.json"]],
    ids=["yolo", "sandbox settings"],
)
def test_sandbox_with_another_permission_flag_exits_2_before_the_name_is_checked(
    tmp_path: Path, fake_claude: Path, other: list[str]
) -> None:
    run = run_with_flags(tmp_path, "--sandbox", "nope", *other)

    assert run.res.code == 2, run.res
    assert run.res.err == f"{PRESET_CONFLICT_ERROR}\n"
    assert run.records == [], "claude must not run"


def test_a_later_run_adds_a_deleted_sandbox_and_leaves_an_edited_one(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = sandboxes_folder(home)
    (folder / "tight.json").unlink()
    edited = '{"sandbox": {"note": "my large"}}\n'
    (folder / "large.json").write_text(edited, encoding="utf-8")

    first = run_with_flags(tmp_path, "--yolo")

    assert first.res.code == 0, first.res
    assert f"Added {folder / 'tight.json'}\n" in first.res.out
    assert "large.json" not in first.res.out
    assert json.loads((folder / "tight.json").read_text(encoding="utf-8")) == TIGHT_DOC
    assert (folder / "large.json").read_text(encoding="utf-8") == edited

    again = tmp_path / "again"
    again.mkdir()
    second = run_with_flags(again, "--yolo")

    assert second.res.code == 0, second.res
    assert "Added" not in second.res.out
    assert (folder / "large.json").read_text(encoding="utf-8") == edited


def test_a_real_run_with_the_sandboxes_folder_gone_adds_both_files(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = sandboxes_folder(home)
    shutil.rmtree(folder)

    run = run_with_flags(tmp_path, "--sandbox", "tight")

    assert run.res.code == 0, run.res
    assert f"Added {folder / 'large.json'}\n" in run.res.out
    assert f"Added {folder / 'tight.json'}\n" in run.res.out
    assert json.loads(run.records[0]["argv"][4]) == TIGHT_DOC


def test_a_dry_run_with_the_sandboxes_folder_gone_reads_the_package_and_creates_nothing(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = sandboxes_folder(home)
    shutil.rmtree(folder)

    res, _ = run_dry_run(tmp_path, VALID_TASKS_YAML, "--sandbox", "tight")

    assert res.code == 0, res
    assert (
        f"sandbox settings: {folder / 'tight.json'} (tight; added on a real run)\n"
        in res.out
    )
    assert "Added" not in res.out
    assert res.err == ""
    assert not folder.exists()


def test_dry_run_names_a_sandbox_picked_by_name_and_writes_nothing(
    tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    folder = sandboxes_folder(home)
    before = sorted(path.name for path in folder.iterdir())

    res, tasks_path = run_dry_run(tmp_path, VALID_TASKS_YAML, "--sandbox", "large")

    assert res.code == 0, res
    assert res.out == (
        f"{ONE_TASK_TABLE}sandbox settings: {folder / 'large.json'} (large)\n"
        f"{tasks_path} is valid\n"
    )
    assert sorted(path.name for path in folder.iterdir()) == before


def test_a_folders_sandbox_json_is_ignored_with_one_notice(
    tmp_path: Path, fake_claude: Path
) -> None:
    # Not valid settings: reading it would end the run with exit 3.
    run, folder = run_folder(tmp_path, "null", "--yolo")

    assert run.res.code == 0, run.res
    assert run.res.out.count("is no longer read") == 1
    assert folder_notice(folder) in run.res.out
    assert [record["argv"] for record in run.records] == [
        ["--print", "--dangerously-skip-permissions"]
    ]


def test_a_folders_sandbox_json_does_not_choose_the_sandbox(
    tmp_path: Path, fake_claude: Path
) -> None:
    run, folder = run_folder(tmp_path, "{}")

    assert run.res.code == 2, run.res
    assert run.res.err == f"{NEITHER_FLAG_ERROR}\n"
    assert folder_notice(folder) in run.res.out
    assert run.records == [], "claude must not run"


def test_a_named_sandbox_leaves_the_folders_sandbox_json_alone(
    tmp_path: Path, fake_claude: Path
) -> None:
    mine = '{"mine":true}'

    run, folder = run_folder(tmp_path, mine, "--sandbox", "large")

    assert run.res.code == 0, run.res
    assert (folder / "sandbox.json").read_text(encoding="utf-8") == mine
    assert json.loads(run.records[0]["argv"][4]) == LARGE_DOC


def test_a_dry_run_notices_the_folders_sandbox_json_and_names_no_sandbox(
    tmp_path: Path, fake_claude: Path
) -> None:
    run, folder = run_folder(tmp_path, "{}", "--dry-run")

    assert run.res.code == 0, run.res
    assert run.res.out == (
        f"{folder_notice(folder)}{ONE_TASK_TABLE}{run.tasks_path} is valid\n"
    )
    assert run.records == [], "claude must not run"


def put_a_file_where_the_sandboxes_belong(home: Path) -> str:
    """Replace the sandboxes folder with a file; return the refusal every run must give."""
    folder = sandboxes_folder(home)
    shutil.rmtree(folder)
    folder.write_text("in the way", encoding="utf-8")
    return f"error: refused: invalid-config: {folder} is not a folder; remove it yourself\n"


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["--yolo"], id="yolo"),
        pytest.param(["--sandbox", "tight"], id="sandbox by name"),
    ],
)
def test_a_dry_run_refuses_a_sandboxes_path_that_is_a_file(
    flags: list[str], tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    refusal = put_a_file_where_the_sandboxes_belong(home)

    res, _ = run_dry_run(tmp_path, VALID_TASKS_YAML, *flags)

    assert res.code == 3, res
    assert res.out == ""
    assert res.err == refusal
    assert sandboxes_folder(home).read_text(encoding="utf-8") == "in the way"


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["--yolo"], id="yolo"),
        pytest.param(["--sandbox", "tight"], id="sandbox by name"),
    ],
)
def test_a_real_run_refuses_a_sandboxes_path_that_is_a_file(
    flags: list[str], tmp_path: Path, home: Path, fake_claude: Path
) -> None:
    refusal = put_a_file_where_the_sandboxes_belong(home)

    run = run_with_flags(tmp_path, *flags)

    assert run.res.code == 3, run.res
    assert run.res.out == ""
    assert run.res.err == refusal
    assert run.records == [], "claude must not run"
    assert sandboxes_folder(home).read_text(encoding="utf-8") == "in the way"


def test_a_refused_run_leaves_nothing_in_the_folder(
    tmp_path: Path, fake_claude: Path
) -> None:
    run = run_with_flags(tmp_path, "--sandbox", "tight", tasks_yaml=FAILED_TASKS_YAML)

    folder = run.tasks_path.parent
    assert run.res.code == 3, run.res
    assert [path.name for path in folder.iterdir()] == ["tasks.yaml"]
    assert run.records == [], "claude must not run"
