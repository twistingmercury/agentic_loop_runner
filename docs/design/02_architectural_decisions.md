---
version: 3
date: 2026-10-06
notes: "Fixed the path to gralph's own decision log, which a bulk rename had pointed at this repository's docs/design."
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

| ADR # | Title                                                  | Status   | Date       |
| ----- | ------------------------------------------------------ | -------- | ---------- |
| 001   | Port gralph to Python, drop-in compatible              | Accepted | 2026-10-04 |
| 002   | Engine first; the TUI is the next design               | Accepted | 2026-10-04 |
| 003   | An async engine                                        | Accepted | 2026-10-04 |
| 004   | One engine, two front ends                             | Accepted | 2026-10-04 |
| 005   | PyYAML nodes and hand-written validation, no pydantic  | Accepted | 2026-10-04 |
| 006   | Port the tests first, with a fake claude on PATH       | Accepted | 2026-10-04 |
| 007   | Compatible in use, Python-native inside                | Accepted | 2026-10-04 |
| 008   | The shared prompt and gates live in the task file      | Accepted | 2026-10-06 |
| 009   | The run folder's sandbox.json answers the permission choice | Accepted | 2026-10-06 |
| 010   | Unit tests under `make test`, e2e tests in a container | Accepted | 2026-10-06 |

## Inherited Decisions

These gralph decisions apply to `alr` unchanged. Their full text is in gralph's `docs/architecture/02_architectural_decisions.md` at v0.12.0. Where a later gralph ADR amends one of them, the last column says which `alr` ADR carries the change.

| gralph ADR | Title                                                          | Amended by                |
| ---------- | -------------------------------------------------------------- | ------------------------- |
| ADR-001    | Claude Code only, no agent abstraction                         |                           |
| ADR-002    | YAML task file + shared prompt, strict parsing                 | ADR-008: one file         |
| ADR-003    | One fresh session per task, no retry                           |                           |
| ADR-004    | The runner owns task state, atomic writes                      |                           |
| ADR-005    | Outcome from JSON result line, not exit code                   |                           |
| ADR-006    | Failed task blocks run until manual reset                      |                           |
| ADR-007    | Unix only, process group lifecycle management                  |                           |
| ADR-013    | The runner runs the gates after a completed session            |                           |
| ADR-014    | Sessions run in Claude Code's sandbox; bypass only on request  | ADR-009: found in the run folder |
| ADR-015    | The runner commits a completed task on request                 |                           |
| ADR-016    | The runner logs a run on request                               | ADR-008: no `prompt_file` |
| ADR-019    | One gate list per task file                                    | ADR-008: `shared.gates`   |
| ADR-020    | The shared prompt and gates live in the task file              | ADR-008 is `alr`'s version |
| ADR-021    | The run folder's sandbox.json answers the permission choice    | ADR-009 is `alr`'s version |

gralph's ADR-008 (Docker-first CI) is already met by this repository's own build. ADR-009 to ADR-012, ADR-017, and ADR-018 cover the skill, the TUI, releases, and the wizard, and are left to later designs. gralph's ADR-022 (no check of the installed skill) supersedes its ADR-010 and needs nothing here: `alr` has no skill check, and the next design's `--install-skill` will not add one. gralph's ADR-018 is partly ported already: `-d` finds `tasks.yaml` (and, by ADR-009, `sandbox.json`); the wizard is left to the next design.

## Decisions

### ADR-001: Port gralph to Python, drop-in compatible

**Status:** Accepted; what "drop-in compatible" covers is narrowed by ADR-007, and its reference version moves to gralph v0.12.0 under ADR-008 and ADR-009

**Context:**

The runner is meant to grow into a full agent on a Python framework such as LangGraph or CrewAI. gralph is Go. `alr` was started as a simpler Python runner written by hand as a learning project, with a different task model. Three levels of faithfulness were considered: drop-in compatible, the same behavior with warts tidied, and a rethink shaped around the future agent.

**Decision:**

Port gralph to Python in this repository, replacing the existing `alr` code and keeping the name `alr`. The port is drop-in compatible: same flags, task file format, result line, gates, commit, sandbox behavior, run log, and error text. gralph at commit `170dc6f` is the behavioral reference. The code is written by Claude and reviewed by the owner; the earlier rule that the owner writes every line no longer applies.

Tidying was rejected because every tidy-up is a compatibility decision that slows the port. A rethink was rejected because it has no acceptance tests and guesses at what the agent will need.

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

