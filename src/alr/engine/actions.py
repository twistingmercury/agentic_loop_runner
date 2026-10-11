"""Actions: commands alr runs itself after a session reports completed."""

import asyncio
import time
from collections.abc import Sequence

from alr.engine.frontend import FrontEnd
from alr.errors import RunCancelledError, quote
from alr.tasks import COMPLETED, FAILED, Action, Task, parse_timeout

# Applies to every action that neither the flag nor the file gives a timeout;
# there is deliberately no way to run an action without a limit.
DEFAULT_ACTION_TIMEOUT = "10m"


def first_line(text: str) -> str:
    """The part of a multi-line value that messages quote, so it stays on one line."""
    return text.split("\n", 1)[0]


def action_limit(action: Action, override: str) -> tuple[str, str]:
    """Pick the timeout that applies, as written where it came from, and name that source.

    The text stays as written so a failure message can repeat it.
    """
    if override:
        return override, "flag"

    if action.timeout:
        return action.timeout, "action"

    return DEFAULT_ACTION_TIMEOUT, "default"


def action_failure(
    action: Action, limit: str, error: str | None, timed_out: bool
) -> str:
    """Word an action's failure; a timeout must not read as the kill that ended it."""
    name = quote(first_line(action.command))
    if timed_out:
        return f"action {name} timed out after {limit}"

    return f"action {name} failed: {error}"


async def run_actions(
    task: Task, actions: Sequence[Action], override: str, front_end: FrontEnd
) -> tuple[str, str]:
    """Run the file's actions in order; the first that fails makes the task failed."""
    for action in actions:
        limit, _ = action_limit(action, override)
        try:
            error, timed_out = await _run_action(task, action, limit, front_end)
        except asyncio.CancelledError:
            # Nothing is saved, so the next run picks the task up again.
            raise RunCancelledError(
                f"task {task.id}: {task.name}: stopped during action {quote(first_line(action.command))}"
            ) from None

        if error is None and not timed_out:
            continue

        return FAILED, action_failure(action, limit, error, timed_out)

    return COMPLETED, ""


async def _run_action(
    task: Task, action: Action, limit: str, front_end: FrontEnd
) -> tuple[str | None, bool]:
    """Run one action and report how it ended; a cancelled action reports nothing."""
    started = time.monotonic()
    error, timed_out = await _run_limited(task, action, limit, front_end)
    front_end.action_finished(
        task, action, limit, error, timed_out, time.monotonic() - started
    )
    return error, timed_out


async def _run_limited(
    task: Task, action: Action, limit: str, front_end: FrontEnd
) -> tuple[str | None, bool]:
    """Run one action under its limit and return its exit text and whether the limit ended it."""
    try:
        seconds = parse_timeout(limit)
    except ValueError as exc:
        return str(exc), False

    try:
        async with asyncio.timeout(seconds):
            return await front_end.run_action(task, action), False
    except TimeoutError:
        return None, True
