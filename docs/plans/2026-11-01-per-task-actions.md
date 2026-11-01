# Per-Task Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A task in `tasks.yaml` may carry its own `actions` list, which alr runs after that task's session and before the shared `after_task` actions, so a docs task does not run the unit tests.

**Architecture:** `Task` gains an `actions` field parsed and saved by `tasks.py` with the same checks the shared lists get. The engine builds one list per task, the task's own actions followed by `shared.after_task`, and hands it to the existing `run_actions`, so the timeout, the environment variables, the first-failure stop, and the log lines are unchanged. The dry run and the view's progress count read the same list. No new runner, no new event, no new dialog.

**Tech Stack:** Python 3.12, PyYAML, Textual, pytest. Local checks: `make test` (ruff format, ruff check, unit tests), `./build/build.sh` (the main session runs it; subagents never run Docker).

**Spec:** `docs/design/02_architectural_decisions.md`, ADR-029. Read it first; it decides the design and this plan only orders the work. Issue #22 is the request.

## Global Constraints

- The per-task key is `actions`. Entries have `action` (required) and `timeout` (optional); any other key is an error. Absent and `[]` both mean none; there is no undecided state.
- Run order for a task: its own `actions`, then `shared.after_task`, as one list. The first failure or timeout fails the task and skips every later action from either list.
- Per-task actions get the same environment variables as `after_task` actions: `ALR_TASK_ID`, `ALR_TASK_NAME`, `ALR_TASK_FILE`, `ALR_LOG_DIR`. The timeout rule is unchanged: `--action-timeout`, else the action's `timeout`, else `10m`.
- Error messages name the task, in the existing shape: `tasks[<i>] (id <id>): actions: must be a sequence`, `tasks[<i>] (id <id>): actions[<j>]: <field>: <problem>`. The per-task `after_task`/`after_run` refusal becomes, verbatim: `<where>: <key>: actions are set once for the whole file, under shared, not per task; a task's own actions go under its actions key`.
- The save writes `actions` after `prompt` and before `state`, only when the list is non-empty.
- Dry-run line for a per-task action, verbatim: `task <id> actions: <first line>: <limit> (<source>)`. These lines come before the `after_task:` lines, in task order, then list order.
- The view's step count for a task is `len(task.actions) + len(shared.after_task)`. The Actions dialog is not changed.
- The run log format does not change: a per-task action is an `action_finished` event under its task.
- No version bump in this plan; the version is set at release time. `CHANGELOG.md` gets an entry under `[Unreleased]`.
- Branch: `feature/22-per-task-actions`, created from `develop` by the main session before Task 1. Subagents never `git push`. Commits are signed off (`git commit -s`) with a one-line imperative subject, as the repository's history shows.
- Code shape: `~/.claude/rules/python/python.md` loads when a `.py` file is touched; keep docstrings (the owner asked for no docstring removal); comments say why, not what. Casual wiki tone for `.md` files, with exact key names, flag names, and error text kept verbatim.

## Review Focus

Inputs the spec implies that no obvious test covers, most likely first. Each has its check in the task that owns it.

1. A task whose own list has an action with a bad `timeout` must be refused naming the task, not `shared.after_task` (Task 1, error table).
2. A task with `actions: []` must parse, run only the shared actions, and save without an `actions` key, so a file does not grow a key on every save (Task 1 and Task 2).
3. A per-task action that fails must skip the shared commit action, or a task's broken work gets committed (Task 3, first-failure test).
4. A rerun that skips a completed task must not run that task's own actions (Task 3, skipped-task test). The existing shared-list test covers the shared half; the per-task half needs its own assertion.
5. The progress line's `action i of n` must say `n` for the running task's total, so a file whose tasks have different counts does not show `action 3 of 2` (Task 5).

---

### Task 1: Parse a task's `actions`

**Files:**
- Modify: `src/alr/tasks.py` (`Task`, `TaskList`, `_parse_task`, `_check_actions`, `_check_action`)
- Test: `tests/unit/test_tasks_parse.py`

**Interfaces:**
- Produces: `Task.actions: list[Action]` (default empty), and `TaskList.actions_for(task: Task) -> list[Action]` returning `task.actions + self.after_task_list()`. Tasks 3, 4, and 5 use both.
- Changes: `_check_actions(label: str, node: yaml.Node) -> list[Action]`, where `label` is the full prefix for messages (`shared.after_task` or `tasks[0] (id 1): actions`). `_optional_actions` passes `f"shared.{key}"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_tasks_parse.py`:

```python
def test_a_task_may_carry_its_own_actions() -> None:
    text = """shared:\n  prompt: shared\ntasks:
  - id: 1
    name: Only task
    prompt: Do it.
    actions:
      - action: uv run pytest
        timeout: 15m
      - action: markdownlint README.md
"""

    got = tasks.parse_tasks(text)

    assert got.tasks[0].actions == [
        Action(command="uv run pytest", timeout="15m"),
        Action(command="markdownlint README.md"),
    ]


@pytest.mark.parametrize("snippet", ["", "    actions: []\n"], ids=["absent", "empty"])
def test_absent_and_empty_task_actions_both_read_as_none(snippet: str) -> None:
    text = f"shared:\n  prompt: shared\ntasks:\n  - id: 1\n    name: Only task\n    prompt: Do it.\n{snippet}"

    got = tasks.parse_tasks(text)

    assert got.tasks[0].actions == []


def test_actions_for_a_task_is_its_own_then_the_shared_ones() -> None:
    own = Action(command="own")
    shared = Action(command="shared")
    task_list = TaskList(
        shared=Shared(prompt="p", after_task=[shared]),
        tasks=[Task(id=1, name="A", prompt="a", actions=[own]), Task(id=2, name="B", prompt="b")],
    )

    assert task_list.actions_for(task_list.tasks[0]) == [own, shared]
    assert task_list.actions_for(task_list.tasks[1]) == [shared]


@pytest.mark.parametrize(
    ("snippet", "want"),
    [
        ("    actions: run tests\n", "tasks[0] (id 1): actions: must be a sequence"),
        ("    actions:\n      - uv run pytest\n", "tasks[0] (id 1): actions[0]: must be a mapping"),
        ("    actions:\n      - action: ''\n", "tasks[0] (id 1): actions[0]: action: must not be empty or whitespace"),
        ("    actions:\n      - action: x\n        timeout: 30\n", "tasks[0] (id 1): actions[0]: timeout: must be a duration string such as 90s or 10m"),
        ("    actions:\n      - action: x\n        when: later\n", "tasks[0] (id 1): actions[0]: when: unknown key; an action has only action and timeout"),
        ("    actions:\n      - cmd: x\n", "tasks[0] (id 1): actions[0]: cmd: renamed to action; see the README (Migrating from commit modes and gates)"),
        ("    after_task: []\n", "tasks[0] (id 1): after_task: actions are set once for the whole file, under shared, not per task; a task's own actions go under its actions key"),
    ],
)
def test_bad_task_actions_name_the_task(snippet: str, want: str) -> None:
    text = f"shared:\n  prompt: shared\ntasks:\n  - id: 1\n    name: Only task\n    prompt: Do it.\n{snippet}"

    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert str(exc.value) == want
```

Update the existing `test_parse_tasks_task_level_actions_are_an_error` assertion to the new full message.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_tasks_parse.py -k "own_actions or task_actions or actions_for" -v`
Expected: FAIL, `Task.__init__() got an unexpected keyword argument 'actions'` and `AttributeError: 'TaskList' object has no attribute 'actions_for'`.

- [ ] **Step 3: Implement**

In `src/alr/tasks.py`:

- `Task` gets `actions: list[Action] = field(default_factory=list)` after `error`. Import `field` from `dataclasses`.
- `TaskList.actions_for(self, task: Task) -> list[Action]`, with a docstring saying the task's own actions come first.
- `_check_actions(label, node)` uses `label` in both messages; `_check_action` is unchanged. `_optional_actions` calls it with `f"shared.{key}"`.
- `_parse_task` reads `actions` with a new helper `_task_actions(where: str, fields: dict[str, yaml.Node]) -> list[Action]` that returns `[]` when the key is absent and otherwise `_check_actions(f"{where}: actions", node)`. Call it after the `after_task`/`after_run` refusal loop, and update that loop's message to the verbatim text in Global Constraints.

- [ ] **Step 4: Run the whole parse module**

Run: `uv run pytest tests/unit/test_tasks_parse.py -v`
Expected: PASS, including the unchanged `shared.after_task[<j>]` error tests.

- [ ] **Step 5: Commit**

```bash
git add src/alr/tasks.py tests/unit/test_tasks_parse.py
git commit -s -m "Parse a task's own actions list"
```

---

### Task 2: Save a task's `actions`

**Files:**
- Modify: `src/alr/tasks.py` (`_task_entry`)
- Test: `tests/unit/test_tasks_save.py`

**Interfaces:**
- Consumes: `Task.actions` from Task 1.
- Produces: a saved file whose task carries `actions` between `prompt` and `state` when non-empty, and no `actions` key otherwise.

- [ ] **Step 1: Write the failing tests**

Add a `pytest.param` to the `test_save_tasks_round_trip` parametrize list with a `TaskList` whose first task has `actions=[Action(command="uv run pytest", timeout="15m"), Action(command="markdownlint README.md")]` and whose second task has none, id `a task's own actions survive the save`. Add:

