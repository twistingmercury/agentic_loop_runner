# alr Engine Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current `alr` code with a drop-in compatible Python port of gralph's engine and plain mode, plus the engine side of the full-screen path (stream parsing, events, run log).

**Architecture:** One async engine (`alr.engine`) holds the rules of a run and never prints. It is handed a front end and calls it to run each subprocess and to report progress. `alr.plain` is the front end for plain mode; `alr.stream` is the front end that emits events and feeds the run log. `alr.cli` always picks plain in this plan.

**Tech Stack:** Python 3.12+, `asyncio`, `argparse`, PyYAML, pytest, pytest-asyncio, ruff, uv.

**Spec:** `docs/design/00_overview.md`, `01_requirements.md`, `02_architectural_decisions.md`, `03_system_architecture.md`. Each file carries its version in its frontmatter.

**Follows gralph v0.12.0 (2026-10-06):** the spec now matches v0.12.0, not `170dc6f`. The shared prompt lives in the task file (`shared.prompt`, ADR-008), `-p/--prompt` and `prompt.md` are gone, `-d` also finds `sandbox.json` (ADR-009), and the unit and e2e suites are split (ADR-010). The text of Tasks 1-9, 12, and 13 below is historical where it still names the prompt file or `-p`; the `.local/sync/` loop brings the code and tests in line.

**Reference source:** `/home/jeremy/dev/gralph` at tag `v0.12.0` (read-only; `170dc6f` for the history below). Paths below that start with `gralph:` are relative to that folder.

## Global Constraints

- Python 3.12 or later; `src/` layout; uv for everything (`uv run ...`).
- Runtime dependency: PyYAML only. Remove `pydantic`. Dev dependencies: `pytest`, `pytest-asyncio`, `ruff`.
- Compatible with gralph at `170dc6f` in what a user sees and types (ADR-007): same flags, task file format, stdin text, Claude argv, outcome rule, and gate and commit behavior. Messages that name the command say `alr`.
- Python-native inside (ADR-007): no code that exists only to imitate a Go library. YAML is read and written with PyYAML's standard safe rules. Error messages must be clear and name the task and field; they do not have to match gralph's wording.
- Known differences from gralph: `--install-skill` is not accepted; no wizard, no TUI, no terminal detection (always plain); `--help` text; `--version` prints one line, `alr version: <version>`; saved YAML is equivalent, not byte-identical; error wording; a plain YAML value such as `yes` or `12:30` must be quoted to be a string; `<<` merge keys are rejected; result-line keys must be the exact lowercase `state` and `error`.
- Import rules: `alr.engine` imports neither `alr.plain`, `alr.stream`, nor `alr.tui`. `alr.plain` and `alr.stream` never import each other. `alr.engine` never writes to stdout or stderr.
- Linux and macOS only. No Windows branches.
- Type-hint every function signature. Catch specific exceptions. Re-raise with `raise ... from ...`.
- Code shape: return early, no deep nesting, a blank line after every `if` block, one job per function, comments say why.
- No inline lint or security suppressions (`# noqa`, `# nosec`).
- Never modify anything under `/home/jeremy/dev/gralph`.
- Gate for every task: `make test` passes (ruff format, ruff lint, unit tests). The e2e suite runs in a container with `make build` (ADR-010).
- End every commit message with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never push.

## Porting Rules

These apply to every task that says "port".

1. **Tests first.** Port the listed Go test functions to pytest before writing the code. Each Go `TestFoo_BarBaz` becomes `test_foo_bar_baz`. Keep every assertion. A Go table test becomes `@pytest.mark.parametrize` with one case per table row and the row's name as the id.
2. **Read the Go test, then the Go code.** The Go files named in each task are the source for behavior. Where this plan and the Go code disagree on what a user sees or types, the Go code wins unless the difference is listed under Global Constraints. How the Go code gets there is not binding.
3. **Error text must be clear, not identical.** Keep the shape gralph uses where the plan gives it (`tasks[<i>] (id <id>): <field>: <problem>`, `gate "<cmd>" failed: <reason>`). When a Go test asserts wording that comes from a Go library (`open <path>: no such file or directory`, `signal: killed`), assert alr's own wording instead: `exc.strerror` for file errors, `proc.exit_text` for exits, `errors.quote` for quoted names.
4. **Async tests** need no decorator: `asyncio_mode = "auto"` is set in `pyproject.toml`.
5. **Tests that depend on file permissions** (unreadable file, unwritable folder) must skip when `os.geteuid() == 0`, because the Docker build runs the tests as root.
6. **Skip, with a one-line comment naming the reason, any Go test that only exercises something out of scope**: the TUI, the wizard, `--install-skill`, the skill check, terminal detection, Windows.
7. **Time in tests.** Go tests that sleep or poll keep the same timeouts. Use `tmp_path` for every file and `monkeypatch` for `PATH`, the working directory, and environment variables.
8. **The fake claude has one set of variables.** gralph had two fakes with overlapping `FAKE_CLAUDE_*` and `FAKECLAUDE_*` variables. alr's fake (`tests/fakeclaude/fake_claude.py`) has one set, all prefixed `FAKECLAUDE_`, and its docstring is the reference. When a Go test sets a variable, use the alr variable that does the same job: output is `FAKECLAUDE_OUTPUT`, stderr text is `FAKECLAUDE_STDERR`, and the record file holds one JSON line of `argv` and `stdin` per call, which replaces the separate args file and attempt log (count the lines to count calls). The ready file holds the fake's PID.

## File Structure

```
pyproject.toml                 modify: dependencies, pytest config
build/Dockerfile               modify: install git for the commit tests
README.md, CHANGELOG.md        modify
src/alr/
  __init__.py                  keep (empty)
  main.py                      rewrite: entry point
  cli.py                       rewrite: flags, startup checks, routing, signals
  errors.py                    create: AlrError family
  durations.py                 create: parse and format durations such as 90s and 1m30.5s
  tasks.py                     rewrite: parse, validate, save
  engine/__init__.py           create (empty)
  engine/frontend.py           create: FrontEnd protocol, SessionResult
  engine/proc.py               create: process groups, stop on cancel
  engine/result.py             create: result line, outcome rule
  engine/sandbox.py            create: session flags
  engine/loop.py               create: load prompt/tasks, run loop
  engine/gates.py              create: gate limit, gate run rule
  engine/commit.py             create: Repo, checks, commit rule
  plain/__init__.py            create (empty)
  plain/frontend.py            create: PlainFrontEnd
  plain/report.py              create: task table, failed-task notice, repo notice
  plain/start.py               create: plain real run
  plain/dryrun.py              create: dry-run report
  stream/__init__.py           create (empty)
  stream/events.py             create: event dataclasses
  stream/parser.py             create: stream-json line parsing
  stream/frontend.py           create: StreamFrontEnd
  stream/runlog.py             create: run log writer
  looper.py, prompt.py         delete
tests/
  conftest.py                  create: fake claude fixture, git repo fixture
  fakeclaude/fake_claude.py    create: scriptable stand-in for claude
  unit/test_*.py               create: one per module
  e2e/conftest.py              create: run_alr helper
  e2e/test_*.py                create: black-box tests
  test_cli.py, test_looper.py, test_main.py, test_prompt.py, test_tasks.py, data/   delete
```

## Shared Interfaces

Every task implicitly consumes these. They are defined by the task named in brackets.

```python
# alr/errors.py  [Task 1]
class AlrError(Exception): ...
class UsageError(AlrError): ...
class TaskFileError(AlrError): ...
class PromptError(AlrError): ...
class FailedTasksError(AlrError):
    def __init__(self, task_list: "TaskList") -> None: ...   # message is FAILED_TASKS_MESSAGE
    task_list: "TaskList"
class SandboxError(AlrError): ...
class RepoError(AlrError): ...
class RunLogError(AlrError): ...
class RunError(AlrError): ...          # a task failed, or its state could not be saved
class RunCancelledError(RunError): ...  # the run was stopped
def quote(text: str) -> str                         # wraps text in double quotes for messages

# alr/durations.py
def parse_duration(text: str) -> float              # seconds; ValueError when invalid
def format_duration(seconds: float) -> str          # "1m30.5s", rounded to the millisecond

# alr/tasks.py  [Tasks 2, 3]
PENDING = "pending"; FAILED = "failed"; COMPLETED = "completed"
@dataclass
class Gate:
    cmd: str
    timeout: str = ""
@dataclass
class Task:
    id: int
    name: str
    prompt: str
    state: str = PENDING
    error: str = ""
    def text(self) -> str                           # Go's Task.String()
@dataclass
class TaskList:
    tasks: list[Task]
    gates: list[Gate] | None = None                 # None: key absent; []: "gates: []"
    def gate_list(self) -> list[Gate]
def parse_tasks(text: str) -> TaskList              # TaskFileError
def parse_timeout(text: str) -> float               # seconds; ValueError with the bare problem text
def save_tasks(path: str, task_list: TaskList) -> None   # TaskFileError

# alr/engine/frontend.py  [Task 5]
@dataclass(frozen=True)
class SessionResult:
    text: str                                       # where the result line is read from
    error: str | None                               # Go-style exit text; None on a zero exit
class FrontEnd(Protocol):
    session_flags: Sequence[str]                    # Claude flags that follow --print
    def task_skipped(self, task: Task) -> None: ...
    def task_started(self, task: Task) -> None: ...
    async def run_session(self, task: Task, argv: Sequence[str], stdin_text: str) -> SessionResult: ...
    def session_finished(self, task: Task, seconds: float) -> None: ...
    async def run_gate(self, task: Task, gate: Gate) -> str | None: ...        # exit text, None on zero
    def gate_finished(self, task: Task, gate: Gate, limit: str, error: str | None, timed_out: bool, seconds: float) -> None: ...
    def commit_started(self, task: Task) -> None: ...
    async def run_git(self, task: Task, argv: Sequence[str], cwd: str) -> str | None: ...
    def committed(self, task: Task, commit_hash: str) -> None: ...
    def task_finished(self, task: Task, error: str | None, seconds: float) -> None: ...
    def run_done(self, error: str | None, seconds: float) -> None: ...

# alr/engine/proc.py  [Task 5]
async def start(argv: Sequence[str], *, stdin: int | None = DEVNULL, stdout: int | None = None,
                stderr: int | None = None, cwd: str | None = None,
                env: Mapping[str, str] | None = None) -> asyncio.subprocess.Process
async def finish(process: asyncio.subprocess.Process, drain: Awaitable[None] | None = None, *,
                 gentle: bool = False) -> str | None
async def capture(argv: Sequence[str], *, cwd: str | None = None, env: Mapping[str, str] | None = None,
                  gentle: bool = False) -> tuple[int, str, str]      # returncode, stdout, stderr
async def read_lines(stream: asyncio.StreamReader, on_line: Callable[[bytes], None]) -> None
def exit_text(returncode: int) -> str | None       # None, "exit status 3", "signal: SIGKILL"
def not_found_text(name: str) -> str               # "claude: not found on PATH"

# alr/engine/result.py  [Task 4]
def parse_result(line: str) -> tuple[str, str] | None
def last_result_line(output: str) -> str
def outcome(run_error: str | None, last_line: str) -> tuple[str, str]     # (state, error)

# alr/engine/sandbox.py  [Task 4]
def bypass_args() -> list[str]
def sandbox_args(path: str) -> list[str]            # SandboxError

# alr/engine/loop.py  [Task 6]
FAILED_TASKS_MESSAGE = "fix the failed tasks and set their state to pending before running"
@dataclass(frozen=True)
class RunOptions:
    tasks_file: str
    gate_timeout: str                               # "" when --gate-timeout was not passed
    session_args: Sequence[str]
    repo: "Repo | None" = None
def load_prompt(path: str) -> str                   # PromptError
def load_tasks(path: str) -> TaskList               # TaskFileError, FailedTasksError
async def run(prompt: str, task_list: TaskList, options: RunOptions, front_end: FrontEnd) -> None   # RunError

# alr/engine/gates.py  [Task 7]
def first_line(text: str) -> str
def gate_limit(gate: Gate, override: str) -> tuple[str, str]      # (limit, "flag" | "gate" | "default")
def gate_failure(gate: Gate, limit: str, error: str | None, timed_out: bool) -> str
async def run_gates(task: Task, gates: Sequence[Gate], override: str, front_end: FrontEnd) -> tuple[str, str]

# alr/engine/commit.py  [Task 8]
@dataclass(frozen=True)
class Repo:
    root: str
async def open_repo(tasks_file: str) -> Repo | None                # RepoError
async def check_log_dir(repo: Repo | None, run_dir: str) -> None   # RepoError
async def commit(repo: Repo, task: Task, front_end: FrontEnd) -> tuple[str, str]

# alr/plain  [Tasks 6-9]
class PlainFrontEnd: ...                                           # implements FrontEnd
def print_tasks(out: TextIO, task_list: TaskList) -> None
def load_tasks_report(out: TextIO, tasks_file: str) -> TaskList    # prints notice + table, then re-raises FailedTasksError
async def repo_for(out: TextIO, tasks_file: str, commit: bool) -> Repo | None
async def start(prompt_file: str, tasks_file: str, gate_timeout: str, session_args: Sequence[str], commit: bool) -> None
async def dry_run(out: TextIO, tasks_file: str, gate_timeout: str, sandbox_file: str, commit: bool) -> None

# alr/stream  [Tasks 10, 11]
TaskStarted, Activity, SessionFinished, GateFinished, Committed, TaskFinished, RunDone   # frozen dataclasses
Event = TaskStarted | Activity | SessionFinished | GateFinished | Committed | TaskFinished | RunDone
def parse_stream_line(line: bytes) -> tuple[list[str], str, bool]
class StreamFrontEnd:                                              # implements FrontEnd
    def __init__(self, report: Callable[[Event], None]) -> None: ...
def run_dir(log_dir: str, start: datetime) -> str
@dataclass(frozen=True)
class Info: version: str; tasks_file: str; prompt_file: str; permissions: str; sandbox_settings: str; gate_timeout: str; commit: bool
class RunLog:
    @classmethod
    def open(cls, run_dir: str, info: Info, now: Callable[[], datetime] = ...) -> "RunLog"   # RunLogError
    def record(self, event: Event) -> None                         # RunLogError
    def close(self) -> None
```

