# alr Outcomes, Stage 2: Commit Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A committing run cannot be undermined by a second alr, a terminal prompt, a killed alr, or a task that un-ignores its own bookkeeping files.

**Architecture:** Four guards around the commit step. A lock file under the git directory (held with `flock`, so a killed alr never leaves it stuck) refuses a second committing run. Git starts in its own session, so it has no controlling terminal and a signing program fails fast. On Linux, git is told to stop if alr dies. Before each `git add -A`, alr re-checks that the task file and the log folder are still ignored.

**Tech Stack:** Python 3.12, asyncio, `fcntl`, `ctypes` (Linux `prctl`), pytest, ruff, uv.

**Spec:** `docs/design/02_architectural_decisions.md`, ADR-025, stage 2. **Depends on stage 1** (`2026-10-08-alr-outcomes-stage-1.md`) being merged: it uses `RefusedError`, `LOCKED`, `CommitFailedError`, and the exit-code contract.

## Global Constraints

- Same as stage 1: `make test` before every commit, `make build` for e2e; never-nester, a blank line after every `if` block, comments explain why, no lint suppressions, keep docstrings; breaking changes allowed pre-1.0; branch off `develop` for the work; nobody but the user pushes; commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Refusal reason for a held lock: `locked`, first stderr line `error: refused: locked: <message>`, exit 3, no run log, nothing written.
- A pre-add re-check failure is a commit failure (task `failed`, exit 4) with the reason `task file visible: <path>` or `log folder visible: <path>`, and nothing is committed.
- On platforms without `PR_SET_PDEATHSIG` (anything but Linux) the guard is skipped and documented as a limit. The lock uses `fcntl.flock`, which alr already assumes (it installs Unix signal handlers).

## Review Focus

1. A stale lock file left by a killed alr must not block the next run (Task 2).
2. A lock held by a run that is only a dry run must not stay held, and a dry run must still report `locked` when another run holds it (Task 2).
3. A git worktree (where `.git` is a file) still finds its lock location (Task 2).
4. A task that appends `logs/` removal to `.gitignore`, or a gate that deletes the ignore rule, fails the commit with exit 4 and leaves the tree uncommitted (Task 3).
5. A SIGKILL of alr while git's commit hook runs must not let the commit land (Task 1, Linux only).

---

## File Structure

| File | Change |
|---|---|
| `src/alr/engine/proc.py` | `start` takes `new_session` and `die_with_parent` |
| `src/alr/plain/frontend.py`, `src/alr/stream/frontend.py` | `_run_command` passes both for git |
| `src/alr/engine/commit.py` | `git_dir`, `held_lock`, `recheck_ignored`; `_commit_changes` re-checks first |
| `src/alr/engine/loop.py` | `RunOptions.run_dir` |
| `src/alr/plain/start.py`, `src/alr/plain/dryrun.py`, `src/alr/tui/prepare.py` | take and release the lock; pass `run_dir` |
| `tests/unit/test_proc.py`, `test_commit.py`, `test_plain_start.py`, `test_plain_dryrun.py`, `test_tui_prepare.py`; `tests/e2e/test_commit_safety.py` | tests named in each task |
| `README.md`, `CHANGELOG.md`, `docs/design/02,03` | Task 4 |

---

### Task 1: Git runs in its own session and dies with alr

**Files:**
- Modify: `src/alr/engine/proc.py` (`start`), `src/alr/plain/frontend.py` and `src/alr/stream/frontend.py` (`_run_command`)
- Test: `tests/unit/test_proc.py`

**Interfaces:**
- Produces: `proc.start(..., new_session: bool = False, die_with_parent: bool = False)`. With `new_session` the child calls `setsid` (it is its own group leader too, so `_signal_group(pid)` still reaches it). With `die_with_parent` on Linux the child gets SIGTERM when alr dies (SIGTERM, not SIGKILL, so git removes its `index.lock`); elsewhere the flag does nothing.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_new_session_child_has_no_controlling_terminal_group() -> None:
    process = await proc.start(
        ["sleep", "5"], stdout=asyncio.subprocess.DEVNULL, new_session=True
    )
    try:
        assert os.getsid(process.pid) == process.pid
        assert os.getsid(process.pid) != os.getsid(0)
    finally:
        process.kill()
        await process.wait()


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux-only")
def test_a_child_that_dies_with_its_parent_stops_when_the_parent_is_killed(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "child.pid"
    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT.format(pid_file=pid_file)]
    )
    child_pid = int(wait_for_file(pid_file))

    parent.kill()
    parent.wait()

    until_gone(child_pid)  # polls os.kill(pid, 0) for up to 5 s
