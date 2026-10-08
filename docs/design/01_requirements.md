---
version: 14
date: 2026-10-08
notes: "Item 12 adds the ticking status line and the Tasks pane's Time column (ADR-020)."
---

# alr — Requirements

[Back to Overview](00_overview.md) | [Back to Project README](../../README.md)

## Table of Contents

- [Problem Statement](#problem-statement)
- [Behavioral Reference](#behavioral-reference)
- [Goals](#goals)
- [Non-Goals](#non-goals)
- [Command Line](#command-line)
- [Success Criteria](#success-criteria)
- [Constraints](#constraints)
- [Assumptions](#assumptions)

## Problem Statement

gralph, the Go loop runner, has reached the point where its next step is to become a full agent. The frameworks for that (LangGraph, CrewAI) are Python only. The runner therefore has to exist in Python before the agent work can start, and it has to behave exactly like gralph so nothing built around gralph breaks on the way.

`alr` already exists in this repository as a small learning project with a simpler task model. That code is replaced by the port. The project keeps the name `alr`.

## Behavioral Reference

gralph v0.12.0 (commit `433a5c1`) is the reference for behavior. The first version of these documents was written against `v0.9.8-25-g170dc6f`; three gralph releases since then (v0.10.0, v0.11.0, v0.12.0) changed the task file, the command line, and the run folder, and this version follows them. Where this document set is silent, gralph's own documents and code at that tag decide:

- gralph's `docs/architecture/01_requirements.md` (v14), `02_architectural_decisions.md`, `03_system_architecture.md` (v25)
- `docs/howto.md`
- `src/internal/tasks`, `src/internal/looper`, `src/internal/runlog`, `src/cmd/main`, and `tests/e2e`

The gralph decisions that carry over are ADR-001 to ADR-007, ADR-013 to ADR-016, ADR-019, ADR-020, and ADR-021, with ADR-002, ADR-014, ADR-016, and ADR-019 amended by the later ones. See [Architectural Decisions](02_architectural_decisions.md).

## Goals

### Primary Goals

1. Run an ordered list of tasks, each in a fresh `claude --print` session, with `shared.prompt` and the task on stdin in gralph's exact text format
2. Parse and validate the one task file with gralph's field rules and clear error messages that name the task and field: a required `shared` block (a required `prompt` and optional `gates`), and `tasks` with `id`, `name`, `prompt`, optional `state` (`pending`, `completed`, `failed`) and `error`. A top-level `gates` key or any other unknown top-level or `shared` key is an error
3. Decide a session's outcome from the last non-blank, non-fence line of its output, as gralph does
4. Run the file's `shared.gates` after a completed session, each with a time limit, and complete the task only when every gate exits zero
5. Commit a completed task on request (`--commit`) with gralph's startup checks and commit rule
6. Build the session flags from `--sandbox-settings`, a `sandbox.json` found in the `--dir` run folder, or `--skip-permissions`, with gralph's forced sandbox keys; require one choice on a real run
7. Save the task file atomically after every task; stop the run on the first failed task; refuse to start when the file already holds a failed task
8. Stop cleanly on SIGINT or SIGTERM: kill the running process group, leave the task and the file untouched, exit 1
9. Support `--dry-run` with gralph's plain-text report, never starting Claude or writing a file
10. Keep the logic that only plain mode runs clearly apart from the logic that only the full-screen path runs, with the shared rules in one place
11. Port the full-screen path's engine side now: stream-json parsing, run events, and the run log writer, tested against a fake `claude`
12. Open a Textual full-screen view on a terminal, with one main window of three panes (tasks and gates and commits on top, the running task's activity full width below), a settings strip with Commit and Logging dropdowns, a menu whose button names show their hotkeys (no footer), and a status bar; elapsed times show hours, minutes, and seconds. The Task pane (activity lines) keeps the last 5,000 lines per task (the run log keeps every line), and the Gates button does nothing while a run goes. While a run goes, a status line between the top panes and the Task pane says where the run is and ticks every second (`task 2 of 5 · gate 1 of 3: uv run pytest · 12.3s · run 1m 4s`), and the Tasks pane has a Time column that counts the running task up and keeps each finished task's time
13. Start a run only when the user presses Start, with Start disabled and the reason shown while something is missing
14. Ask for a missing folder, permission choice, or gate list in modal dialogs over the main window, using gralph's wizard rules, and let any dialog be reopened until Start
15. Open `tasks.yaml` and `sandbox.json` read-only from the menu
16. Offer a commit setting of one commit per task (the default) or one commit after all tasks, with commits always on inside a git work tree, and run without commits (with a note) outside one
17. Offer a permissions setting of the folder's `sandbox.json` (the default when it exists) or skip permissions, never defaulting to unsandboxed
18. Log every real run to `<folder>/logs/<timestamp>/` in both modes: plain mode always, the view through a Logging dropdown that is on by default. When the run commits inside a git work tree, refuse to start (plain mode) or block Start (the view) if that folder is not git-ignored. If two runs start in the same second, the later run's folder gets a `-1` suffix (then `-2`), and the final name is not reported to the caller
19. Stop a running view cleanly from a Stop button or Ctrl-C, and install the bundled skill with `--install-skill`
20. Run the view entirely from the keyboard, without capturing the mouse: every action, including the setup dialogs, has a key
21. Open a task file that holds failed tasks in the view and reset them to pending from the menu after a confirmation, or silently with `--reset-failed` in either mode

### Secondary Goals

1. Give the later Textual view and agent layer a surface they can use without changing the engine
2. Keep the existing project scaffolding: uv, the Makefile, the Docker-first build, and the CI workflow
3. Type-hint every function

## Non-Goals

- Pause, skip, and retry controls in the view. Stop is the only control during a run (ADR-012)
- A second run in the same view session. A new run means a new launch
- Editing `tasks.yaml` or `sandbox.json` in the view. The file viewer is read-only
- A default gate timeout setting in the view (ADR-015)
- Checking the installed skill at startup. gralph no longer does (its ADR-022)
- Any agent framework, or any change to behavior aimed at the future agent
- Changing what a user sees or types. Differences in flags, files, or run behavior are bugs unless listed under [Command Line](#command-line). Internal behavior and error wording are free to differ (ADR-007)
- Byte-identical task file output. The saved file is equivalent YAML (same keys, values, and order; 2-space indent), not the exact bytes Go's YAML library writes
- Byte-identical `--help` text
- Windows support
- Everything gralph lists as a non-goal: retries, shared context between sessions, an agent abstraction, pushing, default logging

## Command Line

The flags below are accepted, with gralph's meaning and startup order.

| Flag                        | Short | Notes                                                                                   |
| --------------------------- | ----- | --------------------------------------------------------------------------------------- |
| `--version`                 | `-v`  | Prints the version                                                                      |
| `--dir <folder>`            | `-d`  | Run folder holding `tasks.yaml` and, optionally, `sandbox.json`; required in plain mode, and the view asks when it is missing; `--sandbox-settings` overrides the settings file |
| `--dry-run`                 |       | Validate and report only. The old alr short form `-d` now means `--dir`, as in gralph   |
| `--gate-timeout <duration>` |       | Duration such as `90s`, `10m`, or `1h30m`; overrides every gate's own timeout            |
| `--plain`                   |       | Runs plain mode on a terminal. Without it, a terminal run opens the full-screen view   |
| `--sandbox-settings <path>` |       | Sessions run in Claude's sandbox with this settings file; found by itself as `<folder>/sandbox.json` under `--dir` when neither permission flag is passed |
| `--skip-permissions`        |       | Sessions run with `--dangerously-skip-permissions`; wins over a folder's `sandbox.json` with no error |
| `--commit`                  |       | Commit each completed task after its gates pass; pre-selects per task in the view        |
| `--reset-failed`            |       | New in alr. Sets every failed task back to pending before the run, silently, in plain mode and in the view; with `--dry-run` is a usage error |
| `--commit-after-all`        |       | New in alr. One commit after the last task completes; none on a failed or stopped run; pre-selects after all in the view; with `--commit` is a usage error |
| `--install-skill`           |       | Copies the bundled `gralph-docs-writer` skill to `~/.claude/skills/` and exits 0         |

Known differences from gralph in this step:

- The command is `alr`, and messages that name the command say `alr`
- `--commit-after-all` and `--plain` exist only in `alr`
- `-t/--tasks` is not accepted: it is an unknown-flag error, and the task file is always `<folder>/tasks.yaml` (ADR-017). Gralph command lines that use `-t` must use `-d`
- `--log-dir` is not accepted: it is an unknown-flag error, and the run log always goes to `<folder>/logs` (ADR-019)
- `--no-tui`, gralph's flag for plain output, is not accepted: it is an unknown-flag error, and `--plain` replaces it
- `-p/--prompt` is not accepted. It is an unknown-flag error, as in gralph v0.11.0 and later
- Inside a git work tree the view always commits, and offers per task or after all tasks as its two choices; outside one it runs without commits and says so; plain mode still commits only with a flag
- The view's dialogs differ from gralph's wizard: modal pop-ups over a main window, a Start button, a commit setting in the strip rather than a yes/no step, a Logging dropdown on by default, no gate-timeout step, and a read-only file viewer
- `--help` text is generated by Python's `argparse` and is not word-for-word gralph's
- Error messages are worded by `alr` and need not match gralph's
- YAML is read with PyYAML's standard rules: an unquoted `yes`, `on`, or `12:30` is not a string and must be quoted, and `<<` merge keys are rejected
- The result line's keys must be the exact lowercase `state` and `error`; gralph also accepted other letter cases

## Success Criteria

| Criterion              | Target                                                                                          | Measurement Method                                                          |
| ---------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Task file validation   | Same field rules as gralph, including the `shared` block and the rejected keys, with clear errors naming the task and field | Ported `task_test.go` cases                                  |
| Wire contract          | Stdin text and Claude argv identical to gralph's                                                | Golden test on stdin; argv recorded by the fake `claude`                    |
| Outcome rule           | Same state and error for every result-line case                                                 | Ported `result_test.go` and `claude_result_test.go` cases                   |
| Gate enforcement       | Completed only when every gate exits zero; timeouts honored; gates skipped on a failed session  | Ported gate unit and e2e tests with real shell commands                     |
| Session permissions    | Sandboxed argv with the three forced keys, or the bypass flag; neither or both exits 1; a folder's `sandbox.json` answers the choice, and `--skip-permissions` overrides it | Ported sandbox unit and e2e tests |
| Commit on request      | One commit per completed task; none for a failed task; a dirty tree refuses to start            | Ported commit tests against real git repositories in temporary folders     |
| State persistence      | Atomic save after every task; file untouched on cancel                                          | Ported e2e tests check the file after each run                              |
| Signal handling        | SIGINT and SIGTERM kill the whole process group; exit 1; no orphans                             | Ported signal e2e tests with the fake `claude` and a descendant process     |
| Exit codes             | Zero only when every task completed                                                             | Ported e2e matrix                                                           |
| Run log                | Same ledger lines and detail files as gralph for the same events; `run_started` has no `prompt_file` | Ported `runlog_test.go` cases                                          |
| Path separation        | `engine` imports neither `plain` nor `stream`; `plain` and `stream` do not import each other    | A test that inspects the imports                                            |
| View states            | Start disabled with a reason while something is missing; Running locks the settings and greys out the Folder, Permissions, Reset, and Quit buttons; Finished shows a summary and stays open | TUI unit tests under Textual's `run_test` and `Pilot`          |
| Dialog rules           | A dialog opens only for an open step; nothing is written before Start except a confirmed reset | TUI unit tests                                                     |
| Reset                  | A file with failed tasks opens with Start disabled and the reason shown; a confirmed reset saves them pending with no error text; `--reset-failed` does it silently and exits 1 with `--dry-run` | Engine, CLI, and TUI unit tests; e2e                |
| Commit modes           | One commit per task, or one commit after the last task; none after a failed or stopped run; both flags together exit 1 | Unit tests against real git repositories; e2e                  |
| Stop                   | Stop and Ctrl-C end the run and report `stopped`; no orphan processes                          | TUI unit tests; one pseudo-terminal e2e test                                |
| View happy path        | A run in a pseudo-terminal reaches Finished and a clean exit                                    | One pseudo-terminal e2e test in the container                               |
| Skill install          | The bundled skill lands in `~/.claude/skills/gralph-docs-writer/`, replacing any copy           | Unit test with a temporary home; e2e                                        |
| Keyboard only          | The mouse is not captured; the whole setup and a run can be done with keys alone                | TUI unit test of the keyboard-only flow                                     |
| Build                  | `make test` (analyze and unit tests) and `make build` (wheel, then e2e in a container) pass     | CI                                                                          |

## Constraints

### Technical Constraints

- **Python 3.12 or later**, managed with uv, `src/` layout
- **Runtime dependencies: PyYAML and Textual.** `pydantic` is removed. Dev dependencies: pytest, pytest-asyncio, ruff
- **Standard library for the rest**: `asyncio` for subprocesses and cancellation, `argparse` for flags, `json`, `pathlib`
- **Unix process API**: process groups and signals; Linux and macOS only
- **The `claude` CLI on `PATH`**, called with a fixed argv and the prompt on stdin
- **No injectable runner**: tests put a fake `claude` first on `PATH`, as gralph's do
- **No inline lint or security suppressions**

### Business Constraints

- **Emerging maturity**: breaking changes are expected until the port reaches parity with gralph
- **The gralph-docs-writer skill** keeps producing the one task file, shared prompt included; the task field rules must stay in sync with it

### Organizational Constraints

- **Claude Code only**, inherited from gralph and intentional
- **No retry**, inherited from gralph
- **PRs target `develop`**

## Assumptions

| Assumption                                                                 | Impact if Wrong                                                    | Validation Plan                                                  |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| gralph v0.12.0 is the gralph behavior to match                             | The port matches a version that changes before it merges           | Re-diff gralph's engine packages against `v0.12.0` before release |
| Equivalent YAML is enough; nothing depends on the exact saved bytes        | A tool that diffs task files byte for byte would see changes       | Round-trip tests compare parsed content                          |
| PyYAML's node API can enforce gralph's tag and integer rules               | Validation would need a different YAML library                     | The first ported task tests cover these cases                    |
| Go's exit-error wording (such as `exit status 1`) can be reproduced        | Task `error` text would differ from gralph's                       | Ported gate and commit tests assert the strings                  |
| Textual can host the async engine as a worker in its own event loop       | The TUI would need a thread or a child process (ADR-012)           | Textual's worker and `run_test` documentation checked; proven by the first TUI run test |
| The agent framework can consume an async engine without change            | The agent design would have to reopen the engine                   | Checked when the agent design is written                         |

**Next:** [Architectural Decisions](02_architectural_decisions.md)