## Review Focus

1. **YAML 1.1 scalars.** PyYAML reads an unquoted `yes`, `on`, or `12:30` as a bool or an int. Such a name must be rejected with a clear `name: must be a string` message, and the quoted form must load. Test in Task 2.
2. **Multi-line prompts across a save.** A long prompt must come back from a save as a readable `|` block with the exact same text, including colons, quotes, non-ASCII characters, and a trailing newline. Tabs must not stop a prompt being written as a block. Names such as `yes`, `true`, and `~` must still be strings after a save and reload. Test in Task 3.
3. **Session output that is not valid UTF-8.** Claude or a tool it runs can print arbitrary bytes. Plain mode must pass them through and still find the result line, not crash. Test in Task 6.
4. **A stop when no subprocess is running.** SIGINT during startup or between two tasks must exit 1 with a one-line `error:` message and no traceback, and leave the task file as it was. Test in Task 12.
5. **Quotes and non-ASCII text in gate names.** A gate such as `echo "héllo" && test -f a\b` appears inside `gate "..." failed` in the saved `error`. The quoting must keep the message on one line and unambiguous. Test in `tests/unit/test_errors.py`.

---

> **Status, 2026-10-04:** Tasks 1 to 5 are implemented (commits `bc9508a` to `e4a91a0`), followed by a cleanup pass (`a0079ec` and the commit after it) that applied ADR-007 and the review findings. The text of Tasks 1 to 5 below is kept as it was written and no longer matches the code in these ways: `gocompat.py` is gone (see `durations.py`, `errors.quote`, `proc.exit_text`, `proc.not_found_text`), the custom YAML loader is gone, `_GoDumper` is `_TaskDumper`, and `RunCancelled` is `RunCancelledError`. The sections above this note and Tasks 6 to 14 are current.

### Task 1: Clear the old code, set up the project, add the shared helpers

**Files:**
- Delete: `src/alr/looper.py`, `src/alr/prompt.py`, `tests/test_cli.py`, `tests/test_looper.py`, `tests/test_main.py`, `tests/test_prompt.py`, `tests/test_tasks.py`, `tests/data/`
- Modify: `pyproject.toml`
- Create: `src/alr/errors.py`, `src/alr/gocompat.py`, `src/alr/engine/__init__.py`, `src/alr/plain/__init__.py`, `src/alr/stream/__init__.py`, `tests/unit/test_gocompat.py`
- Rewrite as stubs: `src/alr/cli.py`, `src/alr/main.py`, `src/alr/tasks.py`

**Interfaces:**
- Consumes: nothing.
- Produces: everything listed under `alr/errors.py` and `alr/gocompat.py` in Shared Interfaces.

- [ ] **Step 1: Delete the old code and tests**

```bash
git rm -r src/alr/looper.py src/alr/prompt.py tests/test_cli.py tests/test_looper.py tests/test_main.py tests/test_prompt.py tests/test_tasks.py tests/data
```

- [ ] **Step 2: Update `pyproject.toml`**

Change the dependency lists and add the pytest settings. Leave every other key as it is.

```toml
[dependency-groups]
dev = ["pytest", "pytest-asyncio", "ruff"]

[project]
dependencies = [
    "PyYAML>=6",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Run: `uv sync`
Expected: `pydantic` removed, `pytest-asyncio` installed, `uv.lock` updated.

- [ ] **Step 3: Replace `cli.py`, `main.py`, and `tasks.py` with stubs so the package imports**

`src/alr/tasks.py` becomes an empty module with a docstring `"""The task file: parse, validate, save."""`. `src/alr/cli.py`:

```python
"""Command line: flags, startup checks, routing."""


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError
```

`src/alr/main.py`:

```python
"""Entry point for the alr command."""

import sys

from alr import cli


def main() -> None:
    sys.exit(cli.main())
