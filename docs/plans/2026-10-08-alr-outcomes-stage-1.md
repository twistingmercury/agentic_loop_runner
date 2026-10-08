# alr Outcomes, Stage 1: The Contract and Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every way a run can end has its own exit code and a logged reason, and no task is saved as `completed` unless everything its commit mode asks of it succeeded.

**Architecture:** Each error class carries its own `exit_code`, and `cli` maps it, so there is no scattered `return 1`. The engine reports how a run ended (`result` and `exit_code`) through `run_done`, and the run log writes both. `run` mode keeps task states in memory and saves `tasks.yaml` once, at the end.

**Tech Stack:** Python 3.12 (see `pyproject.toml`), asyncio, PyYAML, Textual (view), pytest, ruff, uv.

**Spec:** `docs/design/02_architectural_decisions.md`, ADR-025 (this plan is its stage 1; stage 2, commit safety, is `2026-10-08-alr-outcomes-stage-2.md`).

## Global Constraints

- Run `make test` (ruff format, ruff check --fix, then the unit tests) before every commit. The e2e suite runs with `make build` (a container, against the built wheel).
- Exit codes: `0` completed, `1` task or gate failed, `2` usage error, `3` refused to start, `4` commit failed, `130` SIGINT, `143` SIGTERM.
- A refusal's first stderr line is exactly `error: refused: <reason>: <message>`, with the reasons `dirty-tree`, `task-file-visible`, `log-folder-visible`, `failed-tasks`, `invalid-task-file`, `invalid-sandbox-settings`, `log-unwritable`, `git-error`, `locked`. A refusal always names its reason. The `<message>` is the existing message text, unchanged.
- A refusal writes no run log and changes nothing on disk.
- `run_finished.result` is one of `completed`, `failed`, `commit_failed`, `stopped`. `run_started.commit` is one of `"none"`, `"task"`, `"run"`.
- A commit failure reason is the first non-empty line of git's combined output, trimmed to 200 characters; `task-N.log` keeps the full text.
- Breaking changes are allowed pre-1.0 (ADR-022). Do not add aliases or compatibility shims.
- Code rules: never-nester, a blank line after every `if` block, comments explain why, no inline lint suppressions, keep every existing docstring.
- The packages `engine`, `plain`, `record`, `stream`, `tui` stay apart (`tests/unit/test_import_boundaries.py`). Shared names go in `alr/tasks.py` or `alr/errors.py`.
- Work on branch `feature/outcomes`. Nobody but the user runs `git push`. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

These are inputs the spec implies and no single task's happy path exercises. Each has a test in the task that owns it.

1. A task name containing ` `, `\x85`, or `\r` (not just `\n`) is rejected at load, naming the task id (Task 7).
2. A resumed `run`-mode run: tasks already `completed` from an earlier run stay `completed` when a later task fails, and the commit lists only the tasks that ran (Task 6).
3. A SIGTERM during the final `run` commit leaves every task that ran `pending` in `tasks.yaml` and exits 143 (Task 6).
4. `--reset-failed` on a dirty tree is refused (exit 3) and the failed tasks stay `failed` on disk (Task 4).
5. `--dry-run --commit run` with an invalid task file or a dirty tree exits 3 with the refusal line, and an invalid file's message is not doubled (Task 4).

---

## File Structure

| File | Change |
|---|---|
| `src/alr/errors.py` | `exit_code` on every class; new `RefusedError`, `CommitFailedError`; `FailedTasksError` becomes a refusal |
| `src/alr/tasks.py` | new `RunResult`, `PASSED`; line-break check on task names |
| `src/alr/engine/stop.py` | new: `Stop`, which signal stopped the run |
| `src/alr/engine/frontend.py` | `run_done` gets `result` and `exit_code`; new `FirstLine` helper |
| `src/alr/engine/loop.py` | result and exit mapping; commit failure raises `CommitFailedError`; `run`-mode lifecycle |
| `src/alr/engine/commit.py` | refusals raise `RefusedError`; `commit` returns git's reason |
| `src/alr/record/events.py`, `record/runlog.py` | `RunDone` fields; `run_finished` `result` and `exit`; `commit` mode field |
| `src/alr/plain/*.py`, `src/alr/stream/frontend.py` | `run_done` and `run_git` changes; refusal order in `start` |
| `src/alr/cli.py` | map `exit_code`; signal exit codes; missing flag is exit 2 |
| `src/alr/tui/*.py` | `passed` rows; refusal text without its prefix; drop redundant mount lookup |
| `tests/unit/*`, `tests/e2e/test_outcomes.py` | tests named in each task |
| `README.md`, `CHANGELOG.md`, `src/alr/skills/...`, `docs/design/01,03` | Task 9 |

---

### Task 1: Exit codes live on the error classes

**Files:**
- Modify: `src/alr/errors.py`
- Modify: `src/alr/cli.py` (`_main`, `_install_skill`, `_run`, `_report_missing` callers)
- Test: `tests/unit/test_errors.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Produces: `AlrError.exit_code: int` (default 1); `UsageError.exit_code == 2`; `RefusedError(reason: str, message: str)` with `.reason`, `.message`, `exit_code == 3`, `str(exc) == f"refused: {reason}: {message}"`; `CommitFailedError(RunError)` with `exit_code == 4`; `FailedTasksError(task_list)` is a `RefusedError` with reason `failed-tasks`.
- Produces: reason constants in `errors.py`: `DIRTY_TREE`, `TASK_FILE_VISIBLE`, `LOG_FOLDER_VISIBLE`, `FAILED_TASKS`, `INVALID_TASK_FILE`, `LOCKED` (the strings above).

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_errors.py`

