---
name: alr-task-file
description: >-
  Use when creating or updating the tasks.yaml task list, with its shared
  prompt, that alr feeds to Claude Code, or when breaking a build plan or
  PRD into alr loop tasks.
---

# alr Task File

Generate the one file an alr run needs: `tasks.yaml`, holding a `shared`
block (the shared prompt) and an ordered task list. Respect the user's destination, project
scope, verification, and commit policies.

## How alr uses the file

alr works through `tasks.yaml` in file order. It skips `completed` tasks; for
each `pending` task it combines `shared.prompt` with that task's
`prompt` and hands the result to a new Claude Code session. Every session starts
with no memory of earlier tasks, so the file must carry everything the session
needs.

Each task gets one session. There is no retry and no `--iterations` flag, so
scope every task to what a single session can finish and verify.

alr owns `state` and writes it back to `tasks.yaml`. The session never edits
the file. To remove a task without running it, delete it from the file; there is
no `abandoned` state. alr decides the outcome from the JSON result line the
shared prompt asks for at the end of the session's output. A missing or invalid
result line, or a non-zero exit, marks the task `failed`. alr refuses to run
while any task is `failed`; a person fixes the cause (often by editing the
task's `prompt`) and resets its `state` to `pending` by hand, or runs with
`--reset-failed`, which sets every failed task back to `pending` at startup.

alr runs two optional shared lists of actions, shell commands it runs itself
through `sh -c` in its own working directory. `shared.after_task` runs after
every task whose session reported success; if any action fails or times out,
the task is `failed` (exit 1) and the later actions do not run. A task is
`completed` only when every action in both lists, its own `actions` (next
paragraph) and `after_task`, exited 0. `shared.after_run` runs once at the end
of every run that has no failed task, including a run where every task was
already completed, so a rerun after an `after_run` failure retries it. An
`after_run` failure exits 4 and leaves the tasks `completed`. Each entry has
`action` (required) and `timeout` (optional, such as `90s` or `10m`; the
default is 10 minutes). `--action-timeout` overrides every action's timeout.

A task may also carry its own `actions` list, with the same entry shape. They
run after that task's session and before `shared.after_task`, as one list: the
first failure in either list fails the task and skips every later action from
both. Use it for checks only that task needs: the unit tests for a code task, a
Markdown lint for a docs task, ShellCheck and BATS for a script task. Keep the
commit in `shared.after_task`, so every task is committed the same way, and
never put a commit in a task's own list. An absent `actions` and `actions: []`
both mean none.

```yaml
tasks:
  - id: 2
    name: Update the README
    prompt: |
      ...
    actions:
      - action: markdownlint README.md
```

alr sets these environment variables on every action: `ALR_TASK_FILE` and
`ALR_LOG_DIR`, both absolute paths. A task's own actions and the `after_task`
actions get `ALR_TASK_ID` and `ALR_TASK_NAME` as well. Use them in double
quotes, as `"$ALR_TASK_NAME"`, so a value stays data to the shell.

Give each command its own `action` entry. Never join commands with `&&`:
alr already runs the list in order and stops at the first failure, and with
one command per action the failure reason names the exact command.

A commit is just an action. alr has no commit mode and checks nothing about
git, so a task is not guaranteed its own commit: the commit actions take
whatever is in the work tree. Put the three commit actions LAST in
`after_task`, because alr does not undo what earlier actions did, and keep
`tasks.yaml` and the logs out of the commit:

```yaml
after_task:
  - action: GENERATE_BUILD_AND_TEST_COMMAND
  - action: git add -A -- :/
  - action: git reset -q -- "$ALR_TASK_FILE" "$ALR_LOG_DIR"
  - action: git diff --cached --quiet || git commit -s -m "$ALR_TASK_NAME"
```

The first action adds everything from the repository root. The second
unstages the run folder's own files, so an ignored run folder (the layout alr
has always asked for) works and a visible one is still kept out. The third
commits only when something is staged; its `||` is one step, not a chain,
because a task that changed nothing has nothing to commit and without the test
a clean tree makes the action fail. The run folder (`tasks.yaml` and `logs/`)
must be inside the git work tree for the `git reset` action. If it is outside,
git refuses the paths (exit 128), so drop that action and keep the run folder
out of the repository.

An absent `after_task` means the run's actions are undecided: the full-screen
view asks for them. `after_task: []` records a decision that there are none.
An absent `after_run` and `after_run: []` both mean none.

A `tasks.yaml` that does not match the rules below is invalid and alr
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
3. Ask the user whether alr should commit after each task. If it should,
   `after_task` ends with the commit action above, the `Commits` line in
   `shared.prompt` reads "Do not commit. Leave your changes in the work tree.",
   task names are written as commit subjects (imperative, under about 70
   characters), and no task prompt mentions committing. Ask too what, if
   anything, should run after the last task (a build, a push), for `after_run`.
4. Draft the task list and show the user a summary, one line per task:
   `<id>: <name> - <one short sentence>`. Nothing else goes in the summary.
   Ask whether they approve it or want changes. Revise and show the summary
   again until they approve. Write nothing before approval.