```python
def test_save_tasks_writes_actions_between_prompt_and_state_and_omits_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    task_list = TaskList(
        shared=Shared(prompt="p"),
        tasks=[
            Task(id=1, name="With", prompt="a", actions=[Action(command="uv run pytest")]),
            Task(id=2, name="Without", prompt="b", actions=[]),
        ],
    )

    tasks.save_tasks(str(path), task_list)

    text = path.read_text(encoding="utf-8")
    assert "    prompt: a\n    actions:\n      - action: uv run pytest\n    state: pending\n" in text
    assert text.count("actions:") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_tasks_save.py -v`
Expected: FAIL; the round trip loses the actions and the new test finds no `actions:` block.

- [ ] **Step 3: Implement**

In `_task_entry`, build the entry as `id`, `name`, `prompt`, then `actions` as `[_action_entry(action) for action in task.actions]` only when `task.actions` is non-empty, then `state`, then `error` as today. Widen the return type to `dict[str, object]`.

- [ ] **Step 4: Run the save and parse modules**

Run: `uv run pytest tests/unit/test_tasks_save.py tests/unit/test_tasks_parse.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alr/tasks.py tests/unit/test_tasks_save.py
git commit -s -m "Save a task's own actions back to the file"
```

---

### Task 3: Run a task's actions before the shared ones

**Files:**
- Modify: `src/alr/engine/loop.py` (`_run_task`)
- Test: `tests/unit/test_actions.py`

**Interfaces:**
- Consumes: `TaskList.actions_for(task)` from Task 1.
- Produces: the run behaviour Tasks 6 and 7 document and test end to end.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_actions.py`, using the file's existing `run_loop`, `saved_tasks`, and marker-file style:

```python
async def test_run_loop_a_tasks_own_actions_run_before_the_shared_ones(tmp_path: Path) -> None:
    order = tmp_path / "order"
    task_list = TaskList(
        shared=Shared(prompt="shared", after_task=[Action(command=f"echo shared >> '{order}'")]),
        tasks=[
            Task(id=1, name="With", prompt="p1", actions=[Action(command=f"echo own >> '{order}'")]),
            Task(id=2, name="Without", prompt="p2"),
        ],
    )

    await run_loop(task_list, tmp_path / "tasks.yaml")

    assert order.read_text() == "own\nshared\nshared\n"


async def test_run_loop_a_failing_own_action_skips_the_shared_actions(tmp_path: Path) -> None:
    shared_ran = tmp_path / "shared-ran"
    task_list = TaskList(
        shared=Shared(prompt="shared", after_task=[Action(command=f"touch '{shared_ran}'")]),
        tasks=[Task(id=1, name="With", prompt="p1", actions=[Action(command="exit 7")])],
    )

    with pytest.raises(RunError):
        await run_loop(task_list, tmp_path / "tasks.yaml")

    assert not shared_ran.exists(), "the commit must not run after a failed check"
    saved = saved_tasks(tmp_path / "tasks.yaml")
    assert saved.tasks[0].state == FAILED
    assert saved.tasks[0].error == 'action "exit 7" failed: exit status 7'