**Status:** Accepted

**Context:**

gralph is about 14,700 lines of Go, roughly half tests. It includes a full-screen view and a setup wizard on Bubble Tea. The owner wants a TUI, and a more capable one than the Go version, because plain command-line use is tedious. Porting everything at once makes one very large change to review.

**Decision:**

Split the work. This design covers the task file, the engine, plain mode, dry-run, and the engine side of the full-screen path (stream-json parsing, events, the run log). The Textual view, the wizard, `--install-skill`, and the skill check are the next design.

Until the view exists, `alr` always runs plain mode: `--no-tui` is accepted and does nothing, and `--log-dir` on a real run gives gralph's plain-mode error.

**Consequences:**

*Positive:*

- Each step is small enough to review
- The TUI is designed for Textual's strengths instead of being a line-by-line copy of the Bubble Tea view
- The engine the TUI and the agent build on is finished and tested first

*Negative:*

- The stream path and the run log are built and tested but not reachable from the command line until the next step
- `alr` is not a full replacement for gralph in a terminal until the TUI lands

### ADR-003: An async engine

**Status:** Accepted

**Context:**

The engine runs subprocesses, reads two pipes at once on the stream path, enforces gate timeouts, and must stop cleanly on a signal. gralph does this with goroutines and context cancellation. In Python the choice is `asyncio` or synchronous code with threads. The planned TUI library (Textual) is built on `asyncio`, and LangGraph is designed for async use.

**Decision:**

Build the engine on `asyncio`: `asyncio` subprocesses, task cancellation for stopping, and loop signal handlers for SIGINT and SIGTERM. A synchronous port with reader threads was rejected: it would be simpler to read today, but the TUI and agent layer would have to run it in a worker thread, and a thread blocked on a subprocess cannot be cancelled without hand-built machinery.

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

**Status:** Accepted

**Context:**

gralph has two ways to run a task, picked by whether a `report` hook is set. The plain path prints and passes output through. The stream path captures output and reports events. The choice is checked inside the looper, the gate runner, and the committer, so plain-only and TUI-only logic are mixed into the same functions. The owner wants a clean, clear line between what the plain path runs and what the TUI path runs.

**Decision:**

Split the package three ways. `alr.engine` holds the shared rules and never prints. `alr.plain` holds only what plain mode does. `alr.stream` holds only what the full-screen path does. The engine is handed a front end object and calls it to run a session, a gate, or a git command and to hear about progress. `alr.cli` picks the front end once.

Import rules, enforced by a test: `engine` imports neither `plain`, `stream`, nor `tui`; `plain` and `stream` do not import each other.

**Consequences:**

*Positive:*

- `plain/` can be read top to bottom to see exactly what a no-TUI run does
- The outcome rule, gate rule, commit rule, and save happen in one place, so the two paths cannot drift apart
- The TUI and the agent layer plug in without changing the engine

*Negative:*

- One abstraction, the front end interface, that gralph does not have
- Files do not map one-to-one onto gralph's, so comparing against the Go source takes more care

### ADR-005: PyYAML nodes and hand-written validation, no pydantic

**Status:** Accepted

**Context:**

gralph validates the task file on raw YAML nodes: it rejects any tag outside the core set, requires `id` to be a real YAML integer, detects duplicate keys, and reports errors as `tasks[<index>] (id <id>): <field>: <problem>`. The old `alr` used pydantic models. Matching gralph's rules and exact error text through pydantic means fighting its own error format.

**Decision:**

Parse with PyYAML's node API (`yaml.compose`), validate with hand-written checks, and hold the result in plain dataclasses. Remove pydantic. Parse flags with the standard library's `argparse`, with prefix abbreviation turned off.

**Consequences:**

*Positive:*

- Validation rules and error text can match gralph exactly
- One runtime dependency
- The checks port almost directly from `task.go`

*Negative:*

- More validation code than a schema library would need
- `--help` text differs from gralph's

### ADR-006: Port the tests first, with a fake claude on PATH

**Status:** Accepted; its last sentence is amended by ADR-010

**Context:**

Drop-in compatibility needs proof. gralph has unit tests per package and a black-box e2e suite that runs the real binary against a scriptable fake `claude` placed first on `PATH` and driven by `FAKECLAUDE_*` environment variables.

**Decision:**

