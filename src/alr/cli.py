"""Command line: flags, startup checks, routing."""

import argparse
import asyncio
import platform
import signal
import sys
from collections.abc import Callable, Coroutine
from importlib import metadata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alr.config import (
    SandboxDefault,
    config_dir,
    config_folder_exists,
    create_folder,
    load,
    paths_to_create,
)
from alr.engine.sandbox import bypass_args, sandbox_args, settings_args
from alr.engine.stop import Stop
from alr.errors import (
    CONFIG_SETUP_MISSING,
    AlrError,
    RefusedError,
    RunCancelledError,
    UsageError,
)
from alr.plain.dryrun import dry_run
from alr.plain.start import start
from alr.sandboxes import add_missing, find, names_text, read_text
from alr.skillinstall import install
from alr.tasks import parse_timeout
from alr.tui.app import run_app
from alr.tui.settings import Permissions, RunSettings

_INTERRUPTED = "error: interrupted"
# The config's sandbox default that is not a sandbox name (see alr.config).
_YOLO = "yolo"
_YES_ANSWERS = ("y", "yes")


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


@dataclass(frozen=True)
class Answer:
    """What the permission flags settle: a settings file, with the sandbox's name when it was picked by name, or the skip choice."""

    sandbox_file: str
    skip: bool
    sandbox_name: str


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
        # Before anything else a real run does, so no later refusal hides the question.
        if not config_folder_exists():
            return _handle_missing_config_folder(args.dry_run)

        # A dry run writes nothing (requirement 9); it reads a missing shipped file from the package instead.
        if not args.dry_run:
            _add_missing_sandboxes()

        tasks_file = resolve_dir(args.dir)
        _note_ignored_folder_sandbox(args.dir)
        config = load(dry_run=args.dry_run)
        apply_sandbox_default(args, config.sandbox)
        answer = resolve_permissions(
            args.sandbox_settings, args.yolo, args.sandbox, args.dry_run
        )
        # An explicitly empty value counts as passed, and is bad.
        if args.action_timeout is not None:
            check_action_timeout(args.action_timeout)

        check_reset_flag(args.reset_failed, args.dry_run)
        if wants_tui(args):
            return _open_view(args, tasks_file, answer)

        missing = _missing_flags(tasks_file)
        if missing:
            _report_missing(parser, missing)
            return UsageError.exit_code

        session = session_args(answer, args.dry_run)
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
                answer.sandbox_file,
                answer.sandbox_name,
                _added_on_real_run(answer, args.dry_run),
            ),
            stop,
        )

    return _run(
        start(
            tasks_file,
            action_timeout,
            session,
            args.reset_failed,
            sandbox_settings=answer.sandbox_file,
            stop=stop,
        ),
        stop,
    )


def _add_missing_sandboxes() -> None:
    """Add the shipped sandboxes the folder lacks, saying so for each, so a file that comes back is not a surprise."""
    for path in add_missing():
        print(f"Added {path}")


def _note_ignored_folder_sandbox(dir: str) -> None:
    """Tell a user upgrading that the run folder's sandbox.json no longer answers the permission question.

    The file is left alone and the run goes on as if it were absent.
    """
    if not dir:
        return

    path = Path(dir) / "sandbox.json"
    if path.is_file():
        print(f"{path} is no longer read; pick a sandbox with --sandbox <name>")


def _install_skill() -> int:
    """Install the bundled skill and print where it went."""
    try:
        print(install())
    except AlrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    return 0


def _handle_missing_config_folder(dry_run: bool) -> int:
    """Offer the setup on a real run at a terminal, and refuse everywhere else.

    A dry run never asks and never writes (requirement 9), and without a
    terminal nobody can answer, so both refuse instead of offering the setup.
    """
    if dry_run or not _terminal_attached():
        raise _setup_missing("run alr in a terminal once to create it")

    return _set_up_config()


def _set_up_config() -> int:
    """Ask before creating the config folder, then stop so the user starts the run themselves.

    Nothing is created without a typed yes. The questions and answers go to
    stdout, so a refusal stays the first thing on stderr.
    """
    print(_setup_notice())
    answer = _read_answer("Create them? [y/N] ")
    if answer.strip().lower() not in _YES_ANSWERS:
        print("Nothing was created.")
        raise _setup_missing("run alr again and answer y to create it")

    create_folder()
    print(
        f"The configuration is set up in {config_dir()}. "
        "Run alr again to start your run."
    )
    return 0


def _setup_missing(advice: str) -> RefusedError:
    """Build the refusal for a config folder that is not set up, ending in what to do about it."""
    return RefusedError(
        CONFIG_SETUP_MISSING,
        f"the config setup is missing ({config_dir()}); {advice}",
    )


