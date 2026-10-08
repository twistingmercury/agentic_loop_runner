# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
