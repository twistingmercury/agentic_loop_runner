# Agentic Loop Runner (alr) - Requirements

> **Version**: v01
> **Date**: 2026-09-14
> **Notes**: Consolidated requirements from the capture-requirements trial.

## What we're building and why

`alr` (Agentic Loop Runner) is a new console app that runs Ralph loops using a
YAML PRD/task list and a shared Markdown prompt. 

## Goals

- Run the supplied tasks using Claude Code or Codex.
- Keep the logic easy to follow and add complexity in small steps.
- Save task progress, logs, and results so work can resume when the user restarts.

## What's included

The first increment implements YAML validation through `--dry-run` (FR-005).
Later increments add the required task-running behavior in FR-001 through
FR-004, FR-006, and FR-007. Build and check each increment
before moving on. There's no need to pause for user review between increments.

Out of scope: automatic retries, concurrent task execution within a run,
concurrent-run protection (withdrawn FR-008), flags to change the log and result directory,
model-selection flags, and platforms other than CON-002. Live agent output is
optional. The user chose Python (CON-003); architects must not change the language.

## Where these requirements come from

These requirements come from the conversation with the user. The supplied
templates describe the task file, shared instructions, logs, and results:

- `shared/skills/ralph-loop-docs-writer/templates/loop_tasks_template_v02.yaml`
- `shared/skills/ralph-loop-docs-writer/templates/loop_prompt_template_v02.md`
- `shared/skills/ralph-loop-docs-writer/templates/activity_log_template_v02.md`
- `shared/skills/ralph-loop-docs-writer/templates/activity_result_template_v02.json`

