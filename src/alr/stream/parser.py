"""Turn one line of claude's stream-json output into what a run shows."""

import json
from typing import Any

from alr.engine.gates import first_line
from alr.engine.result import replace_lone_surrogates

# The input field each tool's target comes from.
_TARGET_FIELDS = {
    "Bash": "command",
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "Grep": "pattern",
    "Glob": "pattern",
}


def parse_stream_line(line: bytes) -> tuple[list[str], str, bool]:
    """Return the activity lines and, for a result event, its text.

    Lines that do not decode to an object, and event types other than
    assistant and result, give nothing.
    """
    try:
        event = json.loads(line)
    except (ValueError, RecursionError):
        # Deeply nested garbage overflows the decoder's stack; like any invalid
        # JSON, it is only a line to drop.
        return [], "", False

    if not isinstance(event, dict):
        return [], "", False

    kind = event.get("type")
    if kind == "result":
        return [], _text(event.get("result")), True

    if kind != "assistant":
        return [], "", False

    activity: list[str] = []
    for block in _blocks(event):
        activity.extend(_block_activity(block))

    return activity, "", False


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []

    content = message.get("content")
    if not isinstance(content, list):
        return []

    return [block for block in content if isinstance(block, dict)]


def _block_activity(block: dict[str, Any]) -> list[str]:
    kind = block.get("type")
    if kind == "text":
        return _text_activity(_text(block.get("text")))

    if kind == "tool_use":
        return [_tool_activity(_text(block.get("name")), block.get("input"))]

    return []


def _text_activity(text: str) -> list[str]:
    clean = replace_lone_surrogates(text)
    return [line for line in clean.split("\n") if line.strip()]


def _tool_activity(name: str, tool_input: Any) -> str:
    """The "→ <tool> <target>" line, with only the target's first line so a
    multi-line command stays one activity line."""
    field = _TARGET_FIELDS.get(name)
    if field is None or not isinstance(tool_input, dict):
        return f"→ {name}"

    target = replace_lone_surrogates(first_line(_text(tool_input.get(field))))
    if not target:
        return f"→ {name}"

    return f"→ {name} {target}"
