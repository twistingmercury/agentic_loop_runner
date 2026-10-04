"""The result line a session ends with, and the rule that turns it into a task outcome."""

import json
import re

from alr.tasks import COMPLETED, FAILED

_NO_RESULT = "no valid result line in session output"
_FAILED_WITHOUT_ERROR = "session reported failed with no error"
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def parse_result(line: str) -> tuple[str, str] | None:
    """Return (state, error) from a result line, or None when it is not a valid one."""
    try:
        data = json.loads(line, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        # A line nested deeply enough overflows Python's parser; Go's reports
        # it as invalid JSON.
        return None

    # Go decodes a bare null into an empty result, which then has no state.
    if not isinstance(data, dict):
        return None

    state = _field(data, "state")
    error = _field(data, "error")
    if state is None or error is None:
        return None

    state = state.strip().lower()
    if state not in (COMPLETED, FAILED):
        return None

    return state, _replace_lone_surrogates(error.strip())


def _reject_constant(name: str) -> None:
    """Refuse NaN and Infinity, which Python's json accepts and Go's does not."""
    raise ValueError(f"invalid JSON constant {name}")


def _replace_lone_surrogates(text: str) -> str:
    """A `\\ud800` escape decodes to a character that cannot be written as UTF-8.

    The json module joins a valid pair into one character, so any surrogate
    left over is a lone one.
    """
    return _LONE_SURROGATE.sub("\ufffd", text)


def _field(data: dict[str, object], name: str) -> str | None:
    """Read a string field as Go's encoding/json does, or None when it is not a string.

    Keys match ignoring case and the last match wins. A missing field is
    empty, and a null leaves whatever an earlier match set.
    """
    text = ""
    for key, value in data.items():
        if key.casefold() != name or value is None:
            continue

        if not isinstance(value, str):
            return None

        text = value

    return text


def last_result_line(output: str) -> str:
    """Return the last line that is neither blank nor a Markdown fence marker, trimmed."""
    for raw in reversed(output.split("\n")):
        line = raw.strip()
        if line and not line.startswith("```"):
            return line

    return ""


def outcome(run_error: str | None, last_line: str) -> tuple[str, str]:
    """Resolve the state and error to save for a task whose session was not cancelled."""
    result = parse_result(last_line)
    if run_error is not None:
        if result is not None and result[1]:
            return FAILED, result[1]

        return FAILED, run_error

    if result is None:
        return FAILED, _NO_RESULT

    state, error = result
    if state == COMPLETED:
        return COMPLETED, ""

    return FAILED, error or _FAILED_WITHOUT_ERROR