These paths are relative to the mnemonic-agents-skills repository root. The
concept reference is [Huntley's Ralph loop description](https://ghuntley.com/loop/).
Where these requirements differ from the templates, follow this document.
The templates are starting points; the files used in a run contain details for
that project. The source templates have not changed during this trial.

Differences alr needs to support:

| Template convention                   | Required alr behavior                                      |
| ------------------------------------- | ---------------------------------------------------------- |
| Task has `title`, `agent`, `checkpoint` | Task has only `id`, `name`, `prompt`, and `state`         |
| Original statuses exclude `blocked`   | States are exactly `pending`, `completed`, `abandoned`    |
| Runner reserves an empty activity log | Agent creates and writes both files; runner supplies paths |
| Runtime may retry automatically       | No automatic retries                                       |
| Runtime references name Gralph        | Generated instructions must follow alr's requirements      |

Other Gralph features and matching its CLI are not required unless stated here.

## How we'll know it works

| Outcome                   | What to check                                                                                  |
| ------------------------- | ---------------------------------------------------------------------------------------------- |
| Useful first increment    | FR-005 checks valid and invalid sample files and warnings without file changes or agent launch |
| Sequential execution      | FR-002 processes eligible tasks in list order and checks the whole list before starting        |
| Both backends supported   | FR-007 runs tasks in fresh sessions through both supported CLIs                                |
| Reliable outcome handling | FR-004 distinguishes success, reported failure, crashes, and invalid results                   |
| Recoverable execution     | FR-002 and FR-003 preserve checkpoints and earlier logs and results across explicit restarts   |
| Incremental delivery      | Each implemented increment passes its checks before the next begins (CON-001)                  |

## Command-line options

| Flag                            | Execution                                         | Dry-run                                                    |
| ------------------------------- | ------------------------------------------------- | ---------------------------------------------------------- |
| `--tasks <path>`, `-t <path>`   | Required; no default                              | Required; no default                                       |
| `--agent <value>`, `-a <value>` | Required; exactly `codex` or `claude`; no default | Ignored, including supplied value and backend availability |
| `--prompt <path>`, `-p <path>`  | Required; no default                              | Ignored; file is not read or validated                     |
| `--dry-run`                     | Selects validation-only mode                      | Launches no agents and changes no files                    |

```sh
alr -t tasks.yaml --dry-run
alr -t tasks.yaml -a claude -p prompt.md
alr -t tasks.yaml -a codex -p prompt.md
```

The CLI `--agent` chooses Claude Code or Codex. The YAML task's `agent` field
names the specialist, as described in the task template; it does not choose
which CLI to launch.

## What alr must do

These requirements reflect confirmed user decisions and the accepted templates.
Their IDs stay the same even where the order has changed. The checks below
describe what someone using or testing alr should see.

### FR-005 - Validate YAML inputs

**Priority:** First increment. Also required before execution.
**Why:** Start with a small, useful validation step before adding task execution.

Validate YAML syntax and safe parsing, then require a top-level `tasks` list
containing at least one valid task entry with named fields. Reject missing, null, non-list, or
empty task collections. Reject the entire input if any task element is invalid;
never silently skip an element. A bare `-`, `null`, `~`, `{}`, `[]`, and `""`
are invalid task elements, including among otherwise valid tasks.

| Task field   | Presence | Accepted value                                                           |
| ------------ | -------- | ------------------------------------------------------------------------ |
| `id`         | Required | Positive integer, unique within the task list                            |
| `name`       | Required | Nonblank string                                                          |
| `prompt`     | Required | Nonblank string                                                          |
| `state`      | Optional | Exactly `pending`, `completed`, or `abandoned`                           |

Omission or an empty string (`""`) defaults `state` to `pending`. Reject
explicit null, whitespace-only, or unrecognized states. Whitespace-only strings
are invalid for nonblank fields.

What to check:

- Reject executable/custom object tags without constructing objects or running
  code. These checks establish safe parsing and input structure, not the safety
  of executing natural-language instructions.
- Report errors with the affected field and task index or ID when available.
  For file or syntax errors, don't invent a task index or ID.
- Dry-run requires only the task-file input. It ignores backend and prompt flags,
  launches no agents or task commands, and creates or changes no files.
- Dry-run writes a warning to stdout identifying each `abandoned` task and the
  need for manual intervention before execution. These warnings alone do not
  fail validation.
- Dry-run exits `0` when validation passes and nonzero when it fails.
- Normal execution applies all the same YAML checks before launching any agent.
  Validation failure stops execution with a nonzero exit.

**Related:** FR-001, FR-002, CON-001.

### FR-001 - Read the shared prompt for execution

**Priority:** Required for execution.
**Why:** Use the user's existing YAML and shared prompt inputs.

Load the shared prompt identified by `--prompt` or `-p`. Reject execution before
agent launch if the flag is omitted or the file is missing, unreadable, empty,
or whitespace-only. Report the prompt-file problem. Dry-run ignores this input.

**Related:** FR-005, FR-007.

### FR-002 - Run tasks one at a time and update their status

**Priority:** Required for execution.
**Why:** Make task order predictable and let the user restart interrupted work.

The runner owns task status. Execute exactly one task at a time in YAML list
order, not ID or title order. Never launch the next task while the current task
run is active.

| Existing status          | Execution behavior                                                                       |
| ------------------------ | ---------------------------------------------------------------------------------------- |
| Omitted                  | Treat as `pending`                                                                       |
| `pending`                | Eligible in list order                                                                   |
| `in_progress`            | Resume on an explicitly started run using the saved checkpoint and a fresh session       |
| `completed`              | Skip without launching an agent                                                          |
| `blocked` or `abandoned` | Block the entire run before any task launch; report manual intervention and exit nonzero |

Check the entire list for `blocked` or `abandoned` before launching even an
earlier task. If all tasks are completed, exit `0` without launching an agent.

Save the selected task's status as `in_progress` before agent launch. If the
write fails, stop without launching it. Apply FR-004 after the agent exits;
move on only after the task completes successfully and its status is saved. Preserve the
agent's checkpoint updates when writing status.

On Ctrl+C, stop the active agent, preserve saved checkpoints, logs, and results,
leave the task `in_progress`, and exit nonzero without advancing. A later
explicit run resumes from saved progress, not an earlier conversation session.
Do not claim that unsaved work was saved after an interruption.

**Related:** FR-003, FR-004, FR-007.

### FR-003 - Give the agent its instructions and output paths

**Priority:** Required for execution.
**Why:** Give each fresh session the task context and keep the information needed to resume work.

Supply the selected task's ID, title, specialist, full prompt, saved checkpoint,
positive attempt number, task-file path, shared instructions, activity-log path,
and JSON-result path. The agent owns its checkpoint updates; the runner preserves
them when updating status.

What to check:

- Logs and JSON results go in `alr_activity/` under the directory where the
  user started alr.
  This is a fixed location with no override flag; it is not relative to the
  executable, YAML, or prompt file.
- Each task run, including a resumed task, gets fresh paths for both a new log
  and a new JSON result. Preserve earlier runs' files.
- The executing agent creates, writes, and finishes both files. `alr` supplies
  paths but does not reserve empty files, write their contents, or invent
  results on failure.
- Read the current result only from its supplied path. Never substitute a prior
  result for missing output.

The architects can decide how to name files and assign attempt numbers. Each
run must still get fresh paths, identify the right task and attempt, and keep
previous logs and results.

**Related:** FR-002, FR-004, FR-006.

### FR-004 - Read the JSON result and decide what happens next

**Priority:** Required for execution.
**Why:** Use the JSON result to decide what happened, rather than reading the human log.

The agent produces one JSON object with exactly four fields:

| Field         | Required value                                        |
| ------------- | ----------------------------------------------------- |
| `task`        | Positive integer matching the selected task ID        |
| `attempt`     | Positive integer matching the supplied attempt number |
| `disposition` | `completed`, `retry`, or `blocked`                    |
| `summary`     | Nonempty string describing the outcome                |

Reject invalid JSON, a task or attempt number that doesn't match, extra fields,
Markdown around the JSON, and anything after the JSON except whitespace. These
rules follow the supplied JSON format. The runner reads this result; it does
not read or validate the activity log to decide the task's outcome.

| Attempt outcome                                    | Runner behavior                                                                       |
| -------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Valid `completed` result and successful agent exit | Save `completed`, then advance; exit `0` when all tasks complete                      |
| Valid `blocked` or `retry` result                  | Save `blocked` if possible; stop the entire run and exit nonzero                      |
| Agent crash or missing/invalid JSON                | Leave `in_progress`, keep logs, results, and saved checkpoints, stop and exit nonzero |
| Ctrl+C                                             | Apply FR-002 interruption behavior                                                    |

There are no automatic retries: at most one attempt per task in a run. An
explicit restart is separate from an automatic retry. Existing `blocked` tasks
require manual intervention before a later run can execute anything.

Before reporting completion, the agent must follow the accepted prompt's steps
for checking its work, cleaning up, saving its checkpoint, finishing its log,
and making any authorized commits to the project. The agent reports the result
through JSON; alr does not repeat those steps. A reported `completed` result with a nonzero process exit
cannot count as completion.

If a required status write fails, report the failure and exit nonzero without
advancing or claiming the write succeeded. Preserve logs, results, and saved
checkpoints on failures.

**Related:** FR-002, FR-003, FR-006.

### FR-006 - Produce a human activity log

**Priority:** Required for execution.
**Why:** Keep a readable record of what happened, what was checked, and what needs attention.

The executing agent writes a Markdown log following the supplied activity-log
format, with the agent creating the file as described in FR-003. The metadata
at the top records the task and attempt, title, timestamps, and paths to the
task file, prompt, log, and JSON result.
Its sections record activity, created resources, verification, cleanup, retained
resources and preserved work, blockers and next actions, and the summary.

Replace template placeholders with actual values and record when no resources or
checks apply. The finished log includes the actual end time. A crash or interruption
may leave an unfinished log; alr must keep it rather than invent an ending.

**Related:** FR-003, FR-004.

### FR-007 - Launch Claude Code or Codex

**Priority:** Both backends required for execution support.
**Why:** Support the user's chosen coding agents without adding model configuration.

Select one backend for the run using the command-line options above. Launch it in the same
working directory where the user started alr. Every task run starts a fresh
agent session, including resumed tasks; do not reuse previous conversation
sessions. Supply FR-003 context to both backends.

Use each installed CLI's existing model and authentication settings. Add no
alr model-selection flags. Check that both backends receive the
agreed task details and instructions, and that their agents produce the expected
log and JSON result.

**Related:** FR-001 through FR-004, FR-006.

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
FR-005 is the first increment. After an increment's implementation and checks
pass, proceed without a user-review pause. Architects should sequence remaining
requirements into small increments; do not implement the full runner in the
first step or import unrelated template/Gralph features.

### CON-002 - Platforms

Support only Linux and macOS. Build and verify required behavior on both.
Other operating systems, including Windows, are out of scope.

### CON-003 - Language

Implement in Python >= 3.12, managed with uv. The user selected the language;
it is not an architect decision.

## Details for the architects to decide

Language and platform requirements are limited to what the user confirmed.
The architects can decide the following details; these do not need more
requirements questions:

- Internal structure and sequencing of execution increments after FR-005.
- Log and result filenames, attempt numbering, and creation of the containing directory,
  while preserving agent ownership of the files and fresh paths per run.
- How to launch and stop each CLI while respecting its existing settings,
  starting fresh sessions, and handling interruptions as described above.
- Consistent error messages, exact nonzero exit codes, and how to save statuses.

These decisions must not introduce automatic retries, concurrent-run protection,
or other withdrawn/out-of-scope features. The language is fixed by CON-003.

## Questions we've settled

Question IDs stay the same. Earlier answers that were corrected no longer apply.

| ID    | Answer                                                                                                                          |
| ----- | ------------------------------------------------------------------------------------------------------------------------------- |
| Q-001 | Resolved: a new alternative implementation, not a Gralph wrapper                                                                |
| Q-002 | Resolved: Claude Code and Codex, FR-007                                                                                         |
| Q-003 | Resolved for scope: list order, no automatic retries, explicit stop behavior, FR-002/FR-004                                     |
| Q-004 | The opening section explains why this app is needed; a wider survey of users is not required                                    |
| Q-005 | Resolved for scope: use the supplied templates with the differences listed here; no requirement to match everything Gralph does |
| Q-006 | Resolved: understandable logic and incremental delivery, NFR-001/CON-001                                                        |
| Q-007 | Resolved: FR-005 first, no review pause after passing checks                                                                    |
| Q-008 | Resolved: FR-005 field rules, warnings, and validation exit behavior                                                            |
| Q-009 | Resolved for scope: FR-002/FR-004 status, startup, failure, and recovery rules                                                  |
| Q-010 | Resolved: required backend flag for execution; existing CLI model/auth settings                                                 |
| Q-011 | Resolved: Python >= 3.12 with uv, CON-003                                                                                       |

## About this draft

Consolidated the discovery draft in place while it remains unpublished. Stable
requirement and question IDs are preserved. Removed superseded status-ownership,
status-validation, retry, and log/result-ownership statements. Retained FR-008 as
withdrawn. Individual decisions were confirmed during discovery; no separate
approval of this consolidated document is claimed.

## Ready for architecture and design

**Readiness: READY for architecture and staged implementation planning.**
Use this document as the source of requirements for alr in this trial.
FR-005 defines the first increment; the remaining active functional requirements
define execution support. No more interview questions are needed for this
scope. The design details above can be worked out next.

The architect should preserve NFR-001 and CON-001, define small increments after
validation, and map each design decision to the applicable FR/CON IDs. The
execution design must preserve runner-owned status, agent-written logs and results,
one fresh session at a time, no automatic retries, and the different dry-run versus
execution behavior for blocked/abandoned tasks.

Update the generated task files and prompts to reflect the differences listed
here before connecting them to the runner. The source templates themselves remain unchanged by this
cleanup. Check how the installed CLIs actually behave when
implementing their support. The language is Python (CON-003).