```

Create the three empty `__init__.py` files.

- [ ] **Step 4: Write `src/alr/errors.py`**

```python
"""Errors that end a run before or outside a task's own outcome."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alr.tasks import TaskList

FAILED_TASKS_MESSAGE = "fix the failed tasks and set their state to pending before running"


class AlrError(Exception):
    """Base for every error alr reports as `error: <message>` with exit code 1."""


class UsageError(AlrError):
    """A flag is missing, conflicts with another, or has a bad value."""


class TaskFileError(AlrError):
    """The task file cannot be read, parsed, validated, or saved."""


class PromptError(AlrError):
    """The prompt file is missing, unreadable, or empty."""


class FailedTasksError(AlrError):
    """The task file already holds a failed task."""

    def __init__(self, task_list: TaskList) -> None:
        super().__init__(FAILED_TASKS_MESSAGE)
        self.task_list = task_list


class SandboxError(AlrError):
    """The sandbox settings file is missing, unreadable, or not a JSON object."""


class RepoError(AlrError):
    """--commit cannot use the repository as it is."""


class RunLogError(AlrError):
    """The run log cannot be opened or written."""


class RunError(AlrError):
    """A task failed, or its state could not be saved."""


class RunCancelled(RunError):
    """The run was stopped before a task finished."""
```

`alr.engine.loop` re-exports `FAILED_TASKS_MESSAGE` from here in Task 6.

- [ ] **Step 5: Write the failing tests for `gocompat`**

`tests/unit/test_gocompat.py`:

```python
import signal

import pytest

from alr import gocompat


@pytest.mark.parametrize(
    ("text", "quoted"),
    [
        ("make test", '"make test"'),
        ('echo "hi"', '"echo \\"hi\\""'),
        ("test -f a\\b", '"test -f a\\\\b"'),
        ("héllo", '"héllo"'),
        ("a\tb", '"a\\tb"'),
        ("", '""'),
    ],
)
def test_goquote(text: str, quoted: str) -> None:
    assert gocompat.goquote(text) == quoted


def test_os_error_text_matches_go_wording() -> None:
    exc = FileNotFoundError(2, "No such file or directory")
    assert gocompat.os_error_text("open", "tasks.yaml", exc) == "open tasks.yaml: no such file or directory"


@pytest.mark.parametrize(
    ("returncode", "text"),
    [
        (0, None),
        (1, "exit status 1"),
        (127, "exit status 127"),
        (-signal.SIGKILL, "signal: killed"),
        (-signal.SIGTERM, "signal: terminated"),
        (-signal.SIGINT, "signal: interrupt"),
    ],
)
def test_exit_error(returncode: int, text: str | None) -> None:
    assert gocompat.exit_error(returncode) == text


def test_not_found_error() -> None:
    assert gocompat.not_found_error("claude") == 'exec: "claude": executable file not found in $PATH'


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("90s", 90.0),
        ("10m", 600.0),
        ("1h30m", 5400.0),
        ("1.5h", 5400.0),
        ("500ms", 0.5),
        ("0", 0.0),
        ("-5s", -5.0),
        ("1m30.5s", 90.5),
    ],
)
def test_parse_duration(text: str, seconds: float) -> None:
    assert gocompat.parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "30", "ten", "5 s", "1d", "s", "1.2.3s"])
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        gocompat.parse_duration(text)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0.0, "0s"),
        (0.0004, "0s"),
        (0.015, "15ms"),
        (1.0, "1s"),
        (2.5, "2.5s"),
        (1.234, "1.234s"),
        (60.0, "1m0s"),
        (90.5, "1m30.5s"),
        (3600.0, "1h0m0s"),
        (3723.0, "1h2m3s"),
    ],
)
def test_format_duration(seconds: float, text: str) -> None:
    assert gocompat.format_duration(seconds) == text
```

- [ ] **Step 6: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_gocompat.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError` or `AttributeError` for `alr.gocompat`.

- [ ] **Step 7: Write `src/alr/gocompat.py`**

```python
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
    name = _SIGNAL_NAMES.get(number) or (signal.strsignal(number) or f"signal {number}").lower()
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
```

- [ ] **Step 8: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_gocompat.py -x --tb=short`
Expected: PASS.

- [ ] **Step 9: Run the full gate and commit**

Run: `make test`
Expected: ruff clean, all tests pass.

```bash
git add -A
git commit -m "Clear the old alr code and add the shared error and Go-compat helpers"
```

---

### Task 2: Task file parsing and validation

**Files:**
- Modify: `src/alr/tasks.py`
- Test: `tests/unit/test_tasks_parse.py`

**Port from:** `gralph:src/internal/tasks/task.go` (everything except `SaveTasks`), `gralph:src/internal/tasks/task_test.go`

**Interfaces:**
- Consumes: `alr.errors.TaskFileError`, `alr.gocompat.parse_duration`, `alr.gocompat.goquote`.
- Produces: `PENDING`, `FAILED`, `COMPLETED`, `Gate`, `Task`, `Task.text()`, `TaskList`, `TaskList.gate_list()`, `parse_tasks`, `parse_timeout` as listed in Shared Interfaces.

- [ ] **Step 1: Port the tests**

Port these Go tests into `tests/unit/test_tasks_parse.py`: `TestTaskString`, `TestParseTasks_Valid`, `TestParseTasks_PreservesFileOrder`, `TestParseTasks_IgnoresUnknownFields`, `TestParseTasks_MaxID`, `TestStateConstants`, `TestParseTasks_AcceptsEveryValidState`, `TestParseTasks_EmptyStateDefaultsToPending`, `TestParseTasks_DefaultsOnlyTheEmptyStates`, `TestParseTasks_DoesNotAlterNameOrPrompt`, `TestParseTasks_Errors`, `TestParseTasks_FileErrorsNameNoTask`, `TestParseTasks_ReportsFirstInvalidTask`, `TestParseTasks_AcceptsExplicitCoreTags`, `TestParseTasks_ReadsErrorField`, `TestParseTasks_TopLevelGates`, `TestParseTasks_ReadsGates`, `TestParseTasks_ReadsGateTimeout`, `TestParseTasks_GatesAbsentVersusEmpty`, `TestParseTasks_TaskLevelGatesAreAnError`, `TestTaskString_OmitsGates`, `TestParseTasks_GateErrors`, `TestParseTasks_GateErrorReportedBeforeTaskError`, `TestParseTimeout`.

Go's `ParseTasks(yml) (TaskList, error)` becomes `parse_tasks(text) -> TaskList`, raising `TaskFileError`; assert on `str(exc.value)`. Go's `ParseTimeout` error becomes `ValueError`; assert on its text. Where a Go case asserts the exact text of a YAML syntax error, assert only the `failed to parse yaml tasks: ` prefix.

Add the Review Focus test:

```python
@pytest.mark.parametrize("name", ["yes", "no", "on", "off", "12:30", "y", "~x", "1_000 widgets"])
def test_parse_tasks_reads_yaml_11_scalars_as_strings(name: str) -> None:
    text = f"tasks:\n  - id: 1\n    name: {name}\n    prompt: do it\n"
    assert tasks.parse_tasks(text).tasks[0].name == name
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_tasks_parse.py -x --tb=short`
Expected: FAIL, `AttributeError: module 'alr.tasks' has no attribute 'parse_tasks'`.

- [ ] **Step 3: Write the loader that resolves scalars the way gralph's YAML library does**

PyYAML follows YAML 1.1. gralph uses `gopkg.in/yaml.v3`, which resolves plain scalars by YAML 1.2 rules. Put this at the top of `src/alr/tasks.py`:

```python
import re
from dataclasses import dataclass

import yaml

_TAG = "tag:yaml.org,2002:"
_INT_PATTERN = r"^[-+]?(?:0|[1-9][0-9_]*|0[0-7_]+|0o[0-7_]+|0x[0-9a-fA-F_]+|0b[01_]+)$"


class _GoLoader(yaml.SafeLoader):
    """Resolves plain scalars as gopkg.in/yaml.v3 does, so `yes` and `12:30` are strings."""


_GoLoader.yaml_implicit_resolvers = {}
_GoLoader.add_implicit_resolver(_TAG + "bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
_GoLoader.add_implicit_resolver(_TAG + "null", re.compile(r"^(?:~|null|Null|NULL|)$"), ["~", "n", "N", ""])
_GoLoader.add_implicit_resolver(_TAG + "int", re.compile(_INT_PATTERN), list("-+0123456789"))
_GoLoader.add_implicit_resolver(
    _TAG + "float",
    re.compile(r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"),
    list("-+0123456789."),
)

# Timestamps and merge keys resolve the same in both libraries; reuse PyYAML's patterns.
_SHARED_TAGS = {_TAG + "timestamp", _TAG + "merge"}
for _first, _resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items():
    for _tag, _pattern in _resolvers:
        if _tag in _SHARED_TAGS:
            _GoLoader.yaml_implicit_resolvers.setdefault(_first, []).append((_tag, _pattern))
```

Compose with `yaml.compose(text, Loader=_GoLoader)`. A `yaml.YAMLError` becomes `TaskFileError(f"failed to parse yaml tasks: {exc}")`.

- [ ] **Step 4: Write the data classes and the validation**

Port `task.go` function by function onto PyYAML nodes (`yaml.MappingNode`, `yaml.SequenceNode`, `yaml.ScalarNode`). Keep one Python function per Go function, with the same order of checks, so the first error reported is the same.

Points where the Python differs from the Go:

- **Tags.** A node's tag is the long form (`tag:yaml.org,2002:str`). The allowed set is `str`, `int`, `float`, `bool`, `null`, `map`, `seq`, `timestamp`, `merge` under that prefix. The error text uses the short form: `line <n>: tag !!python/object is not allowed` for a `tag:yaml.org,2002:` tag and the tag as written (for example `!custom`) otherwise. `<n>` is `node.start_mark.line + 1`.
- **Aliases.** PyYAML's composer already replaces an alias with the node it points to, so there is no alias node to unwrap.
- **Empty document.** `yaml.compose` returns `None` for an empty file; that is `tasks: is required`.
- **`id`.** Accept only a scalar whose tag is `int`. Convert the text with underscores removed: a leading `0o`, `0x`, or `0b` uses that base; a leading `0` followed by more digits is octal; anything else is decimal. The value must be from 1 to 32767; otherwise `id: must be a positive integer no greater than 32767`.
- **Values.** Build `Task` and `Gate` from the validated scalar nodes' `.value` text. Do not call a YAML constructor. `name`, `prompt`, `error`, `cmd`, and `timeout` are stored exactly as written. An empty or missing `state` becomes `PENDING`. A missing `error` is `""`.
- **`state` error.** Go's `%q` is `gocompat.goquote`: `state: must be pending, completed, or failed, got "done"`.
- **`parse_timeout`.** Call `gocompat.parse_duration`; on `ValueError` raise `ValueError("must be a duration string such as 90s or 10m")`; when the result is not greater than zero raise `ValueError("must be greater than zero")`. A gate's bad timeout becomes `gates[<j>]: timeout: <that text>`.
- **`Task.text()`** returns `f"{self.id}: {self.name}\n\n{self.prompt}".strip()`.
- **`TaskList.gate_list()`** returns `[]` when `gates` is `None`.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_tasks_parse.py -x --tb=short`
Expected: PASS.

- [ ] **Step 6: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/tasks.py tests/unit/test_tasks_parse.py
git commit -m "Port task file parsing and validation from gralph"
```

---

### Task 3: Saving the task file

**Files:**
- Modify: `src/alr/tasks.py`
- Test: `tests/unit/test_tasks_save.py`

**Port from:** `SaveTasks` in `gralph:src/internal/tasks/task.go`; the `TestSaveTasks_*` functions in `gralph:src/internal/tasks/task_test.go`

**Interfaces:**
- Consumes: `Task`, `Gate`, `TaskList`, `parse_tasks` from Task 2; `alr.gocompat.os_error_text`, `goquote`.
- Produces: `save_tasks(path: str, task_list: TaskList) -> None`, raising `TaskFileError`.

- [ ] **Step 1: Port the tests**

Port into `tests/unit/test_tasks_save.py`: `TestSaveTasks_ErrorRoundTrip`, `TestSaveTasks_OmitsEmptyError`, `TestSaveTasks_RoundTrip`, `TestSaveTasks_OverwritesExistingFile`, `TestSaveTasks_PathIsDirectory`, `TestSaveTasks_TargetDirNotWritable`, `TestSaveTasks_FailedSaveLeavesExistingFileUntouched`, `TestSaveTasks_WritesTwoSpaceIndent`, `TestSaveTasks_KeepsGatesAbsentEmptyOrSet`, `TestSaveTasks_GatesRoundTrip`, `TestSaveTasks_OmitsAbsentGates`, `TestSaveTasks_GateTimeoutKeptAsWrittenAndOmittedWhenAbsent`.

Where a Go test compares the saved bytes with an exact YAML string, compare `parse_tasks(saved_text)` with the expected `TaskList` instead, and keep any assertion about indentation or a key being absent as a substring check.

Add the Review Focus tests:

```python
PROMPT = 'Line one: with "quotes" and a colon\n\n  indented line\nhéllo — done\n'


def test_save_tasks_writes_multi_line_prompt_as_block(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    tasks.save_tasks(str(path), tasks.TaskList(tasks=[tasks.Task(id=1, name="a", prompt=PROMPT)]))

    saved = path.read_text(encoding="utf-8")
    assert "prompt: |" in saved
    assert "\\n" not in saved
    assert tasks.parse_tasks(saved).tasks[0].prompt == PROMPT


@pytest.mark.parametrize("name", ["yes", "true", "~", "0o17", "12:30", "null", "1e3", "- dash", "a: b", "#hash"])
def test_save_tasks_keeps_awkward_names_as_strings(tmp_path: Path, name: str) -> None:
    path = tmp_path / "tasks.yaml"
    tasks.save_tasks(str(path), tasks.TaskList(tasks=[tasks.Task(id=1, name=name, prompt="p")]))

    assert tasks.parse_tasks(path.read_text(encoding="utf-8")).tasks[0].name == name
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_tasks_save.py -x --tb=short`
Expected: FAIL, `AttributeError: module 'alr.tasks' has no attribute 'save_tasks'`.

- [ ] **Step 3: Write the dumper and `save_tasks`**

```python
class _GoDumper(yaml.SafeDumper):
    """Writes the task file the way gralph does: nested lists indented, long text as blocks."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # PyYAML leaves a list under a key unindented; gralph's files indent it.
        super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar(_TAG + "str", value, style=style)


_GoDumper.add_representer(str, _represent_str)
# Quote any string gralph's library would read back as a number, on top of PyYAML's own rules.
_GoDumper.add_implicit_resolver(_TAG + "int", re.compile(_INT_PATTERN), list("-+0123456789"))
```

`save_tasks`:

1. Build a plain `dict` in key order. `gates` comes first and is included only when `task_list.gates is not None` (an empty list is written as `gates: []`). Each gate is `{"cmd": ...}` plus `"timeout"` only when it is not empty. Then `tasks`: each task is `id`, `name`, `prompt`, `state`, plus `error` only when it is not empty.
2. `yaml.dump(doc, Dumper=_GoDumper, sort_keys=False, allow_unicode=True, default_flow_style=False, indent=2, width=2**31 - 1)`.
3. Write the text to `<path>.tmp` with mode `0o600` (`os.open` with `O_WRONLY | O_CREAT | O_TRUNC`), then `os.replace` it over `<path>`. Normalize the path with `os.path.normpath` first, as Go's `filepath.Clean` does.
4. On `OSError` from either step: remove the temp file if it exists and raise `TaskFileError(f"failed to save tasks to {goquote(path)}: {os_error_text(op, target, exc)}") from exc`, where `op` is `open` for the write and `rename` for the replace. For `rename`, Go's text is `rename <tmp> <path>: <reason>`; pass `f"{tmp} {path}"` as the path argument.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_tasks_save.py tests/unit/test_tasks_parse.py -x --tb=short`
Expected: PASS.

- [ ] **Step 5: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/tasks.py tests/unit/test_tasks_save.py
git commit -m "Port atomic task file saving from gralph"
```

---

### Task 4: The result line rule and the session flags

**Files:**
- Create: `src/alr/engine/result.py`, `src/alr/engine/sandbox.py`
- Test: `tests/unit/test_result.py`, `tests/unit/test_sandbox.py`

**Port from:** `gralph:src/internal/looper/result.go`, `result_test.go`, `sandbox.go`, `sandbox_test.go`

**Interfaces:**
- Consumes: `alr.tasks.COMPLETED`, `FAILED`; `alr.errors.SandboxError`; `alr.gocompat.goquote`, `os_error_text`.
- Produces: `parse_result`, `last_result_line`, `outcome`, `bypass_args`, `sandbox_args` as listed in Shared Interfaces.

- [ ] **Step 1: Port the tests**

`tests/unit/test_result.py`: `TestParseResult`, `TestLastResultLine`, `TestOutcome`.
`tests/unit/test_sandbox.py`: `TestBypassArgs`, `TestSandboxArgs_ForcesSandboxKeys`, `TestSandboxArgs_RejectsBadFiles`, `TestSandboxArgs_UnreadablePath`.

Go's `parseResult` returns `(state, errMsg, ok)`; `parse_result` returns `(state, error)` or `None` when Go's `ok` is false. Go's `outcome(runErr, lastLine)` takes an `error`; the Python takes the error's text or `None`. In the sandbox tests, compare the `--settings` value by loading it with `json.loads`, not as a string.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_result.py tests/unit/test_sandbox.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError: No module named 'alr.engine.result'`.

- [ ] **Step 3: Write `src/alr/engine/result.py`**

```python
"""The result line a session ends with, and the rule that turns it into a task outcome."""

import json

from alr.tasks import COMPLETED, FAILED

_NO_RESULT = "no valid result line in session output"
_FAILED_WITHOUT_ERROR = "session reported failed with no error"


def parse_result(line: str) -> tuple[str, str] | None:
    """Return (state, error) from a result line, or None when it is not a valid one."""
    try:
        data = json.loads(line)
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None

    state = _field(data, "state")
    error = _field(data, "error")
    if state is None or error is None:
        return None

    state = state.strip().lower()
    if state not in (COMPLETED, FAILED):
        return None

    return state, error.strip()


def _field(data: dict[str, object], name: str) -> str | None:
    """Read a string field as Go's encoding/json does: keys match ignoring case, missing or null is empty."""
    for key, value in data.items():
        if key.lower() != name:
            continue

        if value is None:
            return ""

        return value if isinstance(value, str) else None

    return ""


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
```

If a ported `TestParseResult` case disagrees with `_field` (for example on duplicate keys that differ only in case), follow the Go test.

- [ ] **Step 4: Write `src/alr/engine/sandbox.py`**

Port `sandbox.go`:

