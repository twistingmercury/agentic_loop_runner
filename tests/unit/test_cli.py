"""The command line: flags, startup checks in gralph's order, routing, and stopping on a signal."""

import asyncio
import json
import os
import platform
import re
import shutil
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import pytest
from helpers import chmodded, start_alr, wait_for_file

from alr import cli
from alr.config import SandboxDefault
from alr.engine.sandbox import sandbox_args
from alr.engine.stop import Stop
from alr.errors import AlrError, RefusedError, RunCancelledError, UsageError
from alr.skillinstall import sync
from alr.tui.settings import Permissions, RunSettings

ONE_TASK_YAML = (
    "shared:\n  prompt: shared\ntasks:\n  - id: 1\n    name: a\n    prompt: p\n"
)
# ONE_TASK_YAML, but with a prompt that passes every dry-run rule, for the test
# that checks the dry run's printed lines.
CLEAN_ONE_TASK_YAML = """shared:
  prompt: shared
  after_task: []
tasks:
  - id: 1
    name: a
    prompt: |
      Do it.

      Completion criteria:
      - it is done

      Finish:
      Reply in one line.
"""
SANDBOXED = [
    "--permission-mode",
    "acceptEdits",
    "--settings",
    '{"sandbox":{"enabled":true,"allowUnsandboxedCommands":false,"failIfUnavailable":true}}',
]
BOTH_PERMISSIONS = "--sandbox-settings and --yolo cannot be used together"
PRESET_CONFLICT = "--sandbox cannot be used with --sandbox-settings or --yolo"
NO_PERMISSIONS = (
    "pass --sandbox <name>, --sandbox-settings <path>, or --yolo "
    "to run without a sandbox; the sandboxes are: large, tight"
)
BAD_DURATION = "--action-timeout: must be a duration string such as 90s or 10m"
ACTION_TIMEOUT_FLAG = "--action-timeout"


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
    assert cli.session_args(cli.Answer(settings, skip, ""), dry_run) == want


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
            "--sandbox-settings {path}: is not accessible",
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
        cli.session_args(cli.Answer(path, skip, ""), dry_run)

    assert want.format(path=path) in str(caught.value)


# Go's TestSessionArgs_WizardLeavesTheChoiceOpen is not ported: alr has no wizard.


@pytest.mark.parametrize(
    ("value", "want"),
    [
        pytest.param("0s", ACTION_TIMEOUT_FLAG, id="zero"),
        pytest.param("-5m", ACTION_TIMEOUT_FLAG, id="negative"),
    ],
)
def test_check_action_timeout_errors(value: str, want: str) -> None:
    with pytest.raises(UsageError) as caught:
        cli.check_action_timeout(value)

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
    assert cli.main(["--plain", "--action-timeout", "soon", "-d", str(folder)]) == 2


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
    (folder / "tasks.yaml").write_text(CLEAN_ONE_TASK_YAML, encoding="utf-8")

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


def test_empty_action_timeout_is_bad(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(folder), "--dry-run", "--action-timeout", ""]) == 2
    assert f"error: {BAD_DURATION}" in capsys.readouterr().err


# gralph checks --dir, then --action-timeout, then the required flags.
def test_startup_checks_run_in_gralphs_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(tmp_path), "--action-timeout", "30"]) == 2
    assert capsys.readouterr().err == f"error: --dir: no tasks.yaml in {tmp_path}\n"

    assert cli.main(["--action-timeout", "30"]) == 2
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


