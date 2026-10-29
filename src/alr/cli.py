"""Command line: flags, startup checks, routing."""

import argparse
import asyncio
import platform
import signal
import sys
from collections.abc import Callable, Coroutine
from importlib import metadata
from pathlib import Path
from typing import Any

from alr.engine.sandbox import bypass_args, folder_sandbox, sandbox_args
from alr.engine.stop import Stop
from alr.errors import AlrError, RunCancelledError, UsageError
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
        return Stop().exit_code()


def _version_line() -> str:
    """Return the --version line: the version, then the OS and architecture."""
    system = platform.system().lower()
    return f"alr version: {metadata.version('alr')} {system}/{platform.machine()}"


_GATE_TIMEOUT_RENAMED = "--gate-timeout: renamed to --action-timeout"
_COMMIT_REMOVED = "--commit: removed; a commit is an action now: put it in shared.after_task, see the README (Migrating from commit modes and gates)"


def _main(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Apply the startup checks in gralph's order, then start the dry run or the real run."""
    if args.gate_timeout is not None:
        print(f"error: {_GATE_TIMEOUT_RENAMED}", file=sys.stderr)
        return UsageError.exit_code

    if args.commit is not None:
        print(f"error: {_COMMIT_REMOVED}", file=sys.stderr)
        return UsageError.exit_code

    if args.version:
        print(_version_line())
        return 0

    if args.install_skill:
        return _install_skill()

    try:
        tasks_file = resolve_dir(args.dir)
        sandbox_settings = resolve_sandbox(args.dir, args.sandbox_settings, args.yolo)
        # An explicitly empty value counts as passed, and is bad.
        if args.action_timeout is not None:
            check_action_timeout(args.action_timeout)

        check_reset_flag(args.reset_failed, args.dry_run)
        if wants_tui(args):
            return _open_view(args, tasks_file, sandbox_settings)

        missing = _missing_flags(tasks_file)
        if missing:
            _report_missing(parser, missing)
            return UsageError.exit_code

        session = session_args(sandbox_settings, args.yolo, args.dry_run)
    except AlrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    action_timeout = args.action_timeout or ""
    stop = Stop()
    if args.dry_run:
        return _run(
            dry_run(
                sys.stdout,
                tasks_file,
                action_timeout,
                sandbox_settings,
            ),
            stop,
        )

    return _run(
        start(
            tasks_file,
            action_timeout,
            session,
            args.reset_failed,
            sandbox_settings=sandbox_settings,
            stop=stop,
        ),
        stop,
    )


def _install_skill() -> int:
    """Install the bundled skill and print where it went."""
    try:
        print(install())
    except AlrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    return 0


def wants_tui(args: argparse.Namespace) -> bool:
    """Report whether this run opens the full-screen view: a terminal, no --plain, no dry run."""
    if args.plain or args.dry_run:
        return False

    return sys.stdin.isatty() and sys.stdout.isatty()


def _open_view(args: argparse.Namespace, tasks_file: str, sandbox_file: str) -> int:
    """Open the full-screen view with what the flags already answer."""
    # Validates a given settings file; no run starts here.
    session_args(sandbox_file, args.yolo, dry_run=True)
    settings = RunSettings(
        tasks_file=tasks_file,
        sandbox_file=sandbox_file,
        permissions=_permissions(sandbox_file, args.yolo),
        action_timeout=args.action_timeout or "",
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
        help="Install the bundled alr-task-file skill into ~/.claude/skills and exit",
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
    # Hidden: only here to refuse the old name with a message naming the new one.
    parser.add_argument(
        "--gate-timeout", default=None, help=argparse.SUPPRESS, metavar=""
    )
    # None, not "", so an empty value given on purpose can be told apart.
    parser.add_argument(
        "--action-timeout",
        metavar="",
        default=None,
        help="Limit for every action, such as 90s or 10m; overrides each action's own timeout (default: the action's timeout, else 10m)",
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
    # Hidden: only here to refuse the old flag with a message naming the replacement.
    parser.add_argument("--commit", default=None, help=argparse.SUPPRESS, metavar="")
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


def check_action_timeout(value: str) -> None:
    """Apply the same rules to --action-timeout as to an action's timeout in the file."""
    try:
        parse_timeout(value)
    except ValueError as exc:
        raise UsageError(f"--action-timeout: {exc}") from exc


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


def _run(coro: Coroutine[Any, Any, None], stop: Stop) -> int:
    """Run coro to the end; SIGINT or SIGTERM cancels it, which stops the run.

    A stop exits with the code of the first signal, which _with_signals records in stop.
    """
    try:
        asyncio.run(_with_signals(coro, stop))
    except asyncio.CancelledError:
        # The stop landed while no task was running, so there is no task to name.
        print(_INTERRUPTED, file=sys.stderr)
        return stop.exit_code()
    except RunCancelledError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return stop.exit_code()
    except AlrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    return 0


async def _with_signals(coro: Coroutine[Any, Any, None], stop: Stop) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("no running task")

    handler = _stop_once(task, stop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handler, sig)

    await coro


def _stop_once(task: asyncio.Task[None], stop: Stop) -> Callable[[int], None]:
    """Return a signal handler that cancels task the first time and does nothing after.

    The first signal is also the one recorded in stop, so a later one cannot
    change the exit code.

    A stop kills each process group at once, but waits for the exit. A second
    cancel would interrupt that wait and let alr exit with a child still
    running, so an impatient second Ctrl-C is ignored; the stop already under
    way is short.
    """
    stopped = False

    def handle(sig: int) -> None:
        nonlocal stopped
        if stopped:
            return

        stopped = True
        stop.signum = sig
        task.cancel()

    return handle
