# alr Outcomes, Stage 1c: Fixes From the Stage 1b Re-review

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the findings that survived the adversarial re-review of commits `fca867e..6928a30` (stage 1b of ADR-025): five fresh lens reviewers, a skeptic per finding, and a completeness critic. None was high severity; three were medium.

**Architecture:** No new design. ADR-025 says what is right; each task makes the code, the tests, and the documents agree with it. Findings are named by the short labels below.

**Tech Stack:** Python 3.12, asyncio, Textual, pytest, ruff, uv.

**Spec:** `docs/design/02_architectural_decisions.md`, ADR-025 (version 29). Earlier plans: `2026-10-08-alr-outcomes-stage-1.md`, `2026-10-08-alr-outcomes-stage-1b-review-fixes.md`.

## Global Constraints

- Everything in the stage 1 and 1b plans' Global Constraints still applies: exit codes `0/1/2/3/4/130/143`; a refusal's first stderr line `error: refused: <reason>: <message>`; keep every docstring; never-nester; a blank line after every `if` block; no lint suppressions.
- Each task must leave `make test` and `make build` (the whole e2e suite) passing. When a change alters what an existing `tests/e2e/` test asserts, update that test in the same task. The e2e suite imports nothing from `alr`.
- No new refusal reasons. `locked` is still stage 2.
- Every behaviour fixed here gets a test that fails if the fix is reverted. Prove it by mutation: break the code, watch the test fail, restore.

## Review Focus

1. The dry run and a real run on the same inputs give the same refusal and the same code, for every reason a real run can raise before the first task starts (Task 1).
2. A refused run leaves the directory as it found it, including a `logs/` it created (Task 2).
3. After a stop that landed the commit, the view's rows, `tasks.yaml`, and the exit code all agree (Tasks 3, 4, 5).
4. Nothing a user owns is overwritten by a save (Task 2).

## Not fixed here, by decision

- Task names with `\x1c`, `\x1d`, `\x1e` get "must be a single line": the stage 1b plan put them in the line-break parametrization, so that message is the intended one.
- The `add_note` text on a propagating error is never shown: cosmetic.
- Zero-width characters and a BOM in a task name are accepted: they cannot crash a commit.

---

### Task 1: The dry run refuses an unwritable log folder (F-A)

**Files:** `src/alr/plain/dryrun.py`, `src/alr/record/runlog.py` (a check that creates nothing), tests `tests/unit/test_plain_dryrun.py`, `test_runlog.py`, and `tests/e2e/test_dry_run.py`.

**Finding:** `--dry-run --commit task` exits 0 where a real run exits 3 `log-unwritable`, for a `logs/` that is `chmod 500` or a regular file. The dry run only computes the run folder path and never tries to create it.

**Required behaviour:** the dry run decides `log-unwritable` the same way a real run does, in the same place in the order (after the failed-tasks check, where the real run opens the log) and without creating anything: a dry run writes nothing. Check writability of the nearest existing ancestor of the run folder, or equivalent; a `logs/` that is a regular file is refused.

**Tests:** dry run with `logs/` read-only exits 3 `log-unwritable`; with `logs/` a regular file likewise; with a writable or missing `logs/` it exits 0 and creates no folder. An e2e test for the read-only case (the e2e suite runs as non-root).

- [ ] Steps: reproduce, failing tests, implement, `make test`, `make build`.

### Task 2: A refused save leaves nothing behind (F-B, F-C)

**Files:** `src/alr/record/runlog.py` (`discard`, `_create_run_dir`), `src/alr/tasks.py` (`save_tasks`), tests `test_runlog.py`, `test_tasks_save.py` (or the file that tests `save_tasks`), `test_plain_start.py`, `test_tui_prepare.py`.

**Findings:**
- A `task-file-unwritable` refusal leaves an empty `logs/` when `logs/` did not exist (the run log created it with `parents=True`).
- `save_tasks` writes `<tasks.yaml>.tmp` and replaces or unlinks it: a user's own `tasks.yaml.tmp` is overwritten, and deleted when the save fails.

**Required behaviour:** the run log remembers whether it created the `logs/` parent; `discard()` removes that parent too, only when it created it and it is empty. `save_tasks` writes its temporary file under a name that cannot collide with a user's file (a unique name in the same directory, via `tempfile`) and still replaces the target atomically; a failed save removes only its own temporary file.

**Tests:** a refused save with no pre-existing `logs/` leaves no `logs/`; with a pre-existing `logs/` it is kept; a pre-existing `tasks.yaml.tmp` survives a successful and a failed save byte-identical; both through plain `start` and through the view's `prepare`.

- [ ] Steps: reproduce both, failing tests, implement, `make test`, `make build`.

### Task 3: The view's stop state resets after a refused start (F-D, T-3)

**Files:** `src/alr/tui/app.py` (`_stopping`, `action_stop`, `_back_to_ready`, `_on_sigterm`, `_finish`), tests `tests/unit/test_tui_app.py`.

**Finding:** `_stopping` is set in `action_stop` and never cleared. When `prepare` raises (not cancelled) during a stop, the app returns to READY with `_stopping` still `True`; on the next run the first Stop press takes the "already stopping" branch, exits at once, and skips the graceful cancel. `_outcome()` and `_finish_or_fail()` also read the stale flag and would call an ordinary failure "stopped".

