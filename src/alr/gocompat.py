"""Text and durations worded the way gralph's Go code words them.

alr is drop-in compatible with gralph, and some of what gralph saves and
prints comes straight from Go's standard library. These helpers keep that
wording in one place.
"""

import json
import os
import re
import signal

_SIGNAL_NAMES = {
    signal.SIGKILL: "killed",
    signal.SIGTERM: "terminated",
    signal.SIGINT: "interrupt",
    signal.SIGHUP: "hangup",
    signal.SIGQUIT: "quit",
    signal.SIGABRT: "aborted",
    signal.SIGSEGV: "segmentation fault",
    signal.SIGPIPE: "broken pipe",
}

_UNIT_SECONDS = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "μs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}

# "ms" is listed before "m" so the longer unit wins.
_DURATION_PART = re.compile(r"(\d+\.?\d*|\.\d+)(ns|us|µs|μs|ms|s|m|h)")


def goquote(text: str) -> str:
    """Quote text as Go's %q does for the characters gate and file names hold."""
    return json.dumps(text, ensure_ascii=False)


def os_error_text(op: str, path: str, exc: OSError) -> str:
    """Word a file error as Go's os package does: `open <path>: <reason>`."""
    reason = os.strerror(exc.errno) if exc.errno else str(exc)
    return f"{op} {path}: {reason[:1].lower()}{reason[1:]}"


def exit_error(returncode: int) -> str | None:
    """Word a process exit as Go's exec.ExitError does; None for a zero exit."""
    if returncode == 0:
        return None

    if returncode > 0:
        return f"exit status {returncode}"

    number = -returncode
    name = (
        _SIGNAL_NAMES.get(number)
        or (signal.strsignal(number) or f"signal {number}").lower()
    )
    return f"signal: {name}"


def not_found_error(name: str) -> str:
    """Word a missing executable as Go's exec.LookPath does."""
    return f"exec: {goquote(name)}: executable file not found in $PATH"


def parse_duration(text: str) -> float:
    """Parse a Go duration string such as 90s or 1h30m into seconds."""
    sign = -1.0 if text.startswith("-") else 1.0
    body = text[1:] if text[:1] in ("+", "-") else text
    if body == "0":
        return 0.0

    if body == "":
        raise ValueError(f"invalid duration {text!r}")

    total = 0.0
    position = 0
    while position < len(body):
        part = _DURATION_PART.match(body, position)
        if part is None:
            raise ValueError(f"invalid duration {text!r}")

        total += float(part.group(1)) * _UNIT_SECONDS[part.group(2)]
        position = part.end()

    return sign * total


def format_duration(seconds: float) -> str:
    """Format seconds as Go's Duration.String() after rounding to the millisecond."""
    millis = int(seconds * 1000 + 0.5)
    if millis == 0:
        return "0s"

    if millis < 1000:
        return f"{millis}ms"

    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    whole, fraction = divmod(rest, 1000)
    secs = str(whole)
    if fraction:
        secs += "." + f"{fraction:03d}".rstrip("0")

    if hours:
        return f"{hours}h{minutes}m{secs}s"

    if minutes:
        return f"{minutes}m{secs}s"

    return f"{secs}s"
