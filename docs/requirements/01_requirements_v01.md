# Agentic Loop Runner (alr) - Requirements

> **Version**: v01
> **Date**: 2026-09-22

## What we're building and why

`alr` (Agentic Loop Runner) is a new console app that runs Ralph loops using a
YAML PRD/task list and a shared Markdown prompt.

## Goals

- Run the supplied tasks using Claude Code.
- Keep the logic easy to follow and add complexity in small steps.
- Save task state so a rerun skips finished work.

## What's included

FR-005 (YAML validation through `--dry-run`) and FR-001 (shared prompt) are
implemented. FR-002 through FR-004 and FR-007 add task execution. Build and
check each increment before moving on.

Out of scope: automatic retries, concurrent task execution within a run,
concurrent-run protection, activity logs and result files, model-selection
flags, and platforms other than CON-002. Live agent output is
optional. The language is Python (CON-003).

## Inputs

alr reads the task file and shared prompt produced by the
[ralph-loop-docs-writer](https://github.com/twistingmercury/gralph/blob/develop/skills/ralph-loop-docs-writer/SKILL.md)
skill. The task file is
Gralph's [tasks_template.yaml](https://github.com/twistingmercury/gralph/blob/develop/skills/ralph-loop-docs-writer/templates/tasks_template.yaml)
format and the
prompt is
its [prompt_template.md](https://github.com/twistingmercury/gralph/blob/develop/skills/ralph-loop-docs-writer/templates/prompt_template.md)
format. alr's `Task` model uses the
same field names as the YAML, one to one: `id`, `name`, `prompt`, `state`.

The concept reference is [Huntley's Ralph loop description](https://ghuntley.com/loop/).
Matching Gralph's other features or its CLI is not required.

## How we'll know it works

| Outcome                   | What to check                                                                                  |
|---------------------------|------------------------------------------------------------------------------------------------|
| Useful first increment    | FR-005 checks valid and invalid sample files and warnings without file changes or agent launch |
| Sequential execution      | FR-002 runs pending tasks one at a time in list order and skips completed or abandoned ones    |
| Fresh sessions            | FR-007 runs each task in a fresh Claude Code session                                           |
| Reliable outcome handling | FR-004 distinguishes success, reported failure, crashes, and invalid results                   |
| Recoverable execution     | FR-002 saves state after each task, and a rerun starts unfinished tasks fresh                  |
| Incremental delivery      | Each implemented increment passes its checks before the next begins (CON-001)                  |

## Command-line options

| Flag                           | Execution                    | Dry-run                                 |
|--------------------------------|------------------------------|-----------------------------------------|
| `--tasks <path>`, `-t <path>`  | Required; no default         | Required; no default                    |
| `--prompt <path>`, `-p <path>` | Required; no default         | Ignored; file is not read or validated  |
| `--dry-run`                    | Selects validation-only mode | Launches no agents and changes no files |

```sh
alr -t tasks.yaml --dry-run
alr -t tasks.yaml -p prompt.md
```

## What alr must do

The checks below describe what someone using or testing alr should see.

### FR-005 - Validate YAML inputs

**Priority:** First increment. Also required before execution. **Why:** Start with a small, useful validation step
before adding task execution.

Validate YAML syntax and safe parsing, then require a top-level `tasks` list
containing at least one valid task entry with named fields. Reject missing, null, non-list, or
empty task collections. Reject the entire input if any task element is invalid;
never silently skip an element. A bare `-`, `null`, `~`, `{}`, `[]`, and `""`
are invalid task elements, including among otherwise valid tasks.

| Task field | Presence | Accepted value                                 |
|------------|----------|------------------------------------------------|
| `id`       | Required | Positive integer, unique within the task list  |
| `name`     | Required | Nonblank string                                |
| `prompt`   | Required | Nonblank string                                |
| `state`    | Optional | Exactly `pending`, `completed`, or `abandoned` |

Omission or an empty string (`""`) defaults `state` to `pending`. Reject
explicit null, whitespace-only, or unrecognized states. Whitespace-only strings
are invalid for nonblank fields.

What to check:

- Reject executable/custom object tags without constructing objects or running
  code. These checks establish safe parsing and input structure, not the safety
  of executing natural-language instructions.
- Report errors with the affected field and task index or ID when available.
  For file or syntax errors, don't invent a task index or ID.
- Dry-run requires only the task-file input. It ignores the prompt flag,
  launches no agents or task commands, and creates or changes no files.
- Dry-run writes a warning to stdout identifying each `abandoned` task and the
  need for manual intervention before execution. These warnings alone do not
  fail validation.
- Dry-run exits `0` when validation passes and nonzero when it fails.
- Normal execution applies all the same YAML checks before launching any agent.
  Validation failure stops execution with a nonzero exit.

**Related:** FR-001, FR-002, CON-001.

### FR-001 - Read the shared prompt for execution

**Priority:** Required for execution. **Why:** Use the user's existing YAML and shared prompt inputs.

Load the shared prompt identified by `--prompt` or `-p`. Reject execution before
agent launch if the flag is omitted or the file is missing, unreadable, empty,
or whitespace-only. Report the prompt-file problem. Dry-run ignores this input.

**Related:** FR-005, FR-007.

### FR-002 - Run tasks one at a time and update their state

**Priority:** Required for execution. **Why:** Make task order predictable and let the user rerun after a stop.

The runner owns task state. Execute exactly one task at a time in YAML list
order, not ID or name order. Never launch the next task while the current task
run is active.

| Existing state  | Execution behavior                                          |
|-----------------|-------------------------------------------------------------|
| Omitted or `""` | Treat as `pending`                                          |
| `pending`       | Eligible in list order                                      |
| `completed`     | Skip without launching an agent                             |
| `abandoned`     | Print the same warning dry-run prints, skip, and keep going |

If no task is `pending`, exit `0` without launching an agent.

Launch the agent for the selected task. Apply FR-004 after the agent exits;
move on only after the task's new state is saved to the task file. If the
write fails, report it and exit nonzero without advancing or claiming the
write succeeded.

On Ctrl+C, stop the active agent, leave the task
`pending`, and exit nonzero without advancing. There are no checkpoints and
no `in_progress` state; a later run starts the task again from scratch in a
fresh session. Do not claim that unsaved work was saved after an interruption.

**Related:** FR-003, FR-004, FR-007.

### FR-003 - Give the agent its instructions

**Priority:** Required for execution. **Why:** Give each fresh session the task context it needs.

Combine the shared prompt and the selected task into one prompt and pass it
to the agent: the shared prompt, a blank line, a `<id>: <name>` heading, a
blank line, then the task's `prompt`. The ID and name are there so a human
reading the prompt can tell which task it is. alr adds nothing else, such as
the task-file path. The shared prompt is responsible for telling the agent to
end with the FR-004 result line. alr creates no files for the agent.

**Related:** FR-002, FR-004.

### FR-004 - Read the agent's result and decide what happens next

**Priority:** Required for execution. **Why:** Use one small, machine-readable line to decide what happened.

The agent ends its output with one JSON object on the last non-blank line of
stdout:

```json
{
  "state": "completed"
}
```

| Field   | Required value                     |
|---------|------------------------------------|
| `state` | Exactly `completed` or `abandoned` |

Reject invalid JSON, a missing or unrecognized `state`, extra fields, and
Markdown around the JSON. The runner reads this line and nothing else to decide the task's outcome.
No result file is written.

| Attempt outcome                                     | Runner behavior                                                  |
|-----------------------------------------------------|------------------------------------------------------------------|
| Exit `0` and a valid `completed` result             | Save `completed`, then advance; exit `0` when nothing is pending |
| Exit `0` and a valid `abandoned` result             | Save `abandoned`, stop the run, and exit nonzero                 |
| Nonzero exit, crash, or missing/invalid result line | Leave `pending`, stop the run, and exit nonzero                  |
| Ctrl+C                                              | Apply FR-002 interruption behavior                               |

There are no automatic retries: at most one attempt per task in a run. An
explicit rerun is separate from an automatic retry. A reported `completed`
result with a nonzero process exit cannot count as completion.

Before reporting completion, the agent must follow the accepted prompt's steps
for checking its work, cleaning up, and making any
authorized commits to the project. alr does not repeat those steps.

If a required state write fails, report the failure and exit nonzero without
advancing or claiming the write succeeded.

**Related:** FR-002, FR-003.

### FR-007 - Launch Claude Code

**Priority:** Required for execution. **Why:** Run the user's chosen coding agent without adding model configuration.

Launch Claude Code in the same working directory where the user started alr.
Every task run starts a fresh session, including reruns of a task; do not
reuse previous conversation sessions. Supply FR-003 context to the agent.

Use the installed CLI's existing model and authentication settings. Add no
alr model-selection flags. Check that the agent receives the agreed task
details and instructions and produces the expected result line.

**Related:** FR-001 through FR-004.

## Keeping it simple

### NFR-001 - Understandable execution logic

**State:** Confirmed by the user; applies throughout development.

Keep execution logic easy to follow and introduce complexity only to support the
next required behavior. Check this during design and implementation review.
The user has not set numeric limits on code size, dependencies, or complexity.
CON-001 describes how to build in small steps.

### Optional Enhancement - Live output

Showing agent output live in the terminal is desirable, not required. It may be
added later and does not need to be ready for delivery.

## Delivery requirements

### CON-001 - Incremental delivery

Implement, test, and confirm basic functionality before adding complexity.
Sequence the remaining requirements into small increments; do not implement
the full runner in one step or import unrelated Gralph features.

### CON-002 - Platforms

Support only Linux and macOS. Build and verify required behavior on both.
Other operating systems, including Windows, are out of scope.

### CON-003 - Language

Implement in Python >= 3.12, managed with uv.

## Implementation decisions

The following details are implementation decisions, not requirements:

- How to launch and stop the CLI while respecting its existing settings,
  starting fresh sessions, and handling interruptions as described above.
- Consistent error messages, exact nonzero exit codes, and how to save states.

These decisions must not introduce automatic retries, concurrent-run protection,
or other out-of-scope features.