**Required behaviour:** `_stopping` is cleared whenever the app returns to READY. Also pin the SIGTERM handling with tests: `_on_sigterm` sets exit 143, and the `_finish` fallback keeps 143 over a later 130.

**Tests:** the reproduction from the review (monkeypatch `prepare` to raise on cancel, press `s`, `ctrl+c`, wait for READY, assert `_stopping` is `False`, press `s` then `ctrl+c` once: the app stays open and the run is cancelled gracefully); SIGTERM in the view exits 143; a second Stop press never turns a stop into exit 0.

- [ ] Steps: reproduce, failing tests, implement, `make test`, `make build`.

### Task 4: The rows follow the saved state after a stop (F-E, T-2)

**Files:** `src/alr/tui/panes.py` (`TasksPane._note_passed` and the revert logic around it), `src/alr/tui/app.py` if it feeds the pane, tests `tests/unit/test_tui_panes.py`, `test_tui_app.py`.

**Finding:** after a stop that landed the commit, the view resets the rows to pending while `tasks.yaml` says `completed`. A task-mode stop or failure reverting rows is also untested.

**Required behaviour:** the rows show what the task file holds. A task saved `completed` shows completed whatever ended the run; a `run`-mode run that did not complete returns only the rows still `passed` (not saved) to pending; a `failed` task shows `✖`. Decide the rule from ADR-025 (the all-or-nothing `run` mode and "a stop after the commit landed saves the tasks `completed`") and state it in a comment that says why.

**Tests:** stop after the commit in `task` mode and in `run` mode: rows match `tasks.yaml`; a task-mode stop before the commit and a task-mode failure: completed rows stay completed, the running one is pending or `✖`; each fails if the revert logic is mutated.

- [ ] Steps: reproduce, failing tests, implement, `make test`, `make build`.

### Task 5: A task-mode save failure after the commit names the hash (F-F, T-4)

**Files:** `src/alr/engine/loop.py` (`_run_and_save`, `_save`, `_save_committed`), `src/alr/engine/commit.py` (`head`), tests `tests/unit/test_loop.py`, `test_commit.py`.

**Findings:**
- In `task` mode, with no stop, a commit that lands followed by a failed save raises `task N: name: failed to save task state: ...` without the hash, though the commit exists and the file still says `pending`. The same case after a stop, and in `run` mode, names the hash.
- After a stop in the commit step, "git cannot say" (the HEAD read returns `None`) is untested; the `head()` contract on the no-commit and git-error paths is untested.

**Required behaviour:** the task-mode save failure after a landed commit names the commit hash in the error, like the other two paths, and exits 4. Use one code path for the three cases rather than a third copy.

**Tests:** task mode, commit lands, save fails: error names the hash, exit 4, the file unchanged; stop in the commit step with HEAD unreadable; `head()` on a repo with no commits (`None`) and on a git error (the documented result).

- [ ] Steps: reproduce, failing tests, implement, `make test`, `make build`.

### Task 6: Pin the remaining test gaps (T-5)

**Files:** `src/alr/tasks.py`, `src/alr/record/runlog.py`, `src/alr/plain/start.py`, tests `test_tasks_parse.py`, `test_runlog.py`, `test_plain_start.py`, `test_plain_dryrun.py`.

**Findings:** control-character regex boundaries not pinned; `RunLog.discard()` error suppression untested (and the dry-run log path); plain `start` rewrites `tasks.yaml` on `--reset-failed` when nothing was failed.

**Required behaviour:**
- Pin the boundaries of the control-character rule: `\x00`, `\x08`, `\x09` (tab, allowed), `\x0a`, `\x1f`, `\x20` (space, allowed), `\x7f`, `\x80`, `\x9f`, `\xa0` (allowed).
- `discard()`: an `OSError` while removing is suppressed and never hides the refusal that caused it; test it.
- `--reset-failed` with no failed task does not write the file (byte-identical, same mtime). Fix `reset_failed`/`start` if it does.

**Tests:** each of the above; each fails when its code is mutated.

- [ ] Steps: reproduce the rewrite first, failing tests, implement, `make test`, `make build`.

### Task 7: Documents agree with the code (F-G)

**Files:** `docs/design/02_architectural_decisions.md` (ADR-025, bump to version 30; update in place), `README.md`, `src/alr/skills/gralph-docs-writer/SKILL.md`, `CHANGELOG.md` (`[Unreleased]`).

**Required behaviour:**
- ADR-025's name-validation bullet says a line break or any other control character except tab is rejected.
- ADR-025 stops contradicting itself on when a refusal can leave a run folder: a refusal leaves nothing behind (not even a `logs/` it created), the one exception being what the ADR already names; read the whole ADR and make every statement agree with the code after Tasks 1 to 6.
- Check these two claims against the code on this branch and fix them only if they are wrong: the bundled `SKILL.md` says only a line break is rejected in a task name; the README's failed-tasks text ("use --reset-failed") does not match the actual message.
- The CHANGELOG lists Tasks 1 to 6 as Fixed.

- [ ] Steps: read the code, correct the documents, `make test`.

## Self-review notes

- **Coverage:** F-A (Task 1); F-B, F-C (Task 2); F-D (Task 3); F-E (Task 4); F-F (Task 5); the untested paths T-2 to T-5 (Tasks 3 to 6); the doc findings (Task 7).
- **Order:** each task leaves `make test` and `make build` green on its own; no task depends on a later one.
