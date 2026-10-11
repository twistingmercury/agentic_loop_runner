# alr Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `--commit` and `shared.gates` with two lists of actions in the task file, `after_task` and `after_run`, so alr no longer touches git.

**Architecture:** An *action* is a command alr runs through `sh -c`; if it fails, the run stops. `after_task` actions run after each task's session and decide whether the task completes; `after_run` actions run once at the end. alr sets four `ALR_*` environment variables on each action's process. All git code (`engine/commit.py`, the lock, the repository checks, the commit events) is deleted, and exit 4 changes from "commit failed" to "an `after_run` action failed".

**Tech Stack:** Python 3.13, PyYAML nodes, asyncio, Textual, pytest, ruff, mypy. Build and e2e: `build/build.sh` (Docker). Unit tests: `make test`.

**Spec:** `docs/design/02_architectural_decisions.md`, ADR-026. Read it first; it decides the design and this plan only orders the work.

## Global Constraints

- Task file keys: `shared.after_task` and `shared.after_run`, each an optional list of `{action: <str, required>, timeout: <duration str, optional>}`; no other keys in an entry.
- `after_task` absent = undecided (the view asks); `after_task: []` = decided, none. `after_run` absent and `[]` both mean none.
- A task completes only after its session reported success and every `after_task` action exited 0; the first failing or timed-out action saves the task `failed` with that action's text and ends the run with exit 1.
- `after_run` runs once, after the loop ends with no failed task, **even when every task was already completed** (so a rerun after an `after_run` failure retries it). Its failure changes no task's state and exits 4.
- Exit codes: 0, 1, 2, 3, 130, 143 unchanged; 4 = an `after_run` action failed. Contiguous; alr's own codes stay below 64.
- Run log result for an `after_run` failure: `run_failed`. `commit_failed` and the `commit` log field go away.
- Environment on every action: `ALR_TASK_FILE` and `ALR_LOG_DIR` (absolute paths; `ALR_LOG_DIR` is `<run folder>/logs` whether or not logging is on). `after_task` actions also get `ALR_TASK_ID` and `ALR_TASK_NAME`. The action's environment is `os.environ` plus these four; `proc.start(env=...)` replaces the environment, so it must be merged.
- Timeouts keep today's rule from `gate_limit`: `--action-timeout` overrides the action's own `timeout`, which overrides the 10 minute default.
- Hard breaks, no aliases: `--commit`, `--gate-timeout`, `shared.gates`, and `cmd` are refused with a message naming the replacement. Refusal reasons `dirty-tree`, `task-file-visible`, `log-folder-visible`, `git-error`, `locked` are deleted.
- Task ids are positive integers, so id `0` is free: `RUN_SCOPE` (below) is the stand-in task that `after_run` output is reported under.
- Code shape (from `~/.claude/rules/code-shape.md`): early returns, a blank line after every `if` block, named callbacks, no suppressions, keep docstrings. Python rules: type hints, `pathlib`, specific exceptions.
- Loops run on a feature branch off `develop`; never commit on `develop`. Subagents never run `git push` or Docker. The main session runs `./build/build.sh`.

## Review Focus

The input classes the spec implies but no obvious test covers, most likely first. Each has its test in the task that owns the code.

1. A task name full of shell syntax (`fix "auth"; $(touch pwned) \`id\``) reaches a commit action only as data through `ALR_TASK_NAME` and runs nothing (Task 2, and again in Task 6).
2. A rerun after an `after_run` failure, with every task already completed, runs `after_run` again (Task 3).
3. A run folder path with spaces, or given relative, still gives absolute `ALR_TASK_FILE` and `ALR_LOG_DIR` (Task 2).
4. SIGINT during an `after_run` action exits 130, leaves every task `completed`, and does not exit 4 (Task 3).
5. An old file or flag (`shared.gates`, `cmd`, `--commit`, `--gate-timeout`) is refused with the replacement named, and a file with both `gates` and `after_task` is refused, never half-read (Task 1).
6. Actions keep `PATH` and `HOME`: the env is merged, not replaced (Task 2).

---

## File structure

