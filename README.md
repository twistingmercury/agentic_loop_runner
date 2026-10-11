# Agentic Loop Runner (alr)

> **Maturity Level**: Emerging - in active, initial development; expect breaking changes.</br>
> **Version**: v0.0.10
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
alr -d .local/feature --commit task

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
task, with no warning; the run log keeps every line. While a run goes, a status line above the Task
pane ticks every second and says where the run is, such as
`task 2 of 5 · gate 1 of 3: uv run pytest · 12.3s · run 1m 4s` (the task out of the tasks
pending at Start, the step, the step's time, and the run's time). In a narrow window the step is
cut with `…` so the times still show. The line is only `task n of N · run <time>` between tasks.
The Tasks pane's Time column counts the running task up and keeps each finished task's time. The view's Commit dropdown
offers none (the default), per task, and after all, and starts from `--commit` (outside a git work tree, choosing
a commit mode shows a note that nothing will be committed), and it keeps run logs in `<folder>/logs`, which git must ignore when it commits. Use
`--plain`, a non-terminal, or `--dry-run` for plain output.

| Flag                        | Short | Description                                                                              |
| --------------------------- | ----- | ---------------------------------------------------------------------------------------- |
| `--dir <folder>`            | `-d`  | Run folder: uses `<folder>/tasks.yaml` and, if present, `<folder>/sandbox.json`. Required in plain mode; the view asks if it is missing |
| `--dry-run`                 |       | Validate and report only                                                                 |
| `--sandbox-settings <path>` |       | Run sessions in Claude's sandbox with this settings file                                 |
| `--yolo`                    |       | Run sessions with no sandbox (`--dangerously-skip-permissions`); wins over a folder's `sandbox.json`. Replaces `--skip-permissions` |
| `--commit {none,task,run}`  |       | When to commit: `none` (the default) never touches git, `task` commits each task after its gates pass and marks it completed only once the commit succeeds, `run` commits once after the last task (a failed or stopped run makes no commit) |
| `--reset-failed`            |       | Set every failed task back to pending before the run, without asking                     |
| `--install-skill`           |       | Install the bundled `gralph-docs-writer` skill into `~/.claude/skills` and exit          |
| `--plain`                   |       | Use plain output instead of the full-screen view                                         |
| `--gate-timeout <duration>` |       | Limit for every gate, such as `90s` or `10m`; overrides each gate's own timeout          |
| `--version`                 | `-v`  | Print the version, the OS, and the architecture                                          |