- `bypass_args()` returns `["--dangerously-skip-permissions"]`.
- `sandbox_args(path)` reads the file. An `OSError` becomes `SandboxError(f"--sandbox-settings: file {goquote(path)} is not accessible: {os_error_text('open', path, exc)}")`.
- Parse with `json.loads`. A `ValueError` becomes `SandboxError(f"--sandbox-settings: must be a JSON object: {exc}")`. A value that is not a `dict` (including `null`) becomes `SandboxError("--sandbox-settings: must be a JSON object")`.
- `sandbox = settings.get("sandbox", {})`; when the key is present and its value is not a `dict`, raise `SandboxError("--sandbox-settings: sandbox: must be a JSON object")`.
- Set `sandbox["enabled"] = True`, `sandbox["allowUnsandboxedCommands"] = False`, `sandbox["failIfUnavailable"] = True`, put it back under `settings["sandbox"]`, and return `["--permission-mode", "acceptEdits", "--settings", json.dumps(settings, separators=(",", ":"), ensure_ascii=False)]`.
- The file is never written.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_result.py tests/unit/test_sandbox.py -x --tb=short`
Expected: PASS.

- [ ] **Step 6: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/engine/result.py src/alr/engine/sandbox.py tests/unit/test_result.py tests/unit/test_sandbox.py
git commit -m "Port the result line rule and the session flags from gralph"
```

---

### Task 5: Process handling, the front end interface, and the fake claude

**Files:**
- Create: `src/alr/engine/proc.py`, `src/alr/engine/frontend.py`, `tests/fakeclaude/fake_claude.py`, `tests/conftest.py`
- Test: `tests/unit/test_proc.py`, `tests/unit/test_fake_claude.py`

**Port from:** `gralph:src/internal/looper/process_tree_unix.go`, `process_tree_unix_test.go`, `readLines` in `stream.go`, `gralph:src/internal/looper/testdata/fakeclaude/main.go`, `gralph:tests/e2e/testdata/fakeclaude/main.go`, `fakeclaude_test.go`

**Interfaces:**
- Consumes: `alr.gocompat.exit_error`; `alr.tasks.Task`, `Gate`.
- Produces: `alr.engine.proc.start`, `finish`, `capture`, `read_lines`; `alr.engine.frontend.FrontEnd`, `SessionResult`; the `fake_claude` and `git_repo` pytest fixtures.

- [ ] **Step 1: Write the fake claude**

`tests/fakeclaude/fake_claude.py` is one Python script that honors every environment variable documented at the top of both Go fake claude files: the `FAKECLAUDE_*` set (mode, record file, attempt log, exit code, stderr message, descendant PID file, block, ready file), `FAKE_CLAUDE_OUTPUT`, and whatever the looper version adds for stream-json output. Read both Go files and keep each variable's exact meaning, including the default output `{"state":"completed","error":""}` on a zero exit. The record file gets one JSON line per call: `{"argv": [...], "stdin": "..."}`, with `argv` excluding the program name as the Go version does.

- [ ] **Step 2: Write the fixtures**

`tests/conftest.py`:

```python
"""Fixtures shared by the unit and e2e tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

FAKE_CLAUDE_SOURCE = Path(__file__).parent / "fakeclaude" / "fake_claude.py"


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a scriptable `claude` first on PATH and return the folder that holds it."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    # The interpreter running the tests, so the fake works whatever python3 is on PATH.
    script.write_text(f"#!{sys.executable}\n{FAKE_CLAUDE_SOURCE.read_text(encoding='utf-8')}", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    return bin_dir


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an empty git repository with an identity set, and make it the working directory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "--quiet", "--initial-branch=main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    return repo
```

Match the git setup the Go helpers in `commit_test.go` use (read its repo-creating helper and add anything it sets that is missing here).

- [ ] **Step 3: Port the tests**

`tests/unit/test_fake_claude.py`: `TestFakeClaude_StreamJSON`, `TestFakeClaude_StreamJSONBigEvent`, `TestFakeClaude_RecordsArgs`, run against the `fake_claude` fixture with `subprocess.run`.

`tests/unit/test_proc.py`: port `TestTerminateProcessTree` (with its helper process, `TestProcessTreeHelper`, as a small shell or Python child that starts a grandchild and blocks). Add:

```python
async def test_finish_returns_go_style_exit_text() -> None:
    process = await proc.start(["sh", "-c", "exit 3"])
    assert await proc.finish(process) == "exit status 3"


async def test_finish_returns_none_on_zero_exit() -> None:
    process = await proc.start(["true"])
    assert await proc.finish(process) is None


async def test_cancel_kills_the_whole_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    process = await proc.start(["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"])
    waiter = asyncio.ensure_future(proc.finish(process))
    while not pid_file.exists() or not pid_file.read_text().strip():
        await asyncio.sleep(0.01)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    child = int(pid_file.read_text())
    for _ in range(200):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return

        await asyncio.sleep(0.01)

    pytest.fail("the grandchild outlived the cancel")


async def test_read_lines_has_no_length_limit() -> None:
    process = await proc.start(["sh", "-c", "head -c 200000 /dev/zero | tr '\\0' 'x'; echo; printf tail"], stdout=asyncio.subprocess.PIPE)
    lines: list[bytes] = []
    assert process.stdout is not None
    await proc.finish(process, proc.read_lines(process.stdout, lines.append))

    assert len(lines[0]) == 200001
    assert lines[1] == b"tail"


async def test_capture_returns_output_and_code() -> None:
    assert await proc.capture(["sh", "-c", "echo out; echo err >&2; exit 2"]) == (2, "out\n", "err\n")
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_proc.py tests/unit/test_fake_claude.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError: No module named 'alr.engine.proc'`.

- [ ] **Step 5: Write `src/alr/engine/proc.py`**

```python
"""Child processes: each in its own process group, stopped as a group when the run is cancelled."""

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from subprocess import DEVNULL

_GIT_STOP_GRACE = 2.0
_GIT_STOP_POLL = 0.01
_READ_SIZE = 64 * 1024


async def start(
    argv: Sequence[str],
    *,
    stdin: int | None = DEVNULL,
    stdout: int | None = None,
    stderr: int | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> asyncio.subprocess.Process:
    """Start argv in its own process group, so a stop reaches everything it spawns."""
    return await asyncio.create_subprocess_exec(
        *argv, stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd, env=env, process_group=0
    )


async def finish(
    process: asyncio.subprocess.Process,
    drain: Awaitable[None] | None = None,
    *,
    gentle: bool = False,
) -> str | None:
    """Wait for process after drain is done and return its Go-style exit text.

    On cancellation the whole group is stopped before the cancellation carries on.
    gentle is for git, which cleans up its index lock on SIGTERM but not on SIGKILL.
    """
    try:
        if drain is not None:
            await drain

        returncode = await process.wait()
    except asyncio.CancelledError:
        await asyncio.shield(_stop_group(process, gentle))
        raise

    return exit_error(returncode)


async def _stop_group(process: asyncio.subprocess.Process, gentle: bool) -> None:
    if gentle and _signal_group(process.pid, signal.SIGTERM):
        await _wait_for_group_exit(process.pid)

    _signal_group(process.pid, signal.SIGKILL)
    await process.wait()


async def _wait_for_group_exit(pgid: int) -> None:
    deadline = asyncio.get_running_loop().time() + _GIT_STOP_GRACE
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(_GIT_STOP_POLL)
        if not _signal_group(pgid, 0):
            return


def _signal_group(pgid: int, sig: int) -> bool:
    """Send sig to the group; False when the group is already gone."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False

    return True
```

Add `from alr.gocompat import exit_error` to the imports. Then add:

- `read_lines(stream, on_line)`: read `stream.read(_READ_SIZE)` in a loop, split on `b"\n"` keeping the newline on each line, carry a partial line over to the next read, and pass a final line with no newline to `on_line` at end of stream. Do not use `stream.readline()`: it fails on a line longer than its buffer, and one stream-json event can be larger.
- `capture(argv, *, cwd, env, gentle)`: `start` with both pipes, read both to the end at the same time with `asyncio.gather(process.stdout.read(), process.stderr.read())` as the `drain`, `finish`, and return `(process.returncode, stdout_text, stderr_text)` decoded as UTF-8 with `errors="replace"`. A `FileNotFoundError` from `start` is left to the caller.

- [ ] **Step 6: Write `src/alr/engine/frontend.py`**

Write the `SessionResult` dataclass and the `FrontEnd` protocol exactly as listed in Shared Interfaces, each method with a one-line docstring taken from the "Engine asks the front end to" column of the table in `docs/design/03_system_architecture.md`. `task_skipped` is the one call not in that table: "Note that a task was already completed and is not run."

- [ ] **Step 7: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_proc.py tests/unit/test_fake_claude.py -x --tb=short`
Expected: PASS.

- [ ] **Step 8: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/engine/proc.py src/alr/engine/frontend.py tests/conftest.py tests/fakeclaude tests/unit/test_proc.py tests/unit/test_fake_claude.py
git commit -m "Add process group handling, the front end interface, and the fake claude"
```

---

### Task 6: The run loop and the plain session

**Files:**
- Create: `src/alr/engine/loop.py`, `src/alr/plain/frontend.py`, `src/alr/plain/report.py`
- Test: `tests/unit/test_loop.py`, `tests/unit/test_plain_report.py`

**Port from:** `gralph:src/internal/looper/looper.go` (`LoadPrompt`, `LoadTasks`, `LoadTasksReport`, `PrintTasks`, `Run`, `runLoop`, `runTask`, `runTaskPlain`, `claudeCmd`, `finishTask`), `looper_test.go`, `runloop_result_test.go`, `runloop_unix_test.go`

**Interfaces:**
- Consumes: Tasks 1 to 5.
- Produces: `load_prompt`, `load_tasks`, `RunOptions`, `run`, `FAILED_TASKS_MESSAGE` in `alr.engine.loop`; `PlainFrontEnd` with `session_flags`, `task_skipped`, `task_started`, `run_session`, `session_finished`, `task_finished`, `run_done` in `alr.plain.frontend`; `print_tasks` and `load_tasks_report` in `alr.plain.report`. `PlainFrontEnd.run_gate`, `gate_finished`, `commit_started`, `run_git`, and `committed` are added by Tasks 7 and 8; until then they raise `NotImplementedError`.

- [ ] **Step 1: Port the tests**

`tests/unit/test_loop.py`, from `looper_test.go`: `TestLoadPrompt_MissingFile`, `TestLoadPrompt_ReadError`, `TestLoadPrompt_Empty`, `TestLoadPrompt_WhitespaceOnly`, `TestLoadPrompt_Success`, `TestLoadTasks_MissingFile`, `TestLoadTasks_ReadError`, `TestLoadTasks_InvalidYAML`, `TestLoadTasks_Success`, `TestLoadTasks_FailedTaskReturnsListAndErrFailedTasks`, `TestLoadTasks_ParseErrorIsNotErrFailedTasks`, `TestRunLoop_HappyPathInvokesInOrderWithExactStdin`, `TestRunLoop_NonZeroExitStopsAtFirstTask`, `TestRunLoop_ClaudeMissingFromPath`, `TestRunLoop_ContextAlreadyCancelled`, `TestRunLoop_PrintsPromptToStdout`, `TestRunLoop_EmptyTaskList`, `TestRunLoop_SkipsCompletedTasks`, `TestRunTaskPlain_ArgvIsPrintThenSessionArgs`. From `runloop_result_test.go`: all four tests. From `runloop_unix_test.go`: all four tests.

`tests/unit/test_plain_report.py`: `TestLoadTasksReport_FailedTaskWritesNoticeAndTable`, `TestLoadTasksReport_CleanFileWritesNothing`.

Translation notes:

- Go's `runLoop(ctx, prompt, tl, tasksFile, gateTimeout, sessionArgs, repo, nil)` is `await loop.run(prompt, task_list, RunOptions(tasks_file=..., gate_timeout="", session_args=[...]), PlainFrontEnd())`.
- A failed task or a failed save raises `RunError`; assert on its text. `LoadTasks` returning the list with `ErrFailedTasks` is `FailedTasksError` with `.task_list`.
- Go tests capture stdout by swapping `os.Stdout`; use pytest's `capfd` fixture, which also sees what child processes write.
- A cancelled context is a cancelled asyncio task: wrap the call with `asyncio.ensure_future`, cancel it, and expect `RunCancelledError`. For `TestRunLoop_ContextAlreadyCancelled`, assert that `RunCancelledError` is raised and the task file is unchanged; do not assert the error text.