| File | Change |
|---|---|
| `src/alr/tasks.py` | `Gate` → `Action`; `Shared.gates` → `after_task` + `after_run`; `RUN_SCOPE`; migration errors; `RunResult.RUN_FAILED`; drop `PASSED` |
| `src/alr/engine/gates.py` → `engine/actions.py` | `run_gates` → `run_actions`; `action_env` |
| `src/alr/engine/loop.py` | run `after_task` per task, `after_run` at the end; delete all commit code |
| `src/alr/engine/frontend.py` | `run_action`, `action_finished`; drop `commit_started`, `run_git`, `committed` |
| `src/alr/engine/commit.py` | **deleted** |
| `src/alr/errors.py` | `CommitFailedError` → `AfterRunFailedError` (exit 4); drop five refusal reasons |
| `src/alr/record/events.py`, `record/runlog.py` | `ActionStarted/Finished`; drop `CommitStarted`, `Committed`; log fields |
| `src/alr/plain/*`, `src/alr/stream/frontend.py` | rename, env, drop git and repo code |
| `src/alr/cli.py`, `src/alr/main.py` | `--action-timeout`; refuse old flags; drop `CommitMode` |
| `src/alr/tui/*` | drop Commit box; `ActionsDialog` with two lists; pane "Actions"; settings and progress |
| `src/alr/skills/gralph-docs-writer/*`, `README.md`, `CHANGELOG.md`, `docs/design/03_system_architecture.md` | docs |
| `tests/unit/test_commit.py`, `tests/e2e/test_commit*.py` | **deleted**; `test_gates.py` → `test_actions.py` (unit and e2e) |

---

### Task 1: Gates become actions, and the file gets `after_task` and `after_run`

A pure rename plus the new keys. No behavior changes yet: `after_run` is parsed and saved but nothing runs it.

**Files:**
- Modify: `src/alr/tasks.py`, `src/alr/engine/frontend.py`, `src/alr/engine/loop.py`, `src/alr/record/events.py`, `src/alr/record/runlog.py`, `src/alr/plain/frontend.py`, `src/alr/plain/dryrun.py`, `src/alr/plain/start.py`, `src/alr/stream/frontend.py`, `src/alr/stream/parser.py`, `src/alr/cli.py`, `src/alr/tui/*.py`
- Rename: `src/alr/engine/gates.py` → `src/alr/engine/actions.py`; `tests/unit/test_gates.py` → `tests/unit/test_actions.py`; `tests/e2e/test_gates.py` → `tests/e2e/test_actions.py`
- Test: `tests/unit/test_tasks_parse.py`, `tests/unit/test_tasks_save.py`, `tests/unit/test_cli.py`, plus every unit test that names a gate

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class Action:
      command: str
      timeout: str = ""

  @dataclass
  class Shared:
      prompt: str
      after_task: list[Action] | None = None   # None = undecided
      after_run: list[Action] | None = None    # None and [] both mean none

  class TaskList:
      def after_task_list(self) -> list[Action]: ...   # [] when absent
      def after_run_list(self) -> list[Action]: ...    # [] when absent

  RUN_SCOPE = Task(id=0, name="after the run", prompt="")
  ```
  Rename table (apply everywhere, source and tests; `git grep -n -i gate` must come back empty except the migration messages when done):

  | Old | New |
  |---|---|
  | `Gate`, `gate.cmd` | `Action`, `action.command` |
  | `Shared.gates`, `TaskList.gate_list()` | `Shared.after_task`, `TaskList.after_task_list()` |
  | `run_gates`, `run_gate`, `gate_finished` | `run_actions`, `run_action`, `action_finished` |
  | `GateStarted`, `GateFinished` | `ActionStarted`, `ActionFinished` |
  | `gate_limit`, `gate_failure`, `DEFAULT_GATE_TIMEOUT` | `action_limit`, `action_failure`, `DEFAULT_ACTION_TIMEOUT` |
  | `RunOptions.gate_timeout`, `RunSettings.gate_timeout`, `Info.gate_timeout` | `action_timeout` (same for the log field `gate_timeout` → `action_timeout`) |
  | log event `gate_finished` | `action_finished` |
  | `--gate-timeout`, `GatesDialog`, `GatesPane`, menu `gates` | `--action-timeout`, `ActionsDialog`, `ActionsPane`, menu `actions` |
  | plain output `gate: <cmd>`; dry run `gate: <cmd>: <limit> (<source>)` | `action: <cmd>`; dry run `after_task: <cmd>: <limit> (<source>)` then `after_run: <cmd>: <limit> (<source>)` |
  | message "task N: ...: stopped during gate X" | "...stopped during action X" |
  | `"gate X timed out after L"`, `"gate X failed: E"` | `"action X timed out after L"`, `"action X failed: E"` |

  The `Activity` arrow text `→ gate <cmd>` becomes `→ action <cmd>`.

- [ ] **Step 1: Write the failing parse tests** in `tests/unit/test_tasks_parse.py` (follow the neighbors' helper for building a file; these use the same style as the current `shared.gates` tests, which you should rename and keep):

```python
def test_after_task_and_after_run_parse() -> None:
    task_list = parse_tasks(_file(shared_extra="""
  after_task:
    - action: ./build.sh
      timeout: 5m
  after_run:
    - action: git push --dry-run
"""))

    assert task_list.shared.after_task == [Action("./build.sh", "5m")]
    assert task_list.shared.after_run == [Action("git push --dry-run")]


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("  gates:\n    - cmd: x\n", "shared.gates: renamed to shared.after_task"),
        ("  after_task:\n    - cmd: x\n", "shared.after_task[0]: cmd: renamed to action"),
        ("  after_task:\n    - action: x\n      retry: 3\n",
         "shared.after_task[0]: retry: unknown key; an action has only action and timeout"),
        ("  after_run:\n    - action: x\n      timeout: 30\n",
         "shared.after_run[0]: timeout: must be a duration string such as 90s or 10m"),
        ("  gates: []\n  after_task: []\n", "shared.gates: renamed to shared.after_task"),
    ],
)
def test_old_and_bad_action_keys_are_refused(snippet: str, message: str) -> None:
    with pytest.raises(TaskFileError, match=re.escape(message)):
        parse_tasks(_file(shared_extra=snippet))
