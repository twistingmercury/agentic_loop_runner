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
        tasks_file = resolve_dir(args.dir)
        sandbox_settings = resolve_sandbox(args.dir, args.sandbox_settings, args.yolo)
        # An explicitly empty value counts as passed, and is bad.
        if args.gate_timeout is not None:
            check_gate_timeout(args.gate_timeout)

        check_reset_flag(args.reset_failed, args.dry_run)
        if wants_tui(args):
            return _open_view(args, tasks_file, sandbox_settings)

        missing = _missing_flags(tasks_file)
        if missing:
            _report_missing(parser, missing)
            return 1

        commit_mode = CommitMode(args.commit)
        session = session_args(sandbox_settings, args.yolo, args.dry_run)
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
                commit_mode is not CommitMode.NONE,
            )
        )

    return _run(
        start(
            tasks_file,
            gate_timeout,
            session,
            commit_mode,
            args.reset_failed,
            sandbox_settings=sandbox_settings,
        )
    )


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
    commit_mode = CommitMode(args.commit)
    # Validates a given settings file; no run starts here.
    session_args(sandbox_file, args.yolo, dry_run=True)
    settings = RunSettings(
        tasks_file=tasks_file,
        sandbox_file=sandbox_file,
        permissions=_permissions(sandbox_file, args.yolo),
        commit_mode=commit_mode,
        gate_timeout=args.gate_timeout or "",
        reset_failed=args.reset_failed,
    )
    return run_app(settings)


def _permissions(sandbox_file: str, skip: bool) -> Permissions:
    if skip:
        return Permissions.SKIP

    return Permissions.SANDBOX if sandbox_file else Permissions.UNSET


class _BareMetavarFormatter(argparse.HelpFormatter):
    """Drop the stray space argparse leaves after a flag whose metavar is empty."""

    def _format_action_invocation(self, action: argparse.Action) -> str:
        return super()._format_action_invocation(action).replace(" ,", ",").rstrip()


def _parser() -> argparse.ArgumentParser:
    # Abbreviations are off because gralph does not expand a flag prefix.
    parser = argparse.ArgumentParser(
        prog="alr", allow_abbrev=False, formatter_class=_BareMetavarFormatter
    )
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
        metavar="",
        default="",
        help="Run folder holding tasks.yaml (and optionally sandbox.json); required with --plain or --dry-run, and the full-screen view asks for it when it is missing; --sandbox-settings or --yolo overrides the folder's sandbox.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the tasks file and report failed tasks without running anything",
    )
    # None, not "", so an empty value given on purpose can be told apart.
    parser.add_argument(
        "--gate-timeout",
        metavar="",
        default=None,
        help="Limit for every gate, such as 90s or 10m; overrides each gate's own timeout (default: the gate's timeout, else 10m)",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Use plain output instead of the full-screen view",
    )
    parser.add_argument(
        "--reset-failed",
        action="store_true",
        help="Set every failed task back to pending before the run, without asking",
    )
    parser.add_argument(
        "--sandbox-settings",
        metavar="",
        default="",
        help="Path to a Claude Code settings JSON file; sessions run in Claude's sandbox with it. A run needs this or --yolo",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Run sessions with no sandbox and no permission checks (claude --dangerously-skip-permissions); what they do is on you",
    )
    parser.add_argument(
        "--commit",
        metavar="",
        choices=[mode.value for mode in CommitMode],
        default=CommitMode.NONE.value,
        help="When to commit with git: none (default) never touches git; task commits each completed task after its gates pass, with the task name as the message, and in a repository the work tree must be clean; run makes one commit after the last task, and a failed or stopped run makes no commit",
    )
    return parser


def resolve_dir(dir: str) -> str:
    """Turn a run folder into the task file path; no folder gives no path.

    The name is fixed, so the folder is all the command line says.
    """
    if not dir:
        return ""

    path = Path(dir) / "tasks.yaml"
    if not path.exists():
        raise UsageError(f"--dir: no tasks.yaml in {dir}")

    return str(path)


def resolve_sandbox(dir: str, sandbox_settings: str, skip: bool) -> str:
    """Return the sandbox settings path, taking the run folder's sandbox.json when no flag chose.

    The folder only ever supplies the sandboxed choice: either explicit
    permission flag wins, and --yolo is how a run opts out.
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


def check_reset_flag(reset_failed: bool, dry_run: bool) -> None:
    """Refuse --reset-failed with --dry-run, which must write nothing."""
    if reset_failed and dry_run:
        raise UsageError(
            "--reset-failed cannot be used with --dry-run (a dry run writes nothing)"
        )


def session_args(sandbox_file: str, skip: bool, dry_run: bool) -> list[str]:
    """Return the claude flags that set what a session may do.

    A run must choose a sandbox or ask by name to go without one: there is no
    default, so nobody runs unsandboxed by accident. A dry run starts no
    session and needs neither, but its sandbox file is still checked.
    """
    if sandbox_file and skip:
        raise UsageError("--sandbox-settings and --yolo cannot be used together")

    if sandbox_file:
        return sandbox_args(sandbox_file)

    if skip:
        return bypass_args()

    if dry_run:
        return []

    raise UsageError(
        "pass --sandbox-settings <path>, or --yolo to run without a sandbox"
    )


def _missing_flags(tasks_file: str) -> list[str]:
    return [] if tasks_file else ["--dir"]


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