Add the Review Focus test:

```python
async def test_plain_session_survives_output_that_is_not_utf8(fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    script = fake_claude / "claude"
    script.write_text('#!/bin/sh\ncat >/dev/null\nprintf \'\\377\\376 binary\\n{"state":"completed","error":""}\\n\'\n')
    tasks_file = tmp_path / "tasks.yaml"
    task_list = tasks.TaskList(tasks=[tasks.Task(id=1, name="a", prompt="p")])
    tasks.save_tasks(str(tasks_file), task_list)

    await loop.run("shared", task_list, loop.RunOptions(tasks_file=str(tasks_file), gate_timeout="", session_args=[]), PlainFrontEnd())

    assert task_list.tasks[0].state == tasks.COMPLETED
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_loop.py tests/unit/test_plain_report.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError: No module named 'alr.engine.loop'`.

- [ ] **Step 3: Write `src/alr/engine/loop.py`**

Loading, with gralph's exact messages:

- `load_prompt(path)`: an `OSError` becomes `PromptError(f"prompt file {quote(path)} is not accessible: {exc.strerror}")`. Zero bytes: `PromptError("the prompt file is empty")`. Only whitespace: `PromptError("the prompt file is just whitespace")`. Otherwise return the text stripped. Read with `encoding="utf-8", errors="replace"`.
- `load_tasks(path)`: an `OSError` becomes `TaskFileError(f"tasks file {quote(path)} is not accessible: {exc.strerror}")`. A `TaskFileError` from `parse_tasks` is re-raised as `TaskFileError(f"failed to parse tasks yaml: {exc}") from exc`. When any task is `FAILED`, raise `FailedTasksError(task_list)`.

The loop:

```python
@dataclass(frozen=True)
class RunOptions:
    tasks_file: str
    gate_timeout: str
    session_args: Sequence[str]
    repo: Repo | None = None


async def run(prompt: str, task_list: TaskList, options: RunOptions, front_end: FrontEnd) -> None:
    """Run every pending task in file order, saving the file after each one."""
    started = time.monotonic()
    error: str | None = None
    try:
        await _run_loop(prompt, task_list, options, front_end)
    except RunError as exc:
        error = str(exc)
        raise
    finally:
        front_end.run_done(error, time.monotonic() - started)


async def _run_loop(prompt: str, task_list: TaskList, options: RunOptions, front_end: FrontEnd) -> None:
    for task in task_list.tasks:
        if task.state == COMPLETED:
            front_end.task_skipped(task)
            continue

        await _run_and_save(prompt, task, task_list, options, front_end)


async def _run_and_save(prompt: str, task: Task, task_list: TaskList, options: RunOptions, front_end: FrontEnd) -> None:
    front_end.task_started(replace(task))
    started = time.monotonic()
    state, error = await _run_task(prompt, task, task_list.gate_list(), options, front_end)
    task.state, task.error = state, error
    failure = None if state == COMPLETED else f"task {task.id}: {task.name} failed: {error}"
    _save(task, task_list, options.tasks_file, failure)
    front_end.task_finished(replace(task), failure, time.monotonic() - started)
    if failure is not None:
        raise RunError(failure)
```