```

and the save round trip: `after_task: []` and `after_run: []` write back as written; absent stays absent; order in the saved file is `prompt`, `after_task`, `after_run`.

- [ ] **Step 2: Run them, see them fail**

Run: `uv run pytest tests/unit/test_tasks_parse.py tests/unit/test_tasks_save.py -x --tb=short`
Expected: FAIL (`Action` is not defined).

- [ ] **Step 3: Implement `tasks.py`.** Replace `_check_gates`/`_check_gate` with one `_check_actions(where, node)` that takes the key name for messages. `_check_shared` allows `prompt`, `after_task`, `after_run`; the key `gates` raises `shared.gates: renamed to shared.after_task; see the HOWTO`. In `_check_action`, the key `cmd` raises `{where}: cmd: renamed to action; see the HOWTO`; any other unknown key raises `{where}: {key}: unknown key; an action has only action and timeout`. The root-level and per-task `gates` errors keep their wording but say `shared.after_task`. `_document` writes `after_task` then `after_run`, each only when not `None`; `_action_entry` writes `action` then `timeout`. Add `RUN_SCOPE` and rename the `RunResult` member later (Task 3), not here.

- [ ] **Step 4: Mechanical rename** across `src/` and `tests/` using the table. Move the three files with `git mv`. Update imports.

- [ ] **Step 5: CLI.** `--action-timeout` replaces `--gate-timeout` (same checks, error text `--action-timeout: ...`). Keep a hidden `--gate-timeout` (`help=argparse.SUPPRESS`, `default=None`) that raises `UsageError("--gate-timeout: renamed to --action-timeout")` from `_main` before anything else runs. Test both in `tests/unit/test_cli.py`.

- [ ] **Step 6: Run the unit suite**

Run: `make test`
Expected: PASS (ruff clean, all unit tests green). Fix any leftover `gate` name: `git grep -n -i gate -- src tests/unit`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "Rename gates to actions and add the after_task and after_run lists"
```
(Commit body ends with the `Co-Authored-By` trailer the harness gives.)

---

### Task 2: The `ALR_*` environment on every action

**Files:**
- Modify: `src/alr/engine/actions.py`, `src/alr/engine/frontend.py`, `src/alr/engine/loop.py` (`RunOptions.log_dir`), `src/alr/plain/frontend.py`, `src/alr/stream/frontend.py`, `src/alr/plain/start.py`, `src/alr/tui/prepare.py`
- Test: `tests/unit/test_actions.py`, `tests/unit/test_stream_frontend.py`, `tests/unit/test_plain_start.py`

