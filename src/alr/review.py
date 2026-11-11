"""The review: a fresh read-only Claude session reads the task file with the alr-task-review skill.

It is the one place alr starts a model on its own, so every review that starts
leaves a log beside the run logs, whether it finds nothing, finds problems, or
fails.
"""

import asyncio
import json
import os
import shutil
import signal
import sys
from datetime import datetime
from pathlib import Path
from subprocess import PIPE
from typing import TextIO

from alr.durations import parse_duration
from alr.engine import proc
from alr.engine.stop import Stop
from alr.errors import ReviewError
from alr.skillinstall import skills_folder

DEFAULT_TIMEOUT = "10m"

_SKILL_NAME = "alr-task-review"

# Reading tools and the Skill tool the review itself needs. Nothing in this list
# can edit a file or run a command.
_READING_TOOLS = "Read,Grep,Glob,Skill"

# The log holds what the model said about the user's project, so it is private.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


async def review(
    out: TextIO, tasks_file: str, sandbox_file: str, action_timeout: str, stop: Stop
) -> None:
    """Ask a fresh Claude session to review the task file, print what it says, and log it.

    sandbox_file is "" when the run would use none, and action_timeout is ""
    for the ten minute default. A review that cannot run or finish raises
    ReviewError, after writing the failure to the log when the review had
    started. What the review finds never raises, and neither does a log that
    cannot be written: that is a warning on stderr. A stop cancels this
    coroutine; the log says which signal it was, from stop, and the
    cancellation goes on.
    """
    # Both found before anything prints, so a machine without claude or without
    # the skill gets one error line and no log.
    claude = _find_claude()
    _check_skill_installed()
    started = _now()
    out.write(f"review: asking Claude to read {tasks_file} (this uses tokens)\n")
    # The session takes minutes; the line must show before it ends.
    out.flush()
    body = "error: the review ended without a result"
    try:
        reply = await _session(claude, tasks_file, sandbox_file, action_timeout)
        body = reply
        # Printed before the log is written, so a log that fails never costs the reply.
        _write_safely(out, _with_newline(reply))
    except ReviewError as exc:
        body = f"error: {exc}"
        raise
    except asyncio.CancelledError:
        body = f"stopped by {_signal_name(stop.signum)}"
        raise
    finally:
        # In finally, so a stop or a failing print at any point still leaves a log.
        log_path = _write_log(started, tasks_file, sandbox_file, body)

    if log_path is not None:
        out.write(f"review log: {log_path}\n")


def _write_safely(out: TextIO, text: str) -> None:
    """Write text to out, replacing characters the stream's encoding cannot show.

    A reply is the model's words, so it can hold a character a terminal set to
    ascii cannot print. That must cost a replacement character, not the log.
    """
    encoding = out.encoding or "utf-8"
    shown = text.encode(encoding, errors="replace").decode(encoding)
    out.write(shown)


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return str(signum)


def _find_claude() -> str:
    claude = shutil.which("claude")
    if claude is None:
        raise ReviewError("claude is not on the PATH")

    return claude


def _check_skill_installed() -> None:
    """Raise ReviewError unless the review's skill is in the skills folder.

    Without it the session would be started and billed for a review it cannot
    do, for example when the startup sync could not write the folder.
    """
    folder = skills_folder()
    # SKILL.md itself, since an empty skill folder is not a skill.
    if not (folder / _SKILL_NAME / "SKILL.md").is_file():
        raise ReviewError(f"the {_SKILL_NAME} skill is not installed in {folder}")


def _now() -> datetime:
    """The start time of a review; a test replaces it to start two reviews in one second."""
    return datetime.now().astimezone()


