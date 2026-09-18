# Agentic Loop Runner (alr)

> **Maturity Level**: Emerging - in active, initial development; expect breaking changes.  
> **Version**: v0.0.2

---

## Table of Contents

- [Why this when you have gralph?](#why)
- [Usage](#usage)
- [How it works](#how-it-works)
- [Key Considerations](#key-considerations)
- [Development Considerations](#development-considerations)
- [Versioning](#versioning)


## Why this when you have gralph?

I don't like how [gralph](https://github.com/twistingmercury/gralph) evolved. The code is too complicated.
Why? I relied too much on Claude and Codex. Yeah, it works, but...it's hard for me to follow.
Part of the reason it got so complex is that I made the requirements too complex. Durable task states mainly.
That's not needed with a good ole Ralph loop.

So, I'm writing a _new_ agentic loop runner in Python. Why Python? Because I need to learn
Python. I'm using Claude to help tutor me, but I decide on how I want it implemented, I write
the code, and I write tests. Claude is just there to offer advice and help when I get stuck.

## Usage

`alr` is a command line tool used to run ["ralph loops"](https://ghuntley.com/loop/).

```sh
# Validate a task file only; launches no agent and changes no files.
alr -t tasks.yaml --dry-run

# Run the tasks with a coding agent.
alr -t tasks.yaml -a claude -p prompt.md
alr -t tasks.yaml -a codex -p prompt.md
```

| Flag              | Short | Required            | Description                                        |
| ----------------- | ----- | ------------------- | -------------------------------------------------- |
| `--tasks <path>`  | `-t`  | Always              | YAML file holding the task list                    |
| `--prompt <path>` | `-p`  | Execution only      | Markdown file with the shared prompt for each loop |
| `--agent <value>` | `-a`  | Execution only      | Coding agent to launch: `claude` or `codex`        |
| `--dry-run`       | `-d`  | No                  | Validation-only mode; ignores `--prompt`/`--agent` |

## How it works

> Note: the tasks yaml and prompt markdown are created using this skill: [ralph-loop-docs-writer](https://github.com/twistingmercury/mnemonic-agents-skills/blob/develop/shared/skills/ralph-loop-docs-writer/SKILL.md)

A run reads a YAML task file and a shared Markdown prompt. Tasks execute one at
a time in list order, each in a fresh agent session. The runner owns task
status; the agent writes its own activity log and JSON result and updates its
checkpoint so an interrupted run can be resumed by an explicit restart.

Current state: only **FR-005 (YAML input validation)** is in progress. Task
parsing and validation live in `src/alr/tasks.py`; argument parsing lives in
`src/alr/cli.py`. Task execution (FR-001 through FR-004, FR-006, FR-007) is not
implemented yet.

Full requirements: [docs/requirements/01_requirements_v01.md](docs/requirements/01_requirements_v01.md).

## Key Considerations

- Supported platforms are Linux and macOS only.
- No automatic retries: at most one attempt per task per run.
- Logs and JSON results are written to `alr_activity/` under the directory
  where `alr` was started. There is no flag to change this.
- Backends use each installed CLI's own model and authentication settings;
  `alr` adds no model-selection flags.
- The YAML task's `agent` field names a specialist for the prompt. It does not
  choose which CLI `alr` launches; `--agent` does that.

## Development Considerations

Requires Python >= 3.12 and [uv](https://docs.astral.sh/uv/).

### Quick Start

```sh
uv sync
uv run alr -t tests/data/tasks.yaml --dry-run
```

### Testing

```sh
make test      # sync, lint, and run pytest
make analyze   # format and auto-fix lint findings
make help      # list available targets
```

### Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/).

Version is determined from git tags:

```bash
git describe --tags --always
```
