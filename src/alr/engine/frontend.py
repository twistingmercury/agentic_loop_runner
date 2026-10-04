"""What the engine asks of a front end: run the processes and note the progress."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from alr.tasks import Gate, Task


@dataclass(frozen=True)
class SessionResult:
    """What a Claude session left behind."""

    text: str
    """Where the result line is read from."""

    error: str | None
    """Go-style exit text; None on a zero exit."""


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

    async def run_gate(self, task: Task, gate: Gate) -> str | None:
        """Run this gate command under this limit; return the exit."""
        ...

    def gate_finished(
        self,
        task: Task,
        gate: Gate,
        limit: str,
        error: str | None,
        timed_out: bool,
        seconds: float,
    ) -> None:
        """Note a gate's result and duration."""
        ...

    def commit_started(self, task: Task) -> None:
        """Note that something is staged and a commit follows."""
        ...

    async def run_git(self, task: Task, argv: Sequence[str], cwd: str) -> str | None:
        """Run this git command; return the exit."""
        ...

    def committed(self, task: Task, commit_hash: str) -> None:
        """Note the new commit hash."""
        ...

    def task_finished(self, task: Task, error: str | None, seconds: float) -> None:
        """Note the final state and duration."""
        ...

    def run_done(self, error: str | None, seconds: float) -> None:
        """Note the end of the run, with its error if any."""
        ...
