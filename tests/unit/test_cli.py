"""The command line: flags, startup checks in gralph's order, routing, and stopping on a signal."""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from alr import cli
from alr.engine import proc
from alr.errors import AlrError, SandboxError, UsageError

ONE_TASK_YAML = "tasks:\n  - id: 1\n    name: a\n    prompt: p\n"
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
        pytest.param(False, False, SANDBOXED, id="sandbox file"),
        pytest.param(False, True, SANDBOXED, id="dry run, sandbox file"),
    ],
)
def test_session_args_sandbox_file(
    settings: str, skip: bool, dry_run: bool, want: list[str]
) -> None:
    assert cli.session_args(settings, skip, dry_run) == want


@pytest.mark.parametrize(
    ("skip", "dry_run", "want"),
    [
        pytest.param(True, False, ["--dangerously-skip-permissions"], id="skip"),
        pytest.param(False, True, [], id="dry run, neither"),
    ],
)
def test_session_args_no_file(skip: bool, dry_run: bool, want: list[str]) -> None:
    assert cli.session_args("", skip, dry_run) == want


@pytest.mark.parametrize(
    ("file", "skip", "dry_run", "error", "want"),
    [
        pytest.param(
            "sandbox.json", True, False, UsageError, BOTH_PERMISSIONS, id="both"
        ),
        pytest.param("", False, False, UsageError, NO_PERMISSIONS, id="neither"),
        pytest.param(
            "sandbox.json", True, True, UsageError, BOTH_PERMISSIONS, id="dry run, both"
        ),
        pytest.param(
            "missing.json",
            False,
            False,
            SandboxError,
            "--sandbox-settings: file",
            id="bad file",
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


def test_check_gate_timeout_valid() -> None:
    cli.check_gate_timeout("2m")


@pytest.mark.parametrize(
    ("value", "want"),
    [
        pytest.param("30", BAD_DURATION, id="unitless"),
        pytest.param("", BAD_DURATION, id="empty"),
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
        pytest.param("", False, id="not passed, plain"),
        pytest.param("logs", True, id="dry run ignores it"),
    ],
)
def test_check_log_dir_accepted(log_dir: str, dry_run: bool) -> None:
    cli.check_log_dir(log_dir, dry_run)


def test_check_log_dir_plain() -> None:
    with pytest.raises(UsageError) as caught:
        cli.check_log_dir("logs", False)

    assert str(caught.value) == "--log-dir only works with the full-screen view"


@pytest.fixture
def full(tmp_path: Path) -> Path:
    """A run folder holding both files."""
    folder = tmp_path / "full"
    folder.mkdir()
    (folder / "tasks.yaml").write_text("x", encoding="utf-8")
    (folder / "prompt.md").write_text("x", encoding="utf-8")
    return folder


@pytest.fixture
def no_prompt(tmp_path: Path) -> Path:
    """A run folder holding only the task file."""
    folder = tmp_path / "no_prompt"
    folder.mkdir()
    (folder / "tasks.yaml").write_text("x", encoding="utf-8")
    return folder


def test_resolve_dir_no_folder_leaves_the_flags_alone() -> None:
    assert cli.resolve_dir("", "a.yaml", "b.md", False) == ("a.yaml", "b.md")


def test_resolve_dir_folder_fills_both(full: Path) -> None:
    want = (str(full / "tasks.yaml"), str(full / "prompt.md"))

    assert cli.resolve_dir(str(full), "", "", False) == want


def test_resolve_dir_flag_wins_over_the_folder(full: Path) -> None:
    want = ("other.yaml", str(full / "prompt.md"))

    assert cli.resolve_dir(str(full), "other.yaml", "", False) == want


def test_resolve_dir_missing_tasks_yaml(tmp_path: Path) -> None:
    with pytest.raises(UsageError) as caught:
        cli.resolve_dir(str(tmp_path), "", "", False)

    assert str(caught.value) == f"--dir: no tasks.yaml in {tmp_path}"


def test_resolve_dir_missing_prompt_md(no_prompt: Path) -> None:
    with pytest.raises(UsageError) as caught:
        cli.resolve_dir(str(no_prompt), "", "", False)

    assert str(caught.value) == f"--dir: no prompt.md in {no_prompt}"


def test_resolve_dir_dry_run_needs_no_prompt_md(no_prompt: Path) -> None:
    want = (str(no_prompt / "tasks.yaml"), "")

    assert cli.resolve_dir(str(no_prompt), "", "", True) == want


def test_resolve_dir_a_flag_fills_the_missing_file(no_prompt: Path) -> None:
    want = (str(no_prompt / "tasks.yaml"), "p.md")

    assert cli.resolve_dir(str(no_prompt), "", "p.md", False) == want


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
    assert "error: required flag --prompt not set" in err
    assert "error: required flag --tasks not set" in err


def test_dry_run_needs_no_prompt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    tasks_file.write_text(ONE_TASK_YAML, encoding="utf-8")

    assert cli.main(["-t", str(tasks_file), "--dry-run"]) == 0
    assert f"{tasks_file} is valid" in capsys.readouterr().out


def test_short_d_means_dir(full: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (full / "tasks.yaml").write_text(ONE_TASK_YAML, encoding="utf-8")

    assert cli.main(["-d", str(full), "--dry-run", "--no-tui"]) == 0
    assert f"{full / 'tasks.yaml'} is valid" in capsys.readouterr().out


def test_both_permission_flags_are_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        [
            "-t",
            "t.yaml",
            "-p",
            "p.md",
            "--sandbox-settings",
            "s.json",
            "--skip-permissions",
        ]
    )

    assert code == 1
    assert capsys.readouterr().err == f"error: {BOTH_PERMISSIONS}\n"


def test_a_real_run_needs_a_permission_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["-t", "t.yaml", "-p", "p.md"]) == 1
    assert capsys.readouterr().err == f"error: {NO_PERMISSIONS}\n"


def test_log_dir_is_refused_on_a_real_run(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        ["-t", "t.yaml", "-p", "p.md", "--skip-permissions", "--log-dir", "logs"]
    )

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
    def interrupted(*_: object) -> tuple[str, str]:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "resolve_dir", interrupted)

    assert cli.main(["-t", "t.yaml", "--dry-run"]) == 1
    assert capsys.readouterr().err == "error: interrupted\n"


def _start_alr(*args: str) -> subprocess.Popen[str]:
    """Start the real command, as a user's shell would."""
    alr = Path(sys.executable).parent / "alr"
    return subprocess.Popen(
        [str(alr), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_text(path: Path) -> str:
    """Wait for a child to write something to path and return it."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        text = path.read_text().strip() if path.exists() else ""
        if text:
            return text

        time.sleep(0.01)

    pytest.fail(f"{path.name} was never written")


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.usefixtures("fake_claude")
def test_a_signal_leaves_no_traceback_and_no_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: signal.Signals
) -> None:
    ready = tmp_path / "ready"
    tasks_file = tmp_path / "tasks.yaml"
    tasks_file.write_text(ONE_TASK_YAML, encoding="utf-8")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("shared", encoding="utf-8")
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    monkeypatch.setenv("FAKECLAUDE_READY_FILE", str(ready))

    process = _start_alr("-t", str(tasks_file), "-p", str(prompt), "--skip-permissions")
    try:
        claude_pid = int(_wait_for_text(ready))
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
        "gates:\n  - cmd: echo work > one.txt\n"
        "tasks:\n  - id: 1\n    name: a\n    prompt: p\n"
    )
    tasks_file.write_text(tasks_yaml, encoding="utf-8")
    prompt = git_repo / "run" / "prompt.md"
    prompt.write_text("shared", encoding="utf-8")
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    # exec, so the PID in the ready file is the process that ignores SIGTERM.
    hook.write_text(f"#!/bin/sh\ntrap '' TERM\necho $$ > '{ready}'\nexec sleep 60\n")
    hook.chmod(0o700)

    process = _start_alr(
        "-t", str(tasks_file), "-p", str(prompt), "--skip-permissions", "--commit"
    )
    hook_pid = 0
    try:
        hook_pid = int(_wait_for_text(ready))
        process.send_signal(signal.SIGINT)
        time.sleep(0.2)
        process.send_signal(signal.SIGINT)
        _, err = process.communicate(timeout=10)
        hook_still_runs = _is_running(hook_pid)
    finally:
        process.kill()
        if hook_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(hook_pid, signal.SIGKILL)

    assert not hook_still_runs, "alr exited while the commit hook was still running"
    assert process.returncode == 1
    assert err == "error: loop error: task 1: a: stopped during commit\n"
    assert tasks_file.read_text(encoding="utf-8") == tasks_yaml


def _is_running(pid: int) -> bool:
    """Report whether pid is alive, allowing a killed process a moment to be reaped."""
    deadline = time.monotonic() + proc._GENTLE_GRACE / 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False

        time.sleep(0.01)

    return True