**Interfaces:**
- Consumes: `Action`, `run_actions` (Task 1)
- Produces:
  ```python
  # engine/actions.py
  def action_env(task: Task | None, tasks_file: str, log_dir: str) -> dict[str, str]:
      """The ALR_* variables for one action; task is None for after_run."""

  # FrontEnd protocol
  async def run_action(self, task: Task, action: Action, env: Mapping[str, str]) -> str | None: ...
  ```
  `RunOptions` gains `log_dir: str` (absolute `<run folder>/logs`). `run_actions(task, actions, override, front_end, env)` passes `env` to `front_end.run_action`.

- [ ] **Step 1: Write the failing tests** for `action_env`:

```python
def test_action_env_for_a_task(tmp_path: Path) -> None:
    task = Task(id=7, name='fix "auth"; $(x)', prompt="p")
    env = action_env(task, str(tmp_path / "tasks.yaml"), str(tmp_path / "logs"))

    assert env == {
        "ALR_TASK_ID": "7",
        "ALR_TASK_NAME": 'fix "auth"; $(x)',
        "ALR_TASK_FILE": str(tmp_path / "tasks.yaml"),
        "ALR_LOG_DIR": str(tmp_path / "logs"),
    }


def test_action_env_after_the_run_has_no_task_variables(tmp_path: Path) -> None:
    env = action_env(None, str(tmp_path / "tasks.yaml"), str(tmp_path / "logs"))

    assert sorted(env) == ["ALR_LOG_DIR", "ALR_TASK_FILE"]
```

and, in the front-end tests (a real `sh -c 'printf %s "$ALR_TASK_NAME"'` through `run_action`): the value arrives verbatim (quotes, `$()`, backticks, spaces), `PATH` and `HOME` are still set, and a path with spaces survives.

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest tests/unit/test_actions.py -x --tb=short -k env`
Expected: FAIL (`action_env` is not defined).

- [ ] **Step 3: Implement.** `action_env` returns the dict above, with `str(Path(...).resolve())` for the two paths (callers pass strings that may be relative). In both front ends' `_run_command`, take an `env: Mapping[str, str] | None` argument and pass `{**os.environ, **env}` to `proc.start`. `run_action` forwards its `env`. `RunOptions.log_dir` is filled in `plain/start.py` and `tui/prepare.py` from `Path(tasks_file).parent / "logs"` (`RunSettings.log_folder()` already does this).

- [ ] **Step 4: Run the unit suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "Give every action the ALR_TASK_FILE and ALR_LOG_DIR variables, and tasks theirs"
```

---

### Task 3: Run the `after_run` actions; exit 4 means they failed

**Files:**
- Modify: `src/alr/engine/loop.py`, `src/alr/errors.py`, `src/alr/tasks.py` (`RunResult`), `src/alr/record/runlog.py`, `src/alr/plain/frontend.py`, `src/alr/stream/frontend.py`, `src/alr/tui/panes.py` (id 0 rows), `src/alr/engine/actions.py`
- Test: `tests/unit/test_loop.py`, `tests/unit/test_runlog.py`, `tests/unit/test_errors.py`, `tests/unit/test_tui_panes.py`

**Interfaces:**
- Consumes: `run_actions`, `action_env` (Task 2), `RUN_SCOPE` (Task 1)
- Produces:
  ```python
  # errors.py
  class AfterRunFailedError(RunError):
      """An after_run action failed; every task is still completed."""
      exit_code = 4
  # tasks.py
  class RunResult(StrEnum):
      COMPLETED = "completed"; FAILED = "failed"; RUN_FAILED = "run_failed"; STOPPED = "stopped"
  ```
  `CommitFailedError` stays until Task 4 deletes it; do not reuse its name.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_loop.py` (use the file's fake front end; extend it so `run_action` records the `(task_id, command, env)` it saw and can be told to fail a command):

```python
async def test_after_run_runs_once_after_the_last_task() -> None:
    front_end = FakeFrontEnd()
    task_list = _list(tasks=2, after_task=["a"], after_run=["z"])

    await run(task_list, _options(), front_end)

    assert front_end.commands == ["a", "a", "z"]
    assert front_end.scopes[-1] == 0          # RUN_SCOPE


async def test_after_run_failure_exits_4_and_keeps_tasks_completed() -> None:
    front_end = FakeFrontEnd(fail={"z": "exit status 1"})
    task_list = _list(tasks=2, after_run=["z"])

    with pytest.raises(AfterRunFailedError, match='action "z" failed'):
        await run(task_list, _options(), front_end)

    assert [t.state for t in task_list.tasks] == [COMPLETED, COMPLETED]
    assert front_end.done.result is RunResult.RUN_FAILED
    assert front_end.done.exit_code == 4


