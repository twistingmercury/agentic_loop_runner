# alr — Requirements

> **Version**: v01
> **Date**: 2026-10-04
> **Notes**: Initial version. Requirements for porting gralph's engine to Python.

[Back to Overview](00_overview_v01.md) | [Back to Project README](../../README.md)

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

gralph at commit `170dc6f` (branch `feature/setup-wizard`, `v0.9.8-25-g170dc6f`) is the reference for behavior. Where this document set is silent, gralph's own documents and code at that commit decide:

- `docs/architecture/01_requirements.md` (v13), `02_architectural_decisions.md`, `03_system_architecture.md` (v21)
- `docs/howto.md`
- `src/internal/tasks`, `src/internal/looper`, `src/internal/runlog`, `src/cmd/main`, and `tests/e2e`

The gralph decisions that carry over unchanged are ADR-001 to ADR-007, ADR-013 to ADR-016, and ADR-019. See [Architectural Decisions](02_architectural_decisions_v01.md).

## Goals

### Primary Goals

1. Run an ordered list of tasks, each in a fresh `claude --print` session, with the shared prompt and the task on stdin in gralph's exact text format
2. Parse and validate the task file with gralph's field rules and clear error messages that name the task and field: top-level `gates`, and tasks with `id`, `name`, `prompt`, optional `state` (`pending`, `completed`, `failed`) and `error`
3. Decide a session's outcome from the last non-blank, non-fence line of its output, as gralph does
4. Run the file's gates after a completed session, each with a time limit, and complete the task only when every gate exits zero
5. Commit a completed task on request (`--commit`) with gralph's startup checks and commit rule
6. Build the session flags from `--sandbox-settings` or `--skip-permissions`, with gralph's forced sandbox keys; require exactly one on a real run
7. Save the task file atomically after every task; stop the run on the first failed task; refuse to start when the file already holds a failed task
8. Stop cleanly on SIGINT or SIGTERM: kill the running process group, leave the task and the file untouched, exit 1
9. Support `--dry-run` with gralph's plain-text report, never starting Claude or writing a file
10. Keep the logic that only plain mode runs clearly apart from the logic that only the full-screen path runs, with the shared rules in one place
11. Port the full-screen path's engine side now: stream-json parsing, run events, and the run log writer, tested against a fake `claude`

### Secondary Goals

1. Give the later Textual view and agent layer a surface they can use without changing the engine
2. Keep the existing project scaffolding: uv, the Makefile, the Docker-first build, and the CI workflow
3. Type-hint every function

## Non-Goals

- The full-screen view, the setup wizard, and terminal detection. Until they exist, `alr` always runs plain mode
- `--install-skill`, the bundled skill, and the stale-skill check at startup
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
| `--dir <folder>`            | `-d`  | Run folder holding `tasks.yaml` and `prompt.md`; `--tasks` and `--prompt` override      |
| `--tasks <path>`            | `-t`  | Required unless `--dir` gives it                                                        |
| `--prompt <path>`           | `-p`  | Required unless `--dry-run` or `--dir` gives it                                         |
| `--dry-run`                 |       | Validate and report only. The old alr short form `-d` now means `--dir`, as in gralph   |
| `--gate-timeout <duration>` |       | Duration such as `90s`, `10m`, or `1h30m`; overrides every gate's own timeout            |
| `--no-tui`                  |       | Accepted. Has no effect until the full-screen view exists                               |
| `--sandbox-settings <path>` |       | Sessions run in Claude's sandbox with this settings file                                |
| `--skip-permissions`        |       | Sessions run with `--dangerously-skip-permissions`                                      |
| `--commit`                  |       | Commit each completed task after its gates pass                                         |
| `--log-dir <folder>`        |       | On a real run, exits 1 with gralph's plain-mode error until the full-screen view exists |

Known differences from gralph in this step:

