---
version: 9
date: 2026-10-07
notes: "The flag that keeps a terminal run in plain mode is --plain, not gralph's --no-tui (ADR-013); ADR-002 notes the rename."
---

# alr — Architectural Decisions

[Back to Overview](00_overview.md) | [Back to Project README](../../README.md)

## Table of Contents

- [Decision Record Format](#decision-record-format)
- [Decision Summary](#decision-summary)
- [Inherited Decisions](#inherited-decisions)
- [Decisions](#decisions)

## Decision Record Format

Each architectural decision is recorded as an ADR with the following structure:

- **Title**: Short descriptive name for the decision
- **Status**: Proposed | Accepted | Deprecated | Superseded by ADR-NNN
- **Context**: The situation, forces at play, and why a decision is needed
- **Decision**: What was decided and the rationale
- **Consequences**: Both positive outcomes and trade-offs accepted

## Decision Summary

| ADR #                                                                              | Title                                                               | Status   | Date       |
|------------------------------------------------------------------------------------|---------------------------------------------------------------------|----------|------------|
| [001](#adr-001-port-gralph-to-python-drop-in-compatible)                           | Port gralph to Python, drop-in compatible                           | Accepted | 2026-10-04 |
| [002](#adr-002-engine-first-the-tui-is-the-next-design)                            | Engine first; the TUI is the next design                            | Accepted | 2026-10-04 |
| [003](#adr-003-an-async-engine)                                                    | An async engine                                                     | Accepted | 2026-10-04 |
| [004](#adr-004-one-engine-two-front-ends)                                          | One engine, two front ends                                          | Accepted | 2026-10-04 |
| [005](#adr-005-pyyaml-nodes-and-hand-written-validation-no-pydantic)               | PyYAML nodes and hand-written validation, no pydantic               | Accepted | 2026-10-04 |
| [006](#adr-006-port-the-tests-first-with-a-fake-claude-on-path)                    | Port the tests first, with a fake claude on PATH                    | Accepted | 2026-10-04 |
| [007](#adr-007-compatible-in-use-python-native-inside)                             | Compatible in use, Python-native inside                             | Accepted | 2026-10-04 |
| [008](#adr-008-the-shared-prompt-and-gates-live-in-the-task-file)                  | The shared prompt and gates live in the task file                   | Accepted | 2026-10-06 |
| [009](#adr-009-the-run-folders-sandboxjson-answers-the-permission-choice)          | The run folder's sandbox.json answers the permission choice         | Accepted | 2026-10-06 |
| [010](#adr-010-unit-tests-under-make-test-e2e-tests-in-a-container)                | Unit tests under `make test`, e2e tests in a container              | Accepted | 2026-10-06 |
| [011](#adr-011-the-unit-tests-keep-one-case-per-branch)                            | The unit tests keep one case per branch                             | Accepted | 2026-10-06 |
| [012](#adr-012-the-tui-runs-the-engine-in-process-as-a-textual-worker)             | The TUI runs the engine in-process as a Textual worker              | Accepted | 2026-10-07 |
| [013](#adr-013-setup-in-modal-dialogs-over-one-main-window-with-an-explicit-start) | Setup in modal dialogs over one main window, with an explicit Start | Accepted | 2026-10-07 |
| [014](#adr-014-commits-in-the-tui-per-task-or-after-all-tasks-when-there-is-a-work-tree) | Commits in the TUI: per task or after all tasks, when there is a work tree | Accepted | 2026-10-07 |
| [015](#adr-015-logging-is-a-toggle-on-by-default-the-gate-timeout-has-no-dialog)   | Logging is a toggle, on by default; the gate timeout has no dialog  | Accepted | 2026-10-07 |
| [016](#adr-016---install-skill-installs-the-bundled-skill-and-exits)               | `--install-skill` installs the bundled skill and exits              | Accepted | 2026-10-07 |

## Inherited Decisions

These gralph decisions apply to `alr` unchanged. Their full text is in gralph's
`docs/architecture/02_architectural_decisions.md` at v0.12.0. Where a later gralph ADR amends one of them, the last
column says which `alr` ADR carries the change.

| gralph ADR | Title                                                         | Amended by                       |
|------------|---------------------------------------------------------------|----------------------------------|
| ADR-001    | Claude Code only, no agent abstraction                        |                                  |
| ADR-002    | YAML task file + shared prompt, strict parsing                | ADR-008: one file                |
| ADR-003    | One fresh session per task, no retry                          |                                  |
| ADR-004    | The runner owns task state, atomic writes                     |                                  |
| ADR-005    | Outcome from JSON result line, not exit code                  |                                  |
| ADR-006    | Failed task blocks run until manual reset                     |                                  |
| ADR-007    | Unix only, process group lifecycle management                 |                                  |
| ADR-013    | The runner runs the gates after a completed session           |                                  |
| ADR-014    | Sessions run in Claude Code's sandbox; bypass only on request | ADR-009: found in the run folder |
| ADR-015    | The runner commits a completed task on request                |                                  |
| ADR-016    | The runner logs a run on request                              | ADR-008: no `prompt_file`        |
| ADR-019    | One gate list per task file                                   | ADR-008: `shared.gates`          |
| ADR-020    | The shared prompt and gates live in the task file             | ADR-008 is `alr`'s version       |
| ADR-021    | The run folder's sandbox.json answers the permission choice   | ADR-009 is `alr`'s version       |

gralph's ADR-008 (Docker-first CI) is already met by this repository's own build. ADR-009 to ADR-012, ADR-017, and
ADR-018 cover the skill, the TUI, releases, and the wizard, and are left to later designs. gralph's ADR-022 (no check of
the installed skill) supersedes its ADR-010 and needs nothing here: `alr` has no skill check, and the next design's
`--install-skill` will not add one. gralph's ADR-018 is partly ported already: `-d` finds `tasks.yaml` (and, by ADR-009,
`sandbox.json`); the wizard is left to the next design.

## Decisions

### ADR-001: Port gralph to Python, drop-in compatible

[Back to Decision Summary](#decision-summary)

**Status:** Accepted; what "drop-in compatible" covers is narrowed by ADR-007, and its reference version moves to gralph
v0.12.0 under ADR-008 and ADR-009

**Context:**

The runner is meant to grow into a full agent on a Python framework such as LangGraph or CrewAI. gralph is Go. `alr` was
started as a simpler Python runner written by hand as a learning project, with a different task model. Three levels of
faithfulness were considered: drop-in compatible, the same behavior with warts tidied, and a rethink shaped around the
future agent.

**Decision:**

Port gralph to Python in this repository, replacing the existing `alr` code and keeping the name `alr`. The port is
drop-in compatible: same flags, task file format, result line, gates, commit, sandbox behavior, run log, and error text.
gralph at commit `170dc6f` is the behavioral reference. The code is written by Claude and reviewed by the owner; the
earlier rule that the owner writes every line no longer applies.

Tidying was rejected because every tidy-up is a compatibility decision that slows the port. A rethink was rejected
because it has no acceptance tests and guesses at what the agent will need.

**Consequences:**

*Positive:*

- Existing task files, prompts, sandbox settings, and scripts work unchanged
- gralph's unit and e2e tests define done
- The agent work starts from known, tested behavior

*Negative:*

- gralph's quirks come along, including Go-flavored details such as duration syntax and `exit status 1` error text
- The `abandoned` state and the `-d` short form for `--dry-run` from the old `alr` are gone
- Two implementations exist until gralph is retired, and they can drift

### ADR-002: Engine first; the TUI is the next design

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

gralph is about 14,700 lines of Go, roughly half tests. It includes a full-screen view and a setup wizard on Bubble Tea.
The owner wants a TUI, and a more capable one than the Go version, because plain command-line use is tedious. Porting
everything at once makes one very large change to review.

**Decision:**

Split the work. This design covers the task file, the engine, plain mode, dry-run, and the engine side of the
full-screen path (stream-json parsing, events, the run log). The Textual view, the wizard, `--install-skill`, and the
skill check are the next design.

Until the view exists, `alr` always runs plain mode: gralph's `--no-tui` flag was accepted and did nothing (it is now
`--plain`, ADR-013), and `--log-dir` on a real run gives gralph's plain-mode error.

**Consequences:**

*Positive:*

- Each step is small enough to review
- The TUI is designed for Textual's strengths instead of being a line-by-line copy of the Bubble Tea view
- The engine the TUI and the agent build on is finished and tested first

*Negative:*

- The stream path and the run log are built and tested but not reachable from the command line until the next step
- `alr` is not a full replacement for gralph in a terminal until the TUI lands

### ADR-003: An async engine

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

The engine runs subprocesses, reads two pipes at once on the stream path, enforces gate timeouts, and must stop cleanly
on a signal. gralph does this with goroutines and context cancellation. In Python the choice is `asyncio` or synchronous
code with threads. The planned TUI library (Textual) is built on `asyncio`, and LangGraph is designed for async use.

**Decision:**

Build the engine on `asyncio`: `asyncio` subprocesses, task cancellation for stopping, and loop signal handlers for
SIGINT and SIGTERM. A synchronous port with reader threads was rejected: it would be simpler to read today, but the TUI
and agent layer would have to run it in a worker thread, and a thread blocked on a subprocess cannot be canceled
without hand-built machinery.

**Consequences:**

*Positive:*

- Textual and an async agent framework can await the engine directly
- Cancellation has one mechanism, with cleanup in one place
- Events are emitted from one thread, so the run log needs no lock

*Negative:*

- `async` and `await` spread through every function that touches the engine
- The code maps less directly onto the Go source
- Tests need pytest-asyncio

### ADR-004: One engine, two front ends

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

gralph has two ways to run a task, picked by whether a `report` hook is set. The plain path prints and passes output
through. The stream path captures output and reports events. The choice is checked inside the looper, the gate runner,
and the committer, so plain-only and TUI-only logic are mixed into the same functions. The owner wants a clean, clear
line between what the plain path runs and what the TUI path runs.

**Decision:**

Split the package three ways. `alr.engine` holds the shared rules and never prints. `alr.plain` holds only what plain
mode does. `alr.stream` holds only what the full-screen path does. The engine is handed a front end object and calls it
to run a session, a gate, or a git command and to hear about progress. `alr.cli` picks the front end once.

Import rules, enforced by a test: `engine` imports neither `plain`, `stream`, nor `tui`; `plain` and `stream` do not
import each other.

**Consequences:**

*Positive:*

- `plain/` can be read top to bottom to see exactly what a no-TUI run does
- The outcome rule, gate rule, commit rule, and save happen in one place, so the two paths cannot drift apart
- The TUI and the agent layer plug in without changing the engine

*Negative:*

- One abstraction, the front end interface, that gralph does not have
- Files do not map one-to-one onto gralph's, so comparing against the Go source takes more care

### ADR-005: PyYAML nodes and hand-written validation, no pydantic

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

gralph validates the task file on raw YAML nodes: it rejects any tag outside the core set, requires `id` to be a real
YAML integer, detects duplicate keys, and reports errors as `tasks[<index>] (id <id>): <field>: <problem>`. The old
`alr` used pydantic models. Matching gralph's rules and exact error text through pydantic means fighting its own error
format.

**Decision:**

Parse with PyYAML's node API (`yaml.compose`), validate with hand-written checks, and hold the result in plain
dataclasses. Remove pydantic. Parse flags with the standard library's `argparse`, with prefix abbreviation turned off.

**Consequences:**

*Positive:*

- Validation rules and error text can match gralph exactly
- One runtime dependency
- The checks port almost directly from `task.go`

*Negative:*

- More validation code than a schema library would need
- `--help` text differs from gralph's

### ADR-006: Port the tests first, with a fake claude on PATH

[Back to Decision Summary](#decision-summary)

**Status:** Accepted; its last sentence is amended by ADR-010, and its port-every-case approach by ADR-011

**Context:**

Drop-in compatibility needs proof. gralph has unit tests per package and a black-box e2e suite that runs the real binary
against a scriptable fake `claude` placed first on `PATH` and driven by `FAKECLAUDE_*` environment variables.

**Decision:**

For each module, port gralph's tests to pytest first, then write the code to pass them. Port the fake `claude` as a
Python script that honors the same environment variables. Port the e2e scenarios as pytest tests that run the installed
`alr` command as a subprocess. The wizard, TUI, install-skill, and skill-check tests are left to the next design. All
tests run under `make test` and inside the Docker build.

**Consequences:**

*Positive:*

- gralph's tests are the definition of compatible
- No real Claude and no network in any test
- The e2e suite exercises the real command line, signals included

*Negative:*

- A large amount of test code to port before features appear
- A gralph test that asserts Go-specific detail needs judgment to translate

### ADR-007: Compatible in use, Python-native inside

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

ADR-001 called the port "drop-in compatible" and listed error text among the things that must match. The first five
tasks read that strictly: the task file loader was given a custom YAML resolver so plain values such as `yes` and
`12:30` load the way gralph's Go YAML library loads them, and the saver was taught to quote strings that library would
read as numbers. The owner then clarified the intent. `alr` replaces gralph; the two are not meant to run side by side
on the same files. "Drop-in" was about what a person using the tool sees and types, not about reproducing how the Go
code represents things inside.

**Decision:**

Compatibility covers the surface a user works with:

- The command-line flags and what they mean
- The task file format and its field rules, the shared prompt, and the text sent to Claude
- The result line and the outcome rule
- What gates, `--commit`, and the sandbox flags do
- Exit codes and the shape of plain-mode output
- The full-screen view's behavior, when it is built

Compatibility does not cover internal representation:

- YAML is read and written with PyYAML's standard safe loader and dumper rules. A plain value that YAML 1.1 reads as a
  boolean or a number (`yes`, `on`, `12:30`) must be quoted to be a string; `alr` rejects it with a clear message
  otherwise
- Error messages must be clear and name the task and field. They do not have to match gralph's wording, and a ported
  test that asserts Go-specific wording may be adapted
- No new code is added only to imitate a Go library. The `alr.gocompat` module from the first tasks is removed: duration
  parsing and formatting live in `alr.durations` because that is the task file's own format, and the few message helpers
  live where they are used

The saver keeps two behaviors because they serve `alr`'s own users: multi-line text is written as a `|` block, and lists
are indented under their key.

**Consequences:**

*Positive:*

- Less code, and none that exists only to mirror another library
- The task file behaves the way anyone who knows YAML and PyYAML expects

*Negative:*

- A gralph task file with an unquoted name such as `yes` or `12:30` needs quotes added before `alr` accepts it
- Error text can drift from gralph's, so the Go tests are a guide to behavior and no longer an exact oracle for messages

### ADR-008: The shared prompt and gates live in the task file

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

The first version of these documents read the shared prompt from a second file, `prompt.md`, passed with `-p`, and kept
the gates as a top-level key of the task file. gralph v0.11.0 (its ADR-020) merged them: one `tasks.yaml` holds a
required `shared` block with the `prompt` and the optional `gates`, and `-p/--prompt` is gone. The two files always
traveled together, the skill wrote both in one pass, and every consumer had to find, load, and report two paths. `alr`
follows gralph here because ADR-001 makes gralph the reference. It also settles the "single-file input" step that the
overview's roadmap listed for later.

**Decision:**

The task file has two top-level keys, `shared` and `tasks`, and nothing else.

```yaml
shared:
  prompt: |
    You are working on the widget service...
  gates:
    - cmd: uv run pytest
      timeout: 10m
tasks:
  - id: 1
    name: Add the widget repository
    prompt: |
      Objective: ...
```

- **`shared.prompt` is required.** A missing `shared` block, or a prompt that is missing, not a string, empty, or only
  whitespace, fails parsing. The text is trimmed on load.
- **`shared.gates` is optional, with ADR-019's rules unchanged.** Absent means not decided, `[]` means no gates, and a
  save keeps whichever the file had. Error names read `shared.gates[<j>]`.
- **Unknown keys are errors.** A top-level `gates` key is an error that says gates live under `shared.gates`, as is a
  task-level `gates`. Any other top-level key, and any `shared` key other than `prompt` and `gates`, is rejected too.
  Quietly ignoring an old top-level `gates` list would leave a run with no checks.
- **`-p/--prompt` is removed** with no compatibility path. Passing it is the unknown-flag error.
- **`-d <folder>` means `<folder>/tasks.yaml`.** The "no prompt.md" error goes. `-t` stays as the override for a folder
  holding two task files. A dry run needs nothing the real run does not, so the prompt carve-out goes.
- **The run log drops `prompt_file`** from `run_started`. `tasks_file` stays.
- **The wire contract is unchanged.** Claude gets `shared.prompt`, a blank line, the task text, then a newline. Gates
  and `error` are never sent.
- **A save keeps `shared`.** `shared` is written above `tasks`, with the prompt intact.
- **`PromptError` goes** with the file it described. A bad `shared.prompt` is a `TaskFileError`.

Keeping `-p` as an override was rejected: it keeps the two-file code path this removes, and the project is too young to
need a compatibility path.

**Consequences:**

*Positive:*

- One file is the whole run: prompt, gates, and tasks
- One path to name, load, and report; the `-p` flag and the prompt half of the run log go away
- Room for more shared settings under `shared` without new flags

*Negative:*

- A breaking change for any `tasks.yaml` and `prompt.md` pair from before: they must be merged by hand, and a top-level
  `gates` moved under `shared`
- The shared prompt is YAML now: indentation matters, and it loses Markdown editor support
- A save rewrites the file and does not keep comments or the prompt's own formatting (ADR-005's saver rules)

### ADR-009: The run folder's sandbox.json answers the permission choice

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

gralph v0.12.0 (its ADR-021) lets a run folder carry its own sandbox settings, found by a fixed name as `tasks.yaml` is.
Without it, a run has to repeat `--sandbox-settings <folder>/sandbox.json` even though the file sits beside the task
file. `alr` already has `-d`, so the lookup costs little. The same safety rule as ADR-014 applies: a run never ends up
unsandboxed by accident.

**Decision:**

A `sandbox.json` in a `-d` run folder is the run's `--sandbox-settings`.

- **When it applies.** `-d <folder>` was given, `<folder>/sandbox.json` is a regular file, and neither
  `--sandbox-settings` nor `--skip-permissions` was passed. A directory of that name is ignored. The path is
  `<folder>/sandbox.json` as `-d` was given, not made absolute. Only `-d` looks; `-t` alone never does, and `-d` with
  `-t` still uses the folder's file.
- **Precedence.** An explicit `--sandbox-settings` wins. An explicit `--skip-permissions` also wins, with no error: it
  is how a run opts out of a folder's sandbox. The "both flags" error is for the two explicit flags only.
- **It never leads to an unsandboxed run.** It only supplies the sandboxed choice. With no file, nothing changes: a real
  run still exits 1 with the neither-flag error. A found file that is unreadable or not a JSON object fails through the
  existing validation, exit 1, and never falls back to going without a sandbox.
- **Plain output.** Nothing new is printed. `--dry-run -d <folder>` names and validates the file on its existing
  `sandbox settings:` line.
- **Startup order.** The file is resolved with the task file, in the `--dir` step, so every later check sees the final
  value.

**Consequences:**

*Positive:*

- `alr -d .local/feat --commit` is a whole command for a folder that holds its task file and sandbox settings
- A command that names the folder reproduces the run

*Negative:*

- A settings file in a run folder is applied without being named on the command line, so it is as trusted as the
  folder's `tasks.yaml`, which is already code the run executes
- The only way to run a folder that holds a `sandbox.json` unsandboxed is `--skip-permissions`

### ADR-010: Unit tests under `make test`, e2e tests in a container

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

ADR-006 put every test under `make test` and inside the Docker build, with the e2e tests running the installed `alr`
command. In practice the e2e tests shared helpers and fixtures with the unit tests, so they could pass against code a
user never installs, and they ran against the working tree rather than the built wheel.

**Decision:**

Split the suites so they share nothing.

- `tests/unit/` has its own conftest, helpers, and fake `claude`. `make test` runs the ruff format and lint steps, then
  `pytest tests/unit`. The Docker build runs the unit tests only.
- `tests/e2e/` has its own conftest, helpers, requirements, `pytest.ini`, and fake `claude`. It finds `alr` on `PATH`
  and runs the real command as a subprocess. It never imports from `src/`.
- The e2e suite runs in a container (`tests/e2e/Dockerfile`, started by `tests/docker-compose.yaml`) against the wheel
  installed from `dist/`. `make e2e` runs it against the wheel already in `dist/`, and `make build` builds the wheel and
  then runs it. `build/build.sh` drives both.

This amends ADR-006's last sentence only: the tests are still ported first, the fake `claude` is still driven by
`FAKECLAUDE_*` variables, and no test needs a real Claude or the network.

**Consequences:**

*Positive:*

- The e2e tests check what a user installs, not the working tree
- A helper fixed for one suite cannot quietly make the other pass
- `make test` is fast, with no Docker needed

*Negative:*

- A little test code is duplicated between the suites: the fake `claude` exists twice
- The e2e suite needs Docker and a built wheel, so it does not run under plain `make test`

### ADR-011: The unit tests keep one case per branch

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

ADR-006 ported gralph's tests case by case. By the time the stream path was built, the unit suite held 608 tests: 274
test functions and 334 rows in parametrized tables. A review of the suite found about 180 of them redundant. It judged
each one two ways: breaking the source on purpose and seeing whether a test noticed, and comparing branch coverage with
and without the test. The redundant ones fell into four groups:

- Table rows that push different bad values through one branch, so one row proves the rule as well as ten.
- Tests of the fake `claude` on their own, which the tests that rely on it already prove.
- Tests of constants and of helpers a refactor should be free to change.
- About 40 tests that pinned exact error wording or the operating system's own message text, which ADR-007 does not
  require and which differs between Linux and macOS.

They cost upkeep, and the count made the suite look more thorough than it was.

**Decision:**

The unit suite keeps one case per distinct branch or user-visible rule, not one per ported gralph case.

- A test or row earns its place if removing it loses a branch (checked with branch coverage) or lets a deliberate bug
  survive. It is not enough that it reads as a duplicate: the review found three tests it wrongly called duplicates, and
  branch coverage caught each.
- A test asserts the exception type and the field, flag, task, or path the message must name. It does not assert the
  full sentence, hints, or operating system text.
- The fake `claude` is tested through the code that uses it. A test of the fake itself stays only for behavior no other
  test exercises.
- A test written for a real bug stays, with a name or comment that says which bug. Tests that look like duplicates but
  guard such a bug (the process tests for a descendant that holds a pipe open) are judged by re-breaking the fix, not by
  reading.
- A prune is checked by comparing branch coverage of the whole suite before and after, and by a set of deliberate bugs.
  Reading alone is not enough.

This amends ADR-006: gralph's tests still guide the port and define what a user sees, but they are not copied row for
row. It does not change ADR-010: the e2e suite stays black-box and separate.

**Consequences:**

*Positive:*

- A smaller, faster suite that is cheaper to change and does not break on rewording a message
- A test count that says more about what is covered
- A way to check any future prune that does not depend on judgment

*Negative:*

- Less direct traceability from a `alr` test to the gralph test it came from
- Branch coverage does not prove two inputs expose the same bug: an input that reaches an existing branch can still
  reveal a different fault (a byte order mark in a settings file is one), so such a case needs its own row
- A few small checks, such as the leading whitespace of the first line of a gate command, are no longer pinned

### ADR-012: The TUI runs the engine in-process as a Textual worker

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

Step 2 adds a full-screen view. The engine is `asyncio`, Textual runs on `asyncio`, and the stream front end already
reports each run event to a `report` callback. The view has to start a run, show it live, and stop it. Three ways to
host the engine were weighed: an async worker in the app's own event loop, the engine as a child process printing JSON
lines, and the engine in a separate thread.

**Decision:**

Run the engine as an async Textual worker in the app's own event loop.

- An adapter in `alr.tui` is the `report` callback. It turns each `alr.stream.events` value into a Textual message. It
  holds only the function that posts messages, never the app or a widget, so the engine's front end cannot pin the app
  in memory.
- Each pane listens for those messages and knows nothing about the engine.
- **Stop** and Ctrl-C both cancel the worker. Textual receives Ctrl-C as a key press, so the app binds it to Stop. The
  engine's cancel path reports `RunDone` with an error, and a clean finish reports `None`. The app decides the outcome
  from `RunDone.error` and its own stop flag: a run the user stopped is "stopped", any other error is "failed", and
  `None` is "done". The text of a cancelled run's error varies (`task 1: name: stopped`, or a bare `stopped` between
  tasks), so it is never matched. SIGTERM also stops the run.
- During a run the only control is Stop. Pause, skip, and retry would change how the engine records task state, so they
  are left for a later design.

**Consequences:**

*Positive:*

- The least new code: the events and the stream front end already exist
- One event loop, so the engine's process handling and cancellation behave as they do in plain mode
- The same adapter can feed the agent layer later

*Negative:*

- Ctrl-C needs a binding, and the cancel path needs a test under Textual's own input handling
- A bug that blocks the event loop freezes the screen, as it would any Textual app
- No pause, skip, or retry yet

### ADR-013: Setup in modal dialogs over one main window, with an explicit Start

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

Gralph's setup wizard is a separate sequence of full-screen steps (its ADR-018 and ADR-021). The wanted view is one
window that is always there, with the setup as pop-ups, and a run that begins only when the user presses Start.

**Decision:**

One main window and modal dialogs on top of it.

- **Layout.** A settings strip (commit mode dropdown, a permissions label, a logging switch, the Start button, and the
  reason Start is disabled), a menu (Folder, Permissions, Gates, Tasks, Sandbox, and Quit buttons, since Textual has no
  menu-bar widget), three panes (tasks, activity for the selected task, and gates and commits), and a status bar.
- **States.** Setup (something is missing, Start disabled with the reason shown), Ready, Running (Start becomes Stop,
  the settings are locked), and Finished (a summary pop-up with the outcome and time, then the app stays open until
  quit). There is no second run in the same session.
- **When dialogs open.** At launch, only for a step the flags and the task file leave open: folder, permissions, then
  gates. A folder given on the command line, a folder `sandbox.json`, or a task file with `shared.gates` each hides its
  step. If nothing is open, the app starts in Ready. Until Start, any dialog can be reopened from the menu.
- **Folder dialog.** A folder picker that starts in the working directory and shows hidden entries. It checks the answer
  with the functions a run uses: it loads `tasks.yaml`. A folder outside a git work tree is accepted: the run then makes no
  commits (ADR-014). The error for a bad answer shows in the dialog.
- **Permissions.** Sandbox or skip. A folder `sandbox.json` makes sandbox the default. With no such file nothing is
  pre-selected and Start stays disabled until a settings file is picked or skip is chosen, so nobody runs unsandboxed by
  accident (gralph ADR-014). An explicit skip wins over the folder file (ADR-009).
- **Gates dialog.** Add and remove the shared gates, each a command and an optional timeout; a gate is changed by
  removing it and adding it again. Done with no gates is a decision that there are none.
- **File viewer.** The menu opens `tasks.yaml` or `sandbox.json` read-only, at any time. Once Start is pressed the
  Folder, Permissions, and Gates buttons do nothing.
- **Nothing is written before Start.** Gate edits stay in memory. Start runs the usual startup checks, and the gates are
  saved to `tasks.yaml` only if they pass. The dialogs fill the same values the flags fill, and the startup checks run
  again on those values.
- **When the view is used.** `alr` opens the TUI when stdin and stdout are terminals, `--plain` is absent, and the run
  is not a `--dry-run`. Otherwise, it runs plain mode, as before.

**Consequences:**

*Positive:*

- Nothing starts by accident, and the settings are visible together
- A typo in a gate or a wrong folder is fixed without restarting
- The dialogs reuse the run's own checks, so the view cannot accept what a run would refuse

*Negative:*

- A new third-party dependency, Textual, to track and scan
- A second run needs a relaunch
- A folder that holds a `sandbox.json` can still be run unsandboxed from the permissions setting, by an explicit choice

### ADR-014: Commits in the TUI: per task or after all tasks, when there is a work tree

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

The engine commits per task when `--commit` is passed, and not at all otherwise. The wanted view commits whenever it
can, and offers a choice of one commit per task or one commit when every task is done. The second mode does not exist.
Requiring a git repository for the view would be too limiting, and plain mode already runs without commits when
`--commit` is passed outside one.

**Decision:**

Add an engine commit mode, `per_task` (the existing behavior) or `after_all`.

- **After all.** Each task still runs its session and gates as before. When the last task has completed, the engine runs
  `git add -A` and one `git commit`, with the first line `alr: <n> tasks completed` and a body listing `<id>: <name>`
  for each task. A failed or stopped run makes no commit, so the work stays in the tree.
- **Same checks.** The startup checks are the ones `--commit` has: a clean work tree, and a task file that is ignored or
  outside the repository.
- **Flags.** `--commit` keeps its meaning, one commit per task. A new `--commit-after-all` asks for the second mode.
  Passing both is a usage error. Plain mode accepts both, since the engine is shared.
- **In the TUI** the commit setting is a choice of the two modes, per task by default, with no way to turn commits off
  while there is a work tree. A passed flag pre-selects its mode.
- **No work tree.** When the working directory is not inside a git work tree there is nothing to commit to. The setting
  is disabled with the note "no git repository: nothing will be committed", and the run makes no commits, as plain mode
  does with `--commit` outside a repository.

**Consequences:**

*Positive:*

- One commit per feature is possible without squashing afterward
- `--commit` and every command line that uses it behave as before

*Negative:*

- After a failed or stopped run, the completed tasks' work is uncommitted, and the dirty-tree check blocks the next
  start until it is committed or discarded
- Outside a git work tree the view runs but commits nothing, so the changes stay in the folder; only the note beside the
  commit setting says so
- Resuming an after-all run after committing the earlier work by hand gives a commit title with a smaller `n` and a
  body that lists only the tasks that ran in the resumed session

### ADR-015: Logging is a toggle, on by default; the gate timeout has no dialog

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

`--log-dir` is refused on a real run until the view exists. Gralph asks about logging and about a default gate timeout
as wizard steps. Since the TUI commits whenever it is inside a work tree (ADR-014), a log folder inside the repository would dirty the
tree unless it is git-ignored.

**Decision:**

- **Logging.** A toggle in the settings strip, on by default, writing the run log to `<folder>/logs`. An explicit
  `--log-dir` wins and turns the toggle on with that path. Inside a work tree, Start is blocked, with the line to
  add to `.gitignore`, when the log folder is not ignored; outside one there is nothing to ignore. Plain mode still refuses `--log-dir`.
- **Gate timeout.** No dialog and no setting. Each gate keeps its own optional timeout, edited in the Gates dialog, and
  the 10-minute default covers the rest. `--gate-timeout` still works when passed.

**Consequences:**

*Positive:*

- A finished run can be looked at again through its logs
- The strip and the dialogs stay small

*Negative:*

- A folder that is not ready for logs needs a `.gitignore` edit before the first run, or the toggle off
- Setting one default timeout for every gate needs the flag, or editing each gate

### ADR-016: `--install-skill` installs the bundled skill and exits

[Back to Decision Summary](#decision-summary)

**Status:** Accepted

**Context:**

Gralph v0.12.0 has `--install-skill`, which copies its embedded `gralph-docs-writer` skill into `~/.claude/skills/`. It
no longer checks the install afterward (gralph ADR-022). `alr` reads the same `tasks.yaml`, so the same skill applies.

**Decision:**

- `alr --install-skill` copies the bundled `gralph-docs-writer` skill folder (taken from gralph v0.12.0) to
  `~/.claude/skills/gralph-docs-writer/`, replacing any existing copy, prints the path, and exits 0. It runs before the
  other startup checks and opens no screen. An error finding the home folder or writing the files exits 1.
- The skill ships as package data inside the wheel.
- Nothing compares the installed copy with the bundled one later, so a missing or edited skill never stops a run.

**Consequences:**

*Positive:*

- One command sets up the helper that writes task files
- The behavior matches gralph v0.12.0

*Negative:*

- The bundled copy has to be refreshed by hand when gralph's skill changes
- An installed skill can go stale without any warning

**Next:** [System Architecture](03_system_architecture.md)