async def test_run_loop_a_completed_tasks_own_actions_do_not_run(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    task_list = TaskList(
        shared=Shared(prompt="shared", after_task=[]),
        tasks=[Task(id=1, name="Done", prompt="p1", state=COMPLETED, actions=[Action(command=f"touch '{marker}'")])],
    )

    await run_loop(task_list, tmp_path / "tasks.yaml")

    assert not marker.exists()


async def test_run_loop_a_tasks_own_action_sees_the_task_variables(tmp_path: Path) -> None:
    seen = tmp_path / "seen"
    task_list = TaskList(
        shared=Shared(prompt="shared", after_task=[]),
        tasks=[Task(id=4, name="Named task", prompt="p", actions=[Action(command=f"printf '%s|%s' \"$ALR_TASK_ID\" \"$ALR_TASK_NAME\" > '{seen}'")])],
    )

    await run_loop(task_list, tmp_path / "tasks.yaml")

    assert seen.read_text() == "4|Named task"
```

Match the exact failure text to what `test_run_loop_action_failure_fails_task_and_stops_run` asserts today; copy its wording for the exit-status part.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_actions.py -k "own_action" -v`
Expected: FAIL; the order file reads `shared\nshared\n`, the shared marker exists, the failing task completes.

- [ ] **Step 3: Implement**

In `_run_task`, replace `task_list.after_task_list()` with `task_list.actions_for(task)`. Update the docstring to say the task's own actions run first.

- [ ] **Step 4: Run the engine tests**

Run: `uv run pytest tests/unit/test_actions.py tests/unit/test_loop.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alr/engine/loop.py tests/unit/test_actions.py
git commit -s -m "Run a task's own actions before the shared after_task actions"
```

---

### Task 4: Dry run prints per-task action limits

**Files:**
- Modify: `src/alr/plain/dryrun.py` (`_print_action_limits`)
- Test: `tests/unit/test_plain_dryrun.py`

**Interfaces:**
- Consumes: `Task.actions` from Task 1; `_print_list_limits(out, key, actions, override)` as it exists.
- Produces: the `task <id> actions:` lines Task 7 checks end to end.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_plain_dryrun.py`, in the file's existing style (a task file text, a dry run, the captured stdout):

```python
def test_dry_run_prints_a_tasks_own_action_limits_before_the_shared_ones(...) -> None:
    # task 1 has actions [{action: uv run pytest, timeout: 15m}], task 2 has [{action: markdownlint README.md}],
    # shared.after_task has [{action: echo one}]
    ...
    assert (
        "task 1 actions: uv run pytest: 15m (action)\n"
        "task 2 actions: markdownlint README.md: 10m (default)\n"
        "after_task: echo one: 10m (default)\n"
    ) in out
```

Write the task file text and the call the way the test at line 133 of that file does.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_plain_dryrun.py -k own_action -v`
Expected: FAIL; no `task 1 actions:` line in the output.

- [ ] **Step 3: Implement**

In `_print_action_limits`, before the shared lists, loop over `task_list.tasks` and call `_print_list_limits(out, f"task {task.id} actions", task.actions, override)`. Update the docstring: per-task lines first, then `after_task`, then `after_run`.

- [ ] **Step 4: Run the dry-run tests**

Run: `uv run pytest tests/unit/test_plain_dryrun.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alr/plain/dryrun.py tests/unit/test_plain_dryrun.py
git commit -s -m "Print a task's own action limits in the dry run"
```

---

### Task 5: The progress line counts both lists

**Files:**
- Modify: `src/alr/tui/progress.py` (`Progress.__init__`, `feed`)
- Modify: `src/alr/tui/app.py` (`_start_ticking`, the keyword name only)
- Test: `tests/unit/test_tui_progress.py`

**Interfaces:**
- Consumes: `Task.actions` from Task 1; `TaskStarted.task` already carries a copy of the task.
- Changes: `Progress.__init__(self, total_tasks: int, shared_action_count: int, clock=...)`. The old name `action_count` goes; update the four constructor calls in `tests/unit/test_tui_progress.py` and the one in `app.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_tui_progress.py`, in the file's style (a fake clock, `feed`, `text()`):

```python
def test_the_step_count_is_the_running_tasks_own_actions_plus_the_shared_ones() -> None:
    progress = Progress(total_tasks=2, shared_action_count=1)
    first = Task(id=1, name="With", prompt="p", actions=[Action(command="own")])
    second = Task(id=2, name="Without", prompt="p")

    progress.feed(TaskStarted(task=first))
    progress.feed(ActionStarted(task=first, action=Action(command="own")))
    assert "action 1 of 2: own" in progress.text()

    progress.feed(TaskFinished(first, None, 1.0))
    progress.feed(TaskStarted(task=second))
    progress.feed(ActionStarted(task=second, action=Action(command="shared")))
    assert "action 1 of 1: shared" in progress.text()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_tui_progress.py -v`
Expected: FAIL, `unexpected keyword argument 'shared_action_count'` on the new test; the renamed calls fail until Step 3.

- [ ] **Step 3: Implement**

In `Progress.__init__`, store `self._shared_action_count` and set `self._action_count = shared_action_count`. In `feed`, on `TaskStarted(task=task)`, set `self._action_count = len(task.actions) + self._shared_action_count` alongside the existing resets. In `app.py` `_start_ticking`, pass `shared_action_count=actions`. Rename the four test constructor calls.

- [ ] **Step 4: Run the view tests**

Run: `uv run pytest tests/unit/test_tui_progress.py tests/unit/test_tui_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alr/tui/progress.py src/alr/tui/app.py tests/unit/test_tui_progress.py
git commit -s -m "Count a task's own actions in the progress line"
```

---

### Task 6: Skill, template, README, changelog

**Files:**
- Modify: `src/alr/skills/alr-task-file/SKILL.md` (the actions section, lines 35 to 82, and step 5 of the procedure, lines 112 to 130)
- Modify: `src/alr/skills/alr-task-file/templates/tasks_template.yaml` (the task entry)
- Modify: `README.md` (the Actions section, lines 128 to 160)
- Modify: `CHANGELOG.md` (`[Unreleased]`)

**Interfaces:**
- Consumes: the behaviour from Tasks 1 to 5. No code.

- [ ] **Step 1: The skill**

After the paragraph that ends "`--action-timeout` overrides every action's timeout." add a paragraph: a task may carry its own `actions` list, same entry shape, run after that task's session and before `shared.after_task`; use it for checks only that task needs (the issue's example: unit tests for a code task, a Markdown lint for a docs task, ShellCheck and BATS for a script task); keep the commit in `shared.after_task` so every task is committed the same way, and never put a commit in a task's own list. In step 5, where the task fields are listed, add `actions` as optional, and change "Write no other task keys" to name `actions` among the known ones.