5. Write `tasks.yaml` with two top-level keys, `shared` and `tasks`, and no
   others: alr rejects any other top-level key. `shared` is a mapping with a
   required nonblank block-scalar `prompt` (the shared prompt, step 7) and the
   optional `after_task` and `after_run` lists of actions (see the template and
   above); any other key in `shared` or in an action is an error.
   `tasks` is a sequence holding at least one task. Unknown keys inside `shared`
   are rejected too. Each task has a stable positive integer `id` (at most 32767), a
   nonblank `name`, a nonblank block-scalar `prompt`, an optional `actions`
   list for checks only that task needs (see above), and an optional `state`.
   IDs are unique and independent of order; alr runs tasks in file order.
   Names are unique ignoring case and surrounding whitespace, and each name is
   a single line: a line break, or any other control character except a tab, in
   a name is an error. `state` is
   `pending`, `completed`, or `failed`; leave it empty for new work, which
   alr reads as `pending`. The optional `error` field is alr-only: never
   write it; preserve it if present when updating an existing file. Any other
   task key is refused, so the file does not run. Write `after_task` only with
   actions the user asked for or confirmed; leave it out when the user wants to
   be asked in the full-screen view, and write `after_task: []` for a decision
   of none.
6. Put scope, steps, the project's own checks (tests, linters, the build),
   task-specific verification, and completion criteria inside each task's
   prompt, and end every task prompt with the template's Finish block: alr puts
   the task prompt last, so it is the session's last instruction. Preserve
   project-specific instructions. YAML comments are not durable task
   instructions: they never reach the session. alr rewrites
   the task file with every state change, so comments and custom formatting
   are not preserved. Every verification command must be one the session can
   run.

   The session runs under Claude Code's permissions and, when the run uses a
   sandbox, inside it. A run sandboxes its sessions with `--sandbox <name>`
   (a file in `~/.local/alr/sandboxes/`, where `tight.json` and `large.json`
   ship) or `--sandbox-settings <path>`. A command that sets a variable inline
   (`VAR=value cmd`) can be refused, and other users' files and hosts outside
   the sandbox's network list are out of reach. Writes outside the project are
   blocked, so a tool that writes a cache under the home folder (uv's
   `~/.cache/uv`, for one) fails unless the sandbox file adds that folder to
   `sandbox.filesystem.allowWrite`, or the task uses a project-local cache.

   A sandbox file can let named commands out of the sandbox:
   `sandbox.excludedCommands` lists them, and matching `permissions.allow`
   rules let a session nobody can answer run them. The shipped `large.json`
   does this for `docker *`, `./build/build.sh`, and `./build/build.sh *`; a
   sandbox that excludes nothing, such as `tight.json`, keeps Docker out of
   reach. A call only leaves the sandbox when the exclusions cover every
   command in it, so a prompt that tells the session to run such a command
   must say to run it as its own Bash call, exactly as listed, with no
   redirect, pipe, `;`, `&&`, `cd ... &&`, inline `VAR=`, or `echo`. Read the
   sandbox file the run will use (ask the user which one when you do not
   know) and put those exact command strings in that instruction. For a
   command the sandbox keeps out of reach, give the session an equivalent it
   can run.

   The prompt's verification text is for the session to run; a check alr
   should run and judge goes in the task's `actions` instead, or in
   `after_task` when every task needs it. alr runs both lists itself, so a
   check the session cannot run belongs there; tell the user to add it.
7. Write `shared.prompt` from the template. Replace every `GENERATE_*` token
   with project content and actual paths: full build/test commands and the
   project's commit policy, since the session sees only this prompt and one
   task. When an `after_task` action commits, the `Commits` line tells the session not
   to commit; otherwise keep commits conditional on the project's authorization.
   Keep the `tasks.yaml` rule and the Finish section as written so the file
   works without this skill installed. Apart from the project's own checks and
   each task's closing Finish block, keep generic rules out of task prompts;
   they live once, in `shared.prompt`.
8. Validate `tasks.yaml` with `alr -d <run folder> --dry-run` (the run folder
   holds `tasks.yaml`): it applies
   the same rules a real run does and exits non-zero on an invalid file. If
   `alr` is not installed, check YAML syntax and the field rules in step 5
   by hand. Also check paths, runnable verification commands, safe cleanup
   instructions, and preservation of project policies, and confirm no
   `GENERATE_*` token remains in the file.
9. Review the file with the `alr-task-review` skill, in a fresh agent that did
   not write it when you can start one, using the sandbox file the run will use
   (ask the user which one when you do not know). If that skill is not
   installed, tell the user to run `alr --install-skill` and go on. Put the
   findings in your report and change nothing because of them until the user
   says so.
10. Report the output path, the checks performed, and the review's findings or
   `NO FINDINGS`. Do not launch the loop merely to validate generated files.
   When an action commits, also tell the user that the commit takes whatever
   is in the work tree, so a sandbox settings file kept inside the repository
   needs a `.gitignore` line (or its own path in the `git reset` step). Do not edit
   `.gitignore` yourself unless asked.
   Also tell the user not to edit `tasks.yaml` while a run is going: alr
   rewrites it on every state change, and an edit made during a run will be lost.

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
tests: the project's own checks, the task's own `actions`, and any `after_task`
actions the user sets for the run. A task can be complete and tested and still
fail one because of where it sits in the sequence: code that nothing calls
until a later task fails an unused-code linter, and a fixture change can break
a suite another task fixes. Reorder or merge tasks so each one leaves every
check passing.

Honor project policies for source commits unless an `after_task` action does
the committing, and never instruct the session to edit or commit
`tasks.yaml`.
