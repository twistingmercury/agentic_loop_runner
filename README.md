# Agentic Loop Runner (alr)

> **Maturity Level**: Emerging - in active, initial development; expect breaking changes.
> **Version**: v0.0.7
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
alr -d .local/feature --dry-run

# Run the tasks, with Claude in its sandbox.
alr -d .local/feature --sandbox-settings ~/sandbox.json

# Run a folder that holds tasks.yaml and sandbox.json, and commit each task.
alr -d .local/feature --commit

# Install the bundled task-file skill into ~/.claude/skills and exit.
alr --install-skill
```

In a terminal, `alr` opens a full-screen view: a settings strip (Commit and
Logging dropdowns, which lock once a run starts), a menu row, three panes (tasks and gates
and commits on top, activity across the full width below), and dialogs that ask for whatever the flags and the
task file leave open (run folder, permissions, gates). Nothing runs, and nothing is
written, until you press Start. It is keyboard-driven and never captures the mouse:
the menu button names show their keys (there is no footer): `s` starts a run (the button
then reads `Stop (^c)`), Ctrl-C stops it, `q` quits, `f`, `p`, `g` open the folder, permissions,
and gates dialogs (Folder, Permissions, Reset, and Quit grey out during a run), `r` resets failed tasks to pending (after you confirm), `t` and `x`
show `tasks.yaml` and `sandbox.json` (these two open any time), and Esc closes a dialog. The Gates
button does nothing while a run is going. The Task pane shows only the last 5,000 lines of a
task, with no warning; the run log keeps every line. Inside a git work tree the
view always commits, per task by default (outside one it runs without commits and says
so), and keeps run logs in `<folder>/logs`, which git must ignore. Use
`--plain`, a non-terminal, or `--dry-run` for plain output.

| Flag                        | Short | Description                                                                              |
| --------------------------- | ----- | ---------------------------------------------------------------------------------------- |
| `--dir <folder>`            | `-d`  | Run folder: uses `<folder>/tasks.yaml` and, if present, `<folder>/sandbox.json`. Required in plain mode; the view asks if it is missing |
| `--dry-run`                 |       | Validate and report only                                                                 |
| `--sandbox-settings <path>` |       | Run sessions in Claude's sandbox with this settings file                                 |
| `--skip-permissions`        |       | Run sessions with `--dangerously-skip-permissions`; wins over a folder's `sandbox.json`  |
| `--commit`                  |       | Commit each completed task after its gates pass                                          |
| `--reset-failed`            |       | Set every failed task back to pending before the run, without asking                     |
| `--commit-after-all`        |       | Commit once after the last task completes; a failed or stopped run makes no commit       |
| `--install-skill`           |       | Install the bundled `gralph-docs-writer` skill into `~/.claude/skills` and exit          |
| `--plain`                   |       | Use plain output instead of the full-screen view                                         |
| `--gate-timeout <duration>` |       | Limit for every gate, such as `90s` or `10m`; overrides each gate's own timeout          |
| `--version`                 | `-v`  | Print the version                                                                        |

A real run needs one of `--sandbox-settings` (or a folder's `sandbox.json`) or
`--skip-permissions`; the full-screen view asks in a dialog when neither is given.
`--commit` and `--commit-after-all` cannot be used together.

A real run logs to `<folder>/logs/<timestamp>/` in both modes (a dry run writes nothing).
Plain mode always logs; the full-screen view has a Logging dropdown, on by default. When the
run commits, git must ignore `logs/`, or the run will not start. If two runs start in the
same second, the later one's folder gets a `-1` suffix (then `-2`); `alr` does not report the
final folder name back. The git check looks at the plain folder name.

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
uv run alr -d path/to/run-folder --dry-run
```

### Testing

```sh
make test      # format, lint, and run the unit tests
make build     # build the wheel, then run the e2e tests in a container
make e2e       # run the e2e tests against the wheel already in dist/
make analyze   # format and auto-fix lint findings
make help      # list available targets
```

`make test` runs the unit tests in parallel with `pytest-xdist` (`-n auto`), and so does the Docker build. To run them
one at a time, which is the way to use a debugger, use `uv run pytest -n 0 tests/unit` (or point it at one test file or
use `-k`). A `tasks.yaml` gate may use `uv run pytest -n auto tests/unit`.

### Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/).

Version is determined from git tags:

```bash
git describe --tags --always
```
