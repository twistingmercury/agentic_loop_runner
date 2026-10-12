---
version: 22
date: 2026-10-11
notes: "Documents ADR-026: commits are actions; the requirements for gates, commit modes, and exit 4 say after_task, after_run, and an after_run failure, and --commit and --gate-timeout are listed as refused. Earlier: Corrects the refusal count in requirement 22 to ten reasons, as src/alr/errors.py has them (locked included). Earlier: Corrects the documents to ADR-025 as amended after the stage 1 review: a refusal leaves nothing on disk and a --dry-run refuses like a real run (exit 3, failed tasks included); the view exits with the same codes (SIGTERM 143); a stop leaves the file untouched except that a stop after a commit landed saves the tasks it covers completed; a task is completed only after its commit succeeds. Earlier: Documents ADR-025's contract: every outcome has an exit code (0, 1, 2, 3, 4, 130, 143) and a logged reason; refusals name a reason; completed means complete, and a failed or stopped run mode run leaves nothing completed. A bad flag, neither or both permission flags, and --reset-failed with --dry-run exit 2, not 1. Earlier: The commit-modes criterion says a bare --commit and --commit-after-all are usage errors that exit 2, not exit 1 (ADR-024). Earlier: the commit flags are one flag, --commit {none,task,run}, default none, and --version also shows the OS and architecture (ADR-024); the flag table, the view's commit setting, and the commit-modes criterion say so. Earlier: removes the known differences from gralph list: alr no longer aims for gralph compatibility (ADR-022), and the CHANGELOG records breaks. The agent is a separate program that uses alr as a tool (ADR-022). --yolo replaces --skip-permissions with no alias (ADR-021)."
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

gralph, the Go loop runner, was to become a full agent. That agent is now a separate program, built later, that uses `alr` as a tool: it runs `alr --plain` and reads the exit code and the run log. The runner therefore has to exist as a Python command-line tool first, and Python fits the Textual view and the asyncio engine. The port began by matching gralph's behavior; `alr` has since moved on from gralph (ADR-022).

`alr` already exists in this repository as a small learning project with a simpler task model. That code is replaced by the port. The project keeps the name `alr`.

## Behavioral Reference

gralph v0.12.0 (commit `433a5c1`) was the starting reference for the port's behavior (ADR-001, ADR-022). The first version of these documents was written against `v0.9.8-25-g170dc6f`; three gralph releases since then (v0.10.0, v0.11.0, v0.12.0) changed the task file, the command line, and the run folder, and this version followed them. Where this document set is silent about an inherited detail, gralph's own documents and code at that tag decide:

- gralph's `docs/architecture/01_requirements.md` (v14), `02_architectural_decisions.md`, `03_system_architecture.md` (v25)
- `docs/howto.md`
- `src/internal/tasks`, `src/internal/looper`, `src/internal/runlog`, `src/cmd/main`, and `tests/e2e`

The gralph decisions that carry over are ADR-001 to ADR-007, ADR-013 to ADR-016, ADR-019, ADR-020, and ADR-021, with ADR-002, ADR-014, ADR-016, and ADR-019 amended by the later ones. See [Architectural Decisions](02_architectural_decisions.md).

## Goals

### Primary Goals

