# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
