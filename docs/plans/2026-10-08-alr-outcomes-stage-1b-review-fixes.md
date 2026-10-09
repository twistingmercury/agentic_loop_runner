# alr Outcomes, Stage 1b: Fixes From the Stage 1 Review

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every confirmed finding of the adversarial review of commit `cc70ab1` (stage 1 of ADR-025), and the test gaps the mutation review found.

**Architecture:** No new design: ADR-025 (amended in this round) says what is right; each task makes the code, the tests, and the documents agree with it. Findings are named F1 to F16 and T1, T2 as in the review.

**Tech Stack:** Python 3.12, asyncio, Textual, pytest, ruff, uv.

**Spec:** `docs/design/02_architectural_decisions.md`, ADR-025 (version 29). Stage 1 plan: `2026-10-08-alr-outcomes-stage-1.md`.

## Global Constraints

- Everything in the stage 1 plan's Global Constraints still applies, including: exit codes `0/1/2/3/4/130/143`; a refusal's first stderr line `error: refused: <reason>: <message>`; keep every docstring; never-nester; a blank line after every `if` block; no lint suppressions.
- Each task must leave `make test` and `make build` (the whole e2e suite) passing. When a change alters what an existing `tests/e2e/` test asserts, update that test in the same task. The e2e suite imports nothing from `alr`.
- New reason word: `task-file-unwritable` (a `--reset-failed` that cannot save the task file at startup). Nine reasons in all; `locked` is still stage 2.
- Every behaviour fixed here gets a test that fails if the fix is reverted (the review proved weak tests by mutation).

## Review Focus

1. A refused run, in plain mode and in the view, leaves tasks.yaml byte-identical and no run folder under `logs/`, for each refusal (Task 1).
2. A stop that lands after the commit exists never leaves the covered tasks `pending`, in both `task` and `run` mode (Task 3).
3. Exit 143 for SIGTERM in the view, and a second Stop press never turns a stop into exit 0 (Task 2).
4. A `--dry-run` and a real run on the same inputs give the same refusal and the same code (Task 1).
5. A task name with a NUL or any other control character cannot crash a commit (Task 4).

---

### Task 1: A refusal leaves nothing behind, in order (F1, F4, F5, F7, F8, F11, F14)

**Files:** `src/alr/plain/start.py`, `src/alr/plain/dryrun.py`, `src/alr/plain/report.py`, `src/alr/engine/loop.py` (`reset_failed`), `src/alr/record/runlog.py`, `src/alr/errors.py`, `src/alr/tui/app.py` (`_open_file`), `src/alr/tui/prepare.py`; tests `tests/unit/test_plain_start.py`, `test_plain_dryrun.py`, `test_runlog.py`, `test_tui_app.py`, `test_tui_prepare.py`, and the e2e tests whose dry-run exit changes (`test_dry_run.py`, `test_gates.py`, `test_loop.py`).

**Required behaviour (ADR-025, implementation notes):**
- Order in `start` and in the view's `prepare`: read the file, repository checks, apply `--reset-failed` in memory, the failed-tasks check on that list, open the run log (a failure is `log-unwritable`), then save the reset to disk. If the save fails, close the log, remove the run folder it made, and refuse with the new reason `task-file-unwritable` (constant `TASK_FILE_UNWRITABLE`, exit 3, message keeps the text `failed to save tasks to ...`).
- `RunLog.open` removes the run folder it just created when opening `run.jsonl` fails (F8). Add `RunLog.discard()` for the case above: close and remove the folder (and only an empty or just-created one).
- The view's `_open_file` applies `--reset-failed` to the in-memory list only (so Start is enabled); the disk write happens inside `prepare`, in the order above (F4). Quitting without pressing Start leaves the file untouched.
- `prepare` saves the gates before it opens the log (F11): a failed gates save leaves no run folder.
- The dry run runs the same checks in the same order with the same `run_dir` it would use (F5): a failed task prints the existing table and then raises `RefusedError(FAILED_TASKS, ...)` (exit 3); `log-folder-visible` is checked. A dry run writes nothing, so it never applies `--reset-failed` (still a usage error with `--dry-run`).
- The text `failed to start loop runner:` is added only in `start.py`, never in `load_tasks_report`, so the dry run's invalid-file message is `refused: invalid-task-file: failed to parse tasks yaml: ...` again (F14).