```python
from alr.errors import (
    FAILED_TASKS,
    CommitFailedError,
    FailedTasksError,
    RefusedError,
    RunError,
    UsageError,
)
from alr.tasks import TaskList, Shared


def test_exit_codes() -> None:
    assert RunError("x").exit_code == 1
    assert UsageError("x").exit_code == 2
    assert RefusedError("dirty-tree", "x").exit_code == 3
    assert CommitFailedError("x").exit_code == 4
    assert RunError("x").exit_code == 1


def test_a_refusal_names_its_reason_and_keeps_the_message() -> None:
    error = RefusedError("dirty-tree", "--commit needs a clean work tree")

    assert str(error) == "refused: dirty-tree: --commit needs a clean work tree"
    assert error.reason == "dirty-tree"
    assert error.message == "--commit needs a clean work tree"


def test_failed_tasks_is_a_refusal() -> None:
    error = FailedTasksError(TaskList(shared=Shared(prompt=""), tasks=[]))

    assert isinstance(error, RefusedError)
    assert error.reason == FAILED_TASKS
    assert str(error).startswith("refused: failed-tasks: fix the failed tasks")


def test_a_commit_failure_is_a_run_error() -> None:
    assert issubclass(CommitFailedError, RunError)
```

Add to `tests/unit/test_cli.py` (use that file's existing way of calling `cli.main`; look at its first tests for the pattern):

```python
def test_a_bad_flag_value_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--plain", "--gate-timeout", "soon", "-d", str(tmp_dir)]) == 2
```

(Create `tmp_dir` the way the neighbouring tests do, a folder with a valid `tasks.yaml`. `--gate-timeout soon` raises `UsageError`.) Add a second test that `cli.main(["--plain"])` (no `--dir`) returns 2 and prints `error: required flag --dir not set`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_errors.py tests/unit/test_cli.py -v`
Expected: FAIL (`RefusedError` and `exit_code` do not exist; the CLI returns 1).

- [ ] **Step 3: Implement** in `src/alr/errors.py`

```python
FAILED_TASKS_MESSAGE = (
    "fix the failed tasks and set their state to pending before running"
)

DIRTY_TREE = "dirty-tree"
TASK_FILE_VISIBLE = "task-file-visible"
LOG_FOLDER_VISIBLE = "log-folder-visible"
FAILED_TASKS = "failed-tasks"
INVALID_TASK_FILE = "invalid-task-file"
INVALID_SANDBOX_SETTINGS = "invalid-sandbox-settings"
LOG_UNWRITABLE = "log-unwritable"
GIT_ERROR = "git-error"
LOCKED = "locked"


class AlrError(Exception):
    """Base for every error alr reports as `error: <message>`; exit_code is the code it exits with."""

    exit_code = 1


class UsageError(AlrError):
    """A flag is missing, conflicts with another, or has a bad value."""

    exit_code = 2


class RefusedError(AlrError):
    """alr declined to start; nothing ran and nothing was written."""

    exit_code = 3

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"refused: {reason}: {message}")
        self.reason = reason
        self.message = message


class FailedTasksError(RefusedError):
    """The task file already holds a failed task."""

    def __init__(self, task_list: TaskList) -> None:
        super().__init__(FAILED_TASKS, FAILED_TASKS_MESSAGE)
        self.task_list = task_list
```

Keep the other classes, and add after `RunError`:

```python
class CommitFailedError(RunError):
    """A task's work passed its gates but git would not commit it."""

    exit_code = 4
```

In `cli.py`, replace every `return 1` that reports an `AlrError` with `return exc.exit_code` (in `_main`, `_install_skill`, `_run`), change `_report_missing`'s caller to `return 2`, and leave `_INTERRUPTED` (`KeyboardInterrupt` and a stop) for Task 2.

- [ ] **Step 4: Run to verify they pass**, then `make test`.

Expected: the new tests pass. Existing tests that assert the `FailedTasksError` message or `error: ` text for a failed-task file now fail on the `refused: failed-tasks: ` prefix; update their expected text (only the prefix changes).

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Give every error class its exit code and add the refusal and commit-failed errors"
```

---

### Task 2: A stop exits 130 or 143

**Files:**
- Create: `src/alr/engine/stop.py`
- Modify: `src/alr/engine/loop.py` (`RunOptions.stop`), `src/alr/cli.py` (`_run`, `_with_signals`, `_stop_once`, `main`), `src/alr/plain/start.py` (pass `stop`)
- Test: `tests/unit/test_cli.py`, new `tests/unit/test_stop.py`

**Interfaces:**
- Produces: `Stop` dataclass with `signum: int = signal.SIGINT` and `exit_code() -> int` (`128 + signum`).
- Produces: `RunOptions.stop: Stop = field(default_factory=Stop)`.
- Produces: `start(..., stop: Stop | None = None)` passes it into `RunOptions`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_stop.py`:

```python
import signal

from alr.engine.stop import Stop


def test_a_stop_defaults_to_sigint() -> None:
    assert Stop().exit_code() == 130


