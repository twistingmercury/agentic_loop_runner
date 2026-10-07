"""Command line: flags, startup checks, routing."""

import argparse
import asyncio
import signal
import sys
from collections.abc import Callable, Coroutine
from importlib import metadata
from pathlib import Path
from typing import Any

from alr.engine.commit import CommitMode
from alr.engine.sandbox import bypass_args, folder_sandbox, sandbox_args
from alr.errors import AlrError, UsageError
from alr.plain.dryrun import dry_run
from alr.plain.start import start
from alr.skillinstall import install
from alr.tasks import parse_timeout
from alr.tui.app import run_app
from alr.tui.settings import Permissions, RunSettings

_INTERRUPTED = "error: interrupted"


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _main(parser, args)
    except KeyboardInterrupt:
        # Ctrl-C during the startup checks, before the run handles signals itself.
        print(_INTERRUPTED, file=sys.stderr)
        return 1


def _main(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Apply the startup checks in gralph's order, then start the dry run or the real run."""
    if args.version:
        print(f"alr version: {metadata.version('alr')}")
        return 0

    if args.install_skill:
        return _install_skill()

    try:
        tasks_file = resolve_dir(args.dir, args.tasks)
        sandbox_settings = resolve_sandbox(
            args.dir, args.sandbox_settings, args.skip_permissions
        )
        # An explicitly empty value counts as passed, and is bad.
        if args.gate_timeout is not None:
            check_gate_timeout(args.gate_timeout)

        if wants_tui(args):
            return _open_view(args, tasks_file, sandbox_settings)

        missing = _missing_flags(tasks_file)
        if missing:
            _report_missing(parser, missing)
            return 1

        check_log_dir(args.log_dir, args.dry_run)
        commit_mode = check_commit_flags(args.commit, args.commit_after_all)
        session = session_args(sandbox_settings, args.skip_permissions, args.dry_run)
    except AlrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    gate_timeout = args.gate_timeout or ""
    if args.dry_run:
        return _run(
            dry_run(
                sys.stdout,
                tasks_file,
                gate_timeout,
                sandbox_settings,
                commit_mode is not None,
            )
        )

    return _run(start(tasks_file, gate_timeout, session, commit_mode))


def _install_skill() -> int:
    """Install the bundled skill and print where it went."""
    try:
        print(install())
    except AlrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


def wants_tui(args: argparse.Namespace) -> bool:
    """Report whether this run opens the full-screen view: a terminal, no --plain, no dry run."""
    if args.plain or args.dry_run:
        return False

    return sys.stdin.isatty() and sys.stdout.isatty()


def _open_view(args: argparse.Namespace, tasks_file: str, sandbox_file: str) -> int:
    """Open the full-screen view with what the flags already answer."""
    commit_mode = check_commit_flags(args.commit, args.commit_after_all)
    # Validates a given settings file and the both-flags conflict; no run starts here.
    session_args(sandbox_file, args.skip_permissions, dry_run=True)
    settings = RunSettings(
        tasks_file=tasks_file,
        sandbox_file=sandbox_file,
        permissions=_permissions(sandbox_file, args.skip_permissions),
        commit_mode=commit_mode or CommitMode.PER_TASK,
        log_dir=args.log_dir,
        gate_timeout=args.gate_timeout or "",
    )
    return run_app(settings)


def _permissions(sandbox_file: str, skip: bool) -> Permissions:
    if skip:
        return Permissions.SKIP

    return Permissions.SANDBOX if sandbox_file else Permissions.UNSET


def _parser() -> argparse.ArgumentParser:
    # Abbreviations are off because gralph does not expand a flag prefix.
    parser = argparse.ArgumentParser(prog="alr", allow_abbrev=False)
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Show the current version of alr",
    )
    parser.add_argument(
        "--install-skill",
        action="store_true",
        help="Install the bundled gralph-docs-writer skill into ~/.claude/skills and exit",
    )
    parser.add_argument(
        "-d",
        "--dir",
        default="",
        help="Run folder holding tasks.yaml (and optionally sandbox.json); --tasks and --sandbox-settings override",
    )
    parser.add_argument(
        "-t",
        "--tasks",
        default="",
        help="Path to the tasks.yaml task list that drives the loop; required unless --dir gives it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the tasks file and report failed tasks without running anything",
    )
    # None, not "", so an empty value given on purpose can be told apart.
    parser.add_argument(
        "--gate-timeout",
        default=None,
        help="Limit for every gate, such as 90s or 10m; overrides each gate's own timeout (default: the gate's timeout, else 10m)",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Use plain output instead of the full-screen view",
    )
    parser.add_argument(
        "--sandbox-settings",
        default="",
        help="Path to a Claude Code settings JSON file; sessions run in Claude's sandbox with it. A run needs this or --skip-permissions",
    )
    parser.add_argument(
        "--skip-permissions",
        action="store_true",
        help="Run sessions with no sandbox and no permission checks (claude --dangerously-skip-permissions); what they do is on you",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit each completed task with git after its gates pass, with the task name as the message; in a repository, the work tree must be clean",
    )
    parser.add_argument(
        "--commit-after-all",
        action="store_true",
        help="Commit once, after the last task completes, instead of after each task; a failed or stopped run makes no commit",
    )
    parser.add_argument(
        "--log-dir",
        default="",
        help="Directory for a record of the run: a ledger and each task's activity, in a new folder per run; full-screen view only",
    )
    return parser