```

with the module constant

```python
_PARENT = """
import asyncio, pathlib
from alr.engine import proc

async def main():
    process = await proc.start(["sleep", "60"], new_session=True, die_with_parent=True)
    pathlib.Path("{pid_file}").write_text(str(process.pid))
    await asyncio.sleep(60)

asyncio.run(main())
"""
```

`until_gone` goes in `tests/unit/helpers.py`: loop for 5 s calling `os.kill(pid, 0)` until `ProcessLookupError`, else `pytest.fail`. A zombie of a killed child is reaped by init when the parent dies, so this converges.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_proc.py -k "new_session or dies_with" -v`
Expected: FAIL (`start()` got an unexpected keyword argument).

- [ ] **Step 3: Implement** in `proc.py`

`process_group=0` and `start_new_session=True` conflict: CPython runs `setsid` and then `setpgid`, and a session leader cannot be moved. Pass one or the other:

```python
    group = {"start_new_session": True} if new_session else {"process_group": 0}
    spawn = asyncio.ensure_future(
        asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
            env=env,
            preexec_fn=_die_with_parent if die_with_parent else None,
            **group,
        )
    )
```

```python
_PR_SET_PDEATHSIG = 1


def _die_with_parent() -> None:
    """Ask the kernel to SIGTERM this child when alr exits, however alr exits.

    A SIGKILL of alr runs no cleanup, so this is the only way to keep a commit
    from landing after alr is gone. Not available outside Linux, where the
    child simply keeps running.
    """
    if sys.platform != "linux":
        return

    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    # alr may have died before the call above; the signal would never come.
    if os.getppid() == 1:
        os._exit(1)
```

Add `import ctypes, sys`. Document both flags in `start`'s docstring. In both front ends' `_run_command`, pass `new_session=gentle, die_with_parent=gentle` to `proc.start` (`gentle` is true only for git).

- [ ] **Step 4: Run to verify they pass**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Start git in its own session and have the kernel stop it if alr dies"
```

---

### Task 2: A lock refuses a second committing run

**Files:**
- Modify: `src/alr/engine/commit.py`, `src/alr/plain/start.py`, `src/alr/plain/dryrun.py`, `src/alr/tui/prepare.py`
- Test: `tests/unit/test_commit.py`, `tests/unit/test_plain_start.py`, `tests/unit/test_plain_dryrun.py`, `tests/unit/test_tui_prepare.py`

**Interfaces:**
- Consumes: `RefusedError`, `LOCKED` (stage 1).
- Produces: `Repo.git_dir: str` (absolute), filled by `find_repo` from `git rev-parse --absolute-git-dir`.
- Produces: `held_lock(repo: Repo | None) -> ContextManager[None]`: a no-op for `None`; otherwise takes `<git_dir>/alr.lock` with `flock(LOCK_EX | LOCK_NB)` and releases on exit; a held lock raises `RefusedError(LOCKED, "another alr run is committing in this repository")`.
- Produces: `check_unlocked(repo: Repo | None) -> None`: the dry run's read-only look at the same lock.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_commit.py`

```python
async def test_a_second_lock_is_refused(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    with held_lock(repo):
        with pytest.raises(RefusedError) as caught:
            with held_lock(repo):
                pass

    assert caught.value.reason == "locked"


async def test_the_lock_is_released_on_exit(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))

    with held_lock(repo):
        pass

    with held_lock(repo):
        pass


async def test_a_stale_lock_file_does_not_block(git_repo: Path) -> None:
    repo = await find_repo(str(_task_file(git_repo)))
    Path(repo.git_dir, "alr.lock").write_text("left by a killed alr")

    with held_lock(repo):
        pass


async def test_a_linked_worktree_finds_its_lock_location(git_repo: Path, tmp_path: Path) -> None:
    git(git_repo, "worktree", "add", "-q", str(tmp_path / "wt"))
    repo = await find_repo(str(tmp_path / "wt" / "run" / "tasks.yaml"))

    with held_lock(repo):
        pass
```

(For the worktree test, put the task file where the other tests in this module put an ignored `run/tasks.yaml`, using `_ignore_and_commit`.) In `test_plain_start.py`: a `start(..., CommitMode.RUN)` called while `held_lock(repo)` is held by the test raises `RefusedError` with reason `locked` and writes no `logs/` folder. In `test_plain_dryrun.py`: `dry_run(..., commit=True)` while the lock is held raises the same refusal, and after a clean dry run the lock can be taken. In `test_tui_prepare.py`: `prepare(settings)` with the lock held raises `RefusedError`, and `Prepared.close()` releases the lock.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_commit.py tests/unit/test_plain_start.py tests/unit/test_plain_dryrun.py tests/unit/test_tui_prepare.py -v`
Expected: FAIL (`held_lock` and `git_dir` do not exist).

- [ ] **Step 3: Implement** in `commit.py`

```python
@dataclass(frozen=True)
class Repo:
    """The git work tree a --commit run commits into."""

    root: str
    """The work tree's top-level directory."""

    git_dir: str = ""
    """The absolute git directory; a linked worktree has its own."""