1. Run an ordered list of tasks, each in a fresh `claude --print` session, with `shared.prompt` and the task on stdin in gralph's exact text format
2. Parse and validate the one task file with gralph's field rules and clear error messages that name the task and field: a required `shared` block (a required `prompt` and optional `after_task` and `after_run` lists of actions), and `tasks` with `id`, `name`, `prompt`, optional `state` (`pending`, `completed`, `failed`) and `error`. A top-level `gates` key, `shared.gates`, an action's `cmd` key, or any other unknown top-level, `shared`, or action key is an error
3. Decide a session's outcome from the last non-blank, non-fence line of its output, as gralph does
4. Run the file's `shared.after_task` actions after a completed session, in order, each with a time limit, with `ALR_TASK_FILE`, `ALR_LOG_DIR`, `ALR_TASK_ID`, and `ALR_TASK_NAME` set, and complete the task only when every action exits zero; run `shared.after_run` once at the end of every run with no failed task, even when every task was already completed (ADR-026)
5. Treat a commit as an action: alr has no commit mode and does not look at git, so a task is not guaranteed its own commit (ADR-026)
6. Build the session flags from `--sandbox-settings`, a `sandbox.json` found in the `--dir` run folder, or `--yolo`, with gralph's forced sandbox keys; require one choice on a real run
7. Save the task file atomically after every task; stop the run on the first failed task; refuse to start when the file already holds a failed task
8. Stop cleanly on SIGINT or SIGTERM: kill the running process group, leave the interrupted task `pending` and the file untouched, exit 130 for SIGINT and 143 for SIGTERM, in plain mode and in the view (ADR-025)
9. Support `--dry-run` with gralph's plain-text report, never starting Claude or writing a file; it applies the checks a real run starts with, so it refuses like one (exit 3, including for a `failed` task)
10. Keep the logic that only plain mode runs clearly apart from the logic that only the full-screen path runs, with the shared rules in one place
11. Port the full-screen path's engine side now: stream-json parsing, run events, and the run log writer, tested against a fake `claude`
12. Open a Textual full-screen view on a terminal, with one main window of three panes (tasks and actions on top, the running task's activity full width below), a settings strip with a Logging dropdown, a menu whose button names show their hotkeys (no footer), and a status bar; elapsed times show hours, minutes, and seconds. The Task pane (activity lines) keeps the last 5,000 lines per task (the run log keeps every line), and the Actions button does nothing while a run goes. While a run goes, a status line between the top panes and the Task pane says where the run is and ticks every second (`task 2 of 5 · action 1 of 3: uv run pytest · 12.3s · run 1m 4s`; the form is set in [ADR-020](02_architectural_decisions.md#adr-020-the-view-shows-a-ticking-status-line-and-a-time-column)), and the Tasks pane has a Time column that counts the running task up and keeps each finished task's time
13. Start a run only when the user presses Start, with Start disabled and the reason shown while something is missing
14. Ask for a missing folder, permission choice, or action lists in modal dialogs over the main window, using gralph's wizard rules, and let any dialog be reopened until Start
15. Open `tasks.yaml` and `sandbox.json` read-only from the menu
16. Offer one Actions dialog with two lists, "After each task" and "After the run", starting from the task file (ADR-026)
17. Offer a permissions setting of the folder's `sandbox.json` (the default when it exists) or skip permissions, never defaulting to unsandboxed
18. Log every real run to `<folder>/logs/<timestamp>/` in both modes: plain mode always, the view through a Logging dropdown that is on by default. If two runs start in the same second, the later run's folder gets a `-1` suffix (then `-2`), and the final name is not reported to the caller
19. Stop a running view cleanly from a Stop button or Ctrl-C, and install the bundled skill with `--install-skill`
20. Run the view entirely from the keyboard, without capturing the mouse: every action, including the setup dialogs, has a key
21. Open a task file that holds failed tasks in the view and reset them to pending from the menu after a confirmation, or silently with `--reset-failed` in either mode
22. Give every way a run can end its own exit code and a logged reason, so an outside program can tell them apart (ADR-025): 0 completed, 1 a task or one of its `after_task` actions failed, 2 a usage error (a removed flag included), 3 refused to start (first stderr line `error: refused: <reason>: <message>`, one of five reasons; no run log, and the task file and `logs/` as they were), 4 an `after_run` action failed (ADR-026), 130 or 143 stopped. The run log's `run_finished` says the `result` and the `exit` code
23. Mark a task `completed` only when its session and every `after_task` action succeeded; an `after_run` failure leaves the tasks `completed` and a rerun retries `after_run` (ADR-025, ADR-026)

### Secondary Goals

1. Give the Textual view and an outside program (such as the later agent) a surface they can use without changing the engine: plain mode, its exit codes, and the run log (ADR-022)
2. Keep the existing project scaffolding: uv, the Makefile, the Docker-first build, and the CI workflow
3. Type-hint every function

## Non-Goals

- Pause, skip, and retry controls in the view. Stop is the only control during a run (ADR-012)
- A second run in the same view session. A new run means a new launch
- Editing `tasks.yaml` or `sandbox.json` in the view. The file viewer is read-only
- A default action timeout setting in the view (ADR-015)
- Checking the installed skill at startup. gralph no longer does (its ADR-022)
- An agent framework inside `alr`, or any change to behavior aimed at the outside agent. The agent is a separate program that uses `alr` as a tool (ADR-022)
- Keeping gralph's command lines, task files, and error text. `alr` began as a port of gralph and has since evolved beyond it; it changes flags, formats, and behavior as its own needs require, and the CHANGELOG records each break (ADR-022)
- Byte-identical task file output. The saved file is equivalent YAML (same keys, values, and order; 2-space indent), not the exact bytes Go's YAML library writes
- Byte-identical `--help` text
- Windows support
- Everything gralph lists as a non-goal: retries, shared context between sessions, an agent abstraction, pushing, default logging

## Command Line

The flags below are accepted. The CHANGELOG records each change from gralph's flags.

| Flag                        | Short | Notes                                                                                   |
| --------------------------- | ----- | --------------------------------------------------------------------------------------- |
| `--version`                 | `-v`  | Prints the version, then the OS and architecture, such as `alr version: 0.0.9 linux/x86_64` |
| `--dir <folder>`            | `-d`  | Run folder holding `tasks.yaml` and, optionally, `sandbox.json`; required in plain mode, and the view asks when it is missing; `--sandbox-settings` overrides the settings file |
| `--dry-run`                 |       | Validate and report only. The old alr short form `-d` now means `--dir`, as in gralph   |
| `--action-timeout <duration>` |     | Duration such as `90s`, `10m`, or `1h30m`; overrides every action's own timeout (renamed from `--gate-timeout`, which is refused) |
| `--plain`                   |       | Runs plain mode on a terminal. Without it, a terminal run opens the full-screen view   |
| `--sandbox-settings <path>` |       | Sessions run in Claude's sandbox with this settings file; found by itself as `<folder>/sandbox.json` under `--dir` when neither permission flag is passed |
| `--yolo`                    |       | New name for gralph's `--skip-permissions` (ADR-021). Sessions run with `--dangerously-skip-permissions`; wins over a folder's `sandbox.json` with no error |
| `--commit`                  |       | Removed (ADR-026): a commit is an action in `shared.after_task` now. The flag is refused with exit 2 and a message that says so |
| `--reset-failed`            |       | New in alr. Sets every failed task back to pending before the run, silently, in plain mode and in the view; with `--dry-run` is a usage error |
| `--install-skill`           |       | Copies the bundled `gralph-docs-writer` skill to `~/.claude/skills/` and exits 0         |

## Success Criteria

| Criterion              | Target                                                                                          | Measurement Method                                                          |
| ---------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Task file validation   | Same field rules as gralph, including the `shared` block and the rejected keys, with clear errors naming the task and field | Ported `task_test.go` cases                                  |
| Wire contract          | Stdin text and Claude argv identical to gralph's                                                | Golden test on stdin; argv recorded by the fake `claude`                    |
| Outcome rule           | Same state and error for every result-line case                                                 | Ported `result_test.go` and `claude_result_test.go` cases                   |
| Action enforcement     | Completed only when every `after_task` action exits zero; timeouts honored; actions skipped on a failed session; `after_run` runs once at the end, also when every task was already completed | Unit and e2e tests with real shell commands |
| Session permissions    | Sandboxed argv with the three forced keys, or the bypass flag; neither or both is a usage error that exits 2; a folder's `sandbox.json` answers the choice, and `--yolo` overrides it | Ported sandbox unit and e2e tests |
| Commits as actions     | A real `git commit` action commits per task and fails the task when it fails; an `after_run` failure exits 4 with the tasks still `completed` | e2e tests with real git repositories in temporary folders |
| State persistence      | Atomic save after every task; file untouched on cancel | Ported e2e tests check the file after each run                              |
| Signal handling        | SIGINT and SIGTERM kill the whole process group; exit 130 (SIGINT) or 143 (SIGTERM); no orphans                             | Ported signal e2e tests with the fake `claude` and a descendant process     |
| Exit codes             | 0 completed, 1 task or action failed, 2 usage error, 3 refused (the line `error: refused: <reason>: <message>`, no run log, nothing changed on disk; a `--dry-run` too), 4 `after_run` action failed, 130 or 143 stopped; the log's `run_finished` carries the `result` and `exit` | A unit test per code and reason; one e2e test per code (`test_outcomes.py`) |
| Run log                | Same ledger lines and detail files as gralph for the same events; `run_started` has no `prompt_file` | Ported `runlog_test.go` cases                                          |
| Path separation        | `engine` imports neither `plain` nor `stream`; `plain` and `stream` do not import each other    | A test that inspects the imports                                            |
| View states            | Start disabled with a reason while something is missing; Running locks the settings and greys out the Folder, Permissions, Reset, and Quit buttons; Finished shows a summary and stays open | TUI unit tests under Textual's `run_test` and `Pilot`          |
| Dialog rules           | A dialog opens only for an open step; nothing is written before Start except a confirmed reset | TUI unit tests                                                     |
| Reset                  | A file with failed tasks opens with Start disabled and the reason shown; a confirmed reset saves them pending with no error text; `--reset-failed` does it silently and exits 2 with `--dry-run` | Engine, CLI, and TUI unit tests; e2e                |
| Removed flags          | `--commit` and `--gate-timeout` are refused with exit 2 and a message naming the replacement; `shared.gates` and `cmd` are refused naming `shared.after_task` and `action` | Unit and e2e tests |
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

- **Emerging maturity**: breaking changes are expected, and the CHANGELOG records each one (ADR-022)
- **The gralph-docs-writer skill** keeps producing the one task file, shared prompt included; the task field rules must stay in sync with it

### Organizational Constraints

- **Claude Code only**, inherited from gralph and intentional
- **No retry**, inherited from gralph
- **PRs target `develop`**

## Assumptions

| Assumption                                                                 | Impact if Wrong                                                    | Validation Plan                                                  |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| gralph v0.12.0 is the starting reference for the port                      | The port's first version reflects a gralph that changes later      | Not tracked after the port (ADR-022); the CHANGELOG records alr's own changes |
| Equivalent YAML is enough; nothing depends on the exact saved bytes        | A tool that diffs task files byte for byte would see changes       | Round-trip tests compare parsed content                          |
| PyYAML's node API can enforce gralph's tag and integer rules               | Validation would need a different YAML library                     | The first ported task tests cover these cases                    |
| Go's exit-error wording (such as `exit status 1`) can be reproduced        | Task `error` text would differ from gralph's                       | Ported action tests assert the strings                  |
| Textual can host the async engine as a worker in its own event loop       | The TUI would need a thread or a child process (ADR-012)           | Textual's worker and `run_test` documentation checked; proven by the first TUI run test |
| The outside agent drives alr through plain mode, its exit codes, and the run log| A change to those breaks the agent program, so they change only with care and a CHANGELOG entry| Ported plain-mode and run log tests; the e2e exit-code matrix    |

**Next:** [Architectural Decisions](02_architectural_decisions.md)
