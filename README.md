# Agentic Loop Runner (alr)

> **Maturity Level**: Emerging - in active, initial development; expect breaking changes.
> **Version**: v0.0.5
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

`alr` runs ["ralph loops"](https://ghuntley.com/loop/) with Claude Code. It is the
Python port of [gralph](https://github.com/twistingmercury/gralph) and follows
gralph v0.12.0: the same task file, flags, and behavior.

A run starts from one `tasks.yaml`:

```yaml
shared:
  prompt: |
    You are working on the widget service. End your output with one JSON line:
    {"state": "completed"} or {"state": "failed", "error": "why"}.
  gates:
    - cmd: uv run pytest
      timeout: 10m
tasks:
  - id: 1
    name: Add the widget repository
    prompt: |
      Objective: ...
```

```sh
# Validate the task file only; launches no agent and changes no files.
alr -t tasks.yaml --dry-run

# Run the tasks, with Claude in its sandbox.
alr -t tasks.yaml --sandbox-settings sandbox.json

# Run a folder that holds tasks.yaml and sandbox.json, and commit each task.
alr -d .local/feature --commit
```

| Flag                        | Short | Description                                                                              |
| --------------------------- | ----- | ---------------------------------------------------------------------------------------- |
| `--tasks <path>`            | `-t`  | The task file. Required unless `--dir` gives it                                          |
| `--dir <folder>`            | `-d`  | Run folder: uses `<folder>/tasks.yaml` and, if present, `<folder>/sandbox.json`          |
| `--dry-run`                 |       | Validate and report only                                                                 |
| `--sandbox-settings <path>` |       | Run sessions in Claude's sandbox with this settings file                                 |
| `--skip-permissions`        |       | Run sessions with `--dangerously-skip-permissions`; wins over a folder's `sandbox.json`  |
| `--commit`                  |       | Commit each completed task after its gates pass                                          |
| `--gate-timeout <duration>` |       | Limit for every gate, such as `90s` or `10m`; overrides each gate's own timeout          |
| `--version`                 | `-v`  | Print the version                                                                        |

A real run needs one of `--sandbox-settings` (or a folder's `sandbox.json`) or
`--skip-permissions`. `--no-tui` is accepted and does nothing yet, and `--log-dir` is
refused until the full-screen view exists.

## How it works

Tasks run one at a time, in file order, each in a fresh `claude --print` session. The
shared prompt and the task go to Claude on stdin. The runner owns task state: Claude
reports its outcome as one JSON line at the end of its output. When a task completes,
the file's `shared.gates` run, and the task counts as done only if every gate exits
zero. The file is saved after every task, and the first failed task stops the run.

Each task has an `id`, a `name`, a `prompt`, and an optional `state` of `pending`,
`completed`, or `failed`.

Full design: [docs/design/00_overview.md](docs/design/00_overview.md). The build plan is
in [docs/plans/](docs/plans/).

## Key Considerations

- Supported platforms are Linux and macOS only.
- No automatic retries: at most one attempt per task per run.
- Claude Code is the only supported agent. It uses your installed CLI's own model and
  authentication settings; `alr` adds no model-selection flags.
- The task file is code: its prompts go to Claude and its gates run through `sh -c`.
  Do not run `alr` on a task file you do not trust.
- This is a breaking change from earlier `alr` builds: there is no `-p/--prompt` and no
  `prompt.md`, and the `abandoned` state is gone.

## Development Considerations

Requires Python >= 3.12 and [uv](https://docs.astral.sh/uv/). The e2e tests need Docker.

### Quick Start

```sh
uv sync
uv run alr -t path/to/tasks.yaml --dry-run
```

### Testing

```sh
make test      # format, lint, and run the unit tests
make build     # build the wheel, then run the e2e tests in a container
make e2e       # run the e2e tests against the wheel already in dist/
make analyze   # format and auto-fix lint findings
make help      # list available targets
```

### Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/).

Version is determined from git tags:

```bash
git describe --tags --always
```