def test_a_sigterm_stop_exits_143() -> None:
    assert Stop(signal.SIGTERM).exit_code() == 143
```

In `tests/unit/test_cli.py`, a test that runs `_run` with a coroutine that sends the process SIGTERM to itself and then awaits forever, and asserts the return is 143 (SIGINT: 130). Model it on how the file already tests `_run`'s stop path; the coroutine is:

```python
async def stopped_by(sig: signal.Signals) -> None:
    os.kill(os.getpid(), sig)
    await asyncio.sleep(60)
```

and the assertions are `cli._run(stopped_by(signal.SIGTERM), stop) == 143` with `stop = Stop()` passed in. Also assert `cli.main` returns 130 when `_main` raises `KeyboardInterrupt` (monkeypatch `cli._main`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_stop.py tests/unit/test_cli.py -v`
Expected: FAIL (no `alr.engine.stop`).

- [ ] **Step 3: Implement**

`src/alr/engine/stop.py`:

```python
"""Which signal stopped the run, so the exit code and the run log can say so."""

import signal
from dataclasses import dataclass


@dataclass
class Stop:
    """The signal that asked the run to stop; SIGINT until one arrives."""

    signum: int = signal.SIGINT

    def exit_code(self) -> int:
        """The shell's code for a process ended by this signal."""
        return 128 + self.signum
```

`cli.py`: `_run(coro, stop)` and `_with_signals(coro, stop)`. `_stop_once(task, stop, sig)` returns the handler for one signal; register with `loop.add_signal_handler(sig, _stop_once(task, stop, sig))`, but keep one shared `stopped` flag so only the first signal cancels. The handler sets `stop.signum = sig` only when it is the first. In `_run`, `except asyncio.CancelledError` and `except RunCancelledError` both print and `return stop.exit_code()`; the other `AlrError` returns `exc.exit_code`. In `main`, `except KeyboardInterrupt` returns 130. `_main` builds one `Stop()` and passes it to `_run` and `start(..., stop=stop)`. In `start.py`, add `stop: Stop | None = None` and build `RunOptions(..., stop=stop or Stop())`.

- [ ] **Step 4: Run to verify they pass**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Exit 130 on SIGINT and 143 on SIGTERM, from one record of the signal"
```

---

### Task 3: The run log says how the run ended and which commit mode ran

**Files:**
- Modify: `src/alr/tasks.py` (`RunResult`), `src/alr/engine/frontend.py` (`run_done`), `src/alr/engine/loop.py` (`run`, new `_ending`), `src/alr/record/events.py` (`RunDone`), `src/alr/record/runlog.py` (`Info.commit`, `_run_done`, drop `_failed`), `src/alr/plain/frontend.py`, `src/alr/stream/frontend.py`, `src/alr/plain/start.py` (`_info`), `src/alr/tui/prepare.py` (`_open_log`)
- Test: `tests/unit/test_runlog.py`, `tests/unit/test_loop.py`, `tests/unit/test_plain_record.py`, `tests/unit/test_tui_app.py`, `tests/unit/test_tui_progress.py`, `tests/unit/test_tui_prepare.py`

**Interfaces:**
- Produces: `RunResult(StrEnum)` in `alr/tasks.py`: `COMPLETED="completed"`, `FAILED="failed"`, `COMMIT_FAILED="commit_failed"`, `STOPPED="stopped"`.
- Produces: `FrontEnd.run_done(error: str | None, seconds: float, result: RunResult, exit_code: int) -> None`.
- Produces: `RunDone(error, seconds, result: RunResult = RunResult.COMPLETED, exit_code: int = 0)`.
- Produces: `Info.commit: CommitMode`-valued string field `commit: str = "none"`.
- Produces: `run_finished` line fields `result`, `error` (when any), `exit`, `duration`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_runlog.py`, next to the existing `run_finished` tests, assert the ledger for each result:

```python
@pytest.mark.parametrize(
    ("result", "exit_code"),
    [
        (RunResult.COMPLETED, 0),
        (RunResult.FAILED, 1),
        (RunResult.COMMIT_FAILED, 4),
        (RunResult.STOPPED, 143),
    ],
)
def test_run_finished_records_the_result_and_the_exit(
    result: RunResult, exit_code: int, ...  # the file's existing fixtures
) -> None:
    log.record(RunDone(None if result is RunResult.COMPLETED else "why", 1.0, result, exit_code))

    line = last_ledger_line(...)  # the file's existing reader
    assert line["result"] == result.value
    assert line["exit"] == exit_code
```

Add a test that `Info(commit="task")` writes `"commit":"task"` in `run_started`, and that the field is a string for all three modes.

