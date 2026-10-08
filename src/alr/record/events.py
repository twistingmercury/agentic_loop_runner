"""Progress reports from a run on the stream path."""

from dataclasses import dataclass

from alr.tasks import Gate, Task


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
class GateStarted:
    task: Task
    gate: Gate


@dataclass(frozen=True)
class GateFinished:
    task: Task
    gate: Gate
    limit: str  # the timeout the gate ran under, as written
    error: str | None  # None when the gate passed or timed out
    timed_out: bool
    seconds: float


@dataclass(frozen=True)
class CommitStarted:
    task: Task


@dataclass(frozen=True)
class Committed:
    task: Task
    commit_hash: str


@dataclass(frozen=True)
class TaskFinished:
    task: Task  # carries the final state and error
    error: str | None  # the run's failure text when the task failed
    seconds: float


@dataclass(frozen=True)
class RunDone:
    error: str | None
    seconds: float


Event = (
    TaskStarted
    | Activity
    | SessionFinished
    | GateStarted
    | GateFinished
    | CommitStarted
    | Committed
    | TaskFinished
    | RunDone
)