For each module, port gralph's tests to pytest first, then write the code to pass them. Port the fake `claude` as a Python script that honors the same environment variables. Port the e2e scenarios as pytest tests that run the installed `alr` command as a subprocess. The wizard, TUI, install-skill, and skill-check tests are left to the next design. All tests run under `make test` and inside the Docker build.

**Consequences:**

*Positive:*

- gralph's tests are the definition of compatible
- No real Claude and no network in any test
- The e2e suite exercises the real command line, signals included

*Negative:*

- A large amount of test code to port before features appear
- A gralph test that asserts Go-specific detail needs judgment to translate

### ADR-007: Compatible in use, Python-native inside

**Status:** Accepted

**Context:**

ADR-001 called the port "drop-in compatible" and listed error text among the things that must match. The first five tasks read that strictly: the task file loader was given a custom YAML resolver so plain values such as `yes` and `12:30` load the way gralph's Go YAML library loads them, and the saver was taught to quote strings that library would read as numbers. The owner then clarified the intent. `alr` replaces gralph; the two are not meant to run side by side on the same files. "Drop-in" was about what a person using the tool sees and types, not about reproducing how the Go code represents things inside.

**Decision:**

Compatibility covers the surface a user works with:

- The command-line flags and what they mean
- The task file format and its field rules, the shared prompt, and the text sent to Claude
- The result line and the outcome rule
- What gates, `--commit`, and the sandbox flags do
- Exit codes and the shape of plain-mode output
- The full-screen view's behavior, when it is built

Compatibility does not cover internal representation:

- YAML is read and written with PyYAML's standard safe loader and dumper rules. A plain value that YAML 1.1 reads as a boolean or a number (`yes`, `on`, `12:30`) must be quoted to be a string; `alr` rejects it with a clear message otherwise
- Error messages must be clear and name the task and field. They do not have to match gralph's wording, and a ported test that asserts Go-specific wording may be adapted
- No new code is added only to imitate a Go library. The `alr.gocompat` module from the first tasks is removed: duration parsing and formatting live in `alr.durations` because that is the task file's own format, and the few message helpers live where they are used

The saver keeps two behaviors because they serve `alr`'s own users: multi-line text is written as a `|` block, and lists are indented under their key.

**Consequences:**

*Positive:*

- Less code, and none that exists only to mirror another library
- The task file behaves the way anyone who knows YAML and PyYAML expects

*Negative:*

- A gralph task file with an unquoted name such as `yes` or `12:30` needs quotes added before `alr` accepts it
- Error text can drift from gralph's, so the Go tests are a guide to behavior and no longer an exact oracle for messages

### ADR-008: The shared prompt and gates live in the task file

**Status:** Accepted

**Context:**

The first version of these documents read the shared prompt from a second file, `prompt.md`, passed with `-p`, and kept the gates as a top-level key of the task file. gralph v0.11.0 (its ADR-020) merged them: one `tasks.yaml` holds a required `shared` block with the `prompt` and the optional `gates`, and `-p/--prompt` is gone. The two files always travelled together, the skill wrote both in one pass, and every consumer had to find, load, and report two paths. `alr` follows gralph here because ADR-001 makes gralph the reference. It also settles the "single-file input" step that the overview's roadmap listed for later.

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

- **`shared.prompt` is required.** A missing `shared` block, or a prompt that is missing, not a string, empty, or only whitespace, fails parsing. The text is trimmed on load.
- **`shared.gates` is optional, with ADR-019's rules unchanged.** Absent means not decided, `[]` means no gates, and a save keeps whichever the file had. Error names read `shared.gates[<j>]`.
- **Unknown keys are errors.** A top-level `gates` key is an error that says gates live under `shared.gates`, as is a task-level `gates`. Any other top-level key, and any `shared` key other than `prompt` and `gates`, is rejected too. Quietly ignoring an old top-level `gates` list would leave a run with no checks.
- **`-p/--prompt` is removed** with no compatibility path. Passing it is the unknown-flag error.
- **`-d <folder>` means `<folder>/tasks.yaml`.** The "no prompt.md" error goes. `-t` stays as the override for a folder holding two task files. A dry run needs nothing the real run does not, so the prompt carve-out goes.
- **The run log drops `prompt_file`** from `run_started`. `tasks_file` stays.
- **The wire contract is unchanged.** Claude gets `shared.prompt`, a blank line, the task text, then a newline. Gates and `error` are never sent.
- **A save keeps `shared`.** `shared` is written above `tasks`, with the prompt intact.
- **`PromptError` goes** with the file it described. A bad `shared.prompt` is a `TaskFileError`.

