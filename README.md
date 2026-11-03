# Agentic Loop Runner (alr)

![Maturity](https://img.shields.io/badge/maturity-emerging-orange)
![Version](https://img.shields.io/github/v/tag/twistingmercury/agentic_loop_runner?sort=semver)

> **Maturity Level**: Emerging - in active, initial development; expect breaking changes.</br>
> **Version**: v0.4.1
>
> - **Emerging**: Prototype, not production-ready, expect breaking changes
> - **Basic**: Production-ready but actively evolving, expect minor version changes
> - **Mature**: Stable, battle-tested, changes are rare

---

## Table of Contents

- [Usage](#usage)
- [How it works](#how-it-works)
- [Key Considerations](#key-considerations)
- [Development Considerations](#development-considerations)
- [Versioning](#versioning)

## Usage

`alr` runs ["ralph loops"](https://ghuntley.com/loop/) with Claude Code: it works through
a `tasks.yaml` list, one fresh Claude session per task. It began as the Python port of
[gralph](https://github.com/twistingmercury/gralph) and has since evolved beyond it
([ADR-022](docs/design/02_architectural_decisions.md#adr-022-alr-has-evolved-beyond-gralph)).

Everything a user needs lives in the [wiki](https://github.com/twistingmercury/agentic_loop_runner/wiki):

| To do this                                   | Read                                                                                                              |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Install alr                                  | [Install](https://github.com/twistingmercury/agentic_loop_runner/wiki/Install)                                    |
| Make a first run                             | [Your first run](https://github.com/twistingmercury/agentic_loop_runner/wiki/Your-first-run)                      |
| Write a task file                            | [Write a task file](https://github.com/twistingmercury/agentic_loop_runner/wiki/Write-a-task-file)                |
| Run commands around tasks (tests, commits)   | [Actions](https://github.com/twistingmercury/agentic_loop_runner/wiki/Actions)                                    |
| Choose what sessions may touch               | [Permissions and the sandbox](https://github.com/twistingmercury/agentic_loop_runner/wiki/Permissions-and-the-sandbox) |
| Use the full-screen view                     | [Reading the screen](https://github.com/twistingmercury/agentic_loop_runner/wiki/Reading-the-screen)              |
| Script it, read the logs and exit codes      | [Plain mode and the run log](https://github.com/twistingmercury/agentic_loop_runner/wiki/Plain-mode-and-the-run-log) |
| Look up a flag, an exit code, a refusal      | [Command-line reference](https://github.com/twistingmercury/agentic_loop_runner/wiki/Command-line-reference)      |
| Recover from a failed task                   | [When a task fails](https://github.com/twistingmercury/agentic_loop_runner/wiki/When-a-task-fails)                |

[CHANGELOG.md](CHANGELOG.md) records every release and every break.

## How it works

Tasks run one at a time, in file order, each in a fresh `claude --print` session. The
shared prompt and the task go to Claude on stdin. The runner owns task state: Claude
reports its outcome as one JSON line at the end of its output. When a task completes,
its own `actions` and then the file's `shared.after_task` actions run, and the task
counts as done only if every action exits zero. The file is saved after every task, and
the first failed task stops the run. After the last task, `shared.after_run` runs once.

Full design: [docs/design/00_overview.md](docs/design/00_overview.md). Decisions are
recorded as ADRs in [docs/design/02_architectural_decisions.md](docs/design/02_architectural_decisions.md),
and each build plan is in [docs/plans/](docs/plans/).

## Key Considerations

- Supported platforms are Linux and macOS only.
- No automatic retries: at most one attempt per task per run.
- The first run on a machine needs a one-time setup: `alr` asks before creating
  `~/.local/alr` (its `config.yaml` and prompt files) and then stops. Without a
  terminal, or with `--dry-run`, it refuses with `config-setup-missing` and creates
  nothing, so run `alr` once in a terminal on any machine or CI image before scripting it.
- Claude Code is the only supported agent. It uses your installed CLI's own model and
  authentication settings; `alr` adds no model-selection flags.
- The task file is code: its prompts go to Claude and its actions run through `sh -c`.
  Do not run `alr` on a task file you do not trust.
- `alr` ends only what it started, takes no lock, and promises no commit per task. The
  [Actions](https://github.com/twistingmercury/agentic_loop_runner/wiki/Actions) page
  lists these limits.

## Development Considerations

Requires Python >= 3.12 and [uv](https://docs.astral.sh/uv/). The e2e tests need Docker.
Contribution rules and the release process are on the
[Contributing](https://github.com/twistingmercury/agentic_loop_runner/wiki/Contributing)
wiki page.

### Quick Start

```sh
uv sync
uv run alr -d path/to/run-folder --dry-run
```

### Testing

```sh
make test       # format, lint, and run the unit tests
make build      # build the wheel, then run the e2e tests in a container
make analyze    # format and auto-fix lint findings
make install    # install the locally built wheel as a uv tool
make uninstall  # remove it
make help       # list available targets
```

`make test` runs the unit tests in parallel with `pytest-xdist` (`-n auto`), and so does
the Docker build. To run them one at a time, which is the way to use a debugger, use
`uv run pytest -n 0 tests/unit` (or point it at one test file or use `-k`).

The e2e tests are black-box: they share nothing with the unit tests and run in a
container against the installed wheel.

### Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/).

Version is determined from git tags:

```bash
git describe --tags --always
```
