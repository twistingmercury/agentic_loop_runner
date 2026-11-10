"""The review: a fresh read-only Claude session reads the task file with the alr-task-review skill.

It is the one place alr starts a model on its own, so every review that starts
leaves a log beside the run logs, whether it finds nothing, finds problems, or
fails.
"""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TextIO

from alr.durations import parse_duration
from alr.errors import ReviewError

DEFAULT_TIMEOUT = "10m"

# Reading tools and the Skill tool the review itself needs. Nothing in this list
# can edit a file or run a command.
_READING_TOOLS = "Read,Grep,Glob,Skill"

# The log holds what the model said about the user's project, so it is private.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def review(
    out: TextIO, tasks_file: str, sandbox_file: str, action_timeout: str
) -> None:
    """Ask a fresh Claude session to review the task file, print what it says, and log it.

    sandbox_file is "" when the run would use none, and action_timeout is ""
    for the ten minute default. A review that cannot run or finish raises
    ReviewError, after writing the failure to the log when the review had
    started. What the review finds never raises.
    """
    # Found before anything prints, so a machine without claude gets one error line.
    claude = _find_claude()
    started = datetime.now().astimezone()
    log_path = _log_path(tasks_file, started)
    out.write(f"review: asking Claude to read {tasks_file} (this uses tokens)\n")
    # The session takes minutes; the line must show before it ends.
    out.flush()
    try:
        reply = _run_session(claude, tasks_file, sandbox_file, action_timeout)
    except ReviewError as exc:
        _write_log(log_path, started, tasks_file, sandbox_file, f"error: {exc}")
        raise

    _write_log(log_path, started, tasks_file, sandbox_file, reply)
    out.write(_with_newline(reply))
    out.write(f"review log: {log_path}\n")


def _find_claude() -> str:
    claude = shutil.which("claude")
    if claude is None:
        raise ReviewError("claude is not on the PATH")

    return claude


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

    subprocess.run does not wait for its child after a KeyboardInterrupt, so a
    stop (the signal handler raises one) would leave the killed child unreaped
    when alr exits. This kills and waits for the child on every way out.
    """
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=seconds)
    except BaseException:
        process.kill()
        process.wait()
        _close_pipes(process)
        raise

    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _close_pipes(process: "subprocess.Popen[str]") -> None:
    """Close the pipes that the interrupted communicate left open."""
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()


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
    # as one that skips permission checks. --add-dir takes a list, so it goes last.
    return [
        claude,
        "--print",
        "--tools",
        _READING_TOOLS,
        "--allowedTools",
        _READING_TOOLS,
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
        f"Use the alr-task-review skill to review the alr task file {tasks_file}.\n"
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


def _log_path(tasks_file: str, started: datetime) -> Path:
    # The run log's timestamp form, in local time.
    stamp = started.strftime("%Y%m%dT%H%M%S")
    return Path(tasks_file).parent / "logs" / f"review-{stamp}.log"


def _write_log(
    path: Path, started: datetime, tasks_file: str, sandbox_file: str, body: str
) -> None:
    """Write the header and body to path, making the logs folder when it is missing."""
    header = (
        f"time: {started.isoformat(timespec='seconds')}\n"
        f"task file: {tasks_file}\n"
        f"sandbox file: {sandbox_file or 'none'}\n"
    )
    text = f"{header}\n{_with_newline(body)}"
    try:
        path.parent.mkdir(mode=_DIR_MODE, exist_ok=True)
        # Created empty at the private mode first, so the text is never in a file others can read.
        path.touch(mode=_FILE_MODE)
        path.write_text(text, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ReviewError(f"cannot write {path}: {exc.strerror}") from exc


def _with_newline(text: str) -> str:
    """End text with a newline unless it has one: the only change made to a reply."""
    if text.endswith("\n"):
        return text

    return f"{text}\n"