async def _session(
    claude: str, tasks_file: str, sandbox_file: str, action_timeout: str
) -> str:
    """Run the review session and return its reply, or raise ReviewError."""
    limit = action_timeout or DEFAULT_TIMEOUT
    seconds = parse_duration(limit)
    argv = _argv(claude, tasks_file, sandbox_file)
    prompt = _prompt(tasks_file, sandbox_file)
    try:
        process = await proc.start(argv, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    except OSError as exc:
        raise ReviewError(f"claude could not start: {exc.strerror}") from exc

    stdout: list[bytes] = []
    stderr: list[bytes] = []
    drain = _pump(process, prompt.encode("utf-8"), stdout, stderr)
    try:
        async with asyncio.timeout(seconds):
            # Bounded: the review is claude's own exit and reply, so a
            # background child that keeps the pipes open must not hold it up (#61).
            error = await proc.finish(process, drain, bounded=True)
    except TimeoutError as exc:
        raise ReviewError(f"claude did not finish within {limit}") from exc

    _kill_leftovers(process)
    if error is not None:
        raise ReviewError(_exit_reason(error, _text(stderr)))

    reply = _text(stdout)
    # Whitespace alone is no review: even a clean file gets NO FINDINGS.
    if not reply.strip():
        raise ReviewError("claude replied with nothing")

    return reply


def _kill_leftovers(process: asyncio.subprocess.Process) -> None:
    """SIGKILL what the session left in its group, so nothing it started outlives the review."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    # macOS reports EPERM, not ESRCH, for a group that holds only a zombie.
    except (ProcessLookupError, PermissionError):
        pass


async def _pump(
    process: asyncio.subprocess.Process,
    prompt: bytes,
    stdout: list[bytes],
    stderr: list[bytes],
) -> None:
    """Feed the prompt and collect both outputs at the same time, so a full pipe never blocks the session."""
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("the review was started without stdin and output pipes")

    await asyncio.gather(
        proc.feed_stdin(process.stdin, prompt),
        proc.read_lines(process.stdout, stdout.append),
        proc.read_lines(process.stderr, stderr.append),
    )


def _text(pieces: list[bytes]) -> str:
    """Join what the session wrote, replacing bytes that are not UTF-8."""
    return b"".join(pieces).decode("utf-8", errors="replace")


def _argv(claude: str, tasks_file: str, sandbox_file: str) -> list[str]:
    """Build the command line: a new read-only session, with no history and no model chosen."""
    # The run folder and the sandbox file's folder are often outside the
    # project the session starts in.
    folders = [str(Path(tasks_file).parent)]
    if sandbox_file:
        folders.append(str(Path(sandbox_file).parent))

    # --tools removes every other tool outright. --allowedTools approves these
    # ahead of time, since nobody can answer a prompt. dontAsk refuses
    # anything else and replaces a default mode from the user's settings, such
    # as one that skips permission checks. --tools does not cover MCP tools, so
    # --disallowedTools denies them by name pattern. --add-dir takes a list, so it goes last.
    return [
        claude,
        "--print",
        "--tools",
        _READING_TOOLS,
        "--allowedTools",
        _READING_TOOLS,
        "--disallowedTools",
        "mcp__*",
        "--permission-mode",
        "dontAsk",
        "--add-dir",
        *folders,
    ]


def _prompt(tasks_file: str, sandbox_file: str) -> str:
    # Quoted, so a path with a line break cannot add a line to the prompt.
    if sandbox_file:
        quoted_sandbox = json.dumps(sandbox_file)
        sandbox = f"The sandbox settings file the run will use is {quoted_sandbox}."
    else:
        sandbox = "There is no sandbox settings file for this run."

    quoted_tasks = json.dumps(tasks_file)
    return (
        f"Use the {_SKILL_NAME} skill to review the alr task file {quoted_tasks}.\n"
        f"{sandbox}\n"
        "Reply with what the skill says to report and nothing else.\n"
    )


def _exit_reason(exit_text: str, stderr: str) -> str:
    """Say how claude ended, with the last line it wrote to stderr when it wrote any."""
    reason = f"claude failed ({exit_text})"
    lines = stderr.strip().splitlines()
    if not lines:
        return reason

    return f"{reason}: {lines[-1]}"


def _with_newline(text: str) -> str:
    """End text with a newline unless it has one: the only change made to a reply."""
    if text.endswith("\n"):
        return text

    return f"{text}\n"


def _write_log(
    started: datetime, tasks_file: str, sandbox_file: str, body: str
) -> Path | None:
    """Write the header and body to a new log and return its path, or warn and return None.

    The logs folder is made when it is missing. A log that cannot be written
    never raises: the review's reply, or its own error, matters more.
    """
    header = _log_header(started, tasks_file, sandbox_file)
    text = f"{header}\n{_with_newline(body)}"
    # The run log's timestamp form, in local time.
    stamp = started.strftime("%Y%m%dT%H%M%S")
    logs = Path(tasks_file).parent / "logs"
    try:
        logs.mkdir(mode=_DIR_MODE, exist_ok=True)
    except OSError as exc:
        _warn_log_not_written(logs / _log_name(stamp, 0), exc)
        return None

    return _create_log(logs, stamp, text)


def _log_header(started: datetime, tasks_file: str, sandbox_file: str) -> str:
    # Quoted, so a path with a line break cannot add a line to the header.
    sandbox = json.dumps(sandbox_file) if sandbox_file else "none"
    return (
        f"time: {started.isoformat(timespec='seconds')}\n"
        f"task file: {json.dumps(tasks_file)}\n"
        f"sandbox file: {sandbox}\n"
    )


def _create_log(logs: Path, stamp: str, text: str) -> Path | None:
    """Write text to the first free log name in logs and return its path, or None after a warning."""
    number = 0
    while True:
        try:
            return _write_new_log(logs / _log_name(stamp, number), text)
        except FileExistsError:
            # Another review started in the same second: its log is left alone.
            number += 1


def _write_new_log(path: Path, text: str) -> Path | None:
    """Write text to a new file at path; return None after a warning when it cannot be written.

    FileExistsError goes on to the caller, which tries the next name.
    """
    try:
        _write_new_file(path, text)
    except FileExistsError:
        # A subclass of OSError, so it is named first to get past the warning below.
        raise
    except OSError as exc:
        _warn_log_not_written(path, exc)
        return None

    return path


def _log_name(stamp: str, number: int) -> str:
    if number == 0:
        return f"review-{stamp}.log"

    return f"review-{stamp}-{number}.log"


def _write_new_file(path: Path, text: str) -> None:
    """Create path at the private mode and write text; raise FileExistsError when it exists."""
    # O_EXCL fails on an existing file, so no other review's log is replaced, and
    # the file is private from its first byte.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8", errors="replace") as log:
        log.write(text)


def _warn_log_not_written(path: Path, exc: OSError) -> None:
    print(f"warning: review log: cannot write {path}: {exc.strerror}", file=sys.stderr)
