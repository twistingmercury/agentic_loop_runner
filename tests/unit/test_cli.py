"""The command line: flags, startup checks in gralph's order, routing, and stopping on a signal."""

import asyncio
import contextlib
import os
import platform
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from helpers import gone_within, start_alr, wait_for_file

from alr import cli
from alr.engine import proc
from alr.engine.commit import CommitMode
from alr.engine.stop import Stop
from alr.errors import AlrError, RefusedError, RunCancelledError, UsageError
from alr.tui.settings import Permissions, RunSettings

ONE_TASK_YAML = (
    "shared:\n  prompt: shared\ntasks:\n  - id: 1\n    name: a\n    prompt: p\n"
)
SANDBOXED = [
    "--permission-mode",
    "acceptEdits",
    "--settings",
    '{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"failIfUnavailable":true}}',
]
BOTH_PERMISSIONS = "--sandbox-settings and --yolo cannot be used together"
NO_PERMISSIONS = "pass --sandbox-settings <path>, or --yolo to run without a sandbox"
BAD_DURATION = "--gate-timeout: must be a duration string such as 90s or 10m"
GATE_TIMEOUT_FLAG = "--gate-timeout"


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
            RefusedError,
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
        pytest.param("0s", GATE_TIMEOUT_FLAG, id="zero"),
        pytest.param("-5m", GATE_TIMEOUT_FLAG, id="negative"),
    ],
)
def test_check_gate_timeout_errors(value: str, want: str) -> None:
    with pytest.raises(UsageError) as caught:
        cli.check_gate_timeout(value)

    assert want in str(caught.value)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A run folder holding only the task file."""
    path = tmp_path / "run"
    path.mkdir()
    (path / "tasks.yaml").write_text(ONE_TASK_YAML, encoding="utf-8")
    return path


def test_resolve_dir_without_a_folder_gives_no_path() -> None:
    assert cli.resolve_dir("") == ""


def test_resolve_dir_names_the_folders_task_file(folder: Path) -> None:
    assert cli.resolve_dir(str(folder)) == str(folder / "tasks.yaml")


def test_version_prints_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("alr version: ")
    assert out.count("\n") == 1
    assert re.fullmatch(r"alr version: \S+ [a-z]+/\S+\n", out)


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [("Linux", "x86_64", "linux/x86_64"), ("Darwin", "arm64", "darwin/arm64")],
)
def test_version_line_shows_os_and_arch(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str, expected: str
) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)

    assert cli._version_line().endswith(f" {expected}")


def test_missing_required_flags_are_each_named(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([]) == 2

    err = capsys.readouterr().err
    assert err.startswith("error: required flag --dir not set\n")
    assert err.count("required flag") == 1


def test_a_bad_flag_value_exits_2(folder: Path) -> None:
    assert cli.main(["--plain", "--gate-timeout", "soon", "-d", str(folder)]) == 2


def test_a_missing_dir_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--plain"]) == 2
    assert capsys.readouterr().err.startswith("error: required flag --dir not set\n")


@pytest.mark.parametrize("flag", ["-p", "--prompt"])
def test_prompt_flag_is_an_unknown_argument(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([flag, "x", "--dry-run"])

    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err
    assert "required flag" not in err


def test_no_tui_flag_is_an_unknown_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--no-tui", "--dry-run"])

    assert exit_info.value.code == 2
    assert "unrecognized arguments: --no-tui" in capsys.readouterr().err


def test_skip_permissions_flag_is_an_unknown_argument(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["-d", str(folder), "--skip-permissions"])

    assert exit_info.value.code == 2
    assert "unrecognized arguments: --skip-permissions" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["-t", "--tasks"])
def test_tasks_flag_is_an_unknown_argument(
    flag: str, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([flag, str(folder / "tasks.yaml"), "--dry-run"])

    assert exit_info.value.code == 2
    assert f"unrecognized arguments: {flag}" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["-d", "--dir"])
def test_dir_with_only_tasks_yaml_dry_runs(
    flag: str, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([flag, str(folder), "--dry-run", "--plain"]) == 0
    assert f"{folder / 'tasks.yaml'} is valid" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_claude")
def test_dir_with_only_tasks_yaml_runs(folder: Path) -> None:
    assert cli.main(["-d", str(folder), "--yolo"]) == 0


def test_both_permission_flags_are_refused(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [
            "-d",
            str(folder),
            "--sandbox-settings",
            "s.json",
            "--yolo",
        ]
    )

    assert code == 2
    assert capsys.readouterr().err == f"error: {BOTH_PERMISSIONS}\n"


def test_a_real_run_needs_a_permission_flag(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(folder)]) == 2
    assert capsys.readouterr().err == f"error: {NO_PERMISSIONS}\n"


def test_log_dir_flag_is_an_unknown_argument(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["-d", str(folder), "--yolo", "--log-dir", "logs"])

    assert exit_info.value.code == 2
    assert "unrecognized arguments: --log-dir" in capsys.readouterr().err


def test_empty_gate_timeout_is_bad(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(folder), "--dry-run", "--gate-timeout", ""]) == 2
    assert f"error: {BAD_DURATION}" in capsys.readouterr().err


# gralph checks --dir, then --gate-timeout, then the required flags.
def test_startup_checks_run_in_gralphs_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(tmp_path), "--gate-timeout", "30"]) == 2
    assert capsys.readouterr().err == f"error: --dir: no tasks.yaml in {tmp_path}\n"

    assert cli.main(["--gate-timeout", "30"]) == 2
    assert capsys.readouterr().err == f"error: {BAD_DURATION}\n"


def test_a_run_error_is_one_error_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A task file that cannot be read: a folder where tasks.yaml should be.
    (tmp_path / "tasks.yaml").mkdir()

    assert cli.main(["-d", str(tmp_path), "--dry-run"]) == 3

    err = capsys.readouterr().err
    assert err.startswith("error: refused: invalid-task-file: ")
    assert err.count("\n") == 1


def test_flag_prefixes_are_not_expanded() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--dry", "-d", "x"])


def test_a_stop_with_no_task_running_is_one_error_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def stopped() -> None:
        raise asyncio.CancelledError

    assert cli._run(stopped(), Stop()) == 130
    assert capsys.readouterr().err == "error: interrupted\n"


@pytest.mark.parametrize(("sig", "code"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_a_signal_sets_the_exit_code(sig: signal.Signals, code: int) -> None:
    async def stopped_by() -> None:
        os.kill(os.getpid(), sig)
        await asyncio.sleep(60)

    assert cli._run(stopped_by(), Stop()) == code


def test_only_the_first_signal_sets_the_exit_code() -> None:
    async def stopped_twice() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.sleep(60)

    stop = Stop()

    assert cli._run(stopped_twice(), stop) == 143
    assert stop.signum == signal.SIGTERM


def test_a_stop_inside_a_task_exits_with_the_signals_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def stopped_in_a_task() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise RunCancelledError("task 1: a: stopped") from None

    assert cli._run(stopped_in_a_task(), Stop()) == 143
    assert capsys.readouterr().err == "error: task 1: a: stopped\n"


def test_sigint_during_the_startup_checks_is_one_error_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(*_: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "resolve_dir", interrupted)

    assert cli.main(["-d", "x", "--dry-run"]) == 130
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

    process = start_alr("-d", str(tmp_path), "--yolo")
    try:
        claude_pid = int(wait_for_file(ready))
        process.send_signal(stop)
        _, err = process.communicate(timeout=10)
    finally:
        process.kill()

    assert process.returncode == 128 + stop
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

    process = start_alr("-d", str(tasks_file.parent), "--yolo", "--commit", "task")
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
    assert process.returncode == 130
    assert err == "error: loop error: task 1: a: stopped during commit\n"
    assert tasks_file.read_text(encoding="utf-8") == tasks_yaml


def _capture_start(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[list[str]], list[CommitMode]]:
    """Replace the real run with one that records its session flags and commit mode."""
    sessions: list[list[str]] = []
    modes: list[CommitMode] = []

    async def fake_start(
        tasks_file: str,
        gate_timeout: str,
        session: list[str],
        commit: CommitMode,
        reset_failed: bool,
        sandbox_settings: str = "",
        stop: Stop | None = None,
    ) -> None:
        sessions.append(session)
        modes.append(commit)

    monkeypatch.setattr(cli, "start", fake_start)
    return sessions, modes


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture the session flags a real run starts with, without running it."""
    seen, _ = _capture_start(monkeypatch)
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


