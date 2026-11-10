---
version: 37
date: 2026-11-10
notes: "Requirement 26 names the skills folder and its CLAUDE_CONFIG_DIR override (ADR-035). Earlier: Requirement 19 drops `--install-skill`, the flag table drops its row, and the new requirement 26 has alr keep its bundled skills in sync at startup (ADR-035). Earlier: Requirement 9 and the flag table gain the dry run's six warnings and `--strict` (ADR-034); requirement 22 counts nine refusal reasons, with `task-file-warnings`. Earlier: `--install-skill` installs every bundled skill, so requirement 19, the flag table, and the skill-install row say so; the dependencies list names the alr-task-review skill (ADR-033). Earlier: Requirement 22 counts eight refusal reasons, as src/alr/errors.py has them, and the Exit codes row says a refusal changes nothing in the run folder, though a real run may already have added missing shipped sandbox files (ADR-032). Earlier: Sandbox files (ADR-032) are built: requirements 6, 15, 17, and 25, the non-goal about editing sandbox.json, the flag rows for --dir, --sandbox-settings, --sandbox, and --yolo, and the Session permissions row describe sandboxes picked by name from ~/.local/alr/sandboxes/; the run folder's sandbox.json is no longer read or written. Earlier: The dev dependencies include mypy and types-PyYAML. Earlier: The finished summary focuses its button; after a clean run the button is Exit and ends alr with the run's exit code, after a failed or stopped run it is Close and the app stays open. Earlier: Requirement 25 describes the first-run flow as built: the [y/N] question, exit 0 after setup, the config-setup-missing refusal (no, no terminal, --dry-run), and the sandbox default applying only with --dir. Earlier: Requirement 25 (the user config directory) is built, so it is no longer marked Proposed. Earlier: Adds requirement 25 (Proposed, ADR-031) for the user config directory. Earlier: Adds requirement 24 (Proposed, ADR-030) for the task review and task remediation, and clarifies that the retries non-goal does not cover them. Earlier: Requirements 22 and 23 say every action in both lists (ADR-029). Earlier: Documents ADR-029: a task may carry its own actions list, run before shared.after_task as one list whose first failure stops both; requirements 2 and 4 and the Action enforcement row say so, and an unknown task key is an error. Earlier: Documents ADR-027: the --sandbox flag, the preset permissions setting, and the sixth refusal reason sandbox-file-unwritable. Earlier: Documents ADR-026: commits are actions; the requirements for gates, commit modes, and exit 4 say after_task, after_run, and an after_run failure, and --commit and --gate-timeout are listed as refused. Earlier: Corrects the refusal count in requirement 22 to ten reasons, as src/alr/errors.py has them (locked included). Earlier: Corrects the documents to ADR-025 as amended after the stage 1 review: a refusal leaves nothing on disk and a --dry-run refuses like a real run (exit 3, failed tasks included); the view exits with the same codes (SIGTERM 143); a stop leaves the file untouched except that a stop after a commit landed saves the tasks it covers completed; a task is completed only after its commit succeeds. Earlier: Documents ADR-025's contract: every outcome has an exit code (0, 1, 2, 3, 4, 130, 143) and a logged reason; refusals name a reason; completed means complete, and a failed or stopped run mode run leaves nothing completed. A bad flag, neither or both permission flags, and --reset-failed with --dry-run exit 2, not 1. Earlier: The commit-modes criterion says a bare --commit and --commit-after-all are usage errors that exit 2, not exit 1 (ADR-024). Earlier: the commit flags are one flag, --commit {none,task,run}, default none, and --version also shows the OS and architecture (ADR-024); the flag table, the view's commit setting, and the commit-modes criterion say so. Earlier: removes the known differences from gralph list: alr no longer aims for gralph compatibility (ADR-022), and the CHANGELOG records breaks. The agent is a separate program that uses alr as a tool (ADR-022). --yolo replaces --skip-permissions with no alias (ADR-021)."
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
2. Parse and validate the one task file with gralph's field rules and clear error messages that name the task and field: a required `shared` block (a required `prompt` and optional `after_task` and `after_run` lists of actions), and `tasks` with `id`, `name`, `prompt`, optional `state` (`pending`, `completed`, `failed`), `error`, and `actions` (the task's own list of actions, with the same entry shape as the shared lists; absent and `[]` both mean none). A top-level `gates` key, `shared.gates`, an action's `cmd` key, or any other unknown top-level, `shared`, task, or action key is an error (ADR-026, ADR-029)
3. Decide a session's outcome from the last non-blank, non-fence line of its output, as gralph does
4. After a completed session, run the task's own `actions` first and then the file's `shared.after_task` actions, as one list in that order, each with a time limit, with `ALR_TASK_FILE`, `ALR_LOG_DIR`, `ALR_TASK_ID`, and `ALR_TASK_NAME` set; the first failure or timeout fails the task and skips every later action from either list, and the task is completed only when every action in both lists exits zero; run `shared.after_run` once at the end of every run with no failed task, even when every task was already completed (ADR-026, ADR-029)
5. Treat a commit as an action: alr has no commit mode and does not look at git, so a task is not guaranteed its own commit (ADR-026)
6. Build the session flags from `--sandbox-settings`, `--sandbox <name>` (a file in `~/.local/alr/sandboxes/`), or `--yolo`, with gralph's forced sandbox keys; require one choice on a real run (ADR-032)
7. Save the task file atomically after every task; stop the run on the first failed task; refuse to start when the file already holds a failed task
8. Stop cleanly on SIGINT or SIGTERM: kill the running process group, leave the interrupted task `pending` and the file untouched, exit 130 for SIGINT and 143 for SIGTERM, in plain mode and in the view (ADR-025)
9. Support `--dry-run` with gralph's plain-text report, never starting Claude or writing a file; it applies the checks a real run starts with, so it refuses like one (exit 3, including for a `failed` task); after the structural checks it also prints one `warning: <where>: <rule>: <text>` line for each problem six deterministic rules find in the prompts and commands (`finish-block`, `completion-criteria`, `excluded-command-wrapped`, `inline-variable`, `task-file-mentioned`, `no-check`; ADR-034), which never change the exit code unless `--strict` is given
10. Keep the logic that only plain mode runs clearly apart from the logic that only the full-screen path runs, with the shared rules in one place
11. Port the full-screen path's engine side now: stream-json parsing, run events, and the run log writer, tested against a fake `claude`
12. Open a Textual full-screen view on a terminal, with one main window of three panes (tasks and actions on top, the running task's activity full width below), a settings strip with a Logging dropdown, a menu whose button names show their hotkeys (no footer), and a status bar; elapsed times show hours, minutes, and seconds. The Task pane (activity lines) keeps the last 5,000 lines per task (the run log keeps every line), and the Actions button does nothing while a run goes. While a run goes, a status line between the top panes and the Task pane says where the run is and ticks every second (`task 2 of 5 · action 1 of 3: uv run pytest · 12.3s · run 1m 4s`; the form is set in [ADR-020](02_architectural_decisions.md#adr-020-the-view-shows-a-ticking-status-line-and-a-time-column)), and the Tasks pane has a Time column that counts the running task up and keeps each finished task's time
13. Start a run only when the user presses Start, with Start disabled and the reason shown while something is missing
14. Ask for a missing folder, permission choice, or action lists in modal dialogs over the main window, using gralph's wizard rules, and let any dialog be reopened until Start
15. Open `tasks.yaml` and the chosen sandbox file read-only from the menu
16. Offer one Actions dialog with two lists, "After each task" and "After the run", starting from the task file (ADR-026)
17. Offer a permissions setting of the settings file (the default when one answered), one radio per sandbox file in `~/.local/alr/sandboxes/` by name with a preview of the selected file, or skip permissions, never defaulting to unsandboxed; nothing is written when the user presses Start (ADR-032)
18. Log every real run to `<folder>/logs/<timestamp>/` in both modes: plain mode always, the view through a Logging dropdown that is on by default. If two runs start in the same second, the later run's folder gets a `-1` suffix (then `-2`), and the final name is not reported to the caller
19. Stop a running view cleanly from a Stop button or Ctrl-C
20. Run the view entirely from the keyboard, without capturing the mouse: every action, including the setup dialogs, has a key
21. Open a task file that holds failed tasks in the view and reset them to pending from the menu after a confirmation, or silently with `--reset-failed` in either mode
22. Give every way a run can end its own exit code and a logged reason, so an outside program can tell them apart (ADR-025): 0 completed, 1 a task or one of its actions failed, 2 a usage error (a removed flag included), 3 refused to start (first stderr line `error: refused: <reason>: <message>`, one of nine reasons; no run log, and the task file and `logs/` as they were), 4 an `after_run` action failed (ADR-026), 130 or 143 stopped. The run log's `run_finished` says the `result` and the `exit` code
23. Mark a task `completed` only when its session and every action in both lists succeeded; an `after_run` failure leaves the tasks `completed` and a rerun retries `after_run` (ADR-025, ADR-026)
24. *(Proposed, ADR-030.)* Let a task opt in to a task review: a fresh session checks the result against the project's rules and writes a findings file, a task remediation fixes the findings, and the two repeat up to `shared.max_task_review_rounds`; when a task completes with findings still open, file one GitHub issue per finding with `gh`
25. Read defaults from `~/.local/alr/config.yaml` (an optional sandbox default, and the task review prompt and cap), the prompt files beside it, and the sandbox files in `sandboxes/` (ADR-031, ADR-032). `tight.json` and `large.json` ship with alr; every later real run adds any that is missing and never overwrites a file there. When the folder is missing, a real run at a terminal lists what it would create and asks `[y/N]`; a yes creates the folder all at once and exits 0 without starting the run, and a no, or no terminal, or `--dry-run` refuses with `config-setup-missing`. A flag, then the run folder's `tasks.yaml`, then the config file, then alr's shipped default decide each setting; the config's sandbox default (a sandbox name or `yolo`) applies, with or without `--dir`, when no flag chose one
26. Keep the bundled skills (`alr-task-file`, `alr-task-review`) in the skills folder (`$CLAUDE_CONFIG_DIR/skills` when that variable is set, else `~/.claude/skills`) the same as the ones in the installed alr: add or replace them at startup, print `Added <path>` or `Updated <path>`, replace a symlink or file at those two names with a warning, touch nothing else there, and warn and carry on when a skill cannot be written (ADR-035)

### Secondary Goals

1. Give the Textual view and an outside program (such as the later agent) a surface they can use without changing the engine: plain mode, its exit codes, and the run log (ADR-022)
2. Keep the existing project scaffolding: uv, the Makefile, the Docker-first build, and the CI workflow
3. Type-hint every function

## Non-Goals

- Pause, skip, and retry controls in the view. Stop is the only control during a run (ADR-012)
- A second run in the same view session. A new run means a new launch
- Editing `tasks.yaml` or a sandbox file in the view. The file viewer is read-only
- A default action timeout setting in the view (ADR-015)
- Checking the installed skill at startup. gralph no longer does (its ADR-022)
- An agent framework inside `alr`, or any change to behavior aimed at the outside agent. The agent is a separate program that uses `alr` as a tool (ADR-022)
- Keeping gralph's command lines, task files, and error text. `alr` began as a port of gralph and has since evolved beyond it; it changes flags, formats, and behavior as its own needs require, and the CHANGELOG records each break (ADR-022)
- Byte-identical task file output. The saved file is equivalent YAML (same keys, values, and order; 2-space indent), not the exact bytes Go's YAML library writes
- Byte-identical `--help` text
- Windows support
- Everything gralph lists as a non-goal: retries, shared context between sessions, an agent abstraction, pushing, default logging (a task remediation is not a retry of a failed task; ADR-030)

## Command Line

The flags below are accepted. The CHANGELOG records each change from gralph's flags.

| Flag                          | Short | Notes                                                                                                                                                                                                                           |
| ----------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--version`                   | `-v`  | Prints the version, then the OS and architecture, such as `alr version: 0.0.9 linux/x86_64`                                                                                                                                     |
| `--dir <folder>`              | `-d`  | Run folder holding `tasks.yaml`; required in plain mode, and the view asks when it is missing. A `sandbox.json` in it is not read (alr prints a notice)                                                                         |
| `--dry-run`                   |       | Validate and report only, and print the warnings of ADR-034. The old alr short form `-d` now means `--dir`, as in gralph                                                                                                        |
| `--strict`                    |       | With `--dry-run`: a warning refuses the run, `refused: task-file-warnings`, exit 3. Alone: a usage error, exit 2 (ADR-034)                                                                                                      |
| `--action-timeout <duration>` |       | Duration such as `90s`, `10m`, or `1h30m`; overrides every action's own timeout (renamed from `--gate-timeout`, which is refused)                                                                                               |
| `--plain`                     |       | Runs plain mode on a terminal. Without it, a terminal run opens the full-screen view                                                                                                                                            |
| `--sandbox-settings <path>`   |       | Sessions run in Claude's sandbox with this settings file, read as given; for a file kept outside `~/.local/alr/sandboxes/`                                                                                                      |
| `--sandbox <name>`            |       | Sandbox sessions with the named file from `~/.local/alr/sandboxes/` (ADR-032). A name with no file exits 2 and lists the names; with `--sandbox-settings` or `--yolo` it exits 2; `--dry-run` names the file and writes nothing |
| `--yolo`                      |       | New name for gralph's `--skip-permissions` (ADR-021). Sessions run with `--dangerously-skip-permissions`                                                                                                                        |
| `--commit`                    |       | Removed (ADR-026): a commit is an action in `shared.after_task` now. The flag is refused with exit 2 and a message that says so                                                                                                 |
| `--reset-failed`              |       | New in alr. Sets every failed task back to pending before the run, silently, in plain mode and in the view; with `--dry-run` is a usage error                                                                                   |

## Success Criteria

| Criterion            | Target                                                                                                                                                                                                                                                                                                                                                                                              | Measurement Method                                                          |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Task file validation | Same field rules as gralph, including the `shared` block and the rejected keys, with clear errors naming the task and field                                                                                                                                                                                                                                                                         | Ported `task_test.go` cases                                                 |
| Wire contract        | Stdin text and Claude argv identical to gralph's                                                                                                                                                                                                                                                                                                                                                    | Golden test on stdin; argv recorded by the fake `claude`                    |
| Outcome rule         | Same state and error for every result-line case                                                                                                                                                                                                                                                                                                                                                     | Ported `result_test.go` and `claude_result_test.go` cases                   |
| Action enforcement   | Completed only when every action exits zero; a task's own actions run before the shared `after_task` ones, and the first failure stops both lists; timeouts honored; actions skipped on a failed session; `after_run` runs once at the end, also when every task was already completed                                                                                                              | Unit and e2e tests with real shell commands                                 |
| Session permissions  | Sandboxed argv with the three forced keys, or the bypass flag; none is a usage error on a plain real run and any two exit 2; a run folder's `sandbox.json` is never read or written                                                                                                                                                                                                                 | Ported sandbox unit and e2e tests; sandbox file unit and e2e tests          |
| Commits as actions   | A real `git commit` action commits per task and fails the task when it fails; an `after_run` failure exits 4 with the tasks still `completed`                                                                                                                                                                                                                                                       | e2e tests with real git repositories in temporary folders                   |
| State persistence    | Atomic save after every task; file untouched on cancel                                                                                                                                                                                                                                                                                                                                              | Ported e2e tests check the file after each run                              |
| Signal handling      | SIGINT and SIGTERM kill the whole process group; exit 130 (SIGINT) or 143 (SIGTERM); no orphans                                                                                                                                                                                                                                                                                                     | Ported signal e2e tests with the fake `claude` and a descendant process     |
| Exit codes           | 0 completed, 1 task or action failed, 2 usage error, 3 refused (the line `error: refused: <reason>: <message>`, no run log, nothing changed in the run folder; a `--dry-run` too. A real run may already have added missing shipped sandbox files to `~/.local/alr/sandboxes/`, ADR-032), 4 `after_run` action failed, 130 or 143 stopped; the log's `run_finished` carries the `result` and `exit` | A unit test per code and reason; one e2e test per code (`test_outcomes.py`) |
| Run log              | Same ledger lines and detail files as gralph for the same events; `run_started` has no `prompt_file`                                                                                                                                                                                                                                                                                                | Ported `runlog_test.go` cases                                               |
| Path separation      | `engine` imports neither `plain` nor `stream`; `plain` and `stream` do not import each other                                                                                                                                                                                                                                                                                                        | A test that inspects the imports                                            |
| View states          | Start disabled with a reason while something is missing; Running locks the settings and greys out the Folder, Permissions, Reset, and Quit buttons; Finished shows a summary whose focused button is Exit after a clean run (it ends alr) and Close otherwise (the app stays open)                                                                                                                  | TUI unit tests under Textual's `run_test` and `Pilot`                       |
| Dialog rules         | A dialog opens only for an open step; nothing is written before Start except a confirmed reset                                                                                                                                                                                                                                                                                                      | TUI unit tests                                                              |
| Reset                | A file with failed tasks opens with Start disabled and the reason shown; a confirmed reset saves them pending with no error text; `--reset-failed` does it silently and exits 2 with `--dry-run`                                                                                                                                                                                                    | Engine, CLI, and TUI unit tests; e2e                                        |
| Removed flags        | `--commit` and `--gate-timeout` are refused with exit 2 and a message naming the replacement; `shared.gates` and `cmd` are refused naming `shared.after_task` and `action`                                                                                                                                                                                                                          | Unit and e2e tests                                                          |
| Stop                 | Stop and Ctrl-C end the run and report `stopped`; no orphan processes                                                                                                                                                                                                                                                                                                                               | TUI unit tests; one pseudo-terminal e2e test                                |
| View happy path      | A run in a pseudo-terminal reaches Finished and a clean exit                                                                                                                                                                                                                                                                                                                                        | One pseudo-terminal e2e test in the container                               |
| Skill install        | Each bundled skill lands in `~/.claude/skills/<name>/`, replacing any copy                                                                                                                                                                                                                                                                                                                          | Unit test with a temporary home; e2e                                        |
| Keyboard only        | The mouse is not captured; the whole setup and a run can be done with keys alone                                                                                                                                                                                                                                                                                                                    | TUI unit test of the keyboard-only flow                                     |
| Build                | `make test` (analyze and unit tests) and `make build` (wheel, then e2e in a container) pass                                                                                                                                                                                                                                                                                                         | CI                                                                          |

## Constraints

### Technical Constraints

- **Python 3.12 or later**, managed with uv, `src/` layout
- **Runtime dependencies: PyYAML and Textual.** `pydantic` is removed. Dev dependencies: pytest, pytest-asyncio, ruff, mypy, types-PyYAML
- **Standard library for the rest**: `asyncio` for subprocesses and cancellation, `argparse` for flags, `json`, `pathlib`
- **Unix process API**: process groups and signals; Linux and macOS only
- **The `claude` CLI on `PATH`**, called with a fixed argv and the prompt on stdin
- **No injectable runner**: tests put a fake `claude` first on `PATH`, as gralph's do
- **No inline lint or security suppressions**

### Business Constraints

- **Emerging maturity**: breaking changes are expected, and the CHANGELOG records each one (ADR-022)
- **The alr-task-file skill** keeps producing the one task file, shared prompt included; the task field rules must stay in sync with it
- **The alr-task-review skill** reads a task file before a run and reports what would make a task fail, pass without the work, or be too big, as one-line findings; it is advisory and changes nothing (ADR-033)

### Organizational Constraints

- **Claude Code only**, inherited from gralph and intentional
- **No retry**, inherited from gralph
- **PRs target `develop`**

## Assumptions

| Assumption                                                                       | Impact if Wrong                                                                                 | Validation Plan                                                                         |
| -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| gralph v0.12.0 is the starting reference for the port                            | The port's first version reflects a gralph that changes later                                   | Not tracked after the port (ADR-022); the CHANGELOG records alr's own changes           |
| Equivalent YAML is enough; nothing depends on the exact saved bytes              | A tool that diffs task files byte for byte would see changes                                    | Round-trip tests compare parsed content                                                 |
| PyYAML's node API can enforce gralph's tag and integer rules                     | Validation would need a different YAML library                                                  | The first ported task tests cover these cases                                           |
| Go's exit-error wording (such as `exit status 1`) can be reproduced              | Task `error` text would differ from gralph's                                                    | Ported action tests assert the strings                                                  |
| Textual can host the async engine as a worker in its own event loop              | The TUI would need a thread or a child process (ADR-012)                                        | Textual's worker and `run_test` documentation checked; proven by the first TUI run test |
| The outside agent drives alr through plain mode, its exit codes, and the run log | A change to those breaks the agent program, so they change only with care and a CHANGELOG entry | Ported plain-mode and run log tests; the e2e exit-code matrix                           |

**Next:** [Architectural Decisions](02_architectural_decisions.md)