**Tests (each must fail if the fix is reverted):** `--reset-failed` plus an unwritable `logs/` leaves tasks.yaml unchanged and no folder; plus a dirty tree likewise; `--reset-failed` whose save fails gives exit 3 `task-file-unwritable`, the file unchanged and no run folder; the log folder left by a ledger-open failure is removed; the view with `reset_failed=True`, a dirty tree, Start refused, then quit leaves the file unchanged; the view with a failed gates save leaves no `logs/` entry; the dry run with a failed task exits 3; the dry run with a visible log folder exits 3; the dry run's invalid-file text has no `failed to start loop runner:`; and a test that the ledger-open `log-unwritable` wrapper maps to reason `log-unwritable`, not another (mutation 6n, 6p).

- [ ] Steps: write the failing tests, watch them fail, implement, `make test`, `make build`.

### Task 2: The view carries its stop signal (F2, F3)

**Files:** `src/alr/tui/app.py`, `src/alr/tui/prepare.py`; tests `tests/unit/test_tui_app.py`; e2e `tests/e2e/test_tui.py` only if it asserts these codes.

**Required behaviour:**
- One `Stop` object on the app. The SIGTERM handler sets `signum = SIGTERM`, then stops like the user's Stop; `prepare` puts the same object into `RunOptions.stop`; `_finish_or_fail` reads it. A user's Stop button or Ctrl-C keeps SIGINT (130).
- SIGTERM with no run going exits 143 (`app.exit_code` is set from the `Stop`).
- A second Stop press while the engine is still stopping sets `exit_code` to the stop's code before it exits the app, so a stopped run never exits 0.

**Tests:** SIGTERM during a run: `exit_code == 143` and the ledger's `exit` is 143; SIGTERM idle: 143; the second Stop press during a commit (a pre-commit hook that traps TERM and sleeps): `exit_code == 130` and equals the ledger's `exit`; SIGINT stays 130; and the view's "run died without a RunDone" path exits 1 (mutation 12c).

- [ ] Steps: failing tests, implement, `make test`, `make build`.

### Task 3: A stop never undoes a commit that landed, and never touches the file before it (F6, F15, F16)

**Files:** `src/alr/engine/loop.py`, `src/alr/engine/commit.py`; tests `tests/unit/test_commit.py`, `tests/unit/test_loop.py`; e2e `tests/e2e/test_commit.py` / `test_commit_run.py`.

**Required behaviour:**
- Record `HEAD` before `git commit`. When the commit step is cancelled, look at `HEAD` again (shielded from the cancel): if it moved, the commit landed, so save the tasks that commit covers as `completed` (the task in `task` mode; every task that ran in `run` mode), then re-raise the stop. If it did not move, behave as today.
- In `run` mode a stop before the commit lands (during a session, a gate, or the commit itself with `HEAD` unmoved) does not write `tasks.yaml` at all: drop the cancel-path `_finish_deferred` save (the file is still as the run found it; mutation 10j/10m showed that save is redundant).
- `_save` keeps `CommitFailedError` (exit 4) when a commit failure and a save failure happen together (put both texts in the message). When a `run`-mode commit succeeded but the final save failed, raise an error that names the commit hash and says tasks.yaml was not updated, so a driver does not blindly rerun.

**Tests:** a post-commit hook that sleeps, a stop during it, in `task` and `run` mode: the tasks are `completed` in tasks.yaml, the exit is 130 or 143; a stop before the commit in `run` mode leaves tasks.yaml byte-identical (a file with comments and a flow-style task); a commit failure plus a failing save exits 4; a successful commit plus a failing final save names the hash; a stop during the final `run` commit exits 143 for SIGTERM (Review Focus 3 of stage 1); the same stop in `task` mode.

- [ ] Steps: failing tests, implement, `make test`, `make build`.