def _setup_notice() -> str:
    """Say every folder and file setting up the configuration creates, and where."""
    listing = "\n".join(f"  {path}" for path in paths_to_create())
    return f"alr needs to set up its configuration before it can run. It will create:\n{listing}"


def _read_answer(prompt: str) -> str:
    """Ask on the terminal and return the reply; the end of input counts as an empty reply.

    The prompt is printed here and input() gets none: without the readline
    module, input() writes its prompt to stderr, and a refusal must be the
    first thing on stderr.
    """
    print(prompt, end="", flush=True)
    try:
        return input()
    except EOFError:
        # End the question's line, so the next message does not follow the prompt on it.
        print()
        return ""


def _terminal_attached() -> bool:
    """Report whether a person can answer: stdin and stdout are both terminals."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def wants_tui(args: argparse.Namespace) -> bool:
    """Report whether this run opens the full-screen view: a terminal, no --plain, no dry run."""
    if args.plain or args.dry_run:
        return False

    return _terminal_attached()


def _open_view(args: argparse.Namespace, tasks_file: str, answer: Answer) -> int:
    """Open the full-screen view with what the flags already answer."""
    # Validates a given settings file; no run starts here.
    session_args(answer, dry_run=True)
    sandbox_file = answer.sandbox_file
    settings = RunSettings(
        tasks_file=tasks_file,
        sandbox_file=sandbox_file,
        permissions=_permissions(sandbox_file, args.yolo),
        action_timeout=args.action_timeout or "",
        reset_failed=args.reset_failed,
    )
    return run_app(settings)


def _permissions(sandbox_file: str, skip: bool) -> Permissions:
    """Name the answer the flags give: skip, a sandbox from a file, or none yet."""
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
        help="Run folder holding tasks.yaml; required with --plain or --dry-run, and the full-screen view asks for it when it is missing",
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
        help="Path to a Claude Code settings JSON file; sessions run in Claude's sandbox with it. A run needs this, --sandbox, or --yolo",
    )
    parser.add_argument(
        "--sandbox",
        default=None,
        metavar="<name>",
        help="Sandbox sessions with the named file from ~/.local/alr/sandboxes/",
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


def resolve_permissions(
    sandbox_settings: str, skip: bool, name: str | None, dry_run: bool
) -> Answer:
    """Return the permissions the flags choose; --sandbox turns a name into the file it names.

    The conflicts are refused before the name is looked up, so a typo beside
    --yolo is reported as the conflict it is.
    """
    if name is None:
        return Answer(sandbox_settings, skip, "")

    if sandbox_settings or skip:
        raise UsageError("--sandbox cannot be used with --sandbox-settings or --yolo")

    return Answer(str(find(name, dry_run=dry_run)), False, name)


def apply_sandbox_default(
    args: argparse.Namespace, default: SandboxDefault | None
) -> None:
    """Fill in the permission flag nobody passed from the config's sandbox default.

    It sets the same fields the real flags set, so the dry run, the view, and
    start need no change. A flag wins; with one, or with no default, nothing
    changes. The default applies with or without --dir: no file in the run
    folder can answer the question any more.

    _main runs this at startup, before resolve_permissions.
    """
    if default is None:
        return

    if args.sandbox is not None or args.sandbox_settings or args.yolo:
        return

    if default.name == _YOLO:
        args.yolo = True
        return

    args.sandbox = default.name


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


def session_args(answer: Answer, dry_run: bool) -> list[str]:
    """Return the claude flags that set what a session may do.

    A run must choose a sandbox or ask by name to go without one: there is no
    default, so nobody runs unsandboxed by accident. A dry run starts no
    session and needs neither, but its sandbox file is still checked.
    """
    if answer.sandbox_file and answer.skip:
        raise UsageError("--sandbox-settings and --yolo cannot be used together")

    if answer.sandbox_file:
        return _sandbox_flags(answer, dry_run)

    if answer.skip:
        return bypass_args()

    if dry_run:
        return []

    raise UsageError(
        "pass --sandbox <name>, --sandbox-settings <path>, or --yolo to run without a sandbox; "
        f"the sandboxes are: {names_text()}"
    )


def _sandbox_flags(answer: Answer, dry_run: bool) -> list[str]:
    """Return the flags for a sandboxed session, reading the settings from the file or, for a dry run, the package."""
    if _added_on_real_run(answer, dry_run):
        # The file is not in the folder yet; check the copy a real run would add.
        return settings_args(read_text(Path(answer.sandbox_file)))

    return sandbox_args(answer.sandbox_file)


def _added_on_real_run(answer: Answer, dry_run: bool) -> bool:
    """Report whether a dry run names a shipped sandbox whose file a real run would add first.

    A sandbox found by name but absent from the folder can only be that: find
    refuses every other missing name.
    """
    if not dry_run or not answer.sandbox_name:
        return False

    return not Path(answer.sandbox_file).exists()


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