- The command is `alr`, and messages that name the command say `alr`
- `--install-skill` is not accepted
- A bare `alr` in a terminal does not open a wizard; it reports the missing required flags, as gralph does in plain mode
- `--help` text is generated by Python's `argparse` and is not word-for-word gralph's
- Error messages are worded by `alr` and need not match gralph's
- YAML is read with PyYAML's standard rules: an unquoted `yes`, `on`, or `12:30` is not a string and must be quoted, and `<<` merge keys are rejected

## Success Criteria

| Criterion              | Target                                                                                          | Measurement Method                                                          |
| ---------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Task file validation   | Same field rules as gralph, with clear errors naming the task and field                         | Ported `task_test.go` cases                                                 |
| Wire contract          | Stdin text and Claude argv identical to gralph's                                                | Golden test on stdin; argv recorded by the fake `claude`                    |
| Outcome rule           | Same state and error for every result-line case                                                 | Ported `result_test.go` and `claude_result_test.go` cases                   |
| Gate enforcement       | Completed only when every gate exits zero; timeouts honored; gates skipped on a failed session  | Ported gate unit and e2e tests with real shell commands                     |
| Session permissions    | Sandboxed argv with the three forced keys, or the bypass flag; neither or both exits 1          | Ported sandbox unit and e2e tests                                           |
| Commit on request      | One commit per completed task; none for a failed task; a dirty tree refuses to start            | Ported commit tests against real git repositories in temporary folders     |
| State persistence      | Atomic save after every task; file untouched on cancel                                          | Ported e2e tests check the file after each run                              |
| Signal handling        | SIGINT and SIGTERM kill the whole process group; exit 1; no orphans                             | Ported signal e2e tests with the fake `claude` and a descendant process     |
| Exit codes             | Zero only when every task completed                                                             | Ported e2e matrix                                                           |
| Run log                | Same ledger lines and detail files as gralph for the same events                                | Ported `runlog_test.go` cases                                               |
| Path separation        | `engine` imports neither `plain` nor `stream`; `plain` and `stream` do not import each other    | A test that inspects the imports                                            |
| Build                  | `make test` and the Docker build pass                                                           | CI                                                                          |

## Constraints

### Technical Constraints

- **Python 3.12 or later**, managed with uv, `src/` layout
- **Runtime dependency: PyYAML only.** `pydantic` is removed. Dev dependencies: pytest, pytest-asyncio, ruff
- **Standard library for the rest**: `asyncio` for subprocesses and cancellation, `argparse` for flags, `json`, `pathlib`
- **Unix process API**: process groups and signals; Linux and macOS only
- **The `claude` CLI on `PATH`**, called with a fixed argv and the prompt on stdin
- **No injectable runner**: tests put a fake `claude` first on `PATH`, as gralph's do
- **No inline lint or security suppressions**

### Business Constraints

- **Emerging maturity**: breaking changes are expected until the port reaches parity with gralph
- **The gralph-docs-writer skill** keeps producing the task file and prompt; the task field rules must stay in sync with it

### Organizational Constraints

- **Claude Code only**, inherited from gralph and intentional
- **No retry**, inherited from gralph
- **PRs target `develop`**

## Assumptions

| Assumption                                                                 | Impact if Wrong                                                    | Validation Plan                                                  |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| `feature/setup-wizard` at `170dc6f` is the gralph behavior to match        | The port matches a version that changes before it merges           | Re-diff gralph's engine packages against `170dc6f` before release |
| Equivalent YAML is enough; nothing depends on the exact saved bytes        | A tool that diffs task files byte for byte would see changes       | Round-trip tests compare parsed content                          |
| PyYAML's node API can enforce gralph's tag and integer rules               | Validation would need a different YAML library                     | The first ported task tests cover these cases                    |
| Go's exit-error wording (such as `exit status 1`) can be reproduced        | Task `error` text would differ from gralph's                       | Ported gate and commit tests assert the strings                  |
| Textual and the agent framework can consume an async engine without change | The next design would have to reopen the engine                    | Checked when the TUI design is written                           |

**Next:** [Architectural Decisions](02_architectural_decisions_v01.md)