```

`find_repo` fills it:

```python
    repo = Repo(root=root, git_dir=await _find_git_dir(root))
```

```python
async def _find_git_dir(root: str) -> str:
    returncode, stdout, stderr = await proc.capture(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=root, gentle=True
    )
    if returncode != 0:
        raise RefusedError(GIT_ERROR, f"--commit: git rev-parse: {_git_message(returncode, stderr)}")

    return stdout.strip()
```

```python
_LOCK_NAME = "alr.lock"
_LOCKED = "another alr run is committing in this repository"


@contextmanager
def held_lock(repo: Repo | None) -> Iterator[None]:
    """Hold the repository's commit lock for the whole run, or refuse when another run has it.

    flock, not a file that exists or not: the kernel drops the lock when alr
    dies, however it dies, so a killed run never leaves a stuck lock behind.
    """
    if repo is None:
        yield
        return

    fd = _take_lock(repo)
    try:
        yield
    finally:
        os.close(fd)


def check_unlocked(repo: Repo | None) -> None:
    """Refuse like held_lock when another run holds the lock, without keeping it."""
    with held_lock(repo):
        pass


def _take_lock(repo: Repo) -> int:
    fd = os.open(Path(repo.git_dir) / _LOCK_NAME, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RefusedError(LOCKED, _LOCKED) from None

    return fd
```

Add `import fcntl` and `from contextlib import contextmanager`.

`plain/start.py`: after `repo = await repo_for(...)` and before `reset_failed`, enter the lock for the rest of `start` (`with held_lock(repo):` around the reset, load, log open and run; move the rest of the body into a helper `_start_locked(...)` so the function stays flat). `plain/dryrun.py`: after `repo = await repo_for(...)`, call `check_unlocked(repo)`. `tui/prepare.py`: `Prepared` gains `lock: ExitStack | None`; `prepare` enters `held_lock(repo)` on an `ExitStack` right after the repository is found and before anything is written, and `Prepared.close()` closes the stack. Every failure path after the lock is taken must close it (the `except TaskFileError` block already calls `prepared.close()`; wrap the earlier steps so a raise closes the stack).

- [ ] **Step 4: Run to verify they pass**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Refuse a second committing run with a flock the kernel releases when alr dies"
```

---

### Task 3: Re-check the ignore rules before every `git add -A`

**Files:**
- Modify: `src/alr/engine/commit.py` (`recheck_ignored`, `_commit_changes`, `commit`, `commit_all`), `src/alr/engine/loop.py` (`RunOptions.run_dir`), `src/alr/plain/start.py`, `src/alr/tui/prepare.py`
- Test: `tests/unit/test_commit.py`

**Interfaces:**
- Produces: `RunOptions.run_dir: str | None = None`, the run's own log folder (`None` when logging is off).
- Produces: `recheck_ignored(repo, tasks_file, run_dir) -> str | None`, the reason (`task file visible: <path>` or `log folder visible: <path>`) or `None`.
- Produces: `commit(repo, task, front_end, guard)` and `commit_all(repo, tasks, front_end, guard)` where `guard: Callable[[], Awaitable[str | None]]` runs before `git add -A`; a returned reason is the commit's failure and nothing is staged.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_commit.py`

```python
@pytest.mark.usefixtures("fake_claude")
async def test_a_task_that_unignores_the_task_file_fails_its_commit(git_repo: Path) -> None:
    tasks_file = _task_file(git_repo)
    # The gate removes the ignore rule that hides the run folder, then does work.
    unignore = Gate(cmd=": > .gitignore; echo work > one.txt")

    with pytest.raises(CommitFailedError) as caught:
        await run_loop(
            _gated_task(unignore), tasks_file, repo=await _open(tasks_file)
        )

    assert "task file visible" in str(caught.value)
    assert commit_subjects(git_repo) == ["init"], "nothing may be committed"
    assert _saved_states(tasks_file)[0][0] == FAILED


@pytest.mark.usefixtures("fake_claude")
async def test_a_task_that_unignores_the_log_folder_fails_its_commit(git_repo: Path) -> None:
    ...  # same arrangement; the ignore rule covers logs/ only, run_dir=str(git_repo / "logs" / RUN_DIR)
    assert "log folder visible" in str(caught.value)


@pytest.mark.usefixtures("fake_claude")
async def test_run_mode_unignored_file_fails_the_final_commit(git_repo: Path) -> None:
    ...  # CommitMode.RUN; assert the last task is FAILED with the reason and the others PENDING
```

Build the third and second tests from `test_run_commit_survives_a_gate_editing_gitignore` (around line 449), which already shows how to set up the ignore rules and the log folder. Pass `run_dir` through `run_loop`: add a `run_dir: str | None = None` keyword to `tests/unit/helpers.py::run_loop` and into its `RunOptions`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_commit.py -k "unignore" -v`
Expected: FAIL (the commit goes through and includes the task file).

- [ ] **Step 3: Implement**

```python
async def recheck_ignored(
    repo: Repo, tasks_file: str, run_dir: str | None
) -> str | None:
    """Say which bookkeeping path git can see now, or None when both are still hidden.

    A task's own commands can edit .gitignore, and git add -A takes no
    exclusions: an ignore rule is all that keeps the task file and the log
    folder out of the commit, so it is checked again right before staging.
    """
    task_rel = _relative(repo, tasks_file)
    if task_rel is not None and not await _ignored(repo, task_rel):
        return f"task file visible: {task_rel}"

    if run_dir is None:
        return None

    log_rel = _relative(repo, run_dir)
    if log_rel is not None and not await _ignored(repo, log_rel):
        return f"log folder visible: {Path(log_rel).parent}"

    return None
```

`_commit_changes` gains a `guard` argument and starts with:

```python
    reason = await guard()
    if reason is not None:
        return reason
```

`loop.py` builds the guard once per commit with `functools.partial(recheck_ignored, options.repo, options.tasks_file, options.run_dir)` and passes it to `commit` and `commit_all`. A failing `git check-ignore` raises `RefusedError` with reason `git-error`, as stage 1 made it. `plain/start.py` sets `run_dir=str(path)` in `RunOptions` (use the folder `RunLog.open` actually created: `RunLog` exposes it, add a `path` property if it does not); `tui/prepare.py` sets `run_dir=None if log_path is None else str(log_path)`.

- [ ] **Step 4: Run to verify they pass**, then `make test`.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "Re-check the task file and log folder are ignored before every git add"
```

---

### Task 4: e2e, documentation, and closing ADR-025

**Files:**
- Create: `tests/e2e/test_commit_safety.py`
- Modify: `README.md`, `CHANGELOG.md`, `docs/design/02_architectural_decisions.md` (status), `docs/design/03_system_architecture.md`

- [ ] **Step 1: e2e tests** (black-box, run against the wheel in the container; model on `test_commit_run.py` and `test_signal.py`)

| Test | Arrangement | Assertion |
|---|---|---|
| `test_second_committing_run_is_refused` | start one `--commit run` alr with a blocking fake claude; start a second in the same repo | second exits 3, stderr first line starts `error: refused: locked: `, no second `logs/` folder |
| `test_lock_is_free_after_a_kill` | start a committing run, `SIGKILL` it, start another | the second runs and exits 0 |
| `test_unignored_task_file_fails_the_commit` | a gate rewrites `.gitignore` | exit 4; stderr has `task file visible` |
| `test_sigkill_during_a_commit_hook_lands_no_commit` | a `pre-commit` hook that signals ready, then sleeps 5 s; `SIGKILL` alr while it sleeps | after 7 s, `git log` still shows only `init` (skip off Linux) |

- [ ] **Step 2:** `make build` passes with all of them.
- [ ] **Step 3: Docs.** Delegate to the `technical writer`: the README documents the lock (`locked` refusal), the pre-add re-check (exit 4 reasons), and the limit on platforms without `PR_SET_PDEATHSIG`; the CHANGELOG `[Unreleased]` gets an Added/Changed entry; `03_system_architecture.md` describes the guards (use `/design-docs-writer`, bump the frontmatter). In `02_architectural_decisions.md` change ADR-025 from `Proposed` to `Accepted` in both its status line and the Decision Summary row, and bump the frontmatter.
- [ ] **Step 4: Verification and review** as in stage 1, Task 11: `make test`, `make build`, mutation check on each new test, then an adversarial review (fresh reviewers, skeptic stage, completeness critic), then prune reviewer worktrees and helper branches.
- [ ] **Step 5: Commit**

```bash
git add tests/e2e README.md CHANGELOG.md docs
git commit -m "Test and document the commit-safety guards and accept ADR-025"
```

## Self-review notes

- **Spec coverage:** lock (Task 2), git's own session and `PR_SET_PDEATHSIG` (Task 1), the pre-add ignore re-check (Task 3), the `locked` refusal in both plain and the view and in the dry run (Task 2), the Linux-only limit documented (Task 4).
- **Risk to check first:** in Task 1 confirm `start_new_session=True` without `process_group` still lets `proc._signal_group` stop git (a session leader is its own group leader, so it should). The `preexec_fn` is only safe because plain mode is single-threaded when it spawns git; the view runs git from the asyncio loop in the main thread, which is also fine, but check no Textual worker thread spawns git.
