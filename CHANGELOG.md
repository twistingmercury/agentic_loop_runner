# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `--dry-run` prints a one-line summary per task and flags `abandoned` tasks
  with a warning (FR-005).
- Shared prompt loading from `--prompt`, rejecting unsupported extensions and
  empty files (FR-001).

### Changed

- Task states simplified to `pending`, `completed`, `abandoned`. Dropped
  `in_progress` and `blocked`.
- Task fields simplified to `id`, `name`, `prompt`, `state`. Dropped `title`,
  `agent`, `checkpoint`.
- An empty `state` now means `pending` instead of being rejected.
