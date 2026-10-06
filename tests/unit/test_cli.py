"""The command line: flags, startup checks in gralph's order, routing, and stopping on a signal."""

import asyncio
import contextlib
import os
import signal
import time
from pathlib import Path

import pytest
from helpers import gone_within, start_alr, wait_for_file

from alr import cli
from alr.engine import proc
from alr.errors import AlrError, SandboxError, UsageError

ONE_TASK_YAML = (
    "shared:\n  prompt: shared\ntasks:\n  - id: 1\n    name: a\n    prompt: p\n"
)
SANDBOXED = [
    "--permission-mode",
    "acceptEdits",
    "--settings",
    '{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"failIfUnavailable":true}}',
]
BOTH_PERMISSIONS = "--sandbox-settings and --skip-permissions cannot be used together"
NO_PERMISSIONS = (
    "pass --sandbox-settings <path>, or --skip-permissions to run without a sandbox"
)
BAD_DURATION = "--gate-timeout: must be a duration string such as 90s or 10m"
NOT_POSITIVE = "--gate-timeout: must be greater than zero"


@pytest.fixture
def settings(tmp_path: Path) -> str:
    path = tmp_path / "sandbox.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    ("skip", "dry_run", "want"),
    [
        pytest.param(False, True, SANDBOXED, id="dry run, sandbox file"),
    ],
)
def test_session_args_sandbox_file(
    settings: str, skip: bool, dry_run: bool, want: list[str]
) -> None:
    assert cli.session_args(settings, skip, dry_run) == want


@pytest.mark.parametrize(
    ("file", "skip", "dry_run", "error", "want"),
    [
        pytest.param(
            "sandbox.json", True, True, UsageError, BOTH_PERMISSIONS, id="dry run, both"
        ),
        pytest.param(
            "missing.json",
            False,
            True,
            SandboxError,
            "--sandbox-settings: file",
            id="dry run, bad file",
        ),
    ],
)
def test_session_args_errors(
    tmp_path: Path,
    file: str,
    skip: bool,
    dry_run: bool,
    error: type[AlrError],
    want: str,
) -> None:
    path = str(tmp_path / file) if file else ""

    with pytest.raises(error) as caught:
        cli.session_args(path, skip, dry_run)

    assert want in str(caught.value)


# Go's TestSessionArgs_WizardLeavesTheChoiceOpen is not ported: alr has no wizard.


@pytest.mark.parametrize(
    ("value", "want"),
    [
        pytest.param("0s", NOT_POSITIVE, id="zero"),
        pytest.param("-5m", NOT_POSITIVE, id="negative"),
    ],
)
def test_check_gate_timeout_errors(value: str, want: str) -> None:
    with pytest.raises(UsageError) as caught:
        cli.check_gate_timeout(value)

    assert str(caught.value) == want


