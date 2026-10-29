# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** the bundled skill `gralph-docs-writer` is now `alr-task-file`; `--install-skill` installs it under that name and leaves an old `gralph-docs-writer` folder in `~/.claude/skills` alone, so remove that folder yourself.

## [0.1.1] - 2026-10-11

### Added

- `shared.after_task` and `shared.after_run`: lists of actions, each with
  `action` and an optional `timeout`. `after_task` runs after every task's
  session and fails the task if any action fails. `after_run` runs once at the
  end of every run that has no failed task, including a run where every task was
  already completed, so a rerun after an `after_run` failure retries it.
- Every action gets `ALR_TASK_FILE` and `ALR_LOG_DIR` (absolute paths);
  `after_task` actions also get `ALR_TASK_ID` and `ALR_TASK_NAME`.
- The full-screen view's Actions dialog (`a`) holds both lists, "After each
  task" and "After the run".
- The dry run prints an `after_task:` or `after_run:` line for each action.

### Changed

- **Breaking:** a commit is an action now. `--commit` is removed and refused
  (exit 2); put a commit action last in `shared.after_task` instead. The
  bundled skill and template show one that adds everything from the repository
  root, then unstages `tasks.yaml` and the logs with `git reset`, so a
  gitignored run folder works.
- **Breaking:** `shared.gates` is now `shared.after_task`, and a gate's `cmd` is
  now `action`. The old names are refused, naming the new ones.
- **Breaking:** `--gate-timeout` is now `--action-timeout`; the old flag is
  refused (exit 2).
- **Breaking:** exit 4 now means an `after_run` action failed (run log result
  `run_failed`); the tasks stay `completed`. It used to mean a failed commit. A
  failed or timed-out `after_task` action fails the task and exits 1.
- **Breaking:** in the run log, the event `gate_finished` is now
  `action_finished`, and the `run_started` field `gate_timeout` is now
  `action_timeout`.
- **Breaking:** a task is no longer guaranteed its own commit. `alr` does not
  look at git.
- Plain output `gate: <cmd>` is now `action: <cmd>`; the detail-log marker
  `→ gate` is now `→ action`.
- `after_run` output goes to `task-0.log`, and its `action_finished` log lines
  carry `"task": 0`.
- A per-task `after_task` or `after_run` is refused, like a per-task `gates`;
  they are set once for the whole file, under `shared`.
- An `ALR_TASK_ID` or `ALR_TASK_NAME` inherited from an outer alr no longer
  reaches `after_run` actions.
- The view's pane "Gates & commits" is now "Actions", and the `(g)ates` button is
  `(a)ctions`. The Commit dropdown is gone.

### Removed

- The detail-log marker `→ commit`.
- **Breaking:** the commit lock (`alr.lock`), the clean-tree and ignore checks,
  and the refusal reasons `dirty-tree`, `task-file-visible`,
  `log-folder-visible`, `git-error`, and `locked`.
- **Breaking:** the `commit` field of the run log's `run_started`, the result
  `commit_failed`, and the `committed` event.

## [0.1.0] - 2026-10-11

### Changed

- **Breaking:** exit codes. A stop now exits 130 (SIGINT) or 143 (SIGTERM), not
  1; a refusal to start exits 3, not 1; a usage error (a missing `--dir`,
  neither or both of `--sandbox-settings` and `--yolo`, a run folder with no
  `tasks.yaml`) exits 2; a failed commit exits 4. A failed task or gate still
  exits 1.