Keeping `-p` as an override was rejected: it keeps the two-file code path this removes, and the project is too young to need a compatibility path.

**Consequences:**

*Positive:*

- One file is the whole run: prompt, gates, and tasks
- One path to name, load, and report; the `-p` flag and the prompt half of the run log go away
- Room for more shared settings under `shared` without new flags

*Negative:*

- A breaking change for any `tasks.yaml` and `prompt.md` pair from before: they must be merged by hand, and a top-level `gates` moved under `shared`
- The shared prompt is YAML now: indentation matters, and it loses Markdown editor support
- A save rewrites the file and does not keep comments or the prompt's own formatting (ADR-005's saver rules)

### ADR-009: The run folder's sandbox.json answers the permission choice

**Status:** Accepted

**Context:**

gralph v0.12.0 (its ADR-021) lets a run folder carry its own sandbox settings, found by a fixed name as `tasks.yaml` is. Without it, a run has to repeat `--sandbox-settings <folder>/sandbox.json` even though the file sits beside the task file. `alr` already has `-d`, so the lookup costs little. The same safety rule as ADR-014 applies: a run never ends up unsandboxed by accident.

**Decision:**

A `sandbox.json` in a `-d` run folder is the run's `--sandbox-settings`.

- **When it applies.** `-d <folder>` was given, `<folder>/sandbox.json` is a regular file, and neither `--sandbox-settings` nor `--skip-permissions` was passed. A directory of that name is ignored. The path is `<folder>/sandbox.json` as `-d` was given, not made absolute. Only `-d` looks; `-t` alone never does, and `-d` with `-t` still uses the folder's file.
- **Precedence.** An explicit `--sandbox-settings` wins. An explicit `--skip-permissions` also wins, with no error: it is how a run opts out of a folder's sandbox. The "both flags" error is for the two explicit flags only.
- **It never leads to an unsandboxed run.** It only supplies the sandboxed choice. With no file, nothing changes: a real run still exits 1 with the neither-flag error. A found file that is unreadable or not a JSON object fails through the existing validation, exit 1, and never falls back to going without a sandbox.
- **Plain output.** Nothing new is printed. `--dry-run -d <folder>` names and validates the file on its existing `sandbox settings:` line.
- **Startup order.** The file is resolved with the task file, in the `--dir` step, so every later check sees the final value.

**Consequences:**

*Positive:*

- `alr -d .local/feat --commit` is a whole command for a folder that holds its task file and sandbox settings
- A command that names the folder reproduces the run

*Negative:*

- A settings file in a run folder is applied without being named on the command line, so it is as trusted as the folder's `tasks.yaml`, which is already code the run executes
- The only way to run a folder that holds a `sandbox.json` unsandboxed is `--skip-permissions`

### ADR-010: Unit tests under `make test`, e2e tests in a container

**Status:** Accepted

**Context:**

ADR-006 put every test under `make test` and inside the Docker build, with the e2e tests running the installed `alr` command. In practice the e2e tests shared helpers and fixtures with the unit tests, so they could pass against code a user never installs, and they ran against the working tree rather than the built wheel.

**Decision:**

Split the suites so they share nothing.

- `tests/unit/` has its own conftest, helpers, and fake `claude`. `make test` runs the ruff format and lint steps, then `pytest tests/unit`. The Docker build runs the unit tests only.
- `tests/e2e/` has its own conftest, helpers, requirements, `pytest.ini`, and fake `claude`. It finds `alr` on `PATH` and runs the real command as a subprocess. It never imports from `src/`.
- The e2e suite runs in a container (`tests/e2e/Dockerfile`, started by `tests/docker-compose.yaml`) against the wheel installed from `dist/`. `make e2e` runs it against the wheel already in `dist/`, and `make build` builds the wheel and then runs it. `build/build.sh` drives both.

This amends ADR-006's last sentence only: the tests are still ported first, the fake `claude` is still driven by `FAKECLAUDE_*` variables, and no test needs a real Claude or the network.

**Consequences:**

*Positive:*

- The e2e tests check what a user installs, not the working tree
- A helper fixed for one suite cannot quietly make the other pass
- `make test` is fast, with no Docker needed

*Negative:*

- A little test code is duplicated between the suites: the fake `claude` exists twice
- The e2e suite needs Docker and a built wheel, so it does not run under plain `make test`

**Next:** [System Architecture](03_system_architecture.md)