async def test_after_run_runs_even_when_every_task_was_completed() -> None:
    front_end = FakeFrontEnd()
    task_list = _list(tasks=2, state=COMPLETED, after_run=["z"])

    await run(task_list, _options(), front_end)

    assert front_end.commands == ["z"]


async def test_after_run_does_not_run_after_a_failed_task() -> None:
    front_end = FakeFrontEnd(fail={"a": "exit status 1"})
    task_list = _list(tasks=2, after_task=["a"], after_run=["z"])

    with pytest.raises(RunError):
        await run(task_list, _options(), front_end)

    assert "z" not in front_end.commands


async def test_a_stop_during_after_run_is_a_stop_and_changes_nothing() -> None:
    front_end = FakeFrontEnd(cancel_on="z")
    task_list = _list(tasks=1, after_run=["z"])

    with pytest.raises(RunCancelledError):
        await run(task_list, _options(), front_end)

    assert task_list.tasks[0].state == COMPLETED
    assert front_end.done.result is RunResult.STOPPED
```

Also a `test_runlog.py` case: `run_finished` carries `result: "run_failed"` and the action's reason in `error`; and a pane test that an `Activity`/`ActionFinished` for task id 0 shows in the Actions pane and in the activity pane without a task row.

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest tests/unit/test_loop.py -x --tb=short -k after_run`
Expected: FAIL.

- [ ] **Step 3: Implement.** In `_run_loop`, after the `for` loop:

```python
    await _run_after_run(task_list, options, front_end)
```

```python
async def _run_after_run(
    task_list: TaskList, options: RunOptions, front_end: FrontEnd
) -> None:
    """Run the file's after_run actions once; a failure ends the run with its own exit code.

    The tasks are already saved completed, so nothing here changes them.
    """
    actions = task_list.after_run_list()
    if not actions:
        return

    env = action_env(None, options.tasks_file, options.log_dir)
    state, error = await run_actions(
        RUN_SCOPE, actions, options.action_timeout, front_end, env
    )
    if state == COMPLETED:
        return

    raise AfterRunFailedError(error)
```

`run_actions` raises `RunCancelledError(f"stopped during action ...")` for scope 0 without the `task N:` prefix: build the prefix from `task.id == 0`. `_ending` maps `AfterRunFailedError` to `(RunResult.RUN_FAILED, 4)` (replace the `CommitFailedError` branch only in Task 4). In `_run_task`, build `env = action_env(task, ...)` and pass it. Panes: `TasksPane` ignores id 0; `ActivityPane` and the actions pane show it.

- [ ] **Step 4: Run the unit suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "Run the after_run actions at the end of a run; exit 4 means one failed"
```

---

### Task 4: Delete the git code and `--commit`

**Files:**
- Delete: `src/alr/engine/commit.py`, `tests/unit/test_commit.py`, `tests/e2e/test_commit.py`, `tests/e2e/test_commit_run.py`, `tests/e2e/test_commit_safety.py`
- Modify: `src/alr/engine/loop.py`, `src/alr/engine/frontend.py`, `src/alr/engine/proc.py`, `src/alr/errors.py`, `src/alr/tasks.py` (drop `PASSED`), `src/alr/record/events.py`, `src/alr/record/runlog.py`, `src/alr/plain/{frontend,start,report,dryrun}.py`, `src/alr/stream/frontend.py`, `src/alr/cli.py`, `src/alr/tui/{app,panes,progress,prepare,checks,settings}.py`
- Test: `tests/unit/test_cli.py`, `tests/unit/test_errors.py`, `tests/unit/test_loop.py`, `tests/unit/test_plain_*.py`, `tests/unit/test_tui_*.py`, `tests/unit/test_import_boundaries.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 3.
- Produces: no `CommitMode`, `Repo`, `Guard`, `commit`, `commit_all`, `head`, `recheck_ignored`, `work_tree_root`, `repo_for`, `CommitStarted`, `Committed`, `commit_started`, `run_git`, `committed`, `CommitFailedError`, `RunResult.COMMIT_FAILED`, `PASSED`, `_defers_saving`, `_commit_*`, `_finish_deferred*`, `_save_committed`, `_save_if_landed`, `_head_after_stop`, `_landed_since`, `_guard`, `_shown`; refusal constants `DIRTY_TREE`, `TASK_FILE_VISIBLE`, `LOG_FOLDER_VISIBLE`, `GIT_ERROR`, `LOCKED`. In `proc.py`, the `new_session`, `die_with_parent`, `gentle` options and `_prctl` code go too if nothing else uses them (check with `git grep` first; `gentle` is also used by `finish`, which sessions call: keep what is still called, delete the rest).