- **Breaking:** a refusal's first stderr line is now
  `error: refused: <reason>: <message>`, with the reason `dirty-tree`,
  `task-file-visible`, `log-folder-visible`, `failed-tasks`,
  `invalid-task-file`, `invalid-sandbox-settings`, `log-unwritable`,
  `task-file-unwritable` (a save at startup that fails: the `--reset-failed`
  reset or, in the view, the edited gates), `git-error`, or `locked` (another
  committing run holds the repository's commit lock). A refusal leaves
  nothing on disk: no run log, and the task file as it was. `--reset-failed` is
  applied in memory first and saved only after every other check and the log
  folder have passed.
- **Breaking:** `run_started.commit` in the run log is a string, `"none"`,
  `"task"`, or `"run"`, where it was a boolean.
- **Breaking:** `--commit run` saves nothing until the commit lands. If a task
  or gate fails, that task is `failed` and the others that ran are `pending`; if
  the commit fails, the last task is `failed` and the others `pending`; a stop
  leaves every task that ran `pending`. A failed or stopped `run` leaves nothing
  completed, so its leftover work can be discarded and the run repeated. The
  README no longer tells you to commit it by hand.
- **Breaking:** with `--commit task`, `completed` also means committed. A commit
  that fails (git, a hook, a missing identity) marks the task `failed` with
  `commit failed: <reason>`, leaves the work staged, and exits 4.
- **Breaking:** a task name with a line break or any other control character
  (a tab is fine) is rejected when the file loads, with an error that names the
  task id.

### Added

- Commit safety. A committing run (`--commit task` or `run`) takes an exclusive
  lock on `alr.lock` in the repository's git directory until it ends; a second
  committing run, or a dry run with a commit mode, is refused with exit 3 and
  the reason `locked`. The kernel drops the lock when alr dies, so a killed run
  leaves none stuck. The `git add` and `git commit` alr starts run in their own
  session, with no controlling terminal, so a program that prompts on `/dev/tty`
  (a signing program, say) fails instead of waiting. A program that opens a
  terminal by path, or a GUI pinentry, can still wait, and a stop still ends it.
  On Linux the kernel also sends that git SIGTERM if alr dies
  (`PR_SET_PDEATHSIG`); elsewhere a SIGKILL of alr during a commit can still let
  the commit land. Right before every `git add`, alr checks again that git
  ignores the task file and the `logs/` directory: if a task's commands made
  either visible, nothing is staged, the task is `failed` with
  `commit failed: task file visible: <path>` or `commit failed: log folder
  visible: <logs dir>`, and the run exits 4. If git cannot answer that check,
  the task fails the same way with git's message.
- `run_finished` in the run log has `result` (`completed`, `failed`,
  `commit_failed`, or `stopped`) and `exit`, the code alr returns.
- In `run` mode a task whose gates passed is logged and shown as `passed` until
  the commit lands.
- A commit failure's reason is the first line of git's output, up to 200
  characters; `task-N.log` keeps the full text.
- The full-screen view exits with the same codes as plain mode.

### Fixed

- A refused run no longer leaves anything behind. `--reset-failed` used to
  reach the disk before the later checks, so a dirty tree, a visible log folder,
  or an unwritable `logs/` left the file reset; a failed log open left an empty
  run folder; and the view saved the gates and the reset before it opened the
  log. Quitting the view before Start now leaves the file untouched too.
- `--dry-run` refuses like a real run: a task that is `failed` exits 3
  (`failed-tasks`), and with `--commit task` or `--commit run` a log folder git
  can see exits 3 (`log-folder-visible`). Its invalid-file message no longer
  carries `failed to start loop runner:`.
- The full-screen view exits 143 after SIGTERM, not 130, and a second Stop
  press while a run is stopping no longer turns the stop into exit 0.
- A stop that lands after git has already made the commit no longer undoes it:
  the tasks that commit covers are saved `completed` before the stop is
  reported, in `task` and `run` mode, so a rerun never redoes committed work.
- A stop in `run` mode before the commit lands no longer rewrites `tasks.yaml`;
  the file stays byte for byte as it was.
- A commit failure and a failed save together still exit 4, and a successful
  `run` commit whose final save fails names the commit, so a driver does not
  blindly rerun.
- A task `name` with a NUL or another control character could crash a commit;
  it is rejected when the file loads.
- The full-screen view gives `passed` rows their own symbol, returns them to
  pending when a `run` ends other than completed, and marks a task saved
  `failed` with `✖`.
- A run log that cannot be written while another error is already propagating no
  longer hides that error.
- `--dry-run` refuses an unwritable log folder like a real run: a `logs/` that
  is read-only or a regular file exits 3 (`log-unwritable`), and the dry run
  still creates nothing.
- A refused save no longer leaves a `logs/` folder that the run created. Saving
  the task file no longer overwrites, or deletes after a failed save, a
  `tasks.yaml.tmp` of your own: it writes a uniquely named temporary file and
  replaces the task file atomically.
- The full-screen view's stop state resets when a start is refused, so the next
  run's first Stop press stops it gracefully instead of exiting at once, and a
  plain failure is no longer reported as a stop.
- The full-screen view's rows follow the saved state after a stop: a task saved
  `completed` stays completed, and only rows not saved return to pending.
- A failed save after a commit that landed names the commit hash in the error
  and exits 4 in every commit mode. After a stop, that exit 4 replaces the
  stop's 130 or 143.
- Tests now pin the edges of the task name rule (`\x00`, `\x1f`, `\x7f`, `\x9f`,
  with tab, space, and `\xa0` allowed), a failing `discard()` that must not hide
  the refusal, and `--reset-failed` with no failed task leaving `tasks.yaml`
  byte for byte as it was.

## [0.0.10] - 2026-10-08

### Changed

- `--commit` takes `none`, `task`, or `run` and defaults to `none`: `task`
  commits each completed task, and `run` makes one commit after the last task.
- The full-screen view's Commit dropdown gets a `none` option and defaults to
  it, where it used to default to per task. It starts from `--commit`.
- The `run` commit subject says `alr: 1 task completed` for one task, not
  `1 tasks`.
- A committing run whose only dirt is its own log folder now says the log folder
  must be ignored, instead of that the tree is dirty.
- `--version` also shows the OS and architecture, such as
  `alr version: 0.0.9 linux/x86_64`.

### Removed

- `--commit-after-all`; use `--commit run`.
- `--commit` without a value; use `--commit task`.

### Fixed

- A stop (Ctrl-C) that landed while alr was still starting a gate, git, or
  Claude process skipped the gentle stop: only the process itself was killed,
  with SIGKILL, and its children were left running. For git this could leave a
  stale `.git/index.lock`. The whole process group is now stopped the same way
  as for any other stop, SIGTERM first for git.
- The full-screen view could raise an error from a run event delivered after
  the app had already closed its screens; such events are now ignored.
- The README's advice after a failed or stopped committing run could destroy
  the work of tasks already marked completed (stashing or deleting leftovers in
  `run` mode). It now says what is safe in each mode.

## [0.0.9] - 2026-10-08

### Changed

- Plain mode's run log now holds gate output, git output, and Claude's stderr.
  `task-N.log` gets each line as it completes. The terminal shows the same
  output as before, but a gate now sees a pipe, not a terminal, and stdout and
  stderr can interleave slightly differently. The log's format is unchanged.
  A gate or git command that leaves a background process holding its output
  no longer holds the run up beyond 2 seconds after it exits.

## [0.0.8] - 2026-10-08

### Added

- A status line in the full-screen view that ticks every second while a run
  goes, such as `task 2 of 5 · gate 1 of 3: uv run pytest · 12.3s · run 1m 4s`.
- A Time column in the Tasks pane: the running task counts up, and each
  finished task keeps its time.
- `GateStarted` and `CommitStarted` run events, which drive the status line.
  The run log ignores them.

### Changed

- `--skip-permissions` is now `--yolo`: a short, honest name for running
  sessions with no sandbox. It works as before and still wins over a folder's
  `sandbox.json`. The permissions dialog still offers "Skip permissions (no
  sandbox)", the settings strip shows `Permissions: skip`, and the run log still
  records `"permissions": "skip"`.