- [ ] **Step 2: The template**

Under the task entry, after `prompt`, add a commented optional block:

```yaml
    # Optional: checks only this task needs. They run before shared.after_task.
    # Never put a commit here. Delete the key if the task needs none.
    actions:
      - action: GENERATE_TASK_SPECIFIC_CHECK_OR_DELETE
```

- [ ] **Step 3: The README**

In the Actions section, after the two-list table, add a short paragraph and example: a task may have its own `actions`, same entry shape, run first, then `shared.after_task`; the first failure in either list fails the task. Add the `task <id> actions:` line to the `--dry-run` row of the flag table (line 87). Remove or reword any sentence that says actions live only under `shared`.

- [ ] **Step 4: The changelog**

Under `## [Unreleased]`, add `### Added` before `### Changed`:

```markdown
- A task may carry its own `actions` list. They run after that task's session
  and before `shared.after_task`, with the same entry shape, variables, and
  timeout rules. The first failure in either list fails the task. `--dry-run`
  prints them as `task <id> actions:` lines, and the progress line counts
  both lists. (#22)
```

- [ ] **Step 5: Check the template still parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('src/alr/skills/alr-task-file/templates/tasks_template.yaml'))" && make test`
Expected: no error; all unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alr/skills/alr-task-file README.md CHANGELOG.md
git commit -s -m "Document a task's own actions in the skill, template, README, and changelog"
```

---

### Task 7: End-to-end tests

**Files:**
- Test: `tests/e2e/test_actions.py`
- Test: `tests/e2e/e2e_helpers.py` (a task block with actions)

**Interfaces:**
- Consumes: `shared_block`, `write_tasks_yaml`, `run_alr`, `run_args`, `run_dry_run`, `run_log`, `read_tasks_yaml`, `task_states`, `task_errors` as they exist. These tests are black-box against the wheel and share nothing with the unit tests.

- [ ] **Step 1: Add a fixture**

In `e2e_helpers.py`, add `TWO_TASKS_FIRST_WITH_ACTIONS` as a function `two_tasks_first_with_actions(action: str) -> str` that returns the `TWO_TASKS` text with `actions:\n      - action: <action>\n` under task 1 only.

- [ ] **Step 2: Write the tests**

Add to `tests/e2e/test_actions.py`:

```python
def test_actions_a_tasks_own_actions_run_for_that_task_only_and_first(tmp_path, fake_claude):
    # shared after_task appends "shared"; task 1's own action appends "own"
    # assert the marker file reads "own\nshared\nshared\n"
    # assert the run log's action_finished events are, in order:
    #   (task 1, cmd own), (task 1, cmd shared), (task 2, cmd shared)

