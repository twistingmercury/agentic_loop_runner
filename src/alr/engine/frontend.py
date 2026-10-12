"""What the engine asks of a front end: run the processes and note the progress."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from alr.tasks import Action, RunResult, Task


@dataclass(frozen=True)
class SessionResult:
    """What a Claude session left behind."""

    text: str
    """Where the result line is read from."""

    error: str | None
    """`exit status 3` or `signal: SIGKILL`; None on a zero exit."""


class FrontEnd(Protocol):
    """The calls the engine makes while it runs the tasks."""

    session_flags: Sequence[str]
    """The Claude flags that follow `--print`."""

    def task_skipped(self, task: Task) -> None:
        """Note that a task was already completed and is not run."""
        ...

    def task_started(self, task: Task) -> None:
        """Note that a task is starting."""
        ...

    async def run_session(
        self, task: Task, argv: Sequence[str], stdin_text: str
    ) -> SessionResult:
        """Run Claude with this argv and stdin; return the result text and the exit."""
        ...

    def session_finished(self, task: Task, seconds: float) -> None:
        """Note the session outcome and duration."""
        ...

    async def run_action(
        self, task: Task, action: Action, env: Mapping[str, str]
    ) -> str | None:
        """Run this action command with env added to alr's own environment; return the exit.

        The engine applies the time limit around the call.
        """
        ...

    def action_finished(
        self,
        task: Task,
        action: Action,
        limit: str,
        error: str | None,
        timed_out: bool,
        seconds: float,
    ) -> None:
        """Note an action's result and duration."""
        ...

    def task_finished(self, task: Task, error: str | None, seconds: float) -> None:
        """Note the final state and duration."""
        ...

    def run_done(
        self, error: str | None, seconds: float, result: RunResult, exit_code: int
    ) -> None:
        """Note the end of the run.

        error is None only for a run that finished cleanly; otherwise it is the
        text of whatever ended the run, a stop included. result names how it
        ended and exit_code is the code alr will exit with.
        """
        ...