def test_sandbox_settings_flag_wins_over_the_folder(
    folder: Path, tmp_path: Path, sessions: list[list[str]]
) -> None:
    _add_sandbox(folder, '{"permissions": {"deny": ["WebFetch"]}}')
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--sandbox-settings", str(other)]) == 0
    assert sessions == [SANDBOXED]


def test_yolo_opts_out_of_the_folders_sandbox(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _add_sandbox(folder)

    assert cli.main(["-d", str(folder), "--yolo"]) == 0
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

    assert cli.main(["-d", str(folder)]) == 3
    assert sessions == []
    first_line = capsys.readouterr().err.splitlines()[0]
    assert first_line.startswith("error: refused: invalid-sandbox-settings: ")
    assert "--sandbox-settings: must be a JSON object" in first_line


def test_an_unreadable_folder_file_fails(
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_sandbox(folder)
    read_text = Path.read_text

    def refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        # chmod 0 would not stop root, so the refusal is made here instead.
        if self.name == "sandbox.json":
            raise PermissionError(13, "Permission denied")

        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)

    assert cli.main(["-d", str(folder)]) == 3
    assert sessions == []
    err = capsys.readouterr().err
    assert err.startswith("error: refused: invalid-sandbox-settings: ")
    assert "is not accessible" in err


def test_no_folder_file_keeps_the_neither_flag_error(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    (folder / "sandbox.json").mkdir()

    assert cli.main(["-d", str(folder)]) == 2
    assert sessions == []
    assert capsys.readouterr().err == f"error: {NO_PERMISSIONS}\n"


def test_dry_run_names_the_folders_sandbox_json(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _add_sandbox(folder)

    assert cli.main(["-d", str(folder), "--dry-run"]) == 0
    assert f"sandbox settings: {folder}/sandbox.json" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("flags", "want"),
    [
        ([], CommitMode.NONE),
        (["--commit", "none"], CommitMode.NONE),
        (["--commit", "task"], CommitMode.TASK),
        (["--commit", "run"], CommitMode.RUN),
    ],
)
def test_commit_flag_maps_to_a_mode(flags: list[str], want: CommitMode) -> None:
    args = cli._parser().parse_args(flags)

    assert CommitMode(args.commit) is want


@pytest.mark.parametrize(
    "flags",
    [["--commit-after-all"], ["--commit"], ["--commit", "sometimes"]],
    ids=["old flag", "bare", "bad value"],
)
def test_bad_commit_forms_are_usage_errors(flags: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["--yolo", *flags])

    assert caught.value.code == 2


def test_a_bad_commit_value_lists_the_choices(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--commit", "sometimes"])

    assert "(choose from none, task, run)" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flags", "want"),
    [
        ([], CommitMode.NONE),
        (["--commit", "task"], CommitMode.TASK),
        (["--commit", "run"], CommitMode.RUN),
    ],
)
def test_plain_start_gets_the_commit_mode(
    flags: list[str], want: CommitMode, monkeypatch: pytest.MonkeyPatch, folder: Path
) -> None:
    _, modes = _capture_start(monkeypatch)

    assert cli.main(["-d", str(folder), "--yolo", "--plain", *flags]) == 0
    assert modes == [want]


def test_dry_run_with_commit_task_reports_the_commit_checks(
    git_repo: Path, outside: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (outside / "tasks.yaml").write_text(ONE_TASK_YAML, encoding="utf-8")

    assert cli.main(["-d", str(outside), "--dry-run", "--commit", "task"]) == 0
    assert f"commit: {git_repo}" in capsys.readouterr().out


def test_dry_run_commit_run_with_an_invalid_task_file_is_refused_once(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = git_repo / "run"
    (folder / "tasks.yaml").write_text("tasks: not-a-list\n", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--dry-run", "--commit", "run"]) == 3

    err = capsys.readouterr().err
    assert err.startswith("error: refused: invalid-task-file: ")
    assert err.count("failed to parse tasks yaml") == 1
    assert err.count("\n") == 1


def test_dry_run_commit_run_on_a_dirty_tree_is_refused(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = git_repo / "run"
    (folder / "tasks.yaml").write_text(ONE_TASK_YAML, encoding="utf-8")
    (git_repo / "stray.txt").write_text("stray\n", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--dry-run", "--commit", "run"]) == 3

    err = capsys.readouterr().err
    assert err.startswith(
        "error: refused: dirty-tree: --commit needs a clean work tree"
    )


def test_dry_run_without_commit_does_not_consult_git(
    git_repo: Path, outside: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (outside / "tasks.yaml").write_text(ONE_TASK_YAML, encoding="utf-8")

    assert cli.main(["-d", str(outside), "--dry-run"]) == 0
    assert "commit:" not in capsys.readouterr().out


def test_help_shows_commit_and_not_commit_after_all(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    out = capsys.readouterr().out
    assert "--commit" in out
    assert "--commit-after-all" not in out


@pytest.mark.parametrize(
    ("flags", "tty", "want"),
    [
        ([], True, True),
        ([], False, False),
        (["--plain"], True, False),
        (["--dry-run"], True, False),
    ],
    ids=["terminal", "no terminal", "--plain", "--dry-run"],
)
def test_wants_tui(
    flags: list[str], tty: bool, want: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)

    assert cli.wants_tui(cli._parser().parse_args(flags)) is want


def _capture_view(monkeypatch: pytest.MonkeyPatch) -> list[RunSettings]:
    seen: list[RunSettings] = []

    def run_app(settings: RunSettings) -> int:
        seen.append(settings)
        return 0

    monkeypatch.setattr("alr.cli.wants_tui", lambda args: True)
    monkeypatch.setattr("alr.cli.run_app", run_app)
    return seen


def test_the_view_gets_the_resolved_flags(
    monkeypatch: pytest.MonkeyPatch, folder: Path
) -> None:
    seen = _capture_view(monkeypatch)
    _add_sandbox(folder)

    code = cli.main(["-d", str(folder), "--commit", "run", "--gate-timeout", "5m"])

    assert code == 0
    assert seen[0].tasks_file == str(folder / "tasks.yaml")
    assert seen[0].permissions is Permissions.SANDBOX
    assert seen[0].sandbox_file == str(folder / "sandbox.json")
    assert seen[0].commit_mode is CommitMode.RUN
    assert seen[0].gate_timeout == "5m"


def test_the_view_defaults_to_no_commit_and_takes_skip(
    monkeypatch: pytest.MonkeyPatch, folder: Path
) -> None:
    seen = _capture_view(monkeypatch)

    code = cli.main(["-d", str(folder), "--yolo"])

    assert code == 0
    assert seen[0].permissions is Permissions.SKIP
    assert seen[0].commit_mode is CommitMode.NONE


def test_the_view_asks_for_a_missing_dir_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture_view(monkeypatch)

    assert cli.main([]) == 0
    assert seen[0].tasks_file == ""
    assert seen[0].permissions is Permissions.UNSET


def test_the_view_still_refuses_both_permission_flags(
    monkeypatch: pytest.MonkeyPatch,
    folder: Path,
    settings: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen = _capture_view(monkeypatch)

    code = cli.main(["-d", str(folder), "--sandbox-settings", settings, "--yolo"])

    assert code == 2
    assert BOTH_PERMISSIONS in capsys.readouterr().err
    assert seen == []


def test_the_view_still_checks_a_given_settings_file(
    monkeypatch: pytest.MonkeyPatch,
    folder: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen = _capture_view(monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")

    code = cli.main(["-d", str(folder), "--sandbox-settings", str(bad)])

    assert code == 3
    assert capsys.readouterr().err.startswith(
        "error: refused: invalid-sandbox-settings: "
    )
    assert seen == []


def test_install_skill_prints_the_path_and_exits_0(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    code = cli.main(["--install-skill"])

    assert code == 0
    assert capsys.readouterr().out.strip() == str(
        tmp_path / ".claude" / "skills" / "gralph-docs-writer"
    )


def test_install_skill_reports_a_folder_it_cannot_write(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").write_text("a file, not a folder", encoding="utf-8")

    code = cli.main(["--install-skill"])

    assert code == 1
    assert capsys.readouterr().err.startswith("error: ")


@pytest.mark.parametrize("extra", [[], ["--plain"]], ids=["view", "plain"])
def test_reset_failed_with_dry_run_is_a_usage_error(
    folder: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    code = cli.main(["-d", str(folder), "--dry-run", "--reset-failed", *extra])

    assert code == 2
    assert capsys.readouterr().err == (
        "error: --reset-failed cannot be used with --dry-run (a dry run writes nothing)\n"
    )


@pytest.mark.usefixtures("fake_claude")
def test_a_plain_run_with_reset_failed_runs_a_failed_task(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks = folder / "tasks.yaml"
    tasks.write_text(
        ONE_TASK_YAML.replace("prompt: p\n", "prompt: p\n    state: failed\n"),
        encoding="utf-8",
    )

    code = cli.main(["-d", str(folder), "--yolo", "--reset-failed"])

    assert code == 0
    assert "failed" not in capsys.readouterr().out
    assert "state: completed" in tasks.read_text(encoding="utf-8")


def test_a_plain_run_without_reset_failed_still_refuses_a_failed_task(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (folder / "tasks.yaml").write_text(
        ONE_TASK_YAML.replace("prompt: p\n", "prompt: p\n    state: failed\n"),
        encoding="utf-8",
    )

    code = cli.main(["-d", str(folder), "--yolo"])

    assert code == 3
    assert "Some tasks failed previous runs:" in capsys.readouterr().out


def test_the_view_gets_the_reset_failed_flag(
    monkeypatch: pytest.MonkeyPatch, folder: Path
) -> None:
    seen = _capture_view(monkeypatch)

    cli.main(["-d", str(folder), "--yolo", "--reset-failed"])

    assert seen[0].reset_failed is True


@pytest.mark.parametrize(
    ("placeholder", "usage_token", "help_option"),
    [
        ("DIR", "[-d]", "-d, --dir "),
        ("GATE_TIMEOUT", "[--gate-timeout]", "--gate-timeout "),
        ("SANDBOX_SETTINGS", "[--sandbox-settings]", "--sandbox-settings "),
    ],
)
def test_value_flags_show_no_placeholder_in_the_help(
    placeholder: str, usage_token: str, help_option: str
) -> None:
    parser = cli._parser()

    usage = parser.format_usage()
    help_text = parser.format_help()

    assert placeholder not in usage
    assert placeholder not in help_text
    assert usage_token in usage
    assert help_option in help_text