def test_actions_a_failing_own_action_fails_the_task_and_skips_the_shared_ones(tmp_path, fake_claude):
    # task 1 actions: [exit 7]; shared after_task touches a marker
    # assert exit 1, task 1 failed with 'action "exit 7" failed: exit status 7', task 2 pending, no marker

def test_actions_bad_task_actions_are_refused_naming_the_task(tmp_path, fake_claude):
    # task 1 actions: [{action: x, timeout: 30}]
    # assert dry run exits 3 and stderr holds
    #   "tasks[0] (id 1): actions[0]: timeout: must be a duration string such as 90s or 10m"

def test_actions_dry_run_lists_a_tasks_own_limits_first(tmp_path, fake_claude):
    # task 1 actions: [{action: uv run pytest, timeout: 15m}]; shared after_task: [{action: echo one}]
    # assert stdout holds "task 1 actions: uv run pytest: 15m (action)\nafter_task: echo one: 10m (default)\n"

def test_actions_a_tasks_own_actions_survive_the_save(tmp_path, fake_claude):
    # run to completion; read_tasks_yaml shows task 1 still has its actions list and task 2 has no actions key
```

Update `test_actions_task_level_actions_are_rejected` if it asserts the old per-task `after_task` message; the `gates` message it asserts today is unchanged.

- [ ] **Step 3: Run the build**

The main session runs: `./build/build.sh`
Expected: the wheel builds, the unit and e2e suites pass in the container, exit 0. A failing new test here is a bug in Tasks 1 to 5, not a test to loosen.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_actions.py tests/e2e/e2e_helpers.py
git commit -s -m "Cover a task's own actions end to end"
```

---

### Task 8: Design documents and the wiki

**Files:**
- Modify: `docs/design/02_architectural_decisions.md` (ADR-029 status to Accepted; frontmatter version, date, notes)
- Modify: `docs/design/01_requirements.md` (requirements 2, 4, and the Action enforcement row; frontmatter)
- Modify: `docs/design/03_system_architecture.md` (the parse bullets at lines 189 to 198, the run bullet at 228, the dry-run bullet at 297, the progress bullet at 347; frontmatter)
- Modify, in the wiki clone at `../agentic_loop_runner.wiki/`: `Actions.md` (a new section after "The two lists"; drop "alr won't accept them on a single task"), `Write-a-task-file.md` (add `actions` to the task fields)

**Interfaces:**
- Consumes: the built behaviour. The wiki is a separate repository: edit and commit in the local clone; the owner pushes it.

- [ ] **Step 1: ADR-029 to Accepted**

Change its status line to `**Status:** Accepted. It amends ADR-026: ...` keeping the rest of the sentence. Bump the frontmatter to version 45, today's date, and a note that starts `ADR-029 is Accepted: a task's own actions are built and documented. Earlier: `.

- [ ] **Step 2: Requirements**

Requirement 2: add `optional actions` to the task fields. Requirement 4: say the task's own `actions` run first, then `shared.after_task`. The Action enforcement row: "a task's own actions run before the shared ones, first failure stops both". Bump the frontmatter.

- [ ] **Step 3: System architecture**

Parse bullets: a task's `actions` is validated like the shared lists with errors `tasks[<i>] (id <id>): actions[<j>]: <field>: <problem>`; the per-task `after_task`/`after_run` message gains its new ending. Run bullet: one list, the task's own then the shared. Dry-run bullet: `task <id> actions:` lines first. Progress bullet: n is the running task's own plus shared count. Bump the frontmatter.

- [ ] **Step 4: Wiki**

`Actions.md`: a section "A task's own actions" with the issue's three-task example and the rule that the commit stays in `shared.after_task`. Link ADR-029 the way the page links ADR-026. `Write-a-task-file.md`: add `actions` to the task fields list. Commit in the wiki clone with `git commit -s -m "Describe a task's own actions"`; do not push.

- [ ] **Step 5: Commit the design documents**

```bash
git add docs/design
git commit -s -m "Accept ADR-029 and document a task's own actions"
```

---

### Task 9: Finish

- [ ] **Step 1: Whole-branch review**

A fresh reviewer, adversarial per the repository's practice: assume it is wrong, reproduce every finding, check the Review Focus list, and ask what is missing.

- [ ] **Step 2: Hand back**

Report to the owner: the branch name, the build result, and the wiki commit waiting for a push. The owner pushes and opens the PR; nobody else pushes.
