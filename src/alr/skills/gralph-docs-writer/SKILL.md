---
name: gralph-docs-writer
description: >-
  Use when creating or updating the tasks.yaml task list, with its shared
  prompt, that Gralph feeds to Claude Code, or when breaking a build plan or
  PRD into Gralph loop tasks.
---

# Ralph Loop Docs

Generate the one file a Gralph run needs: `tasks.yaml`, holding a `shared`
block (the shared prompt) and an ordered task list. Respect the user's destination, project
scope, verification, and commit policies.

## How Gralph uses the file

Gralph works through `tasks.yaml` in file order. It skips `completed` tasks; for
each `pending` task it combines `shared.prompt` with that task's
`prompt` and hands the result to a new Claude Code session. Every session starts
with no memory of earlier tasks, so the file must carry everything the session
needs.

Each task gets one session. There is no retry and no `--iterations` flag, so
scope every task to what a single session can finish and verify.

Gralph owns `state` and writes it back to `tasks.yaml`. The session never edits
the file. To remove a task without running it, delete it from the file; there is
no `abandoned` state. Gralph decides the outcome from the JSON result line the
shared prompt asks for at the end of the session's output. A missing or invalid
result line, or a non-zero exit, marks the task `failed`. Gralph refuses to run
while any task is `failed`; a person fixes the cause (often by editing the
task's `prompt`) and resets its `state` to `pending` by hand.

With the `--commit` flag, Gralph also commits each task itself: once a task is
`completed`, it commits what a plain `git add -A` would stage, with the task's
`name` as the commit message. A failed task is not committed; its changes stay
in the work tree for a person to sort out. For a `--commit` run the task file
must be ignored by Git or kept outside the repository; Gralph refuses to start
otherwise. A sandbox settings file should also be ignored or kept outside, so it does
not show up as an uncommitted change. Without
the flag, Gralph never touches Git.

A `tasks.yaml` that does not match the rules below is invalid and Gralph
refuses to run it. There is no migration from other formats or older states.

## Published-document preservation

Inspect Git history and remote-tracking refs before editing generated
supporting documents. Preserve published standalone documents by creating the
next snake_case version with synchronized `Version`, `Date`, and `Notes`
metadata. Treat committed documents as published when publication is uncertain.
`tasks.yaml` is a canonical living workflow file; update it in place when
authorized.

## Generate the file

1. Read repository instructions and relevant design/build documents. Establish
   scope, output destination, actual document paths, and project verification
   and Git policies. Do not add automatic commits where none are required.
2. Read [the YAML task template](templates/tasks_template.yaml).
3. Ask the user whether the run will use `gralph --commit`. If it will, the
   session must not commit: the `Commits` line in `shared.prompt` reads "Do not
   commit. Leave your changes in the work tree.", task names are written as
   commit subjects (imperative, under about 70 characters), and no task prompt
   mentions committing.
4. Draft the task list and show the user a summary, one line per task:
   `<id>: <name> - <one short sentence>`. Nothing else goes in the summary.
   Ask whether they approve it or want changes. Revise and show the summary
   again until they approve. Write nothing before approval.
5. Write `tasks.yaml` with two top-level keys, `shared` and `tasks`, and no
   others: Gralph rejects any other top-level key. `shared` is a mapping with a
   required nonblank block-scalar `prompt` (the shared prompt, step 7) and an
   optional `gates`, which you never write; a top-level `gates` key is an error.
   `tasks` is a sequence holding at least one task. Unknown keys inside `shared`
   are rejected too. Each task has a stable positive integer `id` (at most 32767), a
   nonblank `name`, a nonblank block-scalar `prompt`, and an optional `state`.
   IDs are unique and independent of order; Gralph runs tasks in file order.
   Names are unique ignoring case and surrounding whitespace. `state` is
   `pending`, `completed`, or `failed`; leave it empty for new work, which
   Gralph reads as `pending`. The optional `error` field is gralph-only: never
   write it; preserve it if present when updating an existing file. Write no
   other task keys: Gralph ignores them when reading and drops them the first
   time it saves the file. Never write `shared.gates`;
   Gralph asks for the run's gates itself the first time the file runs in the
   full-screen view.
6. Put scope, steps, the project's own checks (tests, linters, the build),
   task-specific verification, and completion criteria inside each task's
   prompt. Preserve project-specific instructions. YAML comments are not
   durable task instructions: they never reach the session. Gralph rewrites
   the task file with every state change, so comments and custom formatting
   are not preserved. Every verification command must be one the session can
   run. The session runs under Claude Code's permissions and, with
   `--sandbox-settings`, inside its sandbox: a command that sets a variable
   inline (`VAR=value cmd`) can be refused, and Docker, other users' files,
   and hosts outside the sandbox's network list are out of reach. Give the
   session an equivalent it can run. A check it cannot run belongs only in the
   run's gates, which Gralph runs itself; tell the user to add it there.
7. Write `shared.prompt` from the template. Replace every `GENERATE_*` token
   with project content and actual paths: full build/test commands and the
   project's commit policy, since the session sees only this prompt and one
   task. For a `--commit` run the `Commits` line tells the session not to
   commit; otherwise keep commits conditional on the project's authorization.
   Keep the `tasks.yaml` rule and the Finish section as written so the file
   works without this skill installed. Apart from the project's own checks,
   keep generic rules out of task prompts; they live once, in `shared.prompt`.
8. Validate `tasks.yaml` with `gralph -t <tasks.yaml> --dry-run`: it applies
   the same rules a real run does and exits non-zero on an invalid file. If
   `gralph` is not installed, check YAML syntax and the field rules in step 5
   by hand. Also check paths, runnable verification commands, safe cleanup
   instructions, and preservation of project policies, and confirm no
   `GENERATE_*` token remains in the file.
9. Report the output path and the checks performed. Do not launch the loop
   merely to validate generated files.
   For a `--commit` run, also tell the user the `.gitignore` line that keeps
   the files out of Git (the output directory, or `tasks.yaml`).
   For a `--commit` run this is required for `tasks.yaml`, not just advice; the
   same for any sandbox settings file, so it does not show up as an
   uncommitted change. Do not edit `.gitignore` yourself unless asked.

## Task scope

Each task is a complete unit of work that can be implemented, tested, and
committed on its own. The session that runs it sees only the shared prompt and
that one task, so a task must not refer to future tasks, leave work for a
later task to finish, or depend on anything a later task will add. It may
build on what earlier tasks delivered, because that work is already in the
repository; order tasks so that holds. Verification commands must fit the
target repository; do not invent passes or require source tests for
documentation-only changes.

Make each task small enough that a person can verify it from its commit alone:
one focused change, a short diff, and a runnable check that shows it works.
Split a larger change into a sequence of such steps, each passing the project's
own checks on its own. When one step would break those checks until another
lands, put the prerequisite step first (for example, update test fixtures
before the change that needs them).

Check each task against every check the run will apply, not only its own
tests: the project's own checks and any gates the user sets for the run. A
task can be complete and tested and still fail one because of where it sits in
the sequence: code that nothing calls until a later task fails an unused-code
linter, and a fixture change can break a suite another task fixes. Reorder or
merge tasks so each one leaves every check passing.

Honor project policies for source commits unless the run uses `--commit`, where
Gralph does the committing, and never instruct the session to edit or commit
`tasks.yaml`.