def resolve_dir(dir: str, tasks_path: str) -> str:
    """Turn a run folder into the task file path.

    The name is fixed, so a folder needs no other flag; --tasks always wins.
    """
    if not dir or tasks_path:
        return tasks_path

    path = Path(dir) / "tasks.yaml"
    if not path.exists():
        raise UsageError(f"--dir: no tasks.yaml in {dir}")

    return str(path)


def resolve_sandbox(dir: str, sandbox_settings: str, skip: bool) -> str:
    """Return the sandbox settings path, taking the run folder's sandbox.json when no flag chose.

    The folder only ever supplies the sandboxed choice: either explicit
    permission flag wins, and --skip-permissions is how a run opts out.
    """
    if not dir or sandbox_settings or skip:
        return sandbox_settings

    return folder_sandbox(dir) or ""


def check_gate_timeout(value: str) -> None:
    """Apply the same rules to --gate-timeout as to a gate's timeout in the file."""
    try:
        parse_timeout(value)
    except ValueError as exc:
        raise UsageError(f"--gate-timeout: {exc}") from exc


def check_log_dir(log_dir: str, dry_run: bool) -> None:
    """Refuse --log-dir on a real run.

    The record is built from events only the full-screen view's path produces,
    and a run that was asked for a record must not quietly leave none. An
    empty value counts as not passed. A dry run records nothing and ignores
    the flag.
    """
    if not log_dir or dry_run:
        return

    raise UsageError("--log-dir only works with the full-screen view")


def check_commit_flags(commit: bool, after_all: bool) -> CommitMode | None:
    """Turn the two commit flags into the mode they ask for; asking for both is an error."""
    if commit and after_all:
        raise UsageError("--commit and --commit-after-all cannot be used together")

    if after_all:
        return CommitMode.AFTER_ALL

    return CommitMode.PER_TASK if commit else None


def session_args(sandbox_file: str, skip: bool, dry_run: bool) -> list[str]:
    """Return the claude flags that set what a session may do.

    A run must choose a sandbox or ask by name to go without one: there is no
    default, so nobody runs unsandboxed by accident. A dry run starts no
    session and needs neither, but its sandbox file is still checked.
    """
    if sandbox_file and skip:
        raise UsageError(
            "--sandbox-settings and --skip-permissions cannot be used together"
        )

    if sandbox_file:
        return sandbox_args(sandbox_file)

    if skip:
        return bypass_args()

    if dry_run:
        return []

    raise UsageError(
        "pass --sandbox-settings <path>, or --skip-permissions to run without a sandbox"
    )


def _missing_flags(tasks_file: str) -> list[str]:
    return [] if tasks_file else ["--tasks"]


def _report_missing(parser: argparse.ArgumentParser, missing: list[str]) -> None:
    for flag in missing:
        print(f"error: required flag {flag} not set", file=sys.stderr)

    print(file=sys.stderr)
    parser.print_usage(sys.stderr)


def _run(coro: Coroutine[Any, Any, None]) -> int:
    """Run coro to the end; SIGINT or SIGTERM cancels it, which stops the run."""
    try:
        asyncio.run(_with_signals(coro))
    except asyncio.CancelledError:
        # The stop landed while no task was running, so there is no task to name.
        print(_INTERRUPTED, file=sys.stderr)
        return 1
    except AlrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


async def _with_signals(coro: Coroutine[Any, Any, None]) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("no running task")

    stop = _stop_once(task)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)

    await coro


def _stop_once(task: asyncio.Task[None]) -> Callable[[], None]:
    """Return a signal handler that cancels task the first time and does nothing after.

    A commit is stopped gently: git gets SIGTERM and a grace period before
    SIGKILL. A second cancel would interrupt that wait and let alr exit with
    git or one of its hooks still running, so an impatient second Ctrl-C is
    ignored; the stop already under way is bounded by the grace period.
    """
    stopped = False

    def stop() -> None:
        nonlocal stopped
        if stopped:
            return

        stopped = True
        task.cancel()

    return stop