# Go's rows with plain false are not ported: alr has no full-screen view.
@pytest.mark.parametrize(
    ("log_dir", "dry_run"),
    [
        pytest.param("logs", True, id="dry run ignores it"),
    ],
)
def test_check_log_dir_accepted(log_dir: str, dry_run: bool) -> None:
    cli.check_log_dir(log_dir, dry_run)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A run folder holding only the task file."""
    path = tmp_path / "run"
    path.mkdir()
    (path / "tasks.yaml").write_text(ONE_TASK_YAML, encoding="utf-8")
    return path


def test_resolve_dir_flag_wins_over_the_folder(folder: Path) -> None:
    assert cli.resolve_dir(str(folder), "other.yaml") == "other.yaml"


def test_version_prints_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("alr version: ")
    assert out.count("\n") == 1


def test_missing_required_flags_are_each_named(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([]) == 1

    err = capsys.readouterr().err
    assert err.startswith("error: required flag --tasks not set\n")
    assert err.count("required flag") == 1


@pytest.mark.parametrize("flag", ["-p", "--prompt"])
def test_prompt_flag_is_an_unknown_argument(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([flag, "x", "-t", "t.yaml", "--dry-run"])

    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err
    assert "required flag" not in err


@pytest.mark.parametrize("flag", ["-d", "--dir"])
def test_dir_with_only_tasks_yaml_dry_runs(
    flag: str, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([flag, str(folder), "--dry-run", "--no-tui"]) == 0
    assert f"{folder / 'tasks.yaml'} is valid" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_claude")
def test_dir_with_only_tasks_yaml_runs(folder: Path) -> None:
    assert cli.main(["-d", str(folder), "--skip-permissions"]) == 0


def test_both_permission_flags_are_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        [
            "-t",
            "t.yaml",
            "--sandbox-settings",
            "s.json",
            "--skip-permissions",
        ]
    )

    assert code == 1
    assert capsys.readouterr().err == f"error: {BOTH_PERMISSIONS}\n"


def test_a_real_run_needs_a_permission_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["-t", "t.yaml"]) == 1
    assert capsys.readouterr().err == f"error: {NO_PERMISSIONS}\n"


def test_log_dir_is_refused_on_a_real_run(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["-t", "t.yaml", "--skip-permissions", "--log-dir", "logs"])

    assert code == 1
    assert (
        "error: --log-dir only works with the full-screen view"
        in capsys.readouterr().err
    )


def test_empty_gate_timeout_is_bad(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["-t", "t.yaml", "--dry-run", "--gate-timeout", ""]) == 1
    assert f"error: {BAD_DURATION}" in capsys.readouterr().err


# gralph checks --dir, then --gate-timeout, then the required flags.
def test_startup_checks_run_in_gralphs_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(tmp_path), "--gate-timeout", "30"]) == 1
    assert capsys.readouterr().err == f"error: --dir: no tasks.yaml in {tmp_path}\n"

    assert cli.main(["--gate-timeout", "30"]) == 1
    assert capsys.readouterr().err == f"error: {BAD_DURATION}\n"


def test_a_run_error_is_one_error_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.yaml"

    assert cli.main(["-t", str(missing), "--dry-run"]) == 1

    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert err.count("\n") == 1


def test_install_skill_is_not_a_flag() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--install-skill"])

    assert exit_info.value.code == 2


def test_flag_prefixes_are_not_expanded() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--dry", "-t", "t.yaml"])


def test_a_stop_with_no_task_running_is_one_error_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def stopped() -> None:
        raise asyncio.CancelledError

    assert cli._run(stopped()) == 1
    assert capsys.readouterr().err == "error: interrupted\n"


def test_sigint_during_the_startup_checks_is_one_error_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(*_: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "resolve_dir", interrupted)

    assert cli.main(["-t", "t.yaml", "--dry-run"]) == 1
    assert capsys.readouterr().err == "error: interrupted\n"


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.usefixtures("fake_claude")
def test_a_signal_leaves_no_traceback_and_no_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: signal.Signals
) -> None:
    ready = tmp_path / "ready"
    tasks_file = tmp_path / "tasks.yaml"
    tasks_file.write_text(ONE_TASK_YAML, encoding="utf-8")
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    monkeypatch.setenv("FAKECLAUDE_READY_FILE", str(ready))

    process = start_alr("-t", str(tasks_file), "--skip-permissions")
    try:
        claude_pid = int(wait_for_file(ready))
        process.send_signal(stop)
        _, err = process.communicate(timeout=10)
    finally:
        process.kill()

    assert process.returncode == 1
    assert err == "error: loop error: task 1: a: stopped\n"
    assert tasks_file.read_text(encoding="utf-8") == ONE_TASK_YAML
    with pytest.raises(ProcessLookupError):
        os.kill(claude_pid, 0)


# git cleans up after itself on SIGTERM, so a commit is stopped gently: SIGTERM,
# then SIGKILL after a grace period. An impatient second Ctrl-C during that wait
# must not end alr early and leave git or its hook running.
@pytest.mark.usefixtures("fake_claude")
def test_a_second_signal_does_not_leave_git_running(
    git_repo: Path, tmp_path: Path
) -> None:
    ready = tmp_path / "ready"
    tasks_file = git_repo / "run" / "tasks.yaml"
    tasks_yaml = (
        "shared:\n  prompt: shared\n  gates:\n    - cmd: echo work > one.txt\n"
        "tasks:\n  - id: 1\n    name: a\n    prompt: p\n"
    )
    tasks_file.write_text(tasks_yaml, encoding="utf-8")
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    # exec, so the PID in the ready file is the process that ignores SIGTERM.
    hook.write_text(f"#!/bin/sh\ntrap '' TERM\necho $$ > '{ready}'\nexec sleep 60\n")
    hook.chmod(0o700)

    process = start_alr("-t", str(tasks_file), "--skip-permissions", "--commit")
    hook_pid = 0
    try:
        hook_pid = int(wait_for_file(ready))
        process.send_signal(signal.SIGINT)
        time.sleep(0.2)
        process.send_signal(signal.SIGINT)
        _, err = process.communicate(timeout=10)
        hook_still_runs = not gone_within(hook_pid, proc._GENTLE_GRACE / 2)
    finally:
        process.kill()
        if hook_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(hook_pid, signal.SIGKILL)

    assert not hook_still_runs, "alr exited while the commit hook was still running"
    assert process.returncode == 1
    assert err == "error: loop error: task 1: a: stopped during commit\n"
    assert tasks_file.read_text(encoding="utf-8") == tasks_yaml


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture the session flags a real run starts with, without running it."""
    seen: list[list[str]] = []

    async def fake_start(
        tasks_file: str, gate_timeout: str, session: list[str], commit: bool
    ) -> None:
        seen.append(session)

    monkeypatch.setattr(cli, "start", fake_start)
    return seen


