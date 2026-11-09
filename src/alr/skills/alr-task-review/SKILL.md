---
name: alr-task-review
description: >-
  Use when a tasks.yaml for alr has been written or edited and is about to run,
  or when asked to review, check, or sanity-check a task list before a loop.
---

# alr Task Review

Read a `tasks.yaml` the way a skeptical colleague would before the loop starts,
and report what would make a task fail, pass without doing the work, or be too
big to verify. Read-only: change nothing, and run no Docker or `build.sh`. When
the writer of the file is the one reviewing, hand this to a fresh agent that
had no part in writing it.

## What you need

The task file, the project it runs in, and the sandbox file the run will use
(ask which one when you do not know). Judge every task as if it were pending:
`state` and `error` belong to alr, so a `completed` task in the file is not a
finding.

## How alr runs the file

- Tasks run in file order, one fresh Claude Code session each, with no retry.
  A session sees only the shared prompt followed by its own task prompt, so the
  task prompt is its last instruction. It never sees other tasks.
- The session runs inside the sandbox file. A command the file's
  `sandbox.excludedCommands` lists runs outside it, only when the call is that
  command alone. Everything else is limited: no network beyond
  `allowedDomains`, no writes outside the project.
- After the session, alr runs the task's own `actions`, then `shared.after_task`
  (a commit is one of these), outside the sandbox. Any failure fails the task
  and stops the run.
- A session must end with the JSON result line from the shared prompt. When an
  action commits, the shared prompt's `Do not commit` line is correct.

## Check every task on these six points

Fill in all six for each task, in order. Look in the file and the repo; do not
rely on what the prompt says exists.

1. **order**: Does the task use a file, symbol, flag, or output that only a
   later task creates? Does it call a file "existing" that is neither in the
   repo nor created by an earlier task?
2. **size**: Could one session finish and verify it, and could a person verify
   it from its commit? More than about five files, or two independent
   deliverables, is too big.
3. **sandbox**: For each command the session must run, check the sandbox file:
   an inline `VAR=value cmd`; a redirect, pipe, `;`, `&&`, or `echo` around an
   excluded command; Docker when nothing excludes it; a network host outside
   `allowedDomains`; a write outside the project. Quote the sandbox entry.
4. **false-pass**: Leave the work tree unchanged. Would every check for this
   task (its `actions` plus `after_task`) still pass? If so, the task needs a
   check that fails on unchanged code, such as a named test or a grep for the
   new symbol. A check that already fails on unchanged code is enough.
5. **criteria**: Is the last section of the prompt a Finish block? Report a
   missing one even when Completion criteria are present. Does the prompt have
   observable Completion criteria (a command and the output or condition
   expected), and report asks of one line each? A prompt that asks for test
   counts, per-file summaries, or mutation proofs risks a dropped result line.
6. **rules**: Does the prompt tell the session to edit or commit `tasks.yaml`?
   Does a named path or command not exist? Would the task's required checks
   fail only because of where it sits in the order (for example an unused
   variable a linter flags until a later task uses it)?

## Report

Check each finding against the file and repo before you report it. Write one
line per finding:

`task=<id> | <order|size|sandbox|false-pass|criteria|rules> | <problem> | <evidence>`

Use `task=all` for a problem with the whole file. A task with nothing wrong
gets no line. If the file has no findings, write `NO FINDINGS`. Report only
what makes a task fail, pass without the work, or be unreviewable; leave out
wording, style, and checks that already fail on unchanged code.