- `alr` no longer aims for gralph compatibility. It began as a port of gralph,
  and its flags, formats, and behavior now change as its own needs require
  (ADR-022).

### Removed

- `--skip-permissions`, with no alias; it is now an unknown-flag error. Command
  lines that use it, including gralph ones, must use `--yolo`.

## [0.0.7] - 2026-10-08

### Added

- Full-screen terminal view (Textual), now the default on a terminal. It has a
  Tasks pane, a Gates & commits pane, and a full-width Task pane that follows the
  running task. It is keyboard-only, with hotkeys in the menu names.
- Setup dialogs for the folder, permissions, and gates, and a summary pop-up
  when a run ends.
- Runs outside a git repository (no commits; the view shows a note).
- `--commit-after-all` to make one commit at the end instead of one per task.
- `--reset-failed` (and a Reset menu item in the view) to put failed tasks back
  to pending. It cannot be combined with `--dry-run`.
- `--install-skill` to copy the bundled `gralph-docs-writer` skill.
- Plain mode now writes a run log (`run.jsonl` and `task-N.log`) like the view
  does.
- Elapsed times show as hours, minutes, and seconds.

### Changed

- `--no-tui` is now `--plain`.
- Logs always go to `<dir>/logs/<timestamp>/`, in both modes. Two runs started
  in the same second get a `-1`/`-2` suffix.
- The task file is always `<dir>/tasks.yaml`; plain mode requires `-d`.
- The run events and run log writer moved into `alr.record`.
- The unit tests run in parallel with pytest-xdist (`make test` and the Docker
  build use `-n auto`); the e2e tests stay serial.

### Removed

- `-t/--tasks`.
- `--log-dir`.

### Fixed

- Racy and root-only unit tests.
- The plain-mode tee re-copying a long partial line.

## [0.0.6] - 2026-10-06

### Added

- Task file format with a required `shared` block (containing a required `prompt`
  and optional `gates`), and an ordered `tasks` array. Each task has `id`,
  `name`, `prompt`, and optional `state` (`pending`, `completed`, `failed`) and
  `error`.
- `--dir` run folder support: finds `tasks.yaml` and, if present,
  `sandbox.json`.
- `--commit` to commit each completed task after all gates pass.
- `--sandbox-settings` and `--skip-permissions` flags to control session
  permissions; `--skip-permissions` wins over a folder's `sandbox.json`.
- `--gate-timeout <duration>` to set a time limit for every gate (e.g. `90s`,
  `10m`).
- `--dry-run` validates the task file and reports each task and gate without
  starting Claude or writing files.
- Clean stop on SIGINT or SIGTERM: kills the running process group, leaves the
  task file untouched, exits 1.
- Engine infrastructure: stream-json parsing, run events, and the run log
  (internal, not yet exposed through the CLI).

### Changed

- Replaces the previous learning-project runner entirely. This is a breaking
  change from earlier `alr` versions.
- Task file format is now `shared` + `tasks` in one YAML file (no separate
  `-p/--prompt`).
- Task states are now `pending`, `completed`, `failed`. Dropped `abandoned`,
  `in_progress`, `blocked`.
- The `-d` flag now means `--dir` run folder (was `--dry-run` short form);
  use `--dry-run` instead.
- Unit tests (`make test`) and container-based e2e tests (`make build`, `make
  e2e`) are now split and share nothing.

### Removed

- `-p/--prompt` and `prompt.md`. The shared prompt moves into the task file's
  `shared.prompt` block.
- `abandoned` task state.
- `--agent` flag and Codex support. Claude Code is the only agent.