def _add_sandbox(folder: Path, content: str = "{}") -> Path:
    path = folder / "sandbox.json"
    path.write_text(content, encoding="utf-8")
    return path


def test_the_folders_sandbox_json_sandboxes_the_run(
    folder: Path, sessions: list[list[str]]
) -> None:
    _add_sandbox(folder)

    assert cli.main(["-d", str(folder)]) == 0
    assert sessions == [SANDBOXED]


def test_the_folders_sandbox_json_still_applies_with_tasks(
    folder: Path, sessions: list[list[str]]
) -> None:
    _add_sandbox(folder)
    tasks = folder / "tasks.yaml"

    assert cli.main(["-d", str(folder), "-t", str(tasks)]) == 0
    assert sessions == [SANDBOXED]


def test_sandbox_settings_flag_wins_over_the_folder(
    folder: Path, tmp_path: Path, sessions: list[list[str]]
) -> None:
    _add_sandbox(folder, '{"permissions": {"deny": ["WebFetch"]}}')
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--sandbox-settings", str(other)]) == 0
    assert sessions == [SANDBOXED]


def test_skip_permissions_opts_out_of_the_folders_sandbox(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _add_sandbox(folder)

    assert cli.main(["-d", str(folder), "--skip-permissions"]) == 0
    assert sessions == [["--dangerously-skip-permissions"]]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("content", ["[]"])
def test_a_bad_folder_file_fails_and_never_runs_unsandboxed(
    content: str,
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _add_sandbox(folder, content)

    assert cli.main(["-d", str(folder)]) == 1
    assert sessions == []
    assert "error: --sandbox-settings: must be a JSON object" in capsys.readouterr().err


def test_an_unreadable_folder_file_fails(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _add_sandbox(folder).chmod(0)
    if os.access(folder / "sandbox.json", os.R_OK):
        pytest.skip("permissions are not enforced for this user")

    assert cli.main(["-d", str(folder)]) == 1
    assert sessions == []
    assert "is not accessible" in capsys.readouterr().err


def test_no_folder_file_keeps_the_neither_flag_error(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    (folder / "sandbox.json").mkdir()

    assert cli.main(["-d", str(folder)]) == 1
    assert sessions == []
    assert capsys.readouterr().err == f"error: {NO_PERMISSIONS}\n"


def test_tasks_alone_ignores_a_neighboring_sandbox_json(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _add_sandbox(folder)

    assert cli.main(["-t", str(folder / "tasks.yaml")]) == 1
    assert sessions == []
    assert capsys.readouterr().err == f"error: {NO_PERMISSIONS}\n"


def test_dry_run_names_the_folders_sandbox_json(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _add_sandbox(folder)

    assert cli.main(["-d", str(folder), "--dry-run"]) == 0
    assert f"sandbox settings: {folder}/sandbox.json" in capsys.readouterr().out