### Task 4: Small fixes (F9, F10, F12)

**Files:** `src/alr/tasks.py`, `src/alr/engine/loop.py`, `src/alr/tui/panes.py`, `src/alr/tui/app.py`; tests `test_tasks_parse.py`, `test_loop.py`, `test_tui_panes.py`, `test_tui_app.py`.

**Required behaviour:**
- A task `name` containing a NUL or any other control character except tab is rejected at load with `name: must not contain control characters` (the task id is named, as for line breaks). Add the characters `\x1c`, `\x1d`, `\x1e` to the tests.
- The view gives `passed` rows their own symbol (`●`, distinct from `✔`); when a `run`-mode run ends other than completed, passed rows return to pending (`·`) and the row of a task saved `failed` shows `✖`.
- In `loop.run`'s `finally`, when an exception is already propagating and `run_done` raises `RunLogError`, the original error propagates (add the log error as a note with `add_note`); a `RunLogError` with no other error still ends the run as today.

**Tests:** each of the above, plus the line-break parametrization keeps passing.

- [ ] Steps: failing tests, implement, `make test`, `make build`.

### Task 5: Tests for what the mutation review found uncovered (T1, T2 and survivors)

**Files:** `tests/unit/test_loop.py`, `test_plain_record.py`, `test_stream_frontend.py`, `test_commit.py`, `test_runlog.py`, `test_tui_app.py`, `tests/e2e/test_outcomes.py`. No production change.

Add a test for each, and prove it by mutation (break the code, watch it fail, restore):
1. `run_started` and `task_finished` in the run ledger say `"state":"passed"` for a `run`-mode run (T1); an e2e test reads `run.jsonl` for it.
2. The view's `run_git` gives the trimmed first git line as the commit failure reason (T2): through `StreamFrontEnd`, not only `FirstLine`.
3. `git check-ignore` failing through the real `_ignored` raises `RefusedError` with reason `git-error` (not a hand-built error).
4. The crash branch of `_ending`: a non-`AlrError` crash is `result: failed`, `exit: 1`.
5. `FirstLine` keeps the first 200 characters of a mixed string, not the last 200.
6. A `run`-mode failed commit e2e test (`pending` + `failed`), and a stop during a commit in e2e (143).
7. The `run_finished.error` text for a failed `task`-mode commit starts `task N: ... failed: commit failed:`; for `run` mode it is `commit failed: ...`.

- [ ] Steps: write each test, mutate, confirm it fails, restore; `make test`, `make build`.

### Task 6: Documents agree with the code (F17)

**Files:** `README.md`, `CHANGELOG.md` (`[Unreleased]`), `docs/design/01_requirements.md`, `docs/design/03_system_architecture.md`, `src/alr/skills/gralph-docs-writer/SKILL.md`. Do not touch `02_architectural_decisions.md` (already amended).

Make each statement true of the code as it now is: refusals leave nothing on disk and the dry run refuses like a real run (exit 3, including `failed-tasks`); the view's exit codes follow the same table (SIGTERM 143); the new reason `task-file-unwritable` (nine reasons) in the README's table; the README flag table and SKILL.md say a task is completed only after its commit succeeds; 03 describes the stop-after-commit rule and that a stop in `run` mode leaves the file untouched (remove the sentence that documents the rewrite, around line 512); 01 items 8 and the "untouched on cancel" line agree; the CHANGELOG lists these as Fixed. Keep `commit failed: exit status 1` examples only where they are labelled as the fallback when git prints nothing.

- [ ] Steps: read the code, correct the documents, `make test`.

## Self-review notes

- **Coverage:** F1, F4, F5, F7, F8, F11, F14 (Task 1); F2, F3 (Task 2); F6, F15, F16 (Task 3); F9, F10, F12 (Task 4); F13 is an ADR clarification, already written, and tested in Task 5; T1, T2 and the survivors (Task 5); F17 (Task 6).
- **Not fixed here, by decision:** nothing from the review is dropped; stage 2 (lock, own session, `PR_SET_PDEATHSIG`, ignore re-check) is unchanged.