- [ ] **Step 1: Write the failing tests** (these pin the new surface; the deletions are proven by the suite still passing without the old code):

```python
def test_commit_flag_is_refused_with_the_replacement() -> None:
    code, err = _main_with(["--plain", "--dir", d, "--yolo", "--commit", "task"])

    assert code == 2
    assert "--commit: removed; a commit is an action now" in err
    assert "shared.after_task" in err


def test_run_saves_each_task_completed_after_its_actions(tmp_path: Path) -> None:
    ...  # loop test: after the last after_task action passes, tasks.yaml says completed, one save per task


def test_exit_codes_are_contiguous() -> None:
    assert {RefusedError.exit_code, AfterRunFailedError.exit_code} == {3, 4}
```

and `test_import_boundaries.py` gets one assertion: no module under `src/alr` mentions `git` as an executable (`git grep -n '"git"' -- src` is empty).

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest tests/unit/test_cli.py -x --tb=short -k commit_flag`
Expected: FAIL.

- [ ] **Step 3: Implement.** `--commit` stays in the parser as a hidden flag (`help=argparse.SUPPRESS`, `default=None`) that raises `UsageError("--commit: removed; a commit is an action now: put it in shared.after_task, see the HOWTO")`. Delete the commit code listed above. After each task the loop saves `tasks.yaml` (the old non-deferred path): one `_save`, no repository. `Info` loses `commit`. The dry run loses its repository checks and its `commit:` lines. The view loses the lock, the work-tree lookup, `in_work_tree`, and the commit note.
- [ ] **Step 4: Delete the test files** listed above and every unit test whose only subject was removed code; keep and port the ones that test the loop's save-after-each-task behavior.
- [ ] **Step 5: Look for strays**

Run: `git grep -n -i -E "commit|repo\b|work.?tree|CommitMode|git " -- src tests/unit`
Expected: only the migration messages, the skill text (Task 7), and comments you update.

- [ ] **Step 6: Run the unit suite**

Run: `make test`
Expected: PASS. `uv run mypy src` clean if the project runs it (see `pyproject.toml`).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "Remove --commit and all git code; a commit is an action"
```

---

### Task 5: The Actions dialog with two lists

**Files:**
- Modify: `src/alr/tui/dialogs.py`, `src/alr/tui/app.py`, `src/alr/tui/settings.py`, `src/alr/tui/prepare.py`, `src/alr/tui/panes.py`, `src/alr/tui/progress.py`
- Test: `tests/unit/test_tui_dialogs.py`, `tests/unit/test_tui_app.py`, `tests/unit/test_tui_settings.py`, `tests/unit/test_tui_prepare.py`, `tests/unit/test_tui_panes.py`, `tests/unit/test_tui_progress.py`, `tests/unit/test_tui_layout.py`

**Interfaces:**
- Consumes: `Action`, `RunSettings` (Task 1), no commit box (Task 4).
- Produces:
  ```python
  # settings.py
  after_task: list[Action] | None = None   # None until edited in the view
  after_run: list[Action] | None = None    # None until edited in the view

  # dialogs.py
  class ActionsDialog(_Dialog[tuple[list[Action], list[Action]]]):
      """Add and remove the file's after_task and after_run actions in two lists."""
  ```
  The dialog dismisses with `(after_task, after_run)`; Escape dismisses with `None`; `Done` with both empty is a decision that there are none.

