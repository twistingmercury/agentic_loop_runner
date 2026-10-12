# Agentic Loop Runner (alr)

> **Maturity Level**: Emerging - in active, initial development; expect breaking changes.</br>
> **Version**: v0.1.0
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

`alr` runs ["ralph loops"](https://ghuntley.com/loop/) with Claude Code. It began as the
Python port of [gralph](https://github.com/twistingmercury/gralph) and has since evolved
beyond it, so its flags, formats, and behavior can differ. [CHANGELOG.md](CHANGELOG.md) records each break
([ADR-022](docs/design/02_architectural_decisions.md#adr-022-alr-has-evolved-beyond-gralph)).

A run starts from one `tasks.yaml`:

```yaml
shared:
  prompt: |
    You are working on the widget service. End your output with one JSON line:
    {"state": "completed"} or {"state": "failed", "error": "why"}.
  after_task:
    - action: uv run pytest
      timeout: 10m
    - action: >-
        git add -A -- . ":(exclude)$ALR_TASK_FILE" ":(exclude)$ALR_LOG_DIR" &&
        (git diff --cached --quiet || git commit -s -m "$ALR_TASK_NAME")
  after_run:
    - action: git push --dry-run
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

# Install the bundled task-file skill into ~/.claude/skills and exit.
alr --install-skill
```

In a terminal, `alr` opens a full-screen view: a settings strip (a Logging
dropdown, which locks once a run starts), a menu row, three panes (tasks and
actions on top, activity across the full width below), and dialogs that ask for
whatever the flags and the task file leave open (run folder, permissions,
actions). Nothing runs, and nothing is written, until you press Start. It is
keyboard-driven and never captures the mouse: the menu button names show their
keys (there is no footer): `s` starts a run (the button then reads `Stop (^c)`),
Ctrl-C stops it, `q` quits, `f`, `p`, `a` open the folder, permissions, and
actions dialogs (Folder, Permissions, Reset, and Quit grey out during a run),
`r` resets failed tasks to pending (after you confirm), `t` and `x` show
`tasks.yaml` and `sandbox.json` (these two open any time), and Esc closes a
dialog. The Actions dialog holds two lists, "After each task" and "After the
run". The Actions button does nothing while a run is going. The Task pane shows
only the last 5,000 lines of a task, with no warning; the run log keeps every
line. While a run goes, a status line above the Task pane ticks every second and
says where the run is, such as
`task 2 of 5 · action 1 of 3: uv run pytest · 12.3s · run 1m 4s` (the task out
of the tasks pending at Start, the step, the step's time, and the run's time).
In a narrow window the step is cut with `…` so the times still show. The line is
only `task n of N · run <time>` between tasks. The Tasks pane's Time column
counts the running task up and keeps each finished task's time. The view keeps
run logs in `<folder>/logs`. Use `--plain`, a non-terminal, or `--dry-run` for
plain output.

| Flag                         | Short | Description                                                                              |
| ---------------------------- | ----- | ---------------------------------------------------------------------------------------- |
| `--dir <folder>`             | `-d`  | Run folder: uses `<folder>/tasks.yaml` and, if present, `<folder>/sandbox.json`. Required in plain mode; the view asks if it is missing |
| `--dry-run`                  |       | Validate and report only; prints each action's limit in `after_task:` and `after_run:` lines |
| `--sandbox-settings <path>`  |       | Run sessions in Claude's sandbox with this settings file                                 |
| `--yolo`                     |       | Run sessions with no sandbox (`--dangerously-skip-permissions`); wins over a folder's `sandbox.json`. Replaces `--skip-permissions` |
| `--reset-failed`             |       | Set every failed task back to pending before the run, without asking                     |
| `--install-skill`            |       | Install the bundled `gralph-docs-writer` skill into `~/.claude/skills` and exit          |
| `--plain`                    |       | Use plain output instead of the full-screen view                                         |
| `--action-timeout <duration>` |      | Limit for every action, such as `90s` or `10m`; overrides each action's own timeout      |
| `--version`                  | `-v`  | Print the version, the OS, and the architecture                                          |

A real run needs one of `--sandbox-settings` (or a folder's `sandbox.json`) or
`--yolo`; the full-screen view asks in a dialog when neither is given.

### Actions

An action is a shell command that `alr` runs itself, through `sh -c`, in its own
working directory. The task file has two lists of them under `shared`. Each entry
has `action` (required) and `timeout` (optional, such as `90s` or `10m`);
nothing else is allowed.

| List         | When it runs                                         | If an action fails                                      |
| ------------ | ---------------------------------------------------- | ------------------------------------------------------- |
| `after_task` | After each task's session reports `completed`, in order | The task is `failed` and the later actions do not run; exit 1 |
| `after_run`  | Once at the end of every run that has no failed task | The run stops; the tasks stay `completed`; exit 4        |

- A task is `completed` only after its session reported success and every
  `after_task` action exited 0. A failed or timed-out action saves the task as
  `failed`, with that action's text as the reason.
- `after_run` also runs when every task was already completed, so a rerun after
  an `after_run` failure retries it.
- `--action-timeout` sets the time limit of every action; without it, an action
  uses its own `timeout`, else 10 minutes. A timeout counts as a failure.
- An absent `after_task` is undecided: the view asks for it. `after_task: []` is
  a decision that there are none. An absent `after_run` and `after_run: []` both
  mean none.
- `alr` sets these environment variables on the action's process. Use them in
  double quotes, as `"$ALR_TASK_NAME"`, so a value is data to the shell and never
  code. Every action gets `ALR_TASK_FILE` and `ALR_LOG_DIR`, both absolute
  paths; `after_task` actions also get `ALR_TASK_ID` and `ALR_TASK_NAME`.
- `alr` does not undo an action, so an action that cannot be repeated, such as a
  commit, goes last in `after_task`.

A commit is just an action: `alr` has no commit mode, does not look at git, and
does not promise a task its own commit. The commit action takes whatever is in
the work tree, so keep `tasks.yaml` and the logs out of it, as the example above
does. It uses the long `:(exclude)` form because both variables are absolute
paths and git ignores the short `:!` form for an absolute path, so it would
exclude nothing. The `git diff --cached --quiet ||` part is there because a task
that changed nothing has nothing to commit; without it a clean tree makes the
action fail. The run folder (`tasks.yaml` and `logs/`) must be inside the git
work tree for this action: if it is outside, git refuses the `:(exclude)` paths
(exit 128), so drop the two entries and use a plain `git add -A`, since git
cannot see those files anyway. A sandbox settings file inside the repository needs its own
`.gitignore` line or `:(exclude)` entry. Signing, hooks, the message, and any
push are the action's job.

### Migrating from commit modes and gates

These were removed with no aliases; each is refused with a message that names
its replacement:

| Before                      | Now                                                      |
| --------------------------- | -------------------------------------------------------- |
| `--commit task`             | A commit action last in `shared.after_task`; `--commit` exits 2 |
| `--commit run`              | A commit action in `shared.after_run`; it runs after every task has already been saved `completed` |
| `shared.gates`              | `shared.after_task`                                      |
| `cmd:` in a gate            | `action:`                                                |
| `--gate-timeout`            | `--action-timeout`                                       |

### Exit codes

| Code  | Meaning                                                                                  |
| ----- | ---------------------------------------------------------------------------------------- |
| `0`   | Every task completed (or the dry run found the file valid)                                |
| `1`   | A task or one of its `after_task` actions failed, or the run hit an error such as a failed save |
| `2`   | Usage error: a bad or missing flag, a removed flag, or a run folder with no `tasks.yaml`  |
| `3`   | Refused to start; nothing ran and nothing was left on disk: no run log, and the task file unchanged |
| `4`   | An `after_run` action failed; every task stays `completed`, and a rerun retries `after_run` |
| `130` | Stopped by SIGINT (Ctrl-C)                                                                |
| `143` | Stopped by SIGTERM                                                                        |

A refusal's first line on stderr is `error: refused: <reason>: <message>`, such as
`error: refused: failed-tasks: ...`. The reason is one of:

| Reason                     | Why alr will not start                                                                |
| -------------------------- | ------------------------------------------------------------------------------------- |
| `failed-tasks`             | The task file holds a failed task; fix it and set it to pending (or `--reset-failed`) |
| `invalid-task-file`        | The task file cannot be read or is not valid                                          |
| `invalid-sandbox-settings` | The sandbox settings file is unreadable or invalid                                    |
| `log-unwritable`           | The run log folder cannot be created or opened                                        |
| `task-file-unwritable`     | A save at startup failed: the `--reset-failed` reset, or edited actions in the view   |

The run log's `run_finished` line has the `result` (`completed`, `failed`,
`run_failed`, or `stopped`), the `error`, and the `exit` code. Every action
writes an `action_finished` line with its result and the limit it ran under, and
`run_started` records `action_timeout` when `--action-timeout` was passed. A `--dry-run` is refused like a real run,
in the same order and with the same exit 3, including for a task that is
`failed` (the table prints first). The full-screen view exits with the same
codes: a stop by SIGTERM is `143`, and a Stop press or Ctrl-C is `130`. A refused
run leaves the task file as it was and nothing under `logs/`, not even a `logs/`
it had to create; `--reset-failed` is saved only after every check has passed.

A real run logs to `<folder>/logs/<timestamp>/` (a dry run writes nothing). Plain
mode always logs; the full-screen view has a Logging dropdown, on by default. If
two runs start in the same second, the later one's folder gets a `-1` suffix
(then `-2`); `alr` does not report the final folder name back.

In plain mode, `task-N.log` also holds each action's output and Claude's stderr,
so you can see why an action failed. The terminal shows the same output, but an
action sees a pipe instead of a terminal.

## How it works

Tasks run one at a time, in file order, each in a fresh `claude --print` session. The
shared prompt and the task go to Claude on stdin. The runner owns task state: Claude
reports its outcome as one JSON line at the end of its output. When a task completes,
the file's `shared.after_task` actions run, and the task counts as done only if every
action exits zero. The file is saved after every task, and the first failed task stops the
run. After the last task, `shared.after_run` runs once.

Each task has an `id`, a `name`, a `prompt`, and an optional `state` of `pending`,
`completed`, or `failed`.

Full design: [docs/design/00_overview.md](docs/design/00_overview.md). The build plan is
in [docs/plans/](docs/plans/).

## Key Considerations

- Supported platforms are Linux and macOS only.
- No automatic retries: at most one attempt per task per run.
- Claude Code is the only supported agent. It uses your installed CLI's own model and
  authentication settings; `alr` adds no model-selection flags.
- The task file is code: its prompts go to Claude and its actions run through `sh -c`.
  Do not run `alr` on a task file you do not trust.
- This is a breaking change from earlier `alr` builds: there is no `-p/--prompt` and no
  `prompt.md`, and the `abandoned` state is gone.

### Action limits

- **`alr` ends only what it started.** After a SIGKILL of `alr`, an action or a
  Claude session can keep running. Prefer Ctrl-C or SIGTERM, which lets `alr` stop
  them, and after a SIGKILL check that nothing is still running before you rerun.
- **Nothing stops two runs from sharing a work tree.** `alr` takes no lock, so two
  runs in one tree can commit each other's changes.
- **A task is not guaranteed its own commit.** A commit action takes whatever is
  in the work tree.
- **An earlier action's effects stay when a later one fails.** Order is yours to
  get right; see [Actions](#actions).

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
use `-k`). A `tasks.yaml` action may use `uv run pytest -n auto tests/unit`.

### Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/).

Version is determined from git tags:

```bash
git describe --tags --always
```
