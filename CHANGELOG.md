# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
