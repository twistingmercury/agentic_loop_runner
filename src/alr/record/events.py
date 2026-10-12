"""Progress reports from a run on the stream path."""

from dataclasses import dataclass

from alr.tasks import Action, RunResult, Task


@dataclass(frozen=True)
class TaskStarted:
    task: Task


@dataclass(frozen=True)
class Activity:
    task: Task
    line: str


@dataclass(frozen=True)
class SessionFinished:
    task: Task  # carries the session's state and error
    seconds: float


@dataclass(frozen=True)
class ActionStarted:
    task: Task
    action: Action


@dataclass(frozen=True)
class ActionFinished:
    task: Task
    action: Action
    limit: str  # the timeout the action ran under, as written
    error: str | None  # None when the action passed or timed out
    timed_out: bool
    seconds: float


@dataclass(frozen=True)
class TaskFinished:
    task: Task  # carries the final state and error
    error: str | None  # the run's failure text when the task failed
    seconds: float


@dataclass(frozen=True)
class RunDone:
    error: str | None
    seconds: float
    result: RunResult = RunResult.COMPLETED
    exit_code: int = 0


Event = (
    TaskStarted
    | Activity
    | SessionFinished
    | ActionStarted
    | ActionFinished
    | TaskFinished
    | RunDone
)