- `_save` calls `save_tasks`. On `TaskFileError` for a completed task raise `RunError(f"task {task.id}: {task.name}: failed to save task state: {exc}") from exc`. For a failed task raise `RunError(f"{failure}\n{exc}") from exc` (Go's `errors.Join` puts each error on its own line).
- `_run_task` builds `stdin_text = f"{prompt}\n\n{task.text()}\n"` and `argv = ["claude", "--print", *front_end.session_flags, *options.session_args]`, awaits `front_end.run_session(replace(task), argv, stdin_text)`, and resolves `outcome(result.error, last_result_line(result.text))`. It then calls `front_end.session_finished(task_copy_with_state_and_error, seconds)`. In this task it returns the session's outcome; Tasks 7 and 8 add the gates and the commit after a completed session.
- Cancellation: wrap the session call in `try`/`except asyncio.CancelledError` and raise `RunCancelledError(f"task {task.id}: {task.name}: stopped") from None`. Nothing is saved and neither `session_finished` nor `task_finished` is called.
- The engine never prints. `import` nothing from `alr.plain` or `alr.stream`.
- Re-export `FAILED_TASKS_MESSAGE` from `alr.errors`. Import `Repo` under `TYPE_CHECKING` only; `alr.engine.commit` does not exist until Task 8, so until then annotate the field as `object | None`.

- [ ] **Step 4: Write `src/alr/plain/report.py`**

Port `PrintTasks` and `LoadTasksReport` exactly, including the ANSI colors, the two emoji, the column widths, and the `← Needs review!` note:

- `print_tasks(out, task_list)` writes the header `   {ID:>w}  {STATE:<9}  NAME`, the rule line, and one row per task as `PrintTasks` formats them. `stateWidth` is `len("completed")`.
- `load_tasks_report(out, tasks_file)` calls `load_tasks`; on `FailedTasksError` it writes `Some tasks failed previous runs:` and the table to `out`, then re-raises.

- [ ] **Step 5: Write `src/alr/plain/frontend.py`**

```python
"""The plain front end: what a run without the full-screen view does with each process."""


class PlainFrontEnd:
    """Shows everything on the terminal as it happens; reports nothing else."""

    session_flags: Sequence[str] = ()

    def task_skipped(self, task: Task) -> None:
        print(f"task {task.id}: {task.name} already completed, skipping", flush=True)

    def task_started(self, task: Task) -> None:
        return

    async def run_session(self, task: Task, argv: Sequence[str], stdin_text: str) -> SessionResult:
        # gralph prints the prompt with Println, so its trailing newline is followed by one more.
        print(stdin_text, flush=True)
        try:
            process = await proc.start(argv, stdin=PIPE, stdout=PIPE)
        except FileNotFoundError:
            return SessionResult(text="", error=not_found_text(argv[0]))

        chunks: list[bytes] = []
        error = await proc.finish(process, _pump(process, stdin_text.encode(), chunks))
        return SessionResult(text=b"".join(chunks).decode("utf-8", errors="replace"), error=error)
```

- `_pump` feeds stdin and tees stdout at the same time with `asyncio.gather`. Feeding: write the bytes, `drain`, close; ignore `BrokenPipeError` and `ConnectionResetError`, since a session may exit without reading. Teeing: read 64 KiB chunks, write each to `sys.stdout.buffer`, flush, and append it to `chunks`.
- stderr is inherited (`stderr=None`), so it reaches the terminal untouched.
- `session_finished`, `task_finished`, and `run_done` do nothing.
- `run_gate`, `gate_finished`, `commit_started`, `run_git`, and `committed` raise `NotImplementedError` for now.

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_loop.py tests/unit/test_plain_report.py -x --tb=short`
Expected: PASS.

- [ ] **Step 7: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/engine/loop.py src/alr/plain tests/unit/test_loop.py tests/unit/test_plain_report.py
git commit -m "Port the run loop and the plain session from gralph"
```

---

### Task 7: Gates

**Files:**
- Create: `src/alr/engine/gates.py`
- Modify: `src/alr/engine/loop.py`, `src/alr/plain/frontend.py`
- Test: `tests/unit/test_gates.py`

**Port from:** `gralph:src/internal/looper/gates.go`, the plain-path tests in `gates_test.go`

**Interfaces:**
- Consumes: `FrontEnd`, `proc.start`, `proc.finish`, `tasks.parse_timeout`, `errors.quote`, `loop.run`.
- Produces: `first_line`, `gate_limit`, `gate_failure`, `run_gates` in `alr.engine.gates`; `PlainFrontEnd.run_gate` and `gate_finished`.

- [ ] **Step 1: Port the tests**

Into `tests/unit/test_gates.py`: `TestRunLoop_GatesPassCompletesTask`, `TestRunLoop_FileGatesRunAfterEveryCompletedTask`, `TestRunLoop_GateFailureFailsTaskAndStopsRun`, `TestRunLoop_GatesSkippedWhenSessionFails`, `TestRunLoop_GatesNeverReachClaude`, `TestRunLoop_GateShellBehavior`, `TestRunLoop_PlainPrintsGateLineAndOutput`, `TestRunLoop_PlainPassesGateStderrThrough`, `TestRunLoop_GateStdinIsEmptyNotInherited`, `TestGateLimit_FlagBeatsGateBeatsDefault`, `TestGateLimit_DefaultIsTenMinutes`, `TestGateFailure_TimedOutNamesTheLimitThatApplied`, `TestGateFirstLine`.

Also port these, running them through `PlainFrontEnd` instead of a report hook where the Go test uses one only to observe the result (assert on the saved task instead of on events): `TestRun_CancelDuringGateLeavesFileUntouched`, `TestRun_GateTimeoutFailsTaskAndSkipsLaterGates`, `TestRun_GateTimeoutKillsTheGatesDescendants`, `TestRun_CancelDuringGateKillsTheGatesDescendants`, `TestRun_GateWithinTimeoutPasses`, `TestRun_GateTimeoutThatCannotBeParsedFailsTheGate`, `TestRun_GateTimeoutFlagOverridesTheGateTimeout`, `TestRun_GateTimeoutFlagIsNeverSaved`, `TestRun_MultiLineGateErrorsNameOnlyTheFirstLine`.

The remaining stream-path gate tests (`TestRun_GateOutputArrivesAsActivity`, `TestRun_GateFailureReportsFailedTask`, `TestRun_ReportsGateFinished`, `TestRun_GateFinishedCarriesANonZeroExit`, `TestRun_CancelDuringGateReportsNoGateFinished`) belong to Task 10.

`gateFailure(gate, limit, runErr)` in Go becomes `gate_failure(gate, limit, error, timed_out)`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_gates.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError: No module named 'alr.engine.gates'`.

- [ ] **Step 3: Write `src/alr/engine/gates.py`**

```python
"""Gates: commands alr runs itself after a session reports completed."""

DEFAULT_GATE_TIMEOUT = "10m"


def first_line(text: str) -> str:
    """The part of a multi-line value that messages quote, so it stays on one line."""
    return text.split("\n", 1)[0]


def gate_limit(gate: Gate, override: str) -> tuple[str, str]:
    """Pick the timeout that applies, as written where it came from, and name that source."""
    if override:
        return override, "flag"

    if gate.timeout:
        return gate.timeout, "gate"

    return DEFAULT_GATE_TIMEOUT, "default"


def gate_failure(gate: Gate, limit: str, error: str | None, timed_out: bool) -> str:
    name = quote(first_line(gate.cmd))
    if timed_out:
        return f"gate {name} timed out after {limit}"

    return f"gate {name} failed: {error}"


async def run_gates(task: Task, gates: Sequence[Gate], override: str, front_end: FrontEnd) -> tuple[str, str]:
    """Run the file's gates in order; the first that fails makes the task failed."""
    for gate in gates:
        limit, _ = gate_limit(gate, override)
        error, timed_out = await _run_gate(task, gate, limit, front_end)
        if error is None and not timed_out:
            continue

        return FAILED, gate_failure(gate, limit, error, timed_out)

    return COMPLETED, ""
```

`_run_gate(task, gate, limit, front_end) -> tuple[str | None, bool]`:

1. `seconds = parse_timeout(limit)`. A `ValueError` is the gate's failure: return `(str(exc), False)` after calling `front_end.gate_finished(...)` with that error. (In Go, `runGate` returns the parse error as the gate's run error.)
2. Run `await front_end.run_gate(task, gate)` inside `async with asyncio.timeout(seconds):`. A `TimeoutError` means the gate hit its limit: `(None, True)`.
3. Measure the time with `time.monotonic()` and call `front_end.gate_finished(task, gate, limit, error, timed_out, seconds_taken)` for a gate that ended on its own or timed out. A cancelled gate reports nothing: let `asyncio.CancelledError` pass through untouched.

In `alr.engine.loop._run_task`, after a completed session: `state, error = await run_gates(task_copy, gates, options.gate_timeout, front_end)`. Extend the cancellation handling: a cancel during the gates raises `RunCancelledError(f"task {task.id}: {task.name}: stopped during gate {quote(first_line(gate.cmd))}")`. To know which gate was running, catch `asyncio.CancelledError` inside `run_gates` around each gate and re-raise it as a small private exception carrying the gate, or keep the current gate on a local the handler can read; pick whichever keeps `run_gates` flat.

- [ ] **Step 4: Add `run_gate` and `gate_finished` to `PlainFrontEnd`**

```python
    async def run_gate(self, task: Task, gate: Gate) -> str | None:
        print(f"gate: {gate.cmd}", flush=True)
        process = await proc.start(["sh", "-c", gate.cmd])
        return await proc.finish(process)

    def gate_finished(self, task: Task, gate: Gate, limit: str, error: str | None, timed_out: bool, seconds: float) -> None:
        return
```

`proc.start` defaults give the gate no stdin and inherited stdout and stderr, so its output passes straight through. The `flush=True` keeps the `gate:` line ahead of the gate's own output.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_gates.py tests/unit/test_loop.py -x --tb=short`
Expected: PASS.

- [ ] **Step 6: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/engine/gates.py src/alr/engine/loop.py src/alr/plain/frontend.py tests/unit/test_gates.py
git commit -m "Port gates from gralph"
```

---

### Task 8: Commits

**Files:**
- Create: `src/alr/engine/commit.py`
- Modify: `src/alr/engine/loop.py`, `src/alr/plain/frontend.py`
- Test: `tests/unit/test_commit.py`

**Port from:** `gralph:src/internal/looper/commit.go` (everything except `repoFor`), the tests in `commit_test.go` that do not use a report hook, `Start`, or `DryRun`

**Interfaces:**
- Consumes: `FrontEnd`, `proc.capture`, `proc.start`, `proc.finish`, `first_line`, `RunOptions.repo`, the `git_repo` fixture.
- Produces: `Repo`, `open_repo`, `check_log_dir`, `commit` in `alr.engine.commit`; `PlainFrontEnd.commit_started`, `run_git`, `committed`.

- [ ] **Step 1: Port the tests**

Into `tests/unit/test_commit.py`: every `TestOpenRepo_*` test (12), `TestRun_CommitsOneCommitPerCompletedTask`, `TestRun_NilRepoNeverCommits`, `TestRun_NothingToCommitStillCompletes`, `TestRun_FailedGateLeavesChangesUncommitted`, `TestRun_SessionFailureSkipsCommit`, `TestRun_CommitFailureFailsTaskAndStopsRun`, `TestRun_CommitSurvivesAGateEditingGitignore`, `TestRun_CommitAfterTheSessionCommittedItself`, `TestRun_FailedAddFailsTheTask`, `TestRun_CommitWithTaskFileOutsideTheTree`, `TestRun_CommitFromSubdirectoryCommitsWholeTree`, `TestRun_CommitOddTaskNames`, `TestRun_PlainPrintsCommitLineAndGitOutput`, `TestRun_CancelDuringCommitLeavesFileUntouched`, `TestRun_CancelDuringCommitKillsAHookThatIgnoresSIGTERM`, and every `TestCheckLogDir_*` test (5).

`OpenRepo(tasksFile) (*Repo, error)` becomes `await open_repo(tasks_file)`, returning `None` outside a work tree and raising `RepoError`. `(*Repo).CheckLogDir(runDir)` becomes `await check_log_dir(repo, run_dir)`.

The stream-path commit tests (`TestRun_StreamReportsCommitAsActivity`, `TestRun_ReportsCommittedWithTheNewHash`, `TestRun_NothingStagedReportsNoCommitted`) belong to Task 10. The `TestStart_*` and `TestDryRun_Commit` tests belong to Task 9.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_commit.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError: No module named 'alr.engine.commit'`.

- [ ] **Step 3: Write `src/alr/engine/commit.py`**

Port `commit.go` function by function. Points where the Python differs:

- Quiet git commands (`rev-parse --show-toplevel`, `status`, `check-ignore`, `diff --cached --quiet`, `rev-parse HEAD`) run through `proc.capture(["git", ...], cwd=repo.root, gentle=True)`. Only `add -A` and `commit -m <name>` go through `front_end.run_git(task, argv, repo.root)`.
- `find_root`: run with `env={**os.environ, "LC_ALL": "C"}` and no `cwd`. `FileNotFoundError` (git not installed) returns `None`. A non-zero exit whose stderr contains `not a git repository` or `must be run in a work tree` returns `None`. Any other failure raises `RepoError(f"--commit: git rev-parse: {reason}")`, where `reason` is `gitMessage`: the first line of stderr, trimmed, without a leading `fatal: `, or the exit text from `proc.exit_text` when stderr is empty.
- `_relative(repo, path)`: `os.path.abspath`, resolve only the parent folder's symlinks (`os.path.realpath` on the nearest existing ancestor, as `resolveExisting` does), `os.path.relpath` against the root, and treat a result that is `..`, starts with `../`, or is absolute as outside the tree.
- Error text, exact: `--commit needs the task file ignored by git or outside the repository: <rel>`; `--commit needs the log directory ignored by git or outside the repository: <dir of rel>`; `--commit needs a clean work tree; commit, stash, or remove:\n<files>`; `--commit: git status: <reason>`; `--commit: git check-ignore: <reason>`.
- `commit(repo, task, front_end) -> tuple[str, str]`: `add -A`; if it fails return `(FAILED, f"commit failed: {error}")`. Check `git diff --cached --quiet`: exit 0 means nothing staged, return `(COMPLETED, "")`; exit 1 means staged; any other exit is a failure, `(FAILED, f"commit failed: {exit text}")`. When staged: `front_end.commit_started(task)`, then `commit -m <task.name>`; on failure `(FAILED, f"commit failed: {error}")`. On success read `git rev-parse HEAD`; when that works call `front_end.committed(task, hash)`; when it does not, call nothing and still return `(COMPLETED, "")`.
- The engine does not print. The `commit:` line belongs to the plain front end.

In `alr.engine.loop`: type `RunOptions.repo` as `Repo | None`. In `_run_task`, after the gates return `COMPLETED` and `options.repo` is not `None`: `state, error = await commit(options.repo, task_copy, front_end)`. A cancel during the commit raises `RunCancelledError(f"task {task.id}: {task.name}: stopped during commit")`.

- [ ] **Step 4: Add the commit calls to `PlainFrontEnd`**

```python
    def commit_started(self, task: Task) -> None:
        print(f"commit: {first_line(task.name)}", flush=True)

    async def run_git(self, task: Task, argv: Sequence[str], cwd: str) -> str | None:
        process = await proc.start(argv, cwd=cwd)
        return await proc.finish(process, gentle=True)

    def committed(self, task: Task, commit_hash: str) -> None:
        return
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_commit.py tests/unit/test_gates.py tests/unit/test_loop.py -x --tb=short`
Expected: PASS.

- [ ] **Step 6: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/engine/commit.py src/alr/engine/loop.py src/alr/plain/frontend.py tests/unit/test_commit.py
git commit -m "Port --commit from gralph"
```

---

### Task 9: The plain run and the dry run

**Files:**
- Create: `src/alr/plain/start.py`, `src/alr/plain/dryrun.py`
- Modify: `src/alr/plain/report.py`
- Test: `tests/unit/test_plain_start.py`, `tests/unit/test_plain_dryrun.py`

**Port from:** `Start`, `DryRun`, `printGateLimits` in `gralph:src/internal/looper/looper.go`; `repoFor` in `commit.go`; the `TestStart_*` and `TestDryRun_*` tests in `looper_test.go` and `commit_test.go`

**Interfaces:**
- Consumes: `load_prompt`, `load_tasks_report`, `print_tasks`, `run`, `RunOptions`, `PlainFrontEnd`, `open_repo`, `gate_limit`, `first_line`.
- Produces: `repo_for(out, tasks_file, commit)` in `alr.plain.report`; `start(prompt_file, tasks_file, gate_timeout, session_args, commit)` in `alr.plain.start`; `dry_run(out, tasks_file, gate_timeout, sandbox_file, commit)` in `alr.plain.dryrun`.

- [ ] **Step 1: Port the tests**

`tests/unit/test_plain_start.py`: `TestStart_MissingPromptFile`, `TestStart_MissingTasksFile`, `TestStart_EmptyPrompt`, `TestStart_WhitespaceOnlyPrompt`, `TestStart_InvalidTasksYAML`, `TestStart_LoadFailuresKeepTheErrorChain`, `TestStart_RunLoopFailureIsWrappedWithLoopErrorPrefix`, `TestStart_Success`, `TestStart_RefusesWhenAnyTaskFailed`, `TestStart_CommitRefusesDirtyTreeBeforeAnySession`, `TestStart_CommitFlagCommitsTheTask`, `TestStart_WithoutCommitFlagIgnoresADirtyTree`.

`tests/unit/test_plain_dryrun.py`: `TestDryRun_ValidFile`, `TestDryRun_NoFailedTasksTable`, `TestDryRun_InvalidFile`, `TestDryRun_FlagsFailedTasks`, `TestDryRun_ListsEffectiveGateTimeouts`, `TestDryRun_NoGatesPrintsNoGateLines`, `TestDryRun_NamesSandboxSettings`, `TestDryRun_Commit`.

For `TestStart_LoadFailuresKeepTheErrorChain`, Go checks `errors.Is`; in Python check that the raised error's `__cause__` chain holds the original `OSError` or `TaskFileError`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_plain_start.py tests/unit/test_plain_dryrun.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError: No module named 'alr.plain.start'`.

- [ ] **Step 3: Write `repo_for`, `start`, and `dry_run`**

`alr.plain.report.repo_for(out, tasks_file, commit)`: return `None` when `commit` is false. Otherwise `repo = await open_repo(tasks_file)`; when it is `None`, write `commit: not a git repository, nothing will be committed` to `out`. Return `repo`.

`alr.plain.start.start`:

```python
async def start(prompt_file: str, tasks_file: str, gate_timeout: str, session_args: Sequence[str], commit: bool) -> None:
    """Run the loop in plain mode: load, check, then run every pending task."""
    try:
        prompt = load_prompt(prompt_file)
        task_list = load_tasks_report(sys.stdout, tasks_file)
    except FailedTasksError:
        raise
    except (PromptError, TaskFileError) as exc:
        raise type(exc)(f"failed to start loop runner: {exc}") from exc

    repo = await repo_for(sys.stdout, tasks_file, commit)
    options = RunOptions(tasks_file=tasks_file, gate_timeout=gate_timeout, session_args=session_args, repo=repo)
    try:
        await run(prompt, task_list, options, PlainFrontEnd())
    except RunCancelledError as exc:
        raise RunCancelledError(f"loop error: {exc}") from exc
    except RunError as exc:
        raise RunError(f"loop error: {exc}") from exc
```

`alr.plain.dryrun.dry_run`, in gralph's order:

1. `load_tasks_report(out, tasks_file)`. On `FailedTasksError` return normally: the notice and table were printed and a dry run exits 0. Any other error propagates unwrapped.
2. `print_tasks(out, task_list)`.
3. One line per gate: `gate: <first line of cmd>: <limit> (<source>)`, from `gate_limit(gate, gate_timeout)`.
4. When `sandbox_file` is not empty: `sandbox settings: <sandbox_file>`.
5. `repo = await repo_for(out, tasks_file, commit)`; when it is not `None`: `commit: <repo.root>`.
6. `<tasks_file> is valid`.

A dry run never starts Claude and never writes a file.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_plain_start.py tests/unit/test_plain_dryrun.py -x --tb=short`
Expected: PASS.

- [ ] **Step 5: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/plain tests/unit/test_plain_start.py tests/unit/test_plain_dryrun.py
git commit -m "Port the plain run and the dry run from gralph"
```

---

### Task 10: The stream front end

**Files:**
- Create: `src/alr/stream/events.py`, `src/alr/stream/parser.py`, `src/alr/stream/frontend.py`
- Test: `tests/unit/test_stream_parser.py`, `tests/unit/test_stream_frontend.py`

**Port from:** `gralph:src/internal/looper/event.go`, `stream.go`, `runCmdStream` in `gates.go`, the report branches in `gates.go` and `commit.go`; `stream_test.go`, `stream_run_test.go`, `event_test.go`, and the stream-path tests left over from `gates_test.go` and `commit_test.go`

**Interfaces:**
- Consumes: `FrontEnd`, `SessionResult`, `proc.start`, `proc.finish`, `proc.read_lines`, `first_line`, `run`, `RunOptions`.
- Produces: the seven event classes and `Event` in `alr.stream.events`; `parse_stream_line` in `alr.stream.parser`; `StreamFrontEnd` in `alr.stream.frontend`.

- [ ] **Step 1: Port the tests**

`tests/unit/test_stream_parser.py`: `TestParseStreamLine`.

`tests/unit/test_stream_frontend.py`: from `stream_run_test.go`, all nine `TestRunTaskStream_*` tests; from `event_test.go`, all five tests; from `gates_test.go`: `TestRun_GateOutputArrivesAsActivity`, `TestRun_GateFailureReportsFailedTask`, `TestRun_ReportsGateFinished`, `TestRun_GateFinishedCarriesANonZeroExit`, `TestRun_CancelDuringGateReportsNoGateFinished`; from `commit_test.go`: `TestRun_StreamReportsCommitAsActivity`, `TestRun_ReportsCommittedWithTheNewHash`, `TestRun_NothingStagedReportsNoCommitted`.

A Go test's `report func(Event)` that appends to a slice becomes `events: list[Event] = []` and `StreamFrontEnd(events.append)`. `Event.Kind == Activity` becomes `isinstance(event, Activity)`. `Event.Err` on `GateFinished` being `context.DeadlineExceeded` becomes `event.timed_out is True`. `TestRunTaskStream_WritesNothingToStdout` uses `capfd` and asserts both streams are empty.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_stream_parser.py tests/unit/test_stream_frontend.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError: No module named 'alr.stream.parser'`.

- [ ] **Step 3: Write `src/alr/stream/events.py`**

```python
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
class GateFinished:
    task: Task
    gate: Gate
    limit: str  # the timeout the gate ran under, as written
    error: str | None  # None when the gate passed or timed out
    timed_out: bool
    seconds: float


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


Event = TaskStarted | Activity | SessionFinished | GateFinished | Committed | TaskFinished | RunDone
```

- [ ] **Step 4: Write `src/alr/stream/parser.py`**

Port `parseStreamLine`, `textActivity`, and `toolActivity`. `parse_stream_line(line: bytes) -> tuple[list[str], str, bool]` returns `(activity, result, is_result)`. A line that is not valid JSON, is not an object, or has a `type` other than `assistant` or `result` returns `([], "", False)`. Read nested fields defensively (`message`, `content`, `input` may be missing or the wrong type; treat that as empty). The tool line is `→ <Name>` or `→ <Name> <first line of target>`, with the target from `command` for `Bash`, `file_path` for `Read`, `Edit`, and `Write`, and `pattern` for `Grep` and `Glob`.

- [ ] **Step 5: Write `src/alr/stream/frontend.py`**

```python
class StreamFrontEnd:
    """Captures everything a run's processes print and reports it as events."""

    session_flags: Sequence[str] = ("--output-format", "stream-json", "--verbose")

    def __init__(self, report: Callable[[Event], None]) -> None:
        self._report = report
```

- `task_skipped`: nothing (gralph sends no event for a skipped task).
- `task_started`: `TaskStarted(task)`.
- `run_session`: start with `stdin=PIPE, stdout=PIPE, stderr=PIPE`. A `FileNotFoundError` returns `SessionResult("", not_found_text(argv[0]))`. Feed stdin, and read stdout and stderr at the same time with `proc.read_lines`. Each stdout line goes through `parse_stream_line`: every activity line becomes `Activity(task, line)`, and a result event's text is kept. Each stderr line becomes `Activity(task, line.decode("utf-8", errors="replace").rstrip("\r\n"))`. Return `SessionResult(text=<kept result text>, error=<exit text>)`. Nothing is written to stdout or stderr.
- `session_finished`: `SessionFinished(task, seconds)`.
- `run_gate`: report `Activity(task, "→ gate " + first_line(gate.cmd))`, then run `sh -c <cmd>` with both pipes and report each stdout and stderr line as `Activity`, stripped of its line ending.
- `gate_finished`: `GateFinished(task, gate, limit, error, timed_out, seconds)`.
- `commit_started`: `Activity(task, "→ commit " + first_line(task.name))`.
- `run_git`: like `run_gate`'s process handling, with `cwd` and `gentle=True`, and no marker line.
- `committed`: `Committed(task, commit_hash)`.
- `task_finished`: `TaskFinished(task, error, seconds)`.
- `run_done`: `RunDone(error, seconds)`.

Share the "run a command and report each output line as activity" code between `run_gate` and `run_git` in one private method, as gralph's `runCmdStream` does.

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_stream_parser.py tests/unit/test_stream_frontend.py -x --tb=short`
Expected: PASS.

- [ ] **Step 7: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/stream tests/unit/test_stream_parser.py tests/unit/test_stream_frontend.py
git commit -m "Port the stream path: events, stream-json parsing, and the stream front end"
```

---

### Task 11: The run log

**Files:**
- Create: `src/alr/stream/runlog.py`
- Test: `tests/unit/test_runlog.py`

**Port from:** `gralph:src/internal/runlog/runlog.go`, `ledger.go`, `runlog_test.go`

**Interfaces:**
- Consumes: the event classes from Task 10; `alr.errors.RunLogError`; `alr.durations.format_duration`; `alr.engine.gates.first_line`; `alr.tasks.FAILED`.
- Produces: `run_dir`, `Info`, `RunLog.open`, `RunLog.record`, `RunLog.close`.

- [ ] **Step 1: Port the tests**

Into `tests/unit/test_runlog.py`: all fifteen tests from `runlog_test.go`. `TestNilLog_RecordsNothing` has no Python equivalent (there is no nil receiver); replace it with a test that `record` after `close` raises `RunLogError`. `TestRecord_ConcurrentActivityKeepsLinesWhole` becomes a test that 1,000 `Activity` events each produce one whole line. The Go tests pass a fixed clock through `open(runDir, info, now)`; pass `now=` to `RunLog.open`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_runlog.py -x --tb=short`
Expected: FAIL, `ModuleNotFoundError: No module named 'alr.stream.runlog'`.

- [ ] **Step 3: Write `src/alr/stream/runlog.py`**

Port `runlog.go`. Exact formats:

- `run_dir(log_dir, start)`: `os.path.join(log_dir, start.strftime("%Y%m%dT%H%M%S"))`.
- `RunLog.open`: `os.makedirs(parent, mode=0o700, exist_ok=True)`, then `os.mkdir(run_dir, 0o700)` (an existing run folder is an error). Open `run.jsonl` with `os.open(path, O_CREAT | O_WRONLY | O_APPEND, 0o600)`. Write the `run_started` line. Any `OSError` becomes `RunLogError(f"{path}: {exc.strerror}") from exc`.
- Every ledger line is `json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n"`, where `line` is a `dict` built in the key order of the matching struct in `ledger.go`, starting with `time` and `event`. Leave out a key marked `omitempty` when its value is empty: `sandbox_settings`, `gate_timeout`, `error`.
- `time` is the clock's value as RFC 3339 with seconds precision and the local offset: `now().astimezone().isoformat(timespec="seconds")`, with a trailing `+00:00` written as `Z`.
- Event to line: `TaskStarted` → `task_started` (`task`, `name`), and open `task-<id>.log` for appending; `Activity` → append `HH:MM:SS <line>\n` to that file; `SessionFinished` → `session_finished` (`task`, `state`, `error`, `duration`); `GateFinished` → `gate_finished` (`task`, `cmd` first line only, `result` one of `passed`, `timed_out`, `failed`, `timeout`, `duration`); `Committed` → `committed` (`task`, `hash`); `TaskFinished` → `task_finished` (same shape as `session_finished`), remember whether the task failed, and close the detail file; `RunDone` → `run_finished` (`result`, `error`, `duration`), where `result` is `completed` with no error, `failed` when the last finished task failed, and `stopped` otherwise.
- `duration` is `format_duration(event.seconds)`.
- Flush after every write. An `OSError` on a write becomes `RunLogError`.
- `close` closes both files and is safe to call twice.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_runlog.py -x --tb=short`
Expected: PASS.

- [ ] **Step 5: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/stream/runlog.py tests/unit/test_runlog.py
git commit -m "Port the run log writer from gralph"
```

---

### Task 12: The command line

**Files:**
- Rewrite: `src/alr/cli.py`
- Test: `tests/unit/test_cli.py`

**Port from:** `gralph:src/cmd/main/main.go` (`main`, `fatal`, `resolveDir`, `requireFile`, `validateGateTimeout`, `checkGateTimeout`, `checkLogDir`, `sessionArgs`, `validateRequiredFlags`); from `main_test.go`: `TestSessionArgs`, `TestCheckGateTimeout`, `TestCheckLogDir`, `TestResolveDir`

**Interfaces:**
- Consumes: `plain.start.start`, `plain.dryrun.dry_run`, `sandbox_args`, `bypass_args`, `parse_timeout`, the error classes.
- Produces: `cli.main(argv: list[str] | None = None) -> int`, plus `resolve_dir`, `check_gate_timeout`, `check_log_dir`, `session_args`, each a plain function the tests call directly.

- [ ] **Step 1: Port the tests**

Into `tests/unit/test_cli.py`: `TestSessionArgs`, `TestCheckGateTimeout`, `TestCheckLogDir`, `TestResolveDir`. Go's `sessionArgs(sandboxFile, skip, dryRun, wizard)` becomes `session_args(sandbox_file, skip, dry_run)`; drop the cases with `wizard` true. Go's `checkLogDir(logDir, plain, dryRun)` becomes `check_log_dir(log_dir, dry_run)`; drop the cases with `plain` false. Errors are `UsageError` (or `SandboxError` from a bad settings file).

Add tests that call `cli.main([...])` and check the return code and `capsys` output:

```python
def test_version_prints_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("alr version: ")


def test_missing_required_flags_are_each_named(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert "error: required flag --prompt not set" in err
    assert "error: required flag --tasks not set" in err


def test_dry_run_needs_no_prompt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tasks_file = tmp_path / "tasks.yaml"
    tasks_file.write_text("tasks:\n  - id: 1\n    name: a\n    prompt: p\n")
    assert cli.main(["-t", str(tasks_file), "--dry-run"]) == 0
    assert f"{tasks_file} is valid" in capsys.readouterr().out


def test_both_permission_flags_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["-t", "t.yaml", "-p", "p.md", "--sandbox-settings", "s.json", "--skip-permissions"])
    assert code == 1
    assert "error: --sandbox-settings and --skip-permissions cannot be used together" in capsys.readouterr().err


def test_log_dir_is_refused_on_a_real_run(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["-t", "t.yaml", "-p", "p.md", "--skip-permissions", "--log-dir", "logs"])
    assert code == 1
    assert "error: --log-dir only works with the full-screen view" in capsys.readouterr().err


def test_empty_gate_timeout_is_bad(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["-t", "t.yaml", "--dry-run", "--gate-timeout", ""]) == 1
    assert "error: --gate-timeout: must be a duration string such as 90s or 10m" in capsys.readouterr().err


def test_install_skill_is_not_a_flag() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--install-skill"])

    assert exit_info.value.code == 2


def test_flag_prefixes_are_not_expanded() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--dry", "-t", "t.yaml"])
```

Add the Review Focus tests. The first covers a stop that lands when no task is running, which reaches `cli` as a bare cancellation. The second sends a real SIGINT to the real command while it waits between starting up and its first session finishing.

```python
def test_a_stop_with_no_task_running_is_one_error_line(capsys: pytest.CaptureFixture[str]) -> None:
    async def stopped() -> None:
        raise asyncio.CancelledError

    assert cli._run(stopped()) == 1
    assert capsys.readouterr().err == "error: interrupted\n"


def test_sigint_leaves_no_traceback_and_no_change(fake_claude: Path, tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    tasks_file = tmp_path / "tasks.yaml"
    tasks_file.write_text("tasks:\n  - id: 1\n    name: a\n    prompt: p\n")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("shared")
    before = tasks_file.read_text()
    alr = Path(sys.executable).parent / "alr"
    env = {**os.environ, "FAKECLAUDE_BLOCK": "1", "FAKECLAUDE_READY_FILE": str(ready)}

    process = subprocess.Popen(
        [str(alr), "-t", str(tasks_file), "-p", str(prompt), "--skip-permissions"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    for _ in range(1000):
        if ready.exists():
            break

        time.sleep(0.01)

    process.send_signal(signal.SIGINT)
    _, err = process.communicate(timeout=10)

    assert process.returncode == 1
    assert "Traceback" not in err
    assert err.strip().startswith("error: ")
    assert tasks_file.read_text() == before
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/unit/test_cli.py -x --tb=short`
Expected: FAIL with `NotImplementedError` from the stub `main`.

- [ ] **Step 3: Write `src/alr/cli.py`**

The parser: `argparse.ArgumentParser(prog="alr", allow_abbrev=False)`, with these arguments and gralph's help strings (replace `gralph` with `alr` in the text):

| Flags | Kind | Default |
| --- | --- | --- |
| `-v`, `--version` | `store_true` | `False` |
| `-d`, `--dir` | string | `""` |
| `-t`, `--tasks` | string | `""` |
| `-p`, `--prompt` | string | `""` |
| `--dry-run` | `store_true` | `False` |
| `--gate-timeout` | string | `None` (so an empty value given on purpose can be told apart) |
| `--no-tui` | `store_true` | `False` |
| `--sandbox-settings` | string | `""` |
| `--skip-permissions` | `store_true` | `False` |
| `--commit` | `store_true` | `False` |
| `--log-dir` | string | `""` |

The helper functions, with gralph's exact messages:

- `resolve_dir(dir, tasks_path, prompt_path, dry_run) -> tuple[str, str]`: as `resolveDir`. A missing file raises `UsageError(f"--dir: no {name} in {dir}")`.
- `check_gate_timeout(value)`: `parse_timeout`; a `ValueError` becomes `UsageError(f"--gate-timeout: {exc}")`.
- `check_log_dir(log_dir, dry_run)`: when `log_dir` is not empty and it is not a dry run, raise `UsageError("--log-dir only works with the full-screen view")`.
- `session_args(sandbox_file, skip, dry_run) -> list[str]`: both given raises `UsageError("--sandbox-settings and --skip-permissions cannot be used together")`; a settings file returns `sandbox_args(sandbox_file)`; `skip` returns `bypass_args()`; a dry run returns `[]`; otherwise raise `UsageError("pass --sandbox-settings <path>, or --skip-permissions to run without a sandbox")`.

`main(argv)`, in gralph's startup order:

1. Parse. `--version` prints `alr version: <importlib.metadata.version("alr")>` and returns 0.
2. `resolve_dir`.
3. `check_gate_timeout` when `--gate-timeout` was passed (the value is not `None`).
4. Required flags: `--prompt` unless `--dry-run`, and `--tasks`. For each one missing, write `error: required flag <flag> not set` to stderr; then a blank line and `parser.print_usage(sys.stderr)`; return 1.
5. `check_log_dir`.
6. `session_args`.
7. Dry run: `return _run(dry_run(sys.stdout, tasks, gate_timeout or "", sandbox_settings, commit))`. Real run: `return _run(start(prompt, tasks, gate_timeout or "", session, commit))`.

Steps 2 to 6 run inside one `try`; an `AlrError` writes `error: <message>` to stderr and returns 1. `_run` reports an `AlrError` from step 7 the same way. A `FailedTasksError` on a real run is reported like any other (its table was already printed).

Running the coroutine and stopping it:

```python
def _run(coro: Coroutine[None, None, None]) -> int:
    """Run coro to the end; SIGINT or SIGTERM cancels it, which stops the run."""
    try:
        asyncio.run(_with_signals(coro))
    except asyncio.CancelledError:
        # The stop landed while no task was running, so there is no task to name.
        print("error: interrupted", file=sys.stderr)
        return 1
    except AlrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


async def _with_signals(coro: Coroutine[None, None, None]) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)

    await coro
```

Write the `task is None` check as an explicit `if task is None: raise RuntimeError("no running task")` instead of the `assert` shown; asserts are stripped under `-O`.

One known gap to cover here: when a commit is being stopped gently (SIGTERM, then up to 2 seconds), a second SIGINT or SIGTERM must not let `alr` exit while git or one of its hooks is still running. Make a second signal during that wait a no-op, and add a test with a hook that ignores SIGTERM.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/unit/test_cli.py -x --tb=short`
Expected: PASS.

- [ ] **Step 5: Try the command by hand**

```bash
printf 'tasks:\n  - id: 1\n    name: smoke\n    prompt: say hi\n' > /tmp/claude-smoke-tasks.yaml
uv run alr -t /tmp/claude-smoke-tasks.yaml --dry-run
uv run alr --version
rm /tmp/claude-smoke-tasks.yaml
```

Expected: the task table and `/tmp/claude-smoke-tasks.yaml is valid`; then `alr version: ...`.

- [ ] **Step 6: Run the full gate and commit**

Run: `make test`

```bash
git add src/alr/cli.py src/alr/main.py tests/unit/test_cli.py
git commit -m "Port the command line and startup checks from gralph"
```

---

### Task 13: The end-to-end suite and the import boundary test

**Files:**
- Create: `tests/e2e/conftest.py`, `tests/e2e/test_loop.py`, `tests/e2e/test_result.py`, `tests/e2e/test_commit.py`, `tests/e2e/test_dir.py`, `tests/e2e/test_dry_run.py`, `tests/e2e/test_gates.py`, `tests/e2e/test_flags.py`, `tests/e2e/test_log_dir.py`, `tests/e2e/test_sandbox.py`, `tests/e2e/test_signal.py`, `tests/unit/test_import_boundaries.py`
- Modify: `build/Dockerfile`

**Port from:** `gralph:tests/e2e/claude_helpers_test.go`, `claude_loop_test.go`, `claude_result_test.go`, `commit_test.go`, `dir_test.go`, `dry_run_test.go`, `gates_test.go`, `gralph_test.go`, `log_dir_test.go`, `no_tui_test.go`, `sandbox_test.go`, `signal_unix_test.go`

**Interfaces:**
- Consumes: the installed `alr` command, the `fake_claude` and `git_repo` fixtures.
- Produces: nothing other tasks use.

- [ ] **Step 1: Write the e2e helpers**

`tests/e2e/conftest.py`: port the helpers in `claude_helpers_test.go`. The core one:

```python
ALR = Path(sys.executable).parent / "alr"


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str


def run_alr(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None, timeout: float = 60.0) -> Result:
    """Run the real alr command, with no terminal, and return what it printed."""
    done = subprocess.run(
        [str(ALR), *args],
        env={**os.environ, **(env or {})},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return Result(done.returncode, done.stdout, done.stderr)
```

Port the remaining helpers (writing a tasks file and prompt into `tmp_path`, reading the fake claude's record file, reading the saved task file back) under the same names in snake_case.

- [ ] **Step 2: Port the tests**

One Python file per Go file, every test function listed here:

- `test_loop.py` from `claude_loop_test.go`: all ten tests. `TestIterationsFlagRejected` and `TestStart_AbandonedStateRejected` stay: both behaviors hold in alr. The first expects exit code 2 from `argparse`.
- `test_result.py` from `claude_result_test.go`: all eight.
- `test_commit.py` from `commit_test.go`: all nine.
- `test_dir.py` from `dir_test.go`: all four.
- `test_dry_run.py` from `dry_run_test.go`: all seven.
- `test_gates.py` from `gates_test.go`: all eleven.
- `test_flags.py` from `gralph_test.go`: `TestVersionFlag` (assert the one-line `alr version: ` output), `TestHelpFlag` (assert exit 0 and that each flag name appears), `TestMissingPrompt`, `TestMissingTasks`, `TestMissingBothRequiredFlags`, `TestNonexistentFiles`. From `no_tui_test.go`: `TestNonTerminal_DefaultsToPlain`, plus one new test that `--no-tui` is accepted and the run behaves the same.
- `test_log_dir.py` from `log_dir_test.go`: all four.
- `test_sandbox.py` from `sandbox_test.go`: all five.
- `test_signal.py` from `signal_unix_test.go`: both. Use `subprocess.Popen` and `send_signal`; keep reading the pipes with `communicate`.

Not ported: `install_skill_test.go`, `skill_check_test.go`, `wizard_signal_linux_test.go`. Put a comment at the top of `tests/e2e/conftest.py` saying so and why (out of scope until the TUI design).

Where a Go assertion names the command, expect `alr`. Where it asserts text after a library prefix (a YAML or JSON syntax message), assert the prefix only.

- [ ] **Step 3: Write the import boundary test**

`tests/unit/test_import_boundaries.py`:

```python
"""engine, plain, and stream stay apart, so each path can be read on its own."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "alr"

FORBIDDEN = {
    "engine": ("alr.plain", "alr.stream", "alr.tui"),
    "plain": ("alr.stream", "alr.tui"),
    "stream": ("alr.plain", "alr.tui"),
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)

        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)

    return names


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_package_does_not_import_across_the_line(package: str) -> None:
    for path in sorted((SRC / package).rglob("*.py")):
        for name in imported_modules(path):
            for banned in FORBIDDEN[package]:
                assert not (name == banned or name.startswith(banned + ".")), f"{path} imports {name}"


def test_engine_never_prints() -> None:
    for path in sorted((SRC / "engine").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "print(" not in source, f"{path} prints"
        assert "sys.stdout" not in source, f"{path} writes to stdout"
        assert "sys.stderr" not in source, f"{path} writes to stderr"
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/e2e tests/unit/test_import_boundaries.py -x --tb=short`
Expected: PASS. A failure here is a compatibility bug in an earlier task's code: fix it in that module, add a unit test for it there, and re-run.

- [ ] **Step 5: Install git in the build image**

The commit tests run real git, and the build image does not have it. In `build/Dockerfile`, add this line right after `WORKDIR /workspace`:

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
```

Run: `make build`
Expected: the image builds, ruff and pytest pass inside it, and the wheel lands in `dist/`.

- [ ] **Step 6: Run the full gate and commit**

Run: `make test`

```bash
git add tests/e2e tests/unit/test_import_boundaries.py build/Dockerfile
git commit -m "Port the end-to-end suite from gralph and pin the import boundaries"
```

---

### Task 14: README and changelog

**Files:**
- Modify: `README.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: the finished command.
- Produces: nothing code uses.

- [ ] **Step 1: Update `README.md`**

Use the `/readme-writer` skill. The README must say, in the owner's casual voice:

- What alr is now: the Python port of gralph, drop-in compatible, on its way to becoming an agent. Replace the "Why this when you have gralph?" section; the learning-project framing no longer applies.
- Usage, with the full flag table from `docs/design/01_requirements.md`, and these examples:

```sh
alr -t tasks.yaml --dry-run
alr -p prompt.md -t tasks.yaml --sandbox-settings sandbox.json
alr -d path/to/folder --skip-permissions --commit
```

- How a run works: one fresh `claude --print` session per task, the JSON result line, gates, `--commit`, a failed task stops the run.
- Key considerations: needs the `claude` CLI on `PATH`; every real run needs `--sandbox-settings` or `--skip-permissions`; gates and commits run unsandboxed; no retries; Linux and macOS only; no full-screen view yet, so `--log-dir` is not usable yet.
- Development: `uv sync`, `make test`, `make build`; a link to `docs/design/00_overview.md`.
- Remove the dated "Current state" paragraph and the link to `docs/requirements/01_requirements_v01.md`.

- [ ] **Step 2: Update `CHANGELOG.md`**

Under `## [Unreleased]`, add:

```markdown
### Changed

- alr is now a Python port of gralph's engine and is drop-in compatible with
  it: same flags, task file, result line, gates, commits, and sandbox
  behavior. See `docs/design/`.
- Task states are `pending`, `completed`, and `failed`. `abandoned` is gone.
- `-d` now means `--dir`. `--dry-run` has no short form.

### Added

- Gates: a top-level `gates:` list of commands run after each completed task,
  with `--gate-timeout`.
- `--commit`: one git commit per completed task.
- `--sandbox-settings` and `--skip-permissions`; a real run needs one of them.
- `--dir`: a run folder holding `tasks.yaml` and `prompt.md`.

### Removed

- The `pydantic` dependency.
```

- [ ] **Step 3: Run the full gate and commit**

Run: `make test`

```bash
git add README.md CHANGELOG.md
git commit -m "Describe the gralph port in the README and changelog"
```

---

## Who Does What

| Tasks | Agent |
| --- | --- |
| 1 to 12, and the Python parts of 13 | `python software engineer` |
| Task 13, Step 5 (`build/Dockerfile`) | `devops engineer` |
| Task 14, Step 1 | `/readme-writer` skill |
| Task 14, Step 2 | `technical writer` |

Each agent needs read access to `/home/jeremy/dev/gralph` for the Go source its task names.