A real run needs one of `--sandbox-settings` (or a folder's `sandbox.json`) or
`--yolo`; the full-screen view asks in a dialog when neither is given.
`--commit` needs a value; a bare `--commit`, a value other than `none`, `task`, or `run`, and the old
`--commit-after-all` are usage errors (argparse rejects them and exits 2). In the view's Commit dropdown, "per task"
is `task` and "after all" is `run`. `--commit` commits into the repository
around the current directory, not the one around `-d`: the Claude sessions and
the gates run in the current directory, so run `alr` from inside the repository
they should edit.

### Exit codes

| Code  | Meaning                                                                                  |
| ----- | ---------------------------------------------------------------------------------------- |
| `0`   | Every task completed (or the dry run found the file valid)                                |
| `1`   | A task or gate failed, or the run hit an error such as a failed save                      |
| `2`   | Usage error: a bad or missing flag, or a run folder with no `tasks.yaml`                  |
| `3`   | Refused to start; nothing ran and nothing was left on disk: no run log, and the task file unchanged |
| `4`   | A commit failed (or the pre-add re-check found a path visible); the task is `failed` and its work stays staged |
| `130` | Stopped by SIGINT (Ctrl-C)                                                                |
| `143` | Stopped by SIGTERM                                                                        |

A refusal's first line on stderr is `error: refused: <reason>: <message>`, such as
`error: refused: dirty-tree: --commit needs a clean work tree; commit, stash, or remove:`.
The reason is one of:

| Reason                     | Why alr will not start                                                                |
| -------------------------- | ------------------------------------------------------------------------------------- |
| `dirty-tree`               | A committing run found uncommitted changes                                            |
| `task-file-visible`        | A committing run found the task file visible to git                                   |
| `log-folder-visible`       | A committing run found the log folder visible to git                                  |
| `locked`                   | Another committing run holds this repository's commit lock                            |
| `failed-tasks`             | The task file holds a failed task; fix it and set it to pending (or `--reset-failed`) |
| `invalid-task-file`        | The task file cannot be read or is not valid                                          |
| `invalid-sandbox-settings` | The sandbox settings file is unreadable or invalid                                    |
| `log-unwritable`           | The run log folder cannot be created or opened                                        |
| `task-file-unwritable`     | A save at startup failed: the `--reset-failed` reset, or edited gates in the view     |
| `git-error`                | git failed while alr looked at the repository, or alr could not open or lock `alr.lock` |

A failed commit's reason is the first line of git's (or the hook's) output, in the task's
`error` as `commit failed: <reason>`; `task-N.log` keeps the full text. The run log's
`run_finished` line has the `result` (`completed`, `failed`, `commit_failed`, or `stopped`),
the `error`, and the `exit` code. `run_started` has `"commit": "none"`, `"task"`, or `"run"`.
A `--dry-run` is refused like a real run, in the same order and with the same exit 3, including for a task that is
`failed` (the table prints first). The full-screen view exits with the same codes: a stop by SIGTERM is `143`, and a
Stop press or Ctrl-C is `130`. A refused run leaves the task file as it was and nothing under `logs/`, not even a `logs/` it had to create;
`--reset-failed` is saved only after every check has passed.

A `run` commit shows in `git log` with the subject `alr: <n> tasks completed` (`alr: 1 task completed` for one task) and
a body that lists `<id>: <name>` for each task that ran. A `task` commit uses the task's name as its subject.

A committing run (`task` or `run`) needs the task file and the run's `logs/`
folder kept out of git: each must be ignored by git or sit outside the
repository. Otherwise the run refuses to start with an error such as
`--commit needs the task file ignored by git or outside the repository:
tasks.yaml`. For a run folder `<folder>` inside the repository, add these lines
to `.gitignore`:

```gitignore
<folder>/tasks.yaml
<folder>/logs/
```

Ignoring the whole folder with `<folder>/` works too. Do not run `git clean -fdx`
in the repository: `-x` also deletes ignored files, which includes the task file
and the logs.

A committing run is guarded three ways, on top of those checks:

- **One committing run per repository.** The run takes an exclusive lock on
  `alr.lock` in the repository's git directory (`.git/alr.lock`; a linked worktree
  has its own git directory, so its own lock) and keeps it until it ends. A second
  committing run, or a `--dry-run` with `--commit task` or `run`, is refused with
  exit 3 and the reason `locked`, whether it comes from plain mode or the full-screen
  view. The check comes after the task file check and before the log folder and
  clean tree checks, because a run that holds the lock leaves the tree dirty. The
  kernel drops the lock when alr dies, even by SIGKILL, so a killed run never leaves
  a stuck lock; the `alr.lock` file a run leaves behind is not a lock, and you can
  leave it or delete it.
- **The ignore rules are checked again before every `git add`.** A task's own
  commands can edit `.gitignore`. If git then sees the task file or the run's log
  folder, nothing is staged, the task is `failed` with `commit failed: task file
  visible: <path>` or `commit failed: log folder visible: <folder>`, and the run
  exits 4. In `--commit run` the check runs once, before the one commit. The log
  folder is only checked when the run keeps a log.
- **git runs in its own session and dies with alr.** The `git add` and `git commit`
  that alr starts have no controlling terminal, so a signing program that wants to
  prompt (a GPG pinentry) fails instead of waiting on a person. On Linux the kernel
  also sends that git SIGTERM if alr dies, even by SIGKILL (`PR_SET_PDEATHSIG`), so
  git removes its index lock and no commit lands after alr is gone. On other
  platforms there is no such stop: a SIGKILL of alr during a commit can still let
  the commit land afterwards.

After a failed or stopped run, the work tree holds the leftovers, and the next
committing run refuses to start until the tree is clean; the error lists the
files. A failed run leaves its task `failed` and needs `--reset-failed`. A
stopped run leaves the interrupted task `pending`, which needs no reset. In
`task` mode a task is `completed` only after its commit lands. A stop that
arrives after git has already made a commit does not undo it: the tasks that
commit covers are saved `completed`, so a rerun never redoes committed work.
Clean up by mode:

- `task`: each earlier task is already committed, so the leftovers are only the
  work of the failed or interrupted task. Discard it with `git reset` (this
  unstages and changes no files), then `git restore .` and `git clean -fd`.
  `git clean -fd` leaves the ignored task file and `logs/` alone. Then run
  again, with `--reset-failed` after a failure.
- `run`: `tasks.yaml` is saved once, at the end, and no task is marked
  `completed` until the one commit lands. A stop before the commit lands does
  not touch the file at all. A failed or stopped run leaves
  nothing completed: the failed task is `failed` and every other task that ran
  is back to `pending`. The leftovers are only work this run made, so discard
  them the same way as in `task` mode, then run again, with `--reset-failed`
  after a failure. Tasks completed by an earlier run stay completed; their work
  is already in that run's commit.

A real run logs to `<folder>/logs/<timestamp>/` in every commit mode (a dry run
writes nothing). Plain mode always logs; the full-screen view has a Logging
dropdown, on by default. If two runs start in the same second, the later one's
folder gets a `-1` suffix (then `-2`); `alr` does not report the final folder
name back. The git check looks at the plain folder name.

In plain mode, `task-N.log` also holds each gate's output, git's output, and Claude's stderr, so you can
see why a gate failed. The terminal shows the same output, but a gate sees a pipe instead of a terminal.

## How it works

Tasks run one at a time, in file order, each in a fresh `claude --print` session. The
shared prompt and the task go to Claude on stdin. The runner owns task state: Claude
reports its outcome as one JSON line at the end of its output. When a task completes,
the file's `shared.gates` run, and the task counts as done only if every gate exits
zero. The file is saved after every task, and the first failed task stops the run. With
`--commit run`, the file is saved once, at the end, after the one commit lands.

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