- [ ] **Step 1: Write the failing tests.** In `test_tui_dialogs.py` (Textual `run_test` pilot, like the existing gates dialog tests): the dialog shows the labels `After each task` and `After the run`; typing a command and pressing Add puts it in the focused list; Remove deletes the highlighted entry of the focused list; `Done` returns both lists; an empty command shows `an action needs a command`; a bad timeout shows the same error text as today. In `test_tui_app.py`: the `(a)ctions` button and the `a` binding open the dialog; the strip has no `#commit` widget; Start saves both lists into `tasks.yaml` when they were edited; an undecided `after_task` opens the dialog at launch and `after_run` alone never does. In `test_tui_panes.py`: the pane title is `Actions` and a result line reads `<id>. <cmd> passed|failed|timed out`, with no hash line. In `test_tui_progress.py`: the step label is `action N of M: <cmd>` with M the `after_task` count; `after_run` actions read `after run: <cmd>`.

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest tests/unit/test_tui_dialogs.py -x --tb=short -k actions`
Expected: FAIL.

- [ ] **Step 3: Implement.** Rename and rework `GatesDialog` into `ActionsDialog`: one `Input` for the command and one for the timeout (as now), two `ListView`s with ids `after-task` and `after-run`, a focus-aware Add/Remove (`Tab` moves between the inputs and lists), and a `Done` button. `app.py`: remove `#commit-box` and its CSS, binding `g` → `a` (`Binding("a", "menu('actions')", "Actions")`; check `r` and `a` do not clash with another binding), button `(a)ctions`; `ask_actions` replaces `ask_gates` and `needs_gates` becomes `needs_actions`. `prepare.py`: save both edited lists with the file at start, through the same save path that saved `settings.gates`.

- [ ] **Step 4: Run the unit suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "Replace the Gates dialog with an Actions dialog holding both lists"
```

---

### Task 6: End-to-end tests with real `git commit` actions

**Files:**
- Create: `tests/e2e/test_commit_actions.py`
- Modify: `tests/e2e/test_actions.py` (renamed in Task 1), `tests/e2e/test_outcomes.py`, `tests/e2e/test_flags.py`, `tests/e2e/test_tui.py`, `tests/e2e/e2e_helpers.py`, `tests/e2e/testdata/fake_claude.py` if a fake needs a file change per task

**Interfaces:**
- Consumes: the installed wheel's `alr` binary, `e2e_helpers` (read it first: it builds a run folder, a git repo, and runs `alr --plain`). E2E tests share nothing with the unit tests.

- [ ] **Step 1: Write the tests** (each is black box: write `tasks.yaml`, run `alr --plain --dir <d> --yolo`, check exit code, stdout, `git log`, and `tasks.yaml`). Use the helpers' names for repo setup:

```python
COMMIT = (
    'git add -A -- . ":!$ALR_TASK_FILE" ":!$ALR_LOG_DIR" '
    '&& git commit -s -m "$ALR_TASK_NAME"'
)

def test_a_commit_action_commits_each_task(tmp_path):
    run = make_run(tmp_path, after_task=[COMMIT], tasks=["one", "two"])   # fake claude writes a file per task

    result = run_alr(run)

    assert result.code == 0
    assert git_subjects(run.repo) == ["two", "one"]
    assert "tasks.yaml" not in git_files(run.repo) and not any(f.startswith("logs/") for f in git_files(run.repo))


def test_a_hostile_task_name_is_data(tmp_path):
    name = 'fix "auth"; $(touch pwned) `touch pwned2`'
    run = make_run(tmp_path, after_task=[COMMIT], tasks=[name])

    assert run_alr(run).code == 0
    assert git_subjects(run.repo) == [name]
    assert not (run.repo / "pwned").exists() and not (run.repo / "pwned2").exists()


def test_a_failing_action_fails_the_task_and_stops(tmp_path):
    run = make_run(tmp_path, after_task=["false", COMMIT], tasks=["one", "two"])

    result = run_alr(run)

    assert result.code == 1
    assert task_states(run) == ["failed", "pending"]
    assert git_subjects(run.repo) == []          # the commit action never ran


def test_after_run_failure_exits_4_and_tasks_stay_completed(tmp_path):
    run = make_run(tmp_path, after_task=[], after_run=["exit 9"], tasks=["one"])

    result = run_alr(run)

    assert result.code == 4
    assert task_states(run) == ["completed"]
    assert "run_failed" in last_log_event(run)["result"]


def test_a_rerun_retries_after_run_when_every_task_is_completed(tmp_path):
    run = make_run(tmp_path, after_task=[], after_run=["test -e marker"], tasks=["one"])
    assert run_alr(run).code == 4

    (run.dir / "marker").touch()

    assert run_alr(run).code == 0


