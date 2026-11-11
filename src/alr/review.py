"""The review: a fresh read-only Claude session reads the task file with the alr-task-review skill.

It is the one place alr starts a model on its own, so every review that starts
leaves a log beside the run logs, whether it finds nothing, finds problems, or
fails.
"""

import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO, TextIO

from alr.durations import parse_duration
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


def review(
    out: TextIO, tasks_file: str, sandbox_file: str, action_timeout: str, stop: Stop
) -> None:
    """Ask a fresh Claude session to review the task file, print what it says, and log it.

    sandbox_file is "" when the run would use none, and action_timeout is ""
    for the ten minute default. A review that cannot run or finish raises
    ReviewError, after writing the failure to the log when the review had
    started. What the review finds never raises, and neither does a log that
    cannot be written: that is a warning on stderr. A stop arrives as a
    KeyboardInterrupt raised by the caller's signal handler, which records the
    signal in stop first; the log says which signal it was, and the
    interrupt goes on.
    """
    # Both found before anything prints, so a machine without claude or without
    # the skill gets one error line and no log.
    claude = _find_claude()
    _check_skill_installed()
    started = _now()
    out.write(f"review: asking Claude to read {tasks_file} (this uses tokens)\n")
    # The session takes minutes; the line must show before it ends.
    out.flush()
    try:
        reply = _run_session(claude, tasks_file, sandbox_file, action_timeout)
    except ReviewError as exc:
        _write_log(started, tasks_file, sandbox_file, f"error: {exc}")
        raise
    except KeyboardInterrupt:
        stopped = f"stopped by {signal.Signals(stop.signum).name}"
        _write_log(started, tasks_file, sandbox_file, stopped)
        raise

    # Printed before the log is written, so a log that fails never costs the reply.
    out.write(_with_newline(reply))
    log_path = _write_log(started, tasks_file, sandbox_file, reply)
    if log_path is not None:
        out.write(f"review log: {log_path}\n")


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
    if not (folder / _SKILL_NAME / "SKILL.md").is_file():
        raise ReviewError(f"the {_SKILL_NAME} skill is not installed in {folder}")


def _run_session(
    claude: str, tasks_file: str, sandbox_file: str, action_timeout: str
) -> str:
    """Run the review session and return its reply, or raise ReviewError."""
    limit = action_timeout or DEFAULT_TIMEOUT
    argv = _argv(claude, tasks_file, sandbox_file)
    prompt = _prompt(tasks_file, sandbox_file)
    try:
        result = _communicate(argv, prompt, parse_duration(limit))
    except subprocess.TimeoutExpired as exc:
        raise ReviewError(f"claude did not finish within {limit}") from exc
    except OSError as exc:
        raise ReviewError(f"claude could not start: {exc.strerror}") from exc

    if result.returncode != 0:
        raise ReviewError(_exit_reason(result.returncode, result.stderr))

    # Whitespace alone is no review: even a clean file gets NO FINDINGS.
    if not result.stdout.strip():
        raise ReviewError("claude replied with nothing")

    return result.stdout


def _communicate(
    argv: list[str], prompt: str, seconds: float
) -> subprocess.CompletedProcess[str]:
    """Run argv with the prompt on stdin and return what it wrote.

    Stdin, stdout, and stderr are temporary files, not pipes. Waiting on pipes
    only ends when every holder closes them, so a background child that kept
    the output open would turn a finished review into a time out (#61). With
    files the wait ends when claude exits, whatever it left behind.
    """
    with TemporaryFile() as stdin, TemporaryFile() as stdout, TemporaryFile() as stderr:
        stdin.write(prompt.encode("utf-8"))
        stdin.seek(0)
        # Its own group, so one kill reaches everything the session started.
        process = subprocess.Popen(
            argv, stdin=stdin, stdout=stdout, stderr=stderr, process_group=0
        )
        _wait_then_kill_group(process, seconds)
        return subprocess.CompletedProcess(
            argv, process.returncode, _read_text(stdout), _read_text(stderr)
        )


def _wait_then_kill_group(process: "subprocess.Popen[bytes]", seconds: float) -> None:
    """Wait for claude to exit, then kill its group, on every way out.

    The group dies when claude exits normally too, so nothing the session
    started outlives the review. subprocess.run does not wait for its child
    after a KeyboardInterrupt (a stop raises one), which would leave the killed
    child unreaped when alr exits, so the child is waited for here.
    """
    try:
        process.wait(timeout=seconds)
    finally:
        _kill_group(process)
        process.wait()


def _kill_group(process: "subprocess.Popen[bytes]") -> None:
    """Send SIGKILL to the process group claude leads; nothing is left to kill when it is gone."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    # macOS reports EPERM, not ESRCH, for a group that holds only a zombie.
    except (ProcessLookupError, PermissionError):
        pass


def _read_text(file: BinaryIO) -> str:
    """Read everything the session wrote to file, replacing bytes that are not UTF-8."""
    file.seek(0)
    return file.read().decode("utf-8", errors="replace")


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
    if sandbox_file:
        sandbox = f"The sandbox settings file the run will use is {sandbox_file}."
    else:
        sandbox = "There is no sandbox settings file for this run."

    return (
        f"Use the {_SKILL_NAME} skill to review the alr task file {tasks_file}.\n"
        f"{sandbox}\n"
        "Reply with what the skill says to report and nothing else.\n"
    )


def _exit_reason(returncode: int, stderr: str) -> str:
    """Say how claude ended, with the last line it wrote to stderr when it wrote any."""
    reason = f"claude exited with status {returncode}"
    lines = stderr.strip().splitlines()
    if not lines:
        return reason

    return f"{reason}: {lines[-1]}"


def _now() -> datetime:
    """The start time of a review; a test replaces it to start two reviews in one second."""
    return datetime.now().astimezone()


def _write_log(
    started: datetime, tasks_file: str, sandbox_file: str, body: str
) -> Path | None:
    """Write the header and body to a new log and return its path, or warn and return None.

    The logs folder is made when it is missing. A log that cannot be written
    never raises: the review's reply, or its own error, matters more.
    """
    header = (
        f"time: {started.isoformat(timespec='seconds')}\n"
        f"task file: {tasks_file}\n"
        f"sandbox file: {sandbox_file or 'none'}\n"
    )
    text = f"{header}\n{_with_newline(body)}"
    # The run log's timestamp form, in local time.
    stamp = started.strftime("%Y%m%dT%H%M%S")
    logs = Path(tasks_file).parent / "logs"
    try:
        logs.mkdir(mode=_DIR_MODE, exist_ok=True)
    except OSError as exc:
        _warn_log_not_written(logs / _log_name(stamp, 0), exc)
        return None

    number = 0
    while True:
        path = logs / _log_name(stamp, number)
        try:
            _write_new_file(path, text)
        except FileExistsError:
            # Another review started in the same second: its log is left alone.
            number += 1
            continue
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


def _with_newline(text: str) -> str:
    """End text with a newline unless it has one: the only change made to a reply."""
    if text.endswith("\n"):
        return text

    return f"{text}\n"
