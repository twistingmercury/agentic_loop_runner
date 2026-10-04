"""Gates: commands alr runs itself after a session reports completed."""

import asyncio
import time
from collections.abc import Sequence

from alr.engine.frontend import FrontEnd
from alr.errors import RunCancelledError, quote
from alr.tasks import COMPLETED, FAILED, Gate, Task, parse_timeout

# Applies to every gate that neither the flag nor the file gives a timeout;
# there is deliberately no way to run a gate without a limit.
DEFAULT_GATE_TIMEOUT = "10m"


def first_line(text: str) -> str:
    """The part of a multi-line value that messages quote, so it stays on one line."""
    return text.split("\n", 1)[0]


def gate_limit(gate: Gate, override: str) -> tuple[str, str]:
    """Pick the timeout that applies, as written where it came from, and name that source.

    The text stays as written so a failure message can repeat it.
    """
    if override:
        return override, "flag"

    if gate.timeout:
        return gate.timeout, "gate"

    return DEFAULT_GATE_TIMEOUT, "default"


def gate_failure(gate: Gate, limit: str, error: str | None, timed_out: bool) -> str:
    """Word a gate's failure; a timeout must not read as the kill that ended it."""
    name = quote(first_line(gate.cmd))
    if timed_out:
        return f"gate {name} timed out after {limit}"

    return f"gate {name} failed: {error}"


async def run_gates(
    task: Task, gates: Sequence[Gate], override: str, front_end: FrontEnd
) -> tuple[str, str]:
    """Run the file's gates in order; the first that fails makes the task failed."""
    for gate in gates:
        limit, _ = gate_limit(gate, override)
        try:
            error, timed_out = await _run_gate(task, gate, limit, front_end)
        except asyncio.CancelledError:
            # Nothing is saved, so the next run picks the task up again.
            raise RunCancelledError(
                f"task {task.id}: {task.name}: stopped during gate {quote(first_line(gate.cmd))}"
            ) from None

        if error is None and not timed_out:
            continue

        return FAILED, gate_failure(gate, limit, error, timed_out)

    return COMPLETED, ""


async def _run_gate(
    task: Task, gate: Gate, limit: str, front_end: FrontEnd
) -> tuple[str | None, bool]:
    """Run one gate and report how it ended; a cancelled gate reports nothing."""
    started = time.monotonic()
    error, timed_out = await _run_limited(task, gate, limit, front_end)
    front_end.gate_finished(
        task, gate, limit, error, timed_out, time.monotonic() - started
    )
    return error, timed_out


async def _run_limited(
    task: Task, gate: Gate, limit: str, front_end: FrontEnd
) -> tuple[str | None, bool]:
    """Run one gate under its limit and return its exit text and whether the limit ended it."""
    try:
        seconds = parse_timeout(limit)
    except ValueError as exc:
        return str(exc), False

    try:
        async with asyncio.timeout(seconds):
            return await front_end.run_gate(task, gate), False
    except TimeoutError:
        return None, True