def _capture_start(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace the real run with one that records its session flags."""
    sessions: list[list[str]] = []

    async def fake_start(
        tasks_file: str,
        action_timeout: str,
        session: list[str],
        reset_failed: bool,
        sandbox_settings: str = "",
        stop: Stop | None = None,
    ) -> None:
        sessions.append(session)

    monkeypatch.setattr(cli, "start", fake_start)
    return sessions


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture the session flags a real run starts with, without running it."""
    return _capture_start(monkeypatch)


def _add_sandbox(folder: Path, content: str = "{}") -> Path:
    """Put a sandbox.json in a run folder, where alr no longer looks."""
    path = folder / "sandbox.json"
    path.write_text(content, encoding="utf-8")
    return path


def _sandboxes(home: Path) -> Path:
    """The folder the sandbox files live in."""
    return home / ".local" / "alr" / "sandboxes"


def _write_sandbox(home: Path, name: str, content: str = "{}") -> Path:
    """Add a sandbox file of the user's own."""
    path = _sandboxes(home) / f"{name}.json"
    path.write_text(content, encoding="utf-8")
    return path


def _notice(folder: Path) -> str:
    """The line alr prints for a run folder that still holds a sandbox.json."""
    return f"{folder}/sandbox.json is no longer read; pick a sandbox with --sandbox <name>\n"


def _first_logged_event(folder: Path) -> dict[str, Any]:
    """The run_started event of the one run logged in a run folder."""
    (log,) = (folder / "logs").glob("*/run.jsonl")
    return json.loads(log.read_text(encoding="utf-8").splitlines()[0])


def test_a_folders_sandbox_json_is_not_read_and_the_notice_is_printed_once(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    # Not valid settings: reading it would end the run with exit 3.
    _add_sandbox(folder, "[]")

    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    assert sessions == [["--dangerously-skip-permissions"]]
    captured = capsys.readouterr()
    assert captured.out == _notice(folder)
    assert captured.err == ""


def test_a_folders_sandbox_json_no_longer_answers_the_permission_question(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _add_sandbox(folder)

    assert cli.main(["-d", str(folder)]) == 2
    assert sessions == []
    captured = capsys.readouterr()
    assert captured.err == f"error: {NO_PERMISSIONS}\n"
    assert captured.out == _notice(folder)


def test_a_folder_without_a_sandbox_json_prints_no_notice(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    assert capsys.readouterr().out == ""


def test_a_directory_named_sandbox_json_is_not_noticed(
    folder: Path, sessions: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    (folder / "sandbox.json").mkdir()

    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    assert capsys.readouterr().out == ""


def test_a_dry_run_prints_the_notice_and_names_no_sandbox(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _add_sandbox(folder)

    assert cli.main(["-d", str(folder), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.count("is no longer read") == 1
    assert "sandbox settings:" not in out


def test_a_named_sandbox_leaves_the_folders_sandbox_json_alone(
    folder: Path, record: Path
) -> None:
    mine = '{"mine": true}'
    _add_sandbox(folder, mine)

    assert cli.main(["-d", str(folder), "--plain", "--sandbox", "large"]) == 0
    assert (folder / "sandbox.json").read_text(encoding="utf-8") == mine


def test_sandbox_settings_flag_runs_the_given_file_and_not_the_folders(
    folder: Path, tmp_path: Path, sessions: list[list[str]]
) -> None:
    _add_sandbox(folder, '{"permissions": {"deny": ["WebFetch"]}}')
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--sandbox-settings", str(other)]) == 0
    assert sessions == [SANDBOXED]


@pytest.mark.parametrize("content", ["[]"])
def test_a_bad_named_sandbox_file_fails_and_never_runs_unsandboxed(
    content: str,
    home: Path,
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_sandbox(home, "my_sandbox", content)

    assert cli.main(["-d", str(folder), "--sandbox", "my_sandbox"]) == 3
    assert sessions == []
    first_line = capsys.readouterr().err.splitlines()[0]
    assert first_line == (
        "error: refused: invalid-sandbox-settings: "
        f"--sandbox my_sandbox: {path}: must be a JSON object"
    )


def test_an_unreadable_named_sandbox_file_fails(
    home: Path,
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_sandbox(home, "my_sandbox")
    read_text = Path.read_text

    def refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        # chmod 0 would not stop root, so the refusal is made here instead.
        if self.name == "my_sandbox.json":
            raise PermissionError(13, "Permission denied")

        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)

    assert cli.main(["-d", str(folder), "--sandbox", "my_sandbox"]) == 3
    assert sessions == []
    err = capsys.readouterr().err
    assert err == (
        "error: refused: invalid-sandbox-settings: "
        f"--sandbox my_sandbox: {path}: is not accessible: Permission denied\n"
    )


def test_a_real_run_names_the_sandboxes_when_no_flag_chose(
    home: Path,
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_sandbox(home, "my_sandbox")

    assert cli.main(["-d", str(folder)]) == 2
    assert sessions == []
    assert capsys.readouterr().err == (
        "error: pass --sandbox <name>, --sandbox-settings <path>, or --yolo "
        "to run without a sandbox; the sandboxes are: large, my_sandbox, tight\n"
    )


def test_dry_run_names_a_sandbox_settings_file_as_given(
    folder: Path, settings: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["-d", str(folder), "--dry-run", "--sandbox-settings", settings])

    assert code == 0
    assert f"sandbox settings: {settings}\n" in capsys.readouterr().out


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
    monkeypatch: pytest.MonkeyPatch, folder: Path, settings: str
) -> None:
    seen = _capture_view(monkeypatch)

    code = cli.main(
        ["-d", str(folder), "--sandbox-settings", settings, "--action-timeout", "5m"]
    )

    assert code == 0
    assert seen[0].tasks_file == str(folder / "tasks.yaml")
    assert seen[0].permissions is Permissions.SANDBOX
    assert seen[0].sandbox_file == settings
    assert seen[0].sandbox_name == ""
    assert seen[0].action_timeout == "5m"


def test_the_view_leaves_the_permissions_unset_beside_a_folders_sandbox_json(
    monkeypatch: pytest.MonkeyPatch, folder: Path
) -> None:
    seen = _capture_view(monkeypatch)
    _add_sandbox(folder)

    assert cli.main(["-d", str(folder)]) == 0
    assert seen[0].permissions is Permissions.UNSET
    assert seen[0].sandbox_file == ""


def test_the_view_takes_skip(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    seen = _capture_view(monkeypatch)

    code = cli.main(["-d", str(folder), "--yolo"])

    assert code == 0
    assert seen[0].permissions is Permissions.SKIP


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
        f"error: refused: invalid-sandbox-settings: --sandbox-settings {bad}: "
    )
    assert seen == []


def test_install_skill_is_an_unknown_flag() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--install-skill"])

    assert exit_info.value.code == 2


def _empty_skills_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Claude's config folder at one that does not exist yet and return its skills folder.

    The autouse home already holds the synced skills, so a skill test needs a
    skills folder somewhere else to see anything change.
    """
    config = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config / "skills"


def test_a_real_run_adds_the_skills_and_says_so(
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = _empty_skills_folder(tmp_path, monkeypatch)

    assert cli.main(["-d", str(folder), "--yolo"]) == 0

    captured = capsys.readouterr()
    task_skill = skills / "alr-task-file"
    review_skill = skills / "alr-task-review"
    assert captured.out == f"Added {task_skill}\nAdded {review_skill}\n"
    assert captured.err == ""
    assert (task_skill / "SKILL.md").is_file()
    assert (review_skill / "SKILL.md").is_file()


def test_a_second_real_run_says_nothing_about_skills(
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _empty_skills_folder(tmp_path, monkeypatch)
    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    capsys.readouterr()

    assert cli.main(["-d", str(folder), "--yolo"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_real_run_updates_an_edited_skill(
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = _empty_skills_folder(tmp_path, monkeypatch)
    sync()
    edited = skills / "alr-task-file" / "SKILL.md"
    bundled_text = edited.read_text(encoding="utf-8")
    edited.write_text("my own edit\n", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--yolo"]) == 0

    captured = capsys.readouterr()
    assert captured.out == f"Updated {skills / 'alr-task-file'}\n"
    assert captured.err == ""
    assert edited.read_text(encoding="utf-8") == bundled_text


def test_a_symlink_at_a_skill_name_is_replaced_with_a_warning_and_an_updated_line(
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = _empty_skills_folder(tmp_path, monkeypatch)
    skills.mkdir(parents=True)
    linked_target = tmp_path / "versioned-skill"
    linked_target.mkdir()
    (linked_target / "SKILL.md").write_text("mine\n", encoding="utf-8")
    link = skills / "alr-task-file"
    link.symlink_to(linked_target, target_is_directory=True)

    assert cli.main(["-d", str(folder), "--yolo"]) == 0

    captured = capsys.readouterr()
    warning = f"warning: {link} was a symlink; replaced it with the bundled skill"
    assert warning in captured.err.splitlines()
    assert f"Updated {link}" in captured.out.splitlines()
    assert not link.is_symlink()
    assert (link / "SKILL.md").is_file()
    assert (linked_target / "SKILL.md").read_text(encoding="utf-8") == "mine\n"


def test_a_plain_file_at_a_skill_name_is_replaced_with_a_warning_and_an_updated_line(
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = _empty_skills_folder(tmp_path, monkeypatch)
    skills.mkdir(parents=True)
    plain_file = skills / "alr-task-file"
    plain_file.write_text("not a folder", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--yolo"]) == 0

    captured = capsys.readouterr()
    warning = f"warning: {plain_file} was a file; replaced it with the bundled skill"
    assert warning in captured.err.splitlines()
    assert f"Updated {plain_file}" in captured.out.splitlines()
    assert (plain_file / "SKILL.md").is_file()


def test_dry_run_says_updated_for_a_differing_skill(
    folder: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = _empty_skills_folder(tmp_path, monkeypatch)
    sync()
    edited = skills / "alr-task-file" / "SKILL.md"
    edited.write_text("my own edit\n", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--dry-run", "--plain"]) == 0

    out = capsys.readouterr().out
    assert f"skill: {skills / 'alr-task-file'} (updated on a real run)\n" in out
    assert "alr-task-review" not in out
    assert edited.read_text(encoding="utf-8") == "my own edit\n"


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_dry_run_says_what_it_would_replace(
    folder: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    skills = _empty_skills_folder(tmp_path, monkeypatch)
    sync()
    target = skills / "alr-task-file"
    shutil.rmtree(target)
    linked_target = tmp_path / "versioned-skill"
    linked_target.mkdir()
    if kind == "symlink":
        target.symlink_to(linked_target, target_is_directory=True)
    else:
        target.write_text("not a folder", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--dry-run", "--plain"]) == 0

    captured = capsys.readouterr()
    assert f"skill: {target} (replaces a {kind} on a real run)\n" in captured.out
    assert captured.err == ""
    assert target.is_symlink() == (kind == "symlink")
    assert target.is_file() == (kind == "file")
    assert list(linked_target.iterdir()) == []


def test_the_skills_sync_before_the_task_file_is_resolved(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = _empty_skills_folder(tmp_path, monkeypatch)
    no_task_file = tmp_path / "empty-run"
    no_task_file.mkdir()

    code = cli.main(["-d", str(no_task_file), "--yolo"])

    assert code == 2
    assert "no tasks.yaml" in capsys.readouterr().err
    assert (skills / "alr-task-file" / "SKILL.md").is_file()
    assert (skills / "alr-task-review" / "SKILL.md").is_file()


def test_a_skills_folder_that_cannot_be_written_warns_and_the_run_goes_on(
    folder: Path,
    record: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "cfg"
    config.write_text("a file, not a folder", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    code = cli.main(["-d", str(folder), "--yolo"])

    err = capsys.readouterr().err
    assert "warning: skill alr-task-file: " in err
    assert "warning: skill alr-task-review: " in err
    assert record.is_file()
    # The run's own code: a warning must not turn a good run into a failure.
    assert code == 0


def test_dry_run_reports_the_skills_and_writes_nothing(
    folder: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (folder / "tasks.yaml").write_text(CLEAN_ONE_TASK_YAML, encoding="utf-8")
    skills = _empty_skills_folder(tmp_path, monkeypatch)

    assert cli.main(["-d", str(folder), "--dry-run", "--plain"]) == 0

    out = capsys.readouterr().out
    task_skill = skills / "alr-task-file"
    review_skill = skills / "alr-task-review"
    assert out.startswith(
        f"skill: {task_skill} (added on a real run)\n"
        f"skill: {review_skill} (added on a real run)\n"
    )
    assert not (tmp_path / "cfg").exists()


def test_dry_run_says_nothing_when_the_skills_are_identical(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (folder / "tasks.yaml").write_text(CLEAN_ONE_TASK_YAML, encoding="utf-8")

    assert cli.main(["-d", str(folder), "--dry-run", "--plain"]) == 0

    assert "skill:" not in capsys.readouterr().out


def test_version_does_not_sync(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _empty_skills_folder(tmp_path, monkeypatch)

    assert cli.main(["--version"]) == 0

    assert "Added" not in capsys.readouterr().out
    assert not (tmp_path / "cfg").exists()


@pytest.mark.parametrize("extra", [[], ["--plain"]], ids=["view", "plain"])
def test_reset_failed_with_dry_run_is_a_usage_error(
    folder: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    code = cli.main(["-d", str(folder), "--dry-run", "--reset-failed", *extra])

    assert code == 2
    assert capsys.readouterr().err == (
        "error: --reset-failed cannot be used with --dry-run (a dry run writes nothing)\n"
    )


@pytest.mark.parametrize("extra", [[], ["--plain"]], ids=["view", "plain"])
def test_strict_without_dry_run_is_a_usage_error(
    folder: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    code = cli.main(["-d", str(folder), "--strict", *extra])

    assert code == 2
    assert capsys.readouterr().err == (
        "error: --strict can only be used with --dry-run\n"
    )


def test_the_strict_flag_is_in_the_parser() -> None:
    args = cli._parser().parse_args(["--dry-run", "--strict"])

    assert args.strict is True
    assert cli._parser().parse_args([]).strict is False
    assert "With --dry-run, refuse when the checks find a warning" in " ".join(
        cli._parser().format_help().split()
    )


def test_a_strict_dry_run_with_a_warning_exits_3(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["-d", str(folder), "--dry-run", "--strict", "--yolo"])

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err.startswith("error: refused: task-file-warnings: ")
    assert "is valid" not in captured.out


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
        ("ACTION_TIMEOUT", "[--action-timeout]", "--action-timeout "),
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


@pytest.mark.parametrize("value", ["5m", ""])
def test_the_old_gate_timeout_flag_is_refused_naming_its_replacement(
    folder: Path, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    code = cli.main(["-d", str(folder), "--dry-run", "--gate-timeout", value])

    assert code == 2
    assert (
        capsys.readouterr().err
        == "error: --gate-timeout: renamed to --action-timeout\n"
    )


def test_the_old_gate_timeout_flag_is_refused_before_any_other_check(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--gate-timeout", "5m", "--version"]) == 2
    assert "renamed to --action-timeout" in capsys.readouterr().err


def test_commit_flag_is_refused_with_the_replacement(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["--plain", "-d", str(folder), "--yolo", "--commit", "task"])

    assert code == 2
    err = capsys.readouterr().err
    assert "--commit: removed; a commit is an action now" in err
    assert "shared.after_task" in err


def test_commit_flag_is_refused_before_any_other_check(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--commit", "run", "--version"]) == 2
    assert "--commit: removed" in capsys.readouterr().err


def test_commit_flag_is_hidden_from_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    assert "--commit" not in capsys.readouterr().out


def test_the_old_gate_timeout_flag_is_hidden_from_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    out = capsys.readouterr().out
    assert "--gate-timeout" not in out
    assert "--action-timeout" in out


@pytest.mark.parametrize("other", ["--sandbox-settings=x.json", "--yolo"])
def test_sandbox_with_another_permission_flag_exits_2(
    other: str, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(folder), "--sandbox", "tight", other]) == 2
    assert capsys.readouterr().err == f"error: {PRESET_CONFLICT}\n"


def test_a_conflict_is_reported_before_an_unknown_name(
    folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["-d", str(folder), "--sandbox", "huge", "--yolo"]) == 2
    assert capsys.readouterr().err == f"error: {PRESET_CONFLICT}\n"


def test_an_unknown_sandbox_name_exits_2_listing_the_names(
    home: Path, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_sandbox(home, "my_sandbox")

    assert cli.main(["-d", str(folder), "--sandbox", "huge"]) == 2
    assert capsys.readouterr().err == (
        "error: --sandbox: no sandbox named huge; "
        "the sandboxes are: large, my_sandbox, tight\n"
    )


@pytest.mark.parametrize(
    "name", ["yolo", "custom", "Tight", "tight.json", "../sandboxes/tight", ""]
)
def test_a_name_that_is_not_listed_exits_2(
    name: str,
    home: Path,
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_sandbox(home, "yolo")
    _write_sandbox(home, "custom")

    assert cli.main(["-d", str(folder), "--sandbox", name]) == 2
    assert sessions == []
    err = capsys.readouterr().err
    assert err.startswith(f"error: --sandbox: no sandbox named {name}; ")


def test_an_unknown_sandbox_name_exits_2_in_the_view_too(
    monkeypatch: pytest.MonkeyPatch, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _capture_view(monkeypatch)

    assert cli.main(["-d", str(folder), "--sandbox", "huge"]) == 2
    assert "no sandbox named huge" in capsys.readouterr().err
    assert seen == []


def _argv(record: Path) -> list[str]:
    """The flags the fake claude was started with, on its first call."""
    line = record.read_text(encoding="utf-8").splitlines()[0]
    return json.loads(line)["argv"]


@pytest.mark.parametrize("name", ["tight", "large"])
def test_sandbox_name_sandboxes_the_run_with_that_file(
    name: str, home: Path, folder: Path, record: Path
) -> None:
    path = _sandboxes(home) / f"{name}.json"
    want = sandbox_args(str(path))

    assert cli.main(["-d", str(folder), "--plain", "--sandbox", name]) == 0
    assert want == _argv(record)[1 : 1 + len(want)]


def test_a_sandbox_of_the_users_own_runs_with_its_settings_and_logs_its_path(
    home: Path, folder: Path, record: Path
) -> None:
    path = _write_sandbox(home, "my_sandbox", '{"permissions": {"deny": ["WebFetch"]}}')
    want = sandbox_args(str(path))

    assert cli.main(["-d", str(folder), "--plain", "--sandbox", "my_sandbox"]) == 0
    assert want == _argv(record)[1 : 1 + len(want)]
    assert '"WebFetch"' in " ".join(_argv(record))
    logged = _first_logged_event(folder)["sandbox_settings"]
    assert logged == str(path)
    assert Path(logged).is_absolute()
    assert not (folder / "sandbox.json").exists()


def test_a_sandbox_that_weakens_the_forced_keys_still_runs_sandboxed(
    home: Path, folder: Path, record: Path
) -> None:
    weak = {
        "sandbox": {
            "enabled": False,
            "allowUnsandboxedCommands": True,
            "failIfUnavailable": False,
        }
    }
    _write_sandbox(home, "weak", json.dumps(weak))

    assert cli.main(["-d", str(folder), "--plain", "--sandbox", "weak"]) == 0
    argv = _argv(record)
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["sandbox"] == {
        "enabled": True,
        "allowUnsandboxedCommands": False,
        "failIfUnavailable": True,
    }


@pytest.mark.usefixtures("fake_claude")
def test_a_relative_folder_with_spaces_logs_the_named_sandboxs_absolute_path(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run dir"
    run.mkdir()
    (run / "tasks.yaml").write_text(ONE_TASK_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["-d", "run dir", "--sandbox", "tight"]) == 0
    assert not (run / "sandbox.json").exists()
    logged = _first_logged_event(run)["sandbox_settings"]
    assert logged == str(_sandboxes(home) / "tight.json")


def test_dry_run_with_a_sandbox_name_names_the_file_and_writes_nothing(
    home: Path, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = sorted(path.name for path in _sandboxes(home).iterdir())

    assert cli.main(["-d", str(folder), "--dry-run", "--sandbox", "tight"]) == 0
    assert (
        f"sandbox settings: {_sandboxes(home) / 'tight.json'} (tight)\n"
        in capsys.readouterr().out
    )
    assert sorted(path.name for path in _sandboxes(home).iterdir()) == before
    assert not (folder / "sandbox.json").exists()
    assert not (folder / "logs").exists()


def test_dry_run_reads_a_deleted_shipped_sandbox_from_the_package(
    home: Path, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (_sandboxes(home) / "tight.json").unlink()

    assert cli.main(["-d", str(folder), "--dry-run", "--sandbox", "tight"]) == 0
    out = capsys.readouterr().out
    assert (
        f"sandbox settings: {_sandboxes(home) / 'tight.json'} "
        "(tight; added on a real run)\n" in out
    )
    assert "Added" not in out
    assert not (_sandboxes(home) / "tight.json").exists()


def test_dry_run_with_the_sandboxes_folder_gone_gives_the_real_runs_verdict(
    home: Path, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.rmtree(_sandboxes(home))

    assert cli.main(["-d", str(folder), "--dry-run", "--sandbox", "tight"]) == 0
    assert "(tight; added on a real run)" in capsys.readouterr().out
    assert not _sandboxes(home).exists()


def test_dry_run_with_the_sandboxes_folder_gone_still_lists_the_shipped_names(
    home: Path, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.rmtree(_sandboxes(home))

    assert cli.main(["-d", str(folder), "--dry-run", "--sandbox", "huge"]) == 2
    assert capsys.readouterr().err == (
        "error: --sandbox: no sandbox named huge; the sandboxes are: large, tight\n"
    )
    assert not _sandboxes(home).exists()


def test_the_view_refuses_a_sandbox_name_with_another_permission_flag(
    monkeypatch: pytest.MonkeyPatch,
    folder: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen = _capture_view(monkeypatch)

    code = cli.main(["-d", str(folder), "--sandbox", "tight", "--yolo"])

    assert code == 2
    assert PRESET_CONFLICT in capsys.readouterr().err
    assert seen == []


def test_the_view_gets_the_named_sandbox_as_a_settings_file(
    monkeypatch: pytest.MonkeyPatch, home: Path, folder: Path
) -> None:
    seen = _capture_view(monkeypatch)

    code = cli.main(["-d", str(folder), "--sandbox", "large"])

    assert code == 0
    assert seen[0].permissions is Permissions.SANDBOX
    assert seen[0].sandbox_file == str(_sandboxes(home) / "large.json")
    assert seen[0].sandbox_name == "large"


def test_the_view_gets_the_named_sandbox_before_a_folder_is_chosen(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    seen = _capture_view(monkeypatch)

    code = cli.main(["--sandbox", "tight"])

    assert code == 0
    assert seen[0].tasks_file == ""
    assert seen[0].permissions is Permissions.SANDBOX
    assert seen[0].sandbox_file == str(_sandboxes(home) / "tight.json")
    assert seen[0].sandbox_name == "tight"


def _answer(argv: list[str], default: SandboxDefault | None) -> cli.Answer:
    """Parse the flags, let the config default fill in, and return what the permissions settle."""
    args = cli._parser().parse_args(argv)
    cli.apply_sandbox_default(args, default)
    return cli.resolve_permissions(
        args.sandbox_settings, args.yolo, args.sandbox, dry_run=False
    )


@pytest.mark.parametrize("mode", ["tight", "large", "my_sandbox"])
def test_a_sandbox_default_fills_in_the_missing_flag(
    home: Path, folder: Path, mode: str
) -> None:
    _write_sandbox(home, "my_sandbox")

    from_default = _answer(["-d", str(folder)], SandboxDefault(mode))
    from_flag = _answer(["-d", str(folder), "--sandbox", mode], None)

    assert from_default == from_flag
    assert from_default.sandbox_name == mode
    assert from_default.sandbox_file == str(_sandboxes(home) / f"{mode}.json")


def test_a_yolo_default_fills_in_the_missing_flag(folder: Path) -> None:
    from_default = _answer(["-d", str(folder)], SandboxDefault("yolo"))
    from_flag = _answer(["-d", str(folder), "--yolo"], None)

    assert from_default == from_flag
    assert from_default.skip is True


@pytest.mark.parametrize("mode", ["tight", "large", "yolo"])
@pytest.mark.parametrize("flag", ["--sandbox", "--sandbox-settings", "--yolo"])
def test_a_flag_beats_the_default(
    folder: Path, settings: str, mode: str, flag: str
) -> None:
    flags = {
        "--sandbox": ["--sandbox", "large" if mode == "tight" else "tight"],
        "--sandbox-settings": ["--sandbox-settings", settings],
        "--yolo": ["--yolo"],
    }[flag]
    argv = ["-d", str(folder), *flags]
    default = SandboxDefault(mode)

    assert _answer(argv, default) == _answer(argv, None)


def test_the_folders_sandbox_json_does_not_stop_the_default(
    home: Path, folder: Path
) -> None:
    _add_sandbox(folder)

    answer = _answer(["-d", str(folder)], SandboxDefault("tight"))

    assert answer == cli.Answer(str(_sandboxes(home) / "tight.json"), False, "tight")


def test_no_default_changes_nothing(folder: Path) -> None:
    args = cli._parser().parse_args(["-d", str(folder)])
    before = vars(args).copy()

    cli.apply_sandbox_default(args, None)

    assert vars(args) == before


@pytest.mark.parametrize("mode", ["tight", "large", "yolo"])
def test_a_default_applies_without_dir(mode: str) -> None:
    args = cli._parser().parse_args([])

    cli.apply_sandbox_default(args, SandboxDefault(mode))

    assert args.yolo is (mode == "yolo")
    assert args.sandbox == (None if mode == "yolo" else mode)


BAD_CONFIG = "version: 1.0.0\nnot_a_key: 1\n"
YOLO_CONFIG = """\
version: 1.0.0
defaults:
  sandbox:
    permissiveness:
      default: yolo
  prompts:
    task_review:
      prompt: ./prompts/task_review.md
      max_task_review_rounds: 1
    task_remediation:
      prompt: ./prompts/task_remediation.md
"""


def _write_config(home: Path, text: str) -> None:
    """Replace config.yaml in the folder the home fixture already created."""
    (home / ".local" / "alr" / "config.yaml").write_text(text, encoding="utf-8")


SETUP_PROMPT = "Create them? [y/N] "
REFUSED_SETUP = "error: refused: config-setup-missing: "


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make alr see a terminal, so a person could answer.

    This patches the check itself: pytest swaps stdin and stdout around each
    phase, so a patch on their isatty made in a fixture does not survive into
    the test. test_a_terminal_needs_both_stdin_and_stdout covers the check.
    """
    monkeypatch.setattr(cli, "_terminal_attached", lambda: True)


@pytest.fixture
def no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make alr see pipes, whatever runs the tests."""
    monkeypatch.setattr(cli, "_terminal_attached", lambda: False)


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty", "want"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
    ids=["both", "only stdin", "only stdout", "neither"],
)
def test_a_terminal_needs_both_stdin_and_stdout(
    stdin_tty: bool, stdout_tty: bool, want: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout_tty)

    assert cli._terminal_attached() is want


def _reply(monkeypatch: pytest.MonkeyPatch, reply: str) -> list[str]:
    """Answer the setup question with reply; the list collects the questions asked."""
    asked: list[str] = []

    def read_answer(prompt: str) -> str:
        asked.append(prompt)
        return reply

    monkeypatch.setattr(cli, "_read_answer", read_answer)
    return asked


def _fail_if_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if alr asks anything."""

    def read_answer(prompt: str) -> str:
        pytest.fail(f"alr asked: {prompt}")

    monkeypatch.setattr(cli, "_read_answer", read_answer)


def test_the_setup_question_names_what_it_will_create_before_creating_it(
    folder: Path,
    fresh_home: Path,
    terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_folder = fresh_home / ".local" / "alr"
    seen_when_asked: list[bool] = []

    def read_answer(prompt: str) -> str:
        seen_when_asked.append(config_folder.exists())
        return "n"

    monkeypatch.setattr(cli, "_read_answer", read_answer)

    cli.main(["-d", str(folder), "--yolo", "--plain"])

    said = capsys.readouterr().out
    assert seen_when_asked == [False]
    assert str(config_folder) in said
    assert str(config_folder / "config.yaml") in said
    assert str(config_folder / "prompts" / "task_review.md") in said
    assert str(config_folder / "prompts" / "task_remediation.md") in said


def _listed(said: str) -> list[str]:
    """Pick the paths the setup notice lists, one per indented line."""
    return [line.strip() for line in said.splitlines() if line.startswith("  ")]


def test_the_setup_notice_lists_every_folder_and_file_in_creation_order(
    folder: Path,
    fresh_home: Path,
    terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _reply(monkeypatch, "n")
    local = fresh_home / ".local"
    config_folder = local / "alr"

    cli.main(["-d", str(folder), "--yolo", "--plain"])

    assert _listed(capsys.readouterr().out) == [
        str(local),
        str(config_folder),
        str(config_folder / "prompts"),
        str(config_folder / "sandboxes"),
        str(config_folder / "prompts" / "task_remediation.md"),
        str(config_folder / "prompts" / "task_review.md"),
        str(config_folder / "sandboxes" / "large.json"),
        str(config_folder / "sandboxes" / "tight.json"),
        str(config_folder / "config.yaml"),
    ]


def test_the_setup_notice_leaves_out_a_parent_that_already_exists(
    folder: Path,
    fresh_home: Path,
    terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _reply(monkeypatch, "n")
    (fresh_home / ".local").mkdir()
    config_folder = fresh_home / ".local" / "alr"

    cli.main(["-d", str(folder), "--yolo", "--plain"])

    listed = _listed(capsys.readouterr().out)
    assert str(fresh_home / ".local") not in listed
    assert listed[0] == str(config_folder)
    assert str(config_folder / "prompts") in listed


def test_the_setup_question_is_asked_with_a_yes_no_prompt(
    folder: Path,
    fresh_home: Path,
    terminal: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _reply(monkeypatch, "n")

    cli.main(["-d", str(folder), "--yolo", "--plain"])

    assert asked == [SETUP_PROMPT]


@pytest.mark.parametrize("reply", ["y", "yes", "Y", "YES", "Yes", " y "])
def test_yes_sets_up_the_config_and_stops_without_a_run(
    reply: str,
    folder: Path,
    fresh_home: Path,
    terminal: None,
    sessions: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _reply(monkeypatch, reply)

    code = cli.main(["-d", str(folder), "--yolo", "--plain"])

    captured = capsys.readouterr()
    config_folder = fresh_home / ".local" / "alr"
    assert code == 0
    assert (config_folder / "config.yaml").is_file()
    assert (config_folder / "prompts" / "task_review.md").is_file()
    assert (config_folder / "prompts" / "task_remediation.md").is_file()
    assert f"The configuration is set up in {config_folder}." in captured.out
    assert "Run alr again" in captured.out
    assert captured.err == ""
    assert sessions == []


@pytest.mark.parametrize(
    "reply", ["n", "N", "no", "", "  ", "maybe", "yy", "yes please", "1"]
)
def test_anything_but_yes_creates_nothing_and_refuses(
    reply: str,
    folder: Path,
    fresh_home: Path,
    terminal: None,
    sessions: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _reply(monkeypatch, reply)

    code = cli.main(["-d", str(folder), "--yolo", "--plain"])

    captured = capsys.readouterr()
    assert code == 3
    assert not (fresh_home / ".local").exists()
    assert "Nothing was created." in captured.out
    assert captured.err.startswith(REFUSED_SETUP)
    assert "run alr again and answer y to create it" in captured.err
    assert len(captured.err.splitlines()) == 1
    assert sessions == []


def test_the_end_of_input_counts_as_no(
    folder: Path,
    fresh_home: Path,
    terminal: None,
    sessions: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def end_of_input(prompt: object = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", end_of_input)

    code = cli.main(["-d", str(folder), "--yolo", "--plain"])

    captured = capsys.readouterr()
    assert code == 3
    assert not (fresh_home / ".local").exists()
    # The unanswered question's line is ended before alr says anything else.
    assert captured.out.startswith("alr needs to set up")
    assert captured.out.endswith(f"{SETUP_PROMPT}\nNothing was created.\n")
    assert captured.err.startswith(REFUSED_SETUP)
    assert "run alr again and answer y to create it" in captured.err
    assert len(captured.err.splitlines()) == 1
    assert sessions == []


def test_the_question_is_printed_to_stdout_not_given_to_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """input() writes a prompt it is given to stderr unless readline is loaded, and stderr must stay clean."""
    prompts_given_to_input: list[tuple[object, ...]] = []

    def typed_yes(*prompt: object) -> str:
        prompts_given_to_input.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", typed_yes)

    assert cli._read_answer(SETUP_PROMPT) == "y"

    captured = capsys.readouterr()
    assert captured.out == SETUP_PROMPT
    assert captured.err == ""
    assert prompts_given_to_input == [()]


def test_without_a_terminal_alr_neither_asks_nor_writes_and_refuses(
    folder: Path,
    fresh_home: Path,
    no_terminal: None,
    sessions: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fail_if_asked(monkeypatch)

    code = cli.main(["-d", str(folder), "--yolo"])

    captured = capsys.readouterr()
    assert code == 3
    assert not (fresh_home / ".local").exists()
    assert captured.out == ""
    assert captured.err.startswith(REFUSED_SETUP)
    assert "the config setup is missing" in captured.err
    assert "run alr in a terminal once to create it" in captured.err
    assert len(captured.err.splitlines()) == 1
    assert sessions == []


@pytest.mark.parametrize(
    ("attached", "reply", "want_code"),
    [(True, "y", 0), (True, "n", 3), (False, "n", 3)],
    ids=["yes", "no", "no terminal"],
)
@pytest.mark.parametrize("problem", ["no run folder", "no permission flag"])
def test_setup_comes_before_the_run_folder_and_the_flags_are_checked(
    attached: bool,
    reply: str,
    want_code: int,
    problem: str,
    folder: Path,
    fresh_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_terminal_attached", lambda: attached)
    _reply(monkeypatch, reply)
    # Either would end the run with exit 2 if it were checked first.
    argv = {
        "no run folder": ["-d", str(fresh_home / "nowhere"), "--yolo"],
        "no permission flag": ["-d", str(folder), "--plain"],
    }[problem]

    assert cli.main(argv) == want_code


def test_an_existing_config_folder_is_never_asked_about(
    folder: Path,
    terminal: None,
    sessions: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fail_if_asked(monkeypatch)

    code = cli.main(["-d", str(folder), "--yolo", "--plain"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert captured.err == ""
    assert sessions == [["--dangerously-skip-permissions"]]


def test_dry_run_refuses_a_missing_config_folder_without_asking_or_creating(
    folder: Path,
    fresh_home: Path,
    terminal: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fail_if_asked(monkeypatch)

    code = cli.main(["-d", str(folder), "--dry-run", "--plain"])

    captured = capsys.readouterr()
    assert code == RefusedError.exit_code
    assert captured.err.startswith(REFUSED_SETUP)
    assert captured.out == ""
    assert not (fresh_home / ".local").exists()


def test_version_neither_asks_nor_creates(
    fresh_home: Path,
    terminal: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fail_if_asked(monkeypatch)

    cli.main(["--version"])

    assert not (fresh_home / ".local").exists()


def test_a_file_where_the_config_folder_belongs_refuses_before_asking(
    folder: Path,
    fresh_home: Path,
    terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fail_if_asked(monkeypatch)
    (fresh_home / ".local").mkdir()
    (fresh_home / ".local" / "alr").write_text("in the way", encoding="utf-8")

    code = cli.main(["-d", str(folder), "--yolo", "--plain"])

    assert code == 3
    assert capsys.readouterr().err.startswith("error: refused: invalid-config: ")


def test_a_bad_config_refuses_with_exit_3_before_any_task(
    folder: Path,
    home: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_config(home, BAD_CONFIG)

    code = cli.main(["-d", str(folder), "--yolo"])

    assert code == 3
    assert capsys.readouterr().err.startswith("error: refused: invalid-config: ")
    assert sessions == []


def test_a_config_default_reaches_the_run(
    folder: Path, home: Path, sessions: list[list[str]]
) -> None:
    _write_config(home, YOLO_CONFIG)

    assert cli.main(["-d", str(folder)]) == 0
    assert sessions == [["--dangerously-skip-permissions"]]


def test_a_yolo_config_default_without_dir_sets_the_view_permissions_to_skip(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(home, YOLO_CONFIG)
    seen = _capture_view(monkeypatch)

    code = cli.main([])

    assert code == 0
    assert seen[0].permissions is Permissions.SKIP


def test_a_sandbox_config_default_without_dir_gives_the_view_its_file(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_sandbox(home, "my_sandbox")
    _write_config(home, YOLO_CONFIG.replace("default: yolo", "default: my_sandbox"))
    seen = _capture_view(monkeypatch)

    code = cli.main([])

    assert code == 0
    assert seen[0].tasks_file == ""
    assert seen[0].permissions is Permissions.SANDBOX
    assert seen[0].sandbox_file == str(path)


def test_a_sandbox_config_default_reaches_the_run(
    folder: Path, home: Path, sessions: list[list[str]]
) -> None:
    path = _write_sandbox(home, "my_sandbox", '{"permissions": {"deny": ["Read"]}}')
    _write_config(home, YOLO_CONFIG.replace("default: yolo", "default: my_sandbox"))

    assert cli.main(["-d", str(folder)]) == 0
    assert sessions == [sandbox_args(str(path))]


def test_a_config_default_applies_beside_the_folders_sandbox_json(
    folder: Path,
    home: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _add_sandbox(folder, "[]")
    _write_config(home, YOLO_CONFIG)

    assert cli.main(["-d", str(folder)]) == 0
    assert sessions == [["--dangerously-skip-permissions"]]
    assert capsys.readouterr().out == _notice(folder)


def test_a_real_run_adds_a_deleted_shipped_sandbox_and_says_so_once(
    home: Path,
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    tight = _sandboxes(home) / "tight.json"
    shipped = tight.read_bytes()
    tight.unlink()

    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    assert capsys.readouterr().out == f"Added {tight}\n"
    assert tight.read_bytes() == shipped

    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    assert capsys.readouterr().out == ""


def test_a_real_run_leaves_an_edited_shipped_sandbox_alone(
    home: Path, folder: Path, sessions: list[list[str]]
) -> None:
    large = _sandboxes(home) / "large.json"
    large.write_text("my edit\n", encoding="utf-8")

    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    assert large.read_text(encoding="utf-8") == "my edit\n"


def test_a_dry_run_adds_no_sandbox(
    home: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tight = _sandboxes(home) / "tight.json"
    tight.unlink()

    def add_missing() -> list[Path]:
        pytest.fail("a dry run added a sandbox")

    monkeypatch.setattr(cli, "add_missing", add_missing)

    assert cli.main(["-d", str(folder), "--dry-run", "--yolo"]) == 0
    assert not tight.exists()


def test_sandboxes_are_added_between_the_config_folder_check_and_the_config_read(
    folder: Path, sessions: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_check = cli.config_folder_exists
    real_add = cli.add_missing
    real_dir = cli.resolve_dir
    real_load = cli.load

    def check() -> bool:
        calls.append("check")
        return real_check()

    def add() -> list[Path]:
        calls.append("add")
        return real_add()

    def resolve(dir: str) -> str:
        calls.append("dir")
        return real_dir(dir)

    def load(**kwargs: Any) -> Any:
        calls.append("load")
        return real_load(**kwargs)

    monkeypatch.setattr(cli, "config_folder_exists", check)
    monkeypatch.setattr(cli, "add_missing", add)
    monkeypatch.setattr(cli, "resolve_dir", resolve)
    monkeypatch.setattr(cli, "load", load)

    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    assert calls == ["check", "add", "dir", "load"]


def test_the_run_folder_notice_comes_after_the_sandboxes_are_added_and_before_the_config_is_read(
    home: Path,
    folder: Path,
    sessions: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tight = _sandboxes(home) / "tight.json"
    tight.unlink()
    _add_sandbox(folder)
    printed_before_the_read: list[str] = []
    real_load = cli.load

    def load(**kwargs: Any) -> Any:
        printed_before_the_read.append(capsys.readouterr().out)
        return real_load(**kwargs)

    monkeypatch.setattr(cli, "load", load)

    assert cli.main(["-d", str(folder), "--yolo"]) == 0
    # The added file first, then the notice, both already on stdout when the config is read.
    assert printed_before_the_read == [f"Added {tight}\n{_notice(folder)}"]
    assert capsys.readouterr().out == ""


def test_a_machine_that_is_not_set_up_gets_no_sandbox_added_and_no_name_check(
    folder: Path,
    fresh_home: Path,
    no_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def add_missing() -> list[Path]:
        pytest.fail("added a sandbox before the config folder exists")

    monkeypatch.setattr(cli, "add_missing", add_missing)

    assert cli.main(["-d", str(folder), "--sandbox", "typo"]) == 3
    assert capsys.readouterr().err.startswith(REFUSED_SETUP)


def test_a_sandboxes_path_that_is_a_file_is_refused_before_the_run_folder_is_read(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.rmtree(_sandboxes(home))
    _sandboxes(home).write_text("in the way", encoding="utf-8")

    assert cli.main(["-d", str(home / "nowhere"), "--yolo"]) == 3
    assert capsys.readouterr().err.startswith("error: refused: invalid-config: ")


def test_sandboxes_are_added_even_when_the_config_turns_out_bad(
    home: Path, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tight = _sandboxes(home) / "tight.json"
    tight.unlink()
    _write_config(home, BAD_CONFIG)

    assert cli.main(["-d", str(folder), "--yolo"]) == 3
    assert tight.exists()
    assert capsys.readouterr().err.startswith("error: refused: invalid-config: ")


@contextmanager
def _sandboxes_is_a_file(home: Path) -> Iterator[str]:
    """Put a file where the sandboxes folder belongs; yield the refusal every run must give."""
    shutil.rmtree(_sandboxes(home))
    _sandboxes(home).write_text("in the way", encoding="utf-8")
    yield f"invalid-config: {_sandboxes(home)} is not a folder; remove it yourself"


@contextmanager
def _sandboxes_cannot_be_searched(home: Path) -> Iterator[str]:
    """Take every permission off the sandboxes folder; yield the refusal every run must give."""
    with chmodded(_sandboxes(home), 0o000):
        yield (
            "config-unwritable: cannot add the sandboxes to "
            f"{_sandboxes(home)}: Permission denied"
        )


@contextmanager
def _sandboxes_missing_from_a_read_only_config_folder(home: Path) -> Iterator[str]:
    """Leave no place to make the sandboxes folder; yield the refusal every run must give."""
    shutil.rmtree(_sandboxes(home))
    with chmodded(home / ".local" / "alr", 0o500):
        yield (
            "config-unwritable: cannot add the sandboxes to "
            f"{_sandboxes(home)}: Permission denied"
        )


@pytest.mark.parametrize("dry_run", [False, True], ids=["real run", "dry run"])
@pytest.mark.parametrize(
    "flags",
    [
        pytest.param([], id="no permission flag"),
        pytest.param(["--yolo"], id="yolo"),
        pytest.param(["--sandbox", "tight"], id="sandbox by name"),
    ],
)
@pytest.mark.parametrize(
    "unusable",
    [
        pytest.param(_sandboxes_is_a_file, id="a file"),
        pytest.param(_sandboxes_cannot_be_searched, id="no access"),
        pytest.param(
            _sandboxes_missing_from_a_read_only_config_folder,
            id="missing, config folder read-only",
        ),
    ],
)
def test_a_sandboxes_folder_alr_cannot_use_is_refused_the_same_way_by_every_run(
    unusable: Callable[[Path], AbstractContextManager[str]],
    flags: list[str],
    dry_run: bool,
    home: Path,
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    locked_folder: None,
) -> None:
    argv = ["-d", str(folder), *flags]
    if dry_run:
        argv.append("--dry-run")

    with unusable(home) as refusal:
        code = cli.main(argv)

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == f"error: refused: {refusal}\n"
    assert captured.out == ""
    assert sessions == []


@pytest.mark.parametrize("dry_run", [False, True], ids=["real run", "dry run"])
@pytest.mark.parametrize("by", ["flag", "config default"])
def test_a_sandboxes_folder_alr_cannot_list_is_refused_when_a_name_is_looked_up(
    by: str,
    dry_run: bool,
    home: Path,
    folder: Path,
    sessions: list[list[str]],
    capsys: pytest.CaptureFixture[str],
    locked_folder: None,
) -> None:
    argv = ["-d", str(folder)]
    if by == "flag":
        argv += ["--sandbox", "tight"]
    else:
        _write_config(home, YOLO_CONFIG.replace("default: yolo", "default: tight"))

    if dry_run:
        argv.append("--dry-run")

    with chmodded(_sandboxes(home), 0o311):
        code = cli.main(argv)

    captured = capsys.readouterr()
    assert code == 3
    assert captured.err == (
        "error: refused: config-unwritable: cannot list the sandboxes in "
        f"{_sandboxes(home)}: Permission denied\n"
    )
    assert sessions == []


def test_a_broken_config_is_refused_before_the_sandbox_name_is_looked_at(
    home: Path, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(home, BAD_CONFIG)

    assert cli.main(["-d", str(folder), "--sandbox", "typo"]) == 3
    assert capsys.readouterr().err.startswith("error: refused: invalid-config: ")


def test_the_sandbox_flag_is_documented_in_the_help() -> None:
    # Argparse wraps the help, so the words are compared without the line breaks.
    help_text = " ".join(cli._parser().format_help().split())

    assert "--sandbox <name>" in help_text
    assert "the named file from ~/.local/alr/sandboxes/" in help_text
