"""--sandbox-settings and --skip-permissions: a run must choose one, and the sandbox keys are forced.

A run folder's sandbox.json makes the sandboxed choice when no flag does.
"""

import json
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
    task_states,
    write_tasks_yaml,
)

NEITHER_FLAG_ERROR = "error: pass --sandbox-settings <path>, or --skip-permissions to run without a sandbox"
BOTH_FLAGS_ERROR = (
    "error: --sandbox-settings and --skip-permissions cannot be used together"
)
FORCED_SETTINGS = {
    "sandbox": {
        "enabled": True,
        "allowUnsandboxedCommands": False,
        "failIfUnavailable": True,
        "filesystem": {"denyRead": ["~/"]},
    }
}


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
    """Run tasks_yaml with the given permission flags, the fake claude recording its argv."""
    tasks_path = write_tasks_yaml(tmp_path, tasks_yaml)
    record_file = tmp_path / "record.ndjson"

    res = run_alr(
        f"--tasks={tasks_path}",
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
            ["--sandbox-settings={settings}", "--skip-permissions"],
            BOTH_FLAGS_ERROR,
            id="both",
        ),
        pytest.param(
            ["--sandbox-settings={settings}", "--skip-permissions", "--dry-run"],
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

    assert run.res.code == 1, run.res
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

    assert run.res.code == 1, run.res
    assert "error: --sandbox-settings: " in run.res.err
    assert want in run.res.err
    assert run.records == [], "claude must not run"


def test_dry_run_names_sandbox_settings(tmp_path: Path, fake_claude: Path) -> None:
    settings = write_sandbox_settings(tmp_path, "{}")

    res, tasks_path = run_dry_run(
        tmp_path, VALID_TASKS_YAML, f"--sandbox-settings={settings}"
    )

    assert res.code == 0, res
    assert res.out == (
        f"{ONE_TASK_TABLE}sandbox settings: {settings}\n{tasks_path} is valid\n"
    )


def run_folder(
    tmp_path: Path, sandbox_json: str | None, *flags: str
) -> tuple[SandboxRun, Path]:
    """Run a folder holding tasks.yaml, and sandbox.json when given, with only --dir to name it."""
    folder = tmp_path / "run"
    folder.mkdir()
    tasks_path = write_tasks_yaml(folder, VALID_TASKS_YAML)
    if sandbox_json is not None:
        write_sandbox_settings(folder, sandbox_json)

    record_file = tmp_path / "record.ndjson"
    res = run_alr(
        "-d",
        str(folder),
        *flags,
        env={"FAKECLAUDE_RECORD_FILE": str(record_file)},
    )
    return SandboxRun(res, fake_claude_records(record_file), tasks_path), folder


def test_dir_sandbox_json_runs_sandboxed(tmp_path: Path, fake_claude: Path) -> None:
    run, _ = run_folder(tmp_path, '{"sandbox":{"filesystem":{"denyRead":["~/"]}}}')

    assert run.res.code == 0, run.res
    assert len(run.records) == 1
    assert_sandboxed(run.records[0]["argv"])
    assert task_states(read_tasks_yaml(run.tasks_path))[1] == "completed"


def test_dir_without_sandbox_json_needs_a_permission_flag(
    tmp_path: Path, fake_claude: Path
) -> None:
    run, _ = run_folder(tmp_path, None)

    assert run.res.code == 1, run.res
    assert NEITHER_FLAG_ERROR in run.res.err
    assert run.records == [], "claude must not run"


def test_dir_skip_permissions_wins_over_sandbox_json(
    tmp_path: Path, fake_claude: Path
) -> None:
    run, _ = run_folder(tmp_path, "{}", "--skip-permissions")

    assert run.res.code == 0, run.res
    assert [record["argv"] for record in run.records] == [
        ["--print", "--dangerously-skip-permissions"]
    ]


def test_dir_sandbox_settings_flag_wins_over_sandbox_json(
    tmp_path: Path, fake_claude: Path
) -> None:
    other = write_sandbox_settings(
        tmp_path, '{"sandbox":{"filesystem":{"denyRead":["~/"]}}}'
    )

    run, _ = run_folder(tmp_path, "null", f"--sandbox-settings={other}")

    assert run.res.code == 0, run.res
    assert_sandboxed(run.records[0]["argv"])


@pytest.mark.parametrize("mode", [[], ["--dry-run"]], ids=["run", "dry run"])
def test_dir_bad_sandbox_json_starts_nothing(
    tmp_path: Path, fake_claude: Path, mode: list[str]
) -> None:
    run, _ = run_folder(tmp_path, "null", *mode)

    assert run.res.code == 1, run.res
    assert "--sandbox-settings: must be a JSON object" in run.res.err
    assert run.records == [], "claude must not run"
    assert run.tasks_path.read_text(encoding="utf-8") == VALID_TASKS_YAML


def test_dir_dry_run_names_the_folders_sandbox_json(
    tmp_path: Path, fake_claude: Path
) -> None:
    run, folder = run_folder(tmp_path, "{}", "--dry-run")

    assert run.res.code == 0, run.res
    assert run.res.out == (
        f"{ONE_TASK_TABLE}sandbox settings: {folder}/sandbox.json\n"
        f"{run.tasks_path} is valid\n"
    )
    assert run.records == [], "claude must not run"
