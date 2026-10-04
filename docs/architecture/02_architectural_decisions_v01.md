# alr — Architectural Decisions

> **Version**: v01
> **Date**: 2026-10-04
> **Notes**: Initial version. Decisions for porting gralph's engine to Python.

[Back to Overview](00_overview_v01.md) | [Back to Project README](../../README.md)

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

## Inherited Decisions

These gralph decisions apply to `alr` unchanged. Their full text is in gralph's `docs/architecture/02_architectural_decisions.md` at commit `170dc6f`.

| gralph ADR | Title                                                          |
| ---------- | -------------------------------------------------------------- |
| ADR-001    | Claude Code only, no agent abstraction                         |
| ADR-002    | YAML task file + shared prompt, strict parsing                 |
| ADR-003    | One fresh session per task, no retry                           |
| ADR-004    | The runner owns task state, atomic writes                      |
| ADR-005    | Outcome from JSON result line, not exit code                   |
| ADR-006    | Failed task blocks run until manual reset                      |
| ADR-007    | Unix only, process group lifecycle management                  |
| ADR-013    | The runner runs the gates after a completed session            |
| ADR-014    | Sessions run in Claude Code's sandbox; bypass only on request  |
| ADR-015    | The runner commits a completed task on request                 |
| ADR-016    | The runner logs a run on request                               |
| ADR-019    | One gate list per task file                                    |

gralph's ADR-008 (Docker-first CI) is already met by this repository's own build. ADR-009 to ADR-012, ADR-017, and ADR-018 cover the skill, the TUI, releases, and the wizard, and are left to later designs.

## Decisions

### ADR-001: Port gralph to Python, drop-in compatible

**Status:** Accepted; what "drop-in compatible" covers is narrowed by ADR-007

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

**Status:** Accepted

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
- No new code is added only to imitate a Go library. The small helpers already in `alr.gocompat` stay because they are tested and the saved `error` text has to say something

The saver keeps two behaviors because they serve `alr`'s own users: multi-line text is written as a `|` block, and lists are indented under their key.

**Consequences:**

*Positive:*

- Less code, and none that exists only to mirror another library
- The task file behaves the way anyone who knows YAML and PyYAML expects

*Negative:*

- A gralph task file with an unquoted name such as `yes` or `12:30` needs quotes added before `alr` accepts it
- Error text can drift from gralph's, so the Go tests are a guide to behavior and no longer an exact oracle for messages

**Next:** [System Architecture](03_system_architecture_v01.md)