In `tests/unit/test_loop.py` add engine-level tests using a recording front end (copy the recorder class the file already defines for `run_done`) that a clean run calls `run_done(None, _, COMPLETED, 0)`, a failed gate calls it with `FAILED, 1`, a cancel with `STOPPED, Stop().exit_code()` (and `STOPPED, 143` when `RunOptions(stop=Stop(signal.SIGTERM))`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_runlog.py tests/unit/test_loop.py -v`
Expected: FAIL (`RunResult` missing, `RunDone` takes two arguments).

- [ ] **Step 3: Implement**

`tasks.py`:

```python
class RunResult(StrEnum):
    """How a run ended, as the run log and the exit code tell it."""

    COMPLETED = "completed"
    FAILED = "failed"
    COMMIT_FAILED = "commit_failed"
    STOPPED = "stopped"
```

`loop.py`, replace the body of `run` and add `_ending`:

```python
async def run(task_list: TaskList, options: RunOptions, front_end: FrontEnd) -> None:
    """Run every pending task in file order, saving the file after each one."""
    started = time.monotonic()
    error: str | None = None
    result, exit_code = RunResult.COMPLETED, 0
    try:
        await _run_loop(task_list, options, front_end)
    except BaseException as exc:
        # Whatever ends the run is recorded, so a crash never reads as a clean finish.
        error = _error_text(exc)
        result, exit_code = _ending(exc, options.stop)
        raise
    finally:
        front_end.run_done(error, time.monotonic() - started, result, exit_code)


def _ending(exc: BaseException, stop: Stop) -> tuple[RunResult, int]:
    """Name how the run ended and the code alr will exit with."""
    if isinstance(exc, RunCancelledError | asyncio.CancelledError | KeyboardInterrupt):
        return RunResult.STOPPED, stop.exit_code()

    if isinstance(exc, CommitFailedError):
        return RunResult.COMMIT_FAILED, exc.exit_code

    if isinstance(exc, AlrError):
        return RunResult.FAILED, exc.exit_code

    return RunResult.FAILED, 1
```

(`RunOptions` gains `stop: Stop = field(default_factory=Stop)`; import `field`.) Update the `FrontEnd` protocol's `run_done` signature and docstring, `PlainFrontEnd.run_done` and `StreamFrontEnd.run_done` to forward all four values into `RunDone`. In `events.py` add the two defaulted fields to `RunDone`. In `runlog.py` delete `self._failed` and `_run_result`, and write:

```python
    def _run_done(self, event: RunDone) -> None:
        fields: dict[str, Any] = {"result": event.result.value}
        if event.error:
            fields["error"] = event.error

        fields["exit"] = event.exit_code
        fields["duration"] = format_duration(seconds=event.seconds)
        self._write_ledger("run_finished", fields)
```

(Match the file's existing `format_duration(event.seconds)` call.) `Info.commit` becomes `commit: str = "none"` and `_start` keeps `fields["commit"] = info.commit`. `plain/start.py::_info` takes the `CommitMode` and passes `commit.value`; `tui/prepare.py::_open_log` passes `settings.commit_mode.value` (the requested mode, even when no repository was found). Update `tests/unit/test_tui_app.py` and `test_tui_progress.py` fakes for the new `run_done` signature.

- [ ] **Step 4: Run to verify they pass**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Record how a run ended and which commit mode ran in the run log"
```

---

### Task 4: Refusals exit 3, before anything is written

**Files:**
- Modify: `src/alr/engine/commit.py` (`check_clean`, `_check_task_file`, `check_log_dir`), `src/alr/plain/report.py` (`load_tasks_report`), `src/alr/plain/start.py` (order), `src/alr/engine/loop.py` (`reset_failed` stays; callers reorder), `src/alr/tui/prepare.py`, `src/alr/tui/app.py` (`_error_text`)
- Test: `tests/unit/test_commit.py`, `tests/unit/test_plain_start.py`, `tests/unit/test_plain_dryrun.py`, `tests/unit/test_tui_prepare.py`

**Interfaces:**
- Consumes: `RefusedError`, reason constants (Task 1).
- Produces: `check_clean`, `_check_task_file`, `check_log_dir` raise `RefusedError` with `DIRTY_TREE`, `TASK_FILE_VISIBLE`, `LOG_FOLDER_VISIBLE`. The git probe failures (`git rev-parse`, `git status`, `git check-ignore` failing) also raise `RefusedError`, reason `GIT_ERROR`; `RepoError` is then unused and is deleted.
- Produces: `load_tasks_report` raises `RefusedError(INVALID_TASK_FILE, <the existing text>)` for an unreadable or invalid file, and `FailedTasksError` as before.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_commit.py`, change the three existing refusal tests (dirty tree, task file, log dir) to `pytest.raises(RefusedError)` and assert `caught.value.reason`. For example:

```python
async def test_open_repo_dirty_tree_is_refused(git_repo: Path) -> None:
    (git_repo / "stray.txt").write_text("x")

    with pytest.raises(RefusedError) as caught:
        await open_repo(str(_task_file(git_repo)))

    assert caught.value.reason == "dirty-tree"
    assert str(caught.value).startswith("refused: dirty-tree: --commit needs a clean work tree")
```

In `tests/unit/test_plain_start.py` add (use the file's helpers to build a git repo and a run folder):

```python
async def test_reset_failed_waits_for_the_other_refusals(...) -> None:
    # a failed task in the file, a dirty tree
    with pytest.raises(RefusedError) as caught:
        await start(tasks_file, "", [], CommitMode.RUN, reset=True)

    assert caught.value.reason == "dirty-tree"
    assert saved_tasks(path).tasks[0].state == "failed", "a refused run changes nothing"
    assert not (path.parent / "logs").exists(), "a refusal writes no run log"


async def test_an_invalid_task_file_is_refused(...) -> None:
    ...  # file with "tasks: not-a-list"
    assert caught.value.reason == "invalid-task-file"
    assert str(caught.value).count("failed to parse tasks yaml") == 1
```

In `tests/unit/test_plain_dryrun.py`: an invalid file and a dirty tree with `commit=True` each raise `RefusedError` (reasons `invalid-task-file`, `dirty-tree`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_commit.py tests/unit/test_plain_start.py tests/unit/test_plain_dryrun.py -v`
Expected: FAIL (`RepoError` raised; the order check fails).

Also add, in `tests/unit/test_commit.py`, `test_a_broken_repository_is_refused_with_git_error` (change the existing `test_open_repo_broken_repository_is_an_error` and `..._status_failure_carries_gits_message` to expect `RefusedError` with reason `git-error`), in `tests/unit/test_cli.py` a bad `--sandbox-settings` file exiting 3 with first stderr line starting `error: refused: invalid-sandbox-settings: `, and in `tests/unit/test_plain_start.py` an unwritable `logs/` folder (make `logs` a regular file) raising `RefusedError` with reason `log-unwritable`.

- [ ] **Step 3: Implement**

`commit.py`: swap every `raise RepoError(...)` for a `RefusedError`; the three that refuse (dirty tree, task file, log dir) get their reasons and the git probe failures get `GIT_ERROR`, each as `raise RefusedError(REASON, "<same text without the leading --commit needs ... change>")`. Keep each message text exactly as written today (it begins with `--commit needs ...`), passing it as `message`.

`report.py`, wrap the load:

```python
def load_tasks_report(out: TextIO, tasks_file: str) -> TaskList:
    """Load the task file; when it holds a failed task, write the notice and the table first."""
    try:
        return load_tasks(tasks_file)
    except FailedTasksError as exc:
        out.write("Some tasks failed previous runs:\n")
        print_tasks(out, exc.task_list)
        raise
    except TaskFileError as exc:
        raise RefusedError(
            INVALID_TASK_FILE, f"failed to start loop runner: {exc}"
        ) from exc
```

`start.py`: the order becomes read, repo checks, reset, failed-tasks check. Replace the top of `start` with:

```python
    task_list = _read_for_start(tasks_file)
    commits = commit is not CommitMode.NONE
    path = run_dir(Path(tasks_file).parent / "logs", datetime.now().astimezone())
    repo = await repo_for(sys.stdout, tasks_file, commits, str(path))
    if reset:
        reset_failed(tasks_file)

    task_list = load_tasks_report(sys.stdout, tasks_file)
```

where `_read_for_start` calls `read_tasks` and turns a `TaskFileError` into the same `RefusedError(INVALID_TASK_FILE, ...)`. Delete the old `try/except TaskFileError` block. A failed task no longer stops the run before the repo checks; `--reset-failed` is applied only after they pass, and without `--reset-failed` a failed task still refuses with `failed-tasks`.

`engine/sandbox.py`: `SandboxError` becomes a `RefusedError` with `INVALID_SANDBOX_SETTINGS` (delete the class, raise `RefusedError` where it was raised). `record/runlog.py`: `RunLog.open` wraps its `RunLogError` from `_create_run_dir` and `_start` in `RefusedError(LOG_UNWRITABLE, ...)` (the message stays); a `RunLogError` raised by `record` later in a run stays a run failure, exit 1. `dryrun.py` needs no change beyond what `load_tasks_report` now raises; check `cli._main` returns `exc.exit_code` for it (Task 1 did). `tui/prepare.py`: catch `RefusedError` instead of `RepoError` in `_check_log_folder`. `tui/app.py::_error_text`: for a `RefusedError` return `exc.message`, so the dialogs show the text they showed before.

- [ ] **Step 4: Run to verify they pass**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Refuse to start with exit 3 and a reason, before the run log or --reset-failed touches anything"
```

---

### Task 5: A failed commit exits 4 and says why (task mode)

**Files:**
- Modify: `src/alr/engine/frontend.py` (`FirstLine`), `src/alr/plain/frontend.py` and `src/alr/stream/frontend.py` (`run_git`, `_run_command`), `src/alr/engine/commit.py` (`commit`), `src/alr/engine/loop.py` (`_run_task`, `_run_and_save`)
- Test: `tests/unit/test_commit.py`, `tests/unit/test_stream_frontend.py`, `tests/unit/test_loop.py`

**Interfaces:**
- Produces: `FirstLine` in `engine/frontend.py`: `take(line: str) -> None` keeps the first non-empty line, trimmed to 200 characters; `reason(error: str | None) -> str | None` is `None` when `error` is `None`, else the kept line, else `error`.
- Produces: `commit(repo, task, front_end) -> str | None`, git's reason or `None` (same shape as `commit_all`).
- Produces: a task-mode commit failure saves the task `failed` with `commit failed: <reason>` and raises `CommitFailedError`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_commit.py`, update `test_run_commit_failure_fails_task_and_stops_run`: the hook prints `hook-says-no` on stderr, so now

```python
    want_error = "commit failed: hook-says-no"

    with pytest.raises(CommitFailedError) as caught:
        ...
    assert caught.value.exit_code == 4
    assert str(caught.value) == f"task 1: First failed: {want_error}"
```

Add a test that a 300-character first line is trimmed to 200, and one that a failure with no output (a hook that does `exit 1` silently) falls back to `exit status 1`:

```python
@pytest.mark.usefixtures("fake_claude")
async def test_run_commit_failure_reason_is_trimmed_to_200(git_repo: Path) -> None:
    _install_hook(git_repo, "printf 'x%.0s' $(seq 300) >&2\nexit 1")
    tasks_file = _task_file(git_repo)

    with pytest.raises(CommitFailedError):
        await run_loop(
            _two_tasks(_work_gate("one.txt")), tasks_file, repo=await _open(tasks_file)
        )

    error = _saved_states(tasks_file)[0][1]
    assert error == "commit failed: " + "x" * 200
```

Unit-test `FirstLine` in `tests/unit/test_stream_frontend.py`: blank lines skipped, first real line kept, later lines ignored, `reason(None)` is `None`, `reason("exit status 1")` with no lines is `"exit status 1"`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_commit.py tests/unit/test_stream_frontend.py -v`
Expected: FAIL (`FirstLine` missing; error text is `exit status 1`).

- [ ] **Step 3: Implement**

`engine/frontend.py`:

```python
_REASON_LIMIT = 200


class FirstLine:
    """The first non-empty line a command printed, kept so a failure can say why."""

    def __init__(self) -> None:
        self._text = ""

    def take(self, line: str) -> None:
        if self._text:
            return

        self._text = line.strip()[:_REASON_LIMIT]

    def reason(self, error: str | None) -> str | None:
        """Return the failure's reason: None for success, else the first line git printed, else the exit text."""
        if error is None:
            return None

        return self._text or error
```

`plain/frontend.py` and `stream/frontend.py`: `_run_command` takes `tap: Callable[[str], None] | None = None`; the line callback it builds calls `tap(text)` for every line. `run_git`:

```python
    async def run_git(self, task: Task, argv: Sequence[str], cwd: str) -> str | None:
        first = FirstLine()
        error = await self._run_command(
            task, argv, cwd=cwd, gentle=True, tap=first.take
        )
        return first.reason(error)
```

`commit.py::commit` becomes:

```python
async def commit(repo: Repo, task: Task, front_end: FrontEnd) -> str | None:
    """Commit what the task left in the work tree, named after the task; return why git refused, or None."""
    return await _commit_changes(repo, task, front_end, [task.name])
```

(Drop the now-unused `COMPLETED`/`FAILED` imports if nothing else uses them.)

`loop.py`: `_run_task` returns a `_Outcome`:

```python
class _Outcome(NamedTuple):
    state: str
    error: str
    commit_failed: bool = False
```

All current `return state, error` become `_Outcome(state, error)`; the commit step becomes

```python
    try:
        reason = await commit(options.repo, replace(task), front_end)
    except asyncio.CancelledError:
        raise RunCancelledError(...) from None

    if reason is None:
        return _Outcome(COMPLETED, "")

    return _Outcome(FAILED, f"commit failed: {reason}", commit_failed=True)
```

and `_run_and_save` raises `CommitFailedError(failure)` instead of `RunError(failure)` when `outcome.commit_failed`. `_save`'s double-raise keeps `RunError`.

- [ ] **Step 4: Run to verify they pass**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Exit 4 when a commit fails, with git's first output line as the reason"
```

---

### Task 6: `run` mode is all-or-nothing

**Files:**
- Modify: `src/alr/tasks.py` (`PASSED`), `src/alr/engine/loop.py` (`_run_loop`, `_run_and_save`, `_commit_after_all`), `src/alr/tui/panes.py`
- Test: `tests/unit/test_commit.py` (the `run` tests near line 735), `tests/unit/test_tui_panes.py`

**Interfaces:**
- Consumes: `CommitFailedError`, `_Outcome` (Task 5), `RunOptions.stop` (Task 2).
- Produces: `PASSED = "passed"` in `alr/tasks.py`, a log-only word never saved to `tasks.yaml`.
- Produces: in `run` mode with a repository, `task_finished` says `passed` for a task whose gates passed, and `tasks.yaml` is written once, at the end, by `_finish_deferred`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_commit.py` (these reuse that file's `_two_tasks`, `_work_gate`, `_open`, `_saved_states`, `run_loop`)

```python
@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_saves_nothing_until_the_commit_lands(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)
    seen: list[list[tuple[str, str]]] = []
    front_end = _SnapshotFrontEnd(tasks_file, seen)  # a PlainFrontEnd that appends _saved_states(tasks_file) on every task_finished

    await run_loop(
        _two_tasks(_work_gate("one.txt")),
        tasks_file,
        repo=await _open(tasks_file),
        commit_mode=CommitMode.RUN,
        front_end=front_end,
    )

    assert seen == [[(PENDING, ""), (PENDING, "")]] * 2, "the file stays untouched mid-run"
    assert _saved_states(tasks_file) == [(COMPLETED, ""), (COMPLETED, "")]


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_failed_task_leaves_the_others_pending(git_repo: Path) -> None:
    # the first task passes, the second's gate fails
    ...
    assert _saved_states(tasks_file) == [(PENDING, ""), (FAILED, "<the gate's text>")]


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_failed_commit_marks_only_the_last_task_failed(git_repo: Path) -> None:
    _install_hook(git_repo, "echo no >&2\nexit 1")
    ...
    with pytest.raises(CommitFailedError):
        ...
    assert _saved_states(tasks_file) == [(PENDING, ""), (FAILED, "commit failed: no")]


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_resumed_run_keeps_earlier_completed_tasks(git_repo: Path) -> None:
    # task 1 is already COMPLETED in the file; task 2's gate fails
    ...
    assert _saved_states(tasks_file) == [(COMPLETED, ""), (FAILED, "<text>")]


async def test_run_mode_stop_during_the_final_commit_leaves_all_pending(git_repo: Path) -> None:
    # adapt test_run_after_all_cancel_during_the_final_commit_stops_the_run (line 843)
    ...
    assert _saved_states(tasks_file) == [(PENDING, ""), (PENDING, "")]
```

`_SnapshotFrontEnd` is a small subclass of `PlainFrontEnd` defined at the top of the test; fill in each `...` by copying the arrangement of the existing `run`-mode test next to it (`test_run_after_all_later_failed_task_leaves_earlier_work_uncommitted`, `..._commit_failure_stops_the_run`, `..._resumed_run_commits_only_the_tasks_that_ran`), then change only what is asserted. Rewrite those existing tests' `_saved_states` assertions to the new states above.

In `tests/unit/test_tui_panes.py`, a test that a `TaskFinished` with state `passed` shows `✔`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_commit.py tests/unit/test_tui_panes.py -v`
Expected: FAIL (the file is saved after every task; no `PASSED`).

- [ ] **Step 3: Implement**

`tasks.py`: `PASSED = "passed"` beside the states, with a comment that it is only ever logged and never saved. `panes.py`: `_SYMBOLS = {COMPLETED: "✔", PASSED: "✔", FAILED: "✖"}` (the view looks the same as before; a failed final commit is reported by the summary dialog).

`loop.py`: add the helper and branch `_run_loop` on it.

```python
def _defers_saving(options: RunOptions) -> bool:
    """Report whether the run saves tasks.yaml once, at the end.

    Only `run` mode with a repository: the one commit decides whether any task
    is complete, so no task may be saved as completed before it lands.
    """
    return options.repo is not None and options.commit_mode is CommitMode.RUN
```

In `_run_and_save`, take `deferred = _defers_saving(options)`. When `deferred` and the outcome state is `COMPLETED`, the task keeps its in-memory state `COMPLETED` for the log as `PASSED` (`front_end.task_finished(replace(task, state=PASSED), ...)`), and `_save` is skipped for passed tasks. When a task fails in deferred mode, call `_finish_deferred(task_list, ran, options, failed=task)` before raising. Replace `_run_loop` and `_commit_after_all`:

```python
async def _run_loop(task_list, options, front_end) -> None:
    ran: list[Task] = []
    for task in task_list.tasks:
        if task.state == COMPLETED:
            front_end.task_skipped(task)
            continue

        await _run_and_save(task, task_list, options, front_end, ran)
        ran.append(replace(task))

    await _commit_after_all(task_list, ran, options, front_end)
```

`_commit_after_all` returns early unless `_defers_saving(options)`; with nothing in `ran` it still returns. On success it calls `_complete_ran(task_list, ran, options)` which sets the state of each task in `ran` (matched by id) to `COMPLETED` and saves once. On a commit error it raises `CommitFailedError(f"commit failed: {error}")` after `_finish_deferred(..., failed=ran[-1], error=f"commit failed: {error}")`. On a cancel it calls `_finish_deferred` with no failed task (everything that ran goes back to `PENDING`, nothing else changes) and then raises `RunCancelledError`. `_finish_deferred` puts every task in `ran` back to `PENDING`, marks `failed` (when given) `FAILED` with its error, and saves once; tasks that were `COMPLETED` before the run are never touched. The save error handling reuses `_save`.

Because the run-mode in-memory state must never reach the file mid-run, `_run_and_save` must not call `_save` for a passed task in deferred mode, and the final `_save` is the only write.

- [ ] **Step 4: Run to verify they pass**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Make run mode all-or-nothing: save tasks.yaml once, after the single commit"
```

---

### Task 7: A task name cannot contain a line break

**Files:**
- Modify: `src/alr/tasks.py` (`_parse_task`)
- Test: `tests/unit/test_tasks_parse.py`
- Modify: `src/alr/skills/gralph-docs-writer/SKILL.md` (document the rule)

**Interfaces:**
- Consumes: `TaskFileError`, `_where` (existing). Produces: `_parse_task` rejects such a name with `tasks[i] (id N): name: must be a single line`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("brk", ["\n", "\r", "\r\n", "\x85", " ", " ", "\x0b", "\x0c"])
def test_a_task_name_with_a_line_break_is_rejected(brk: str) -> None:
    text = f'tasks:\n  - id: 7\n    name: "one{brk}two"\n    prompt: p\n'

    with pytest.raises(TaskFileError) as caught:
        parse_tasks(text)

    assert "id 7" in str(caught.value)
    assert "name: must be a single line" in str(caught.value)
```

(Check `_where`'s exact output with `uv run python -c` and write the assertion to match; the id must appear.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_tasks_parse.py -k line_break -v`
Expected: FAIL (the name is accepted).

- [ ] **Step 3: Implement** in `_parse_task`, right after `name = _text(...)`:

```python
    if len(name.splitlines()) != 1 or name != name.splitlines()[0]:
        raise TaskFileError(f"{where}: name: must be a single line")
```

(`str.splitlines` treats every character the spec names as a break; `"one\n"` splits to one line, so the second clause catches a trailing break.) Add a line to `SKILL.md` where task names are described: a task name is a single line.

- [ ] **Step 4: Run to verify it passes**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Reject a task name that contains a line break"
```

---

### Task 8: Drop the view's redundant mount-time work-tree lookup

**Files:**
- Modify: `src/alr/tui/app.py` (the `_look_for_work_tree` call made on mount)
- Test: `tests/unit/test_tui_app.py`

- [ ] **Step 1: Find the call.** Run `grep -n "_look_for_work_tree" src/alr/tui/app.py`. One call is in the mount path, one in the commit dropdown's changed handler (around line 446).

- [ ] **Step 2: Write the characterization test** in `tests/unit/test_tui_app.py`: start the app with `commit_mode=CommitMode.TASK` from a folder that is not a git repository, and assert the `#commit-note` is displayed after the app settles (`await pilot.pause()`).

- [ ] **Step 3: Run it** to confirm it passes today. Then remove the mount-path call and run it again.

Expected: PASS without the call. If it fails, the dropdown's own event does not fire on mount: stop, keep the call, and tell the user the ADR's "redundant" claim is wrong for a run started with `--commit task`. Do not force the removal.

- [ ] **Step 4: `make test`**, then commit

```bash
git add src tests
git commit -m "Drop the view's redundant mount-time work-tree lookup"
```

---

### Task 9: One e2e test per exit code

**Files:**
- Create: `tests/e2e/test_outcomes.py`
- Test helpers: `tests/e2e/e2e_helpers.py` (`run_alr`, `run_args`, `write_tasks_yaml`, `start_alr`, `shared_block`), `tests/e2e/conftest.py` (`fake_claude`, `git_repo`)

The suite is black-box: it never imports alr, and it shares nothing with the unit tests.

- [ ] **Step 1: Write the tests**, modelled on `test_commit_run.py` and `test_signal.py`

| Test | Arrangement | Assertion |
|---|---|---|
| `test_completed_exits_0` | `ONE_TASK`, fake claude completes | code 0; `run.jsonl` last line `result: completed`, `exit: 0` |
| `test_failed_task_exits_1` | fake claude reports failed | code 1; `result: failed`, `exit: 1` |
| `test_bad_flag_exits_2` | `--gate-timeout soon` | code 2 |
| `test_refusal_exits_3_and_writes_no_log` | git repo, dirty tree, `--commit run` | code 3; first stderr line starts `error: refused: dirty-tree: `; no `logs/` folder in the run folder |
| `test_commit_failure_exits_4` | `pre-commit` hook that prints `no` and exits 1, `--commit task` | code 4; stderr has `commit failed: no`; `result: commit_failed`, `exit: 4`; the task is `failed` in `tasks.yaml` |
| `test_sigint_exits_130`, `test_sigterm_exits_143` | blocked fake claude as in `test_signal.py` | codes 130 and 143; `result: stopped` with `exit` 130 or 143 |

Add one more: `test_run_mode_failed_run_leaves_nothing_completed` (two tasks, the second fails, `--commit run`): `task_states` is `["pending", "failed"]`.

- [ ] **Step 2: Run them.** `make build` builds the wheel and runs the e2e suite in a container.

Expected: all PASS. A failure here is a bug in an earlier task, not in the test.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e
git commit -m "Add an e2e test for each exit code"
```

---

### Task 10: Documentation

Delegate to the `technical writer` agent. This is documentation for behavior that now exists.

**Files:**
- Modify: `README.md` (exit codes table; the commit-leftovers warning for `run` mode is replaced: a failed or stopped `run` leaves nothing completed, so the leftover work can be discarded and the run repeated), `CHANGELOG.md` (`[Unreleased]`: Changed, Added), `src/alr/skills/gralph-docs-writer/SKILL.md` (task names are one line), `docs/design/01_requirements.md`, `docs/design/03_system_architecture.md` (use the `/design-docs-writer` skill; bump each frontmatter)

- [ ] **Step 1:** Brief the writer with ADR-025 and this plan's task list. List the breaking changes in the CHANGELOG: a stop and a refusal no longer exit 1; `commit` in `run_started` is a string; `run` mode saves nothing until the commit lands; `task` mode's `completed` also means committed; a task name with a line break is rejected.
- [ ] **Step 2:** Do not touch the wiki (the user decides, per the ADR's open item) and do not change ADR-025's status; stage 2 completes it.
- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md src/alr/skills docs/design
git commit -m "Document the exit codes, the run log fields, and run mode's new lifecycle"
```

---

### Task 11: Verification before the PR

- [ ] **Step 1:** `make test` and `make build` both pass.
- [ ] **Step 2:** Mutation check: for each new test, break the code it covers and watch it fail, then restore (ADR-025 Testing).
- [ ] **Step 3:** Adversarial review, with fresh reviewers who assume the change is wrong, reproduce every finding, then a skeptic stage and a completeness critic. Prune the reviewers' worktrees and helper branches when they finish.
- [ ] **Step 4:** Hand the branch to the user. The user opens the PR and pushes.

## Self-review notes

- **Spec coverage:** the contract table (Tasks 1, 2, 4, 5, 9), refusals and their order (4), log fields (3), `task` lifecycle (5), `run` lifecycle (6), name validation (7), the view's lookup (8), docs (10), testing and review (9, 11). Stage 2 items (lock, own session, `PR_SET_PDEATHSIG`, pre-add ignore re-check, the `locked` refusal) are in the stage 2 plan.
- **Known gap, to confirm with the user:** the full-screen view's own exit code stays 0 or 1.