def test_old_names_are_refused(tmp_path): ...    # shared.gates, cmd, --commit, --gate-timeout: exit 2 or 3 per the unit tests, message names the replacement
def test_sigint_during_after_run_exits_130(tmp_path): ...   # after_run: sleep 30; send SIGINT; code 130, tasks completed
```

Write the last two out in full against the helper functions; follow `test_signal.py` for the SIGINT pattern. The `:!$ALR_LOG_DIR` absolute-path pathspec is what the first test proves: if git rejects it, the fix is to give relative paths in the variables, not to change the test.

- [ ] **Step 2: Delete** the three old `test_commit*.py` (done in Task 4 if not already) and port anything in `test_outcomes.py` that expects exit 4 for a failed commit to the `after_run` meaning.

- [ ] **Step 3: Run the e2e suite** — the main session runs this, subagents never run Docker.

Run: `./build/build.sh`
Expected: all quality gates and the e2e container pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -s -m "Test commits as real actions end to end"
```

---

### Task 7: Docs, the skill, and the ADR status

**Files:**
- Modify: `src/alr/skills/gralph-docs-writer/SKILL.md`, `src/alr/skills/gralph-docs-writer/templates/tasks_template.yaml`, `README.md`, `CHANGELOG.md`, `docs/design/02_architectural_decisions.md`, `docs/design/03_system_architecture.md`, `docs/design/01_requirements.md` if it mentions commit modes

Use the technical-writer agent for the prose, and the `/design-docs-writer` skill for `docs/design/`. The content below is fixed; the wording is theirs.

- [ ] **Step 1: The skill and template.** In `tasks_template.yaml` replace the `Commits:` placeholder and add the two lists with comments:

```yaml
  # Actions alr runs itself. after_task runs after every task's session; if any
  # fails, the task fails. Put a commit LAST: alr does not undo earlier actions.
  # alr sets ALR_TASK_ID, ALR_TASK_NAME, ALR_TASK_FILE, and ALR_LOG_DIR; use
  # them in quotes, as "$ALR_TASK_NAME". Keep tasks.yaml and the logs out of a commit.
  after_task:
    - action: GENERATE_BUILD_AND_TEST_COMMAND_OR_DELETE
    - action: >-
        git add -A -- . ":!$ALR_TASK_FILE" ":!$ALR_LOG_DIR" &&
        git commit -s -m "$ALR_TASK_NAME"
  # after_run runs once at the end, also when every task was already completed.
  after_run: []
```

`SKILL.md` documents both keys, the four variables (`ALR_TASK_ID` and `ALR_TASK_NAME` only for `after_task`), the commit-last rule, the rule that an `after_run` failure exits 4 and leaves tasks completed, and removes every mention of `gates`, `cmd`, and `--commit`.
- [ ] **Step 2: README and CHANGELOG.** README: replace the gates and commit sections with actions. CHANGELOG, under a new `Unreleased` heading, lists the breaking changes: `--commit` removed; `shared.gates` → `after_task`, `cmd` → `action`; `--gate-timeout` → `--action-timeout`; exit 4 now means an `after_run` action failed; `commit_failed`, `committed`, and the `commit` log field gone; log event `gate_finished` → `action_finished`; the commit lock and refusal reasons gone. The version bump is a separate release step, not part of this plan.
- [ ] **Step 3: ADRs.** Add to ADR-026's text, in the lifecycle bullets: "`after_run` runs at the end of every run that has no failed task, including a run where every task was already completed, so a rerun after an `after_run` failure retries it." Set ADR-026 to Accepted; set ADR-014 and ADR-024 to `Superseded by ADR-026` in their sections and in the Decision Summary; add a one-line "Amended by ADR-026" note to ADR-008, ADR-015, ADR-019, and ADR-025 (its exit table row 4 and the commit parts). Bump the document's frontmatter `version`, `date`, `notes`.
- [ ] **Step 4: `03_system_architecture.md`** loses the commit component and gains the actions description; keep its diagrams as Mermaid.
- [ ] **Step 5: Check** `git grep -n -i -E "gates?\b|--commit|commit mode" -- README.md src/alr/skills docs/design/0[0134]*` returns only the migration notes and the superseded ADRs.
- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "Document actions, update the skill, and accept ADR-026"
```

---

## Final review (before the PR)

Per the adversarial-review rule: fresh reviewers assume the work is wrong, reproduce every finding, a skeptic tries to refute each, and a completeness critic asks what was not checked. Give them the Review Focus list above and ADR-026. Then `./build/build.sh` once more on the final tree, and the user pushes.
