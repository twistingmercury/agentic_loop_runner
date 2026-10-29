# alr Sandbox Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user pick a Tight or Large sandbox, in the view or with `--sandbox tight|large`, and have alr write the run folder's `sandbox.json` on Start, so `--yolo` stops being the easy path.

**Architecture:** A *preset* is a fixed set of three *switches* (web, docker, hide credentials) that one pure function turns into a Claude Code settings document. Both front ends build the session flags from that document and write it to `<folder>/sandbox.json` in the same startup step that saves `tasks.yaml`, with a rollback so a refused run leaves the folder untouched. The permissions dialog becomes one screen of radios, switches, and a preview; launch behavior is otherwise unchanged.

**Tech Stack:** Python 3.13, argparse, asyncio, Textual, pytest, ruff, mypy. Unit tests: `make test`. Build and e2e: `./build/build.sh` (Docker; the main session runs it, subagents never do).

**Spec:** `docs/design/02_architectural_decisions.md`, ADR-027. Read it first; it decides the design and this plan only orders the work.

## Global Constraints

- Presets: `tight` and `large`. Switches: `web`, `docker`, `hide_credentials`. Tight = (off, off, on). Large = (on, on, on).
- Key mapping, inside `sandbox`: web → `network.allowedDomains` = the web list; docker → `excludedCommands: ["docker *"]`; hide_credentials → `filesystem.denyRead` = the credential list. A switch that is off leaves its key absent.
- Credential list, in this order: `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.netrc`, `~/.claude`.
- Web list, in this order: `github.com`, `*.githubusercontent.com`, `crates.io`, `*.crates.io`, `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `proxy.golang.org`, `sum.golang.org`, `storage.googleapis.com`. The Claude Code settings reference documents `*.example.com` wildcards only, not a bare `*`, so the list is the decision; ADR-027's `*` alternative is not taken.
- The written file also carries `enabled: true`, `allowUnsandboxedCommands: false`, `failIfUnavailable: true`. Serialization: `json.dumps(settings, indent=2, sort_keys=True) + "\n"`.
- The file is `<folder>/sandbox.json`, the path `folder_sandbox` already reads. A preset replaces whatever is there. No round-trip, no marker key.
- Write order on Start, in both front ends: after every check passed and the log opened, write `sandbox.json` atomically (temporary file in the same folder, then `Path.replace`), then save `tasks.yaml`. If that save fails, put the previous `sandbox.json` back from the bytes read first, or remove the new file when there was none, then raise as today. A `sandbox.json` that cannot be written is `RefusedError("sandbox-file-unwritable", ...)`, exit 3, and discards the log folder like `task-file-unwritable`.
- `--sandbox {tight,large}` with `--sandbox-settings` or `--yolo` is a `UsageError`, exit 2. A bare `--sandbox` or another value is argparse's own exit 2.
- Dry run with a preset prints `sandbox settings: <folder>/sandbox.json (tight preset; written on a real run)` and writes nothing.
- Run log: unchanged. `permissions` is `sandbox`; `sandbox_settings` is the folder path.
- View strip: `Permissions: sandbox (tight)`, `sandbox (large)`, `sandbox (tight, adjusted)` or `sandbox (large, adjusted)` when a switch differs from the preset, `sandbox` for a file, `skip`, `not chosen`.
- Launch: a flag or the folder's file answers the question and the dialog does not open; `p` opens it any time before Start, pre-selected to the current answer. An unreadable or invalid folder file is still refused before the view opens, as today.
- Code shape (`~/.claude/rules/code-shape.md`): early returns, a blank line after every `if` block, named callbacks, no suppressions, keep docstrings. Python rules: type hints, `pathlib`, specific exceptions, `raise ... from`.
- Work on branch `design-sandbox-presets` (off `develop`, already exists and holds ADR-027). Subagents never `git push` or run Docker.

## Review Focus

Inputs the spec implies that no obvious test covers, most likely first. Each has its test in the task that owns the code.

1. The folder has a `sandbox.json` and the user picks Tight: the old bytes are gone after Start, and the new file is exactly the Tight document (Task 2 plain, Task 3 view).
2. `tasks.yaml` cannot be saved after `sandbox.json` was written: the previous file is byte-identical again, or absent again, and the run is refused with `task-file-unwritable` (Tasks 2 and 3).
3. `--sandbox tight` with `--dry-run`: the folder has no `sandbox.json` afterwards (Task 2).
4. A folder path with spaces, given relative, still writes to that folder and the log records that path (Task 2).
5. In the view, `--sandbox large` with no `-d`: the file path is settled when the folder is chosen, and Start writes there (Task 5).
6. The Docker switch is the only one that puts a command outside the sandbox, so Tight must never emit `excludedCommands` (Task 1).

---

## File structure

| File | Change |
|---|---|
| `src/alr/engine/presets.py` | **new**: `Preset`, `Switches`, the two lists, `switches_for`, `render`, `write_preset`, `restore_previous`, `preset_path` |
| `src/alr/engine/sandbox.py` | split `sandbox_args` so `settings_args(text)` is public |
| `src/alr/errors.py` | `SANDBOX_FILE_UNWRITABLE` |
| `src/alr/cli.py` | `--sandbox` flag; conflicts; pass the preset to the dry run, plain start, and the view |
| `src/alr/plain/start.py` | write the preset before the `tasks.yaml` save, with rollback |
| `src/alr/plain/dryrun.py` | the preset's `sandbox settings:` line |
| `src/alr/tui/settings.py` | `RunSettings.preset`, `.switches`; `missing`, `session_args`, `permissions_text` |
| `src/alr/tui/prepare.py` | write the preset in `_save_changes`, with rollback |
| `src/alr/tui/dialogs.py` | `PermissionsDialog` rewrite; `PermissionChoice` carries the preset |
| `src/alr/tui/app.py` | pass and apply the choice; strip label; folder pick settles the path |
| `tests/unit/test_presets.py` | **new** |
| `tests/unit/test_cli.py`, `test_plain_start.py` (or where `start` is tested), `test_dry_run.py`, `test_tui_settings.py`, `test_tui_prepare.py`, `test_tui_dialogs.py`, `test_tui_app.py` | per task |
| `tests/e2e/test_sandbox.py` | `--sandbox` end to end |
| `README.md`, `CHANGELOG.md`, `docs/design/01_requirements.md`, `03_system_architecture.md`, `02_architectural_decisions.md` (ADR-027 → Accepted) | docs |

---

### Task 1: The preset module

**Files:**
- Create: `src/alr/engine/presets.py`
- Modify: `src/alr/engine/sandbox.py` (`sandbox_args`), `src/alr/errors.py`
- Test: `tests/unit/test_presets.py`, `tests/unit/test_sandbox.py`

**Interfaces:**
- Produces, in `alr.engine.presets`:
  - `class Preset(StrEnum)`: `TIGHT = "tight"`, `LARGE = "large"`
  - `@dataclass(frozen=True) class Switches`: `web: bool`, `docker: bool`, `hide_credentials: bool`
  - `CREDENTIAL_PATHS: tuple[str, ...]`, `WEB_DOMAINS: tuple[str, ...]` (the two lists, in the stated order)
  - `def switches_for(preset: Preset) -> Switches`
  - `def render(switches: Switches) -> str` — the full document text (forced keys included), per the serialization rule
  - `def preset_path(tasks_file: str) -> str` — `str(Path(tasks_file).parent / "sandbox.json")`
  - `def write_preset(path: str, switches: Switches) -> bytes | None` — returns the previous file's bytes, or `None` when there was none; writes atomically; `OSError` → `RefusedError(SANDBOX_FILE_UNWRITABLE, f"{path}: {exc.strerror}")`
  - `def restore_previous(path: str, previous: bytes | None) -> None` — writes the bytes back, or unlinks the file (missing is fine)
- Produces, in `alr.engine.sandbox`: `def settings_args(text: str) -> list[str]` — what `sandbox_args` returns today for a file holding `text`; `sandbox_args(path)` becomes `settings_args(_read_settings(path))`.
- Produces, in `alr.errors`: `SANDBOX_FILE_UNWRITABLE = "sandbox-file-unwritable"`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_presets.py`

```python
def test_tight_switches() -> None:
    assert switches_for(Preset.TIGHT) == Switches(web=False, docker=False, hide_credentials=True)

def test_large_switches() -> None:
    assert switches_for(Preset.LARGE) == Switches(web=True, docker=True, hide_credentials=True)

def test_render_tight_is_the_documented_text() -> None:
    assert render(switches_for(Preset.TIGHT)) == TIGHT_TEXT  # the ADR's JSON block, 2-space indent, sorted keys, trailing newline

def test_render_large_has_the_web_list_and_docker() -> None:
    doc = json.loads(render(switches_for(Preset.LARGE)))["sandbox"]
    assert doc["network"] == {"allowedDomains": list(WEB_DOMAINS)}
    assert doc["excludedCommands"] == ["docker *"]
    assert doc["filesystem"] == {"denyRead": list(CREDENTIAL_PATHS)}

def test_an_off_switch_leaves_its_key_absent() -> None:
    doc = json.loads(render(Switches(web=False, docker=False, hide_credentials=False)))["sandbox"]
    assert set(doc) == {"enabled", "allowUnsandboxedCommands", "failIfUnavailable"}

def test_tight_never_excludes_a_command() -> None:
    assert "excludedCommands" not in render(switches_for(Preset.TIGHT))

def test_render_is_deterministic() -> None:
    assert render(switches_for(Preset.LARGE)) == render(switches_for(Preset.LARGE))

def test_write_preset_returns_none_when_there_was_no_file(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.json"
    assert write_preset(str(path), switches_for(Preset.TIGHT)) is None
    assert path.read_text(encoding="utf-8") == render(switches_for(Preset.TIGHT))

def test_write_preset_returns_the_previous_bytes_and_replaces_them(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.json"; path.write_bytes(b"old")
    assert write_preset(str(path), switches_for(Preset.LARGE)) == b"old"
    assert path.read_text(encoding="utf-8") == render(switches_for(Preset.LARGE))

def test_write_preset_leaves_no_temporary_file(tmp_path: Path) -> None:
    write_preset(str(tmp_path / "sandbox.json"), switches_for(Preset.TIGHT))
    assert [p.name for p in tmp_path.iterdir()] == ["sandbox.json"]

def test_write_preset_into_an_unwritable_folder_is_refused(tmp_path: Path) -> None:
    # make tmp_path read-only (skip as root); expect RefusedError with .reason == SANDBOX_FILE_UNWRITABLE and exit_code 3

def test_restore_previous_puts_the_bytes_back(tmp_path: Path) -> None: ...
def test_restore_previous_removes_the_file_when_there_was_none(tmp_path: Path) -> None: ...
def test_restore_previous_with_none_and_no_file_is_harmless(tmp_path: Path) -> None: ...
def test_preset_path_is_beside_the_task_file() -> None:
    assert preset_path("run dir/tasks.yaml") == "run dir/sandbox.json"
```

And in `tests/unit/test_sandbox.py`: `test_settings_args_equals_sandbox_args_for_the_same_text` (write a file, compare `sandbox_args(path)` to `settings_args(text)`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -n 0 tests/unit/test_presets.py tests/unit/test_sandbox.py -x --tb=short`
Expected: FAIL with `ModuleNotFoundError: alr.engine.presets` / `ImportError: settings_args`

- [ ] **Step 3: Implement** `src/alr/engine/presets.py` per the Interfaces block; add the constant to `errors.py`; split `sandbox_args` in `sandbox.py`. Atomic write: `tempfile.NamedTemporaryFile(dir=parent, prefix=".sandbox-", suffix=".json", delete=False)` then `Path(tmp).replace(path)`, removing the temporary on failure.

- [ ] **Step 4: Run the tests**

Run: `make test`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/alr/engine/presets.py src/alr/engine/sandbox.py src/alr/errors.py tests/unit/test_presets.py tests/unit/test_sandbox.py
git commit -m "Add the sandbox preset module: Tight and Large rendered to a settings document"
```

---

### Task 2: `--sandbox` in plain mode and the dry run

**Files:**
- Modify: `src/alr/cli.py` (`_parser`, `_main`, `resolve_sandbox`, `session_args`), `src/alr/plain/start.py` (`start`), `src/alr/plain/dryrun.py` (`dry_run`)
- Test: `tests/unit/test_cli.py`, the unit tests of `plain.start` and `plain.dryrun`

**Interfaces:**
- Consumes: Task 1's `Preset`, `Switches`, `switches_for`, `render`, `preset_path`, `write_preset`, `restore_previous`, `settings_args`.
- Produces:
  - `_parser()` adds `--sandbox`, `choices=["tight", "large"]`, `default=None`, `metavar="<preset>"`, help: `Sandbox sessions with a preset, tight or large, written to <folder>/sandbox.json at start; replaces an existing file there`.
  - `def resolve_permissions(dir: str, sandbox_settings: str, skip: bool, preset: str | None) -> Answer` replaces `resolve_sandbox`, where `@dataclass(frozen=True) class Answer: sandbox_file: str; skip: bool; switches: Switches | None`. Rules: any two of `sandbox_settings`, `skip`, `preset` → `UsageError("--sandbox cannot be used with --sandbox-settings or --yolo")` (keep today's message for the `--sandbox-settings`/`--yolo` pair); a preset gives `sandbox_file = preset_path(<folder>/tasks.yaml)` and `switches = switches_for(Preset(preset))`; otherwise today's `resolve_sandbox` result.
  - `session_args(answer: Answer, dry_run: bool) -> list[str]`: with switches, `settings_args(render(switches))`; else today's behavior.
  - `start(..., switches: Switches | None = None)`: after the log opens and before `_save_reset`, `previous = write_preset(sandbox_settings, switches)` when `switches` is not None; if the save then raises `RefusedError`, `restore_previous(...)` before `log.discard()` and re-raise. A `write_preset` refusal also discards the log.
  - `dry_run(..., preset: Preset | None = None)`: with a preset the line is `sandbox settings: {sandbox_file} ({preset.value} preset; written on a real run)`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_cli.py` (follow `test_both_permission_flags_are_refused` and `test_the_folders_sandbox_json_sandboxes_the_run` for the fake-claude pattern):

```python
@pytest.mark.parametrize("other", ["--sandbox-settings=x.json", "--yolo"])
def test_sandbox_preset_with_another_permission_flag_exits_2(other, ...) -> None:
    # stderr: "error: --sandbox cannot be used with --sandbox-settings or --yolo"; code 2

def test_sandbox_preset_rejects_an_unknown_value(...) -> None:  # code 2 (argparse)

def test_sandbox_tight_writes_the_folder_file_and_sandboxes_the_run(...) -> None:
    # folder had no sandbox.json; after the run it equals render(switches_for(Preset.TIGHT));
    # fake claude argv == settings_args(render(...)); exit 0

def test_sandbox_large_replaces_an_existing_folder_file(...) -> None:
    # folder had b"{}"; after the run the file equals render(Large)

def test_sandbox_preset_wins_over_the_folders_file_for_the_session_flags(...) -> None:
    # folder file is {"sandbox":{"filesystem":{"denyRead":["~/"]}}}; the session got the preset's settings, not the file's

def test_a_relative_folder_with_spaces_gets_its_sandbox_json(tmp_path, monkeypatch, ...) -> None:
    # chdir to tmp_path, folder "run dir", -d "run dir" --sandbox tight; "run dir/sandbox.json" exists; run.jsonl's sandbox_settings is "run dir/sandbox.json"

def test_dry_run_with_a_preset_names_the_file_and_writes_nothing(...) -> None:
    # stdout has "sandbox settings: <folder>/sandbox.json (tight preset; written on a real run)"; no file afterwards; exit 0
```

In the `plain.start` tests:

```python
async def test_start_writes_the_preset_before_running(...) -> None
async def test_start_puts_the_old_sandbox_json_back_when_the_task_file_save_fails(...) -> None:
    # folder file b"old"; make tasks.yaml unwritable (read-only file, skip as root) with reset=True and a failed task;
    # RefusedError reason task-file-unwritable; sandbox.json == b"old"; no logs folder left
async def test_start_removes_the_new_sandbox_json_when_the_task_file_save_fails(...) -> None  # no file before; none after
async def test_start_refuses_when_the_sandbox_json_cannot_be_written(...) -> None:
    # folder read-only: reason sandbox-file-unwritable, exit_code 3, tasks.yaml unchanged, logs folder removed
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -n 0 tests/unit/test_cli.py tests/unit -k "sandbox_preset or sandbox_tight or sandbox_large or preset" -x --tb=short`
Expected: FAIL (unrecognized argument `--sandbox`, missing parameters)

- [ ] **Step 3: Implement** the flag, `resolve_permissions`, `session_args`, `start`, and `dry_run` per the Interfaces block. In `_main`, the preset is `args.sandbox`; `_open_view` receives the `Answer` (Task 5 uses it; for now pass `answer.sandbox_file` as before so the view still builds).

- [ ] **Step 4: Run the tests**

Run: `make test`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/alr/cli.py src/alr/plain/start.py src/alr/plain/dryrun.py tests/unit
git commit -m "Add --sandbox tight|large: plain mode writes the preset to the run folder on start"
```

---

### Task 3: The view's settings and Start

**Files:**
- Modify: `src/alr/tui/settings.py`, `src/alr/tui/prepare.py` (`_save_changes`, `_open_log`)
- Test: `tests/unit/test_tui_settings.py`, `tests/unit/test_tui_prepare.py`

**Interfaces:**
- Consumes: Task 1.
- Produces, on `RunSettings`:
  - fields `preset: Preset | None = None` and `switches: Switches | None = None`. A preset answer is `permissions = SANDBOX`, `switches` set, `preset` set to the radio chosen, `sandbox_file = preset_path(tasks_file)` once the task file is known. A file answer has `switches is None`.
  - `missing()`: `SANDBOX` with no `sandbox_file` and no `switches` → `"choose a sandbox settings file"` (unchanged text); `SANDBOX` with switches but an empty `sandbox_file` → `"choose a run folder"` (the file path needs the folder).
  - `session_args()`: with switches, `settings_args(render(self.switches))`; else as today.
  - `def permissions_text(self) -> str`: the strip text from Global Constraints, without the `Permissions: ` prefix (`not chosen`, `skip`, `sandbox`, `sandbox (tight)`, `sandbox (large, adjusted)`). Adjusted means `self.switches != switches_for(self.preset)`.
- Produces, in `prepare._save_changes`: when `settings.switches` is not None, `previous = write_preset(settings.sandbox_file, settings.switches)` first; the existing save follows; on `RefusedError` from the save, `restore_previous(settings.sandbox_file, previous)` then re-raise. The caller's existing `except RefusedError: prepared.discard(); raise` still removes the log folder for both refusals.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_tui_settings.py`:

```python
def test_a_preset_answer_is_not_missing_once_the_folder_is_known() -> None
def test_a_preset_answer_without_a_folder_asks_for_the_folder() -> None
def test_session_args_for_a_preset_come_from_the_rendered_document() -> None:
    assert RunSettings(tasks_file="t/tasks.yaml", permissions=Permissions.SANDBOX, preset=Preset.TIGHT, switches=switches_for(Preset.TIGHT), sandbox_file="t/sandbox.json").session_args() == settings_args(render(switches_for(Preset.TIGHT)))
@pytest.mark.parametrize(("settings", "text"), [...])  # not chosen / skip / sandbox / sandbox (tight) / sandbox (large) / sandbox (tight, adjusted) / sandbox (large, adjusted)
def test_permissions_text(settings, text) -> None
```

`tests/unit/test_tui_prepare.py` (follow `test_prepare_refuses_and_removes_the_run_folder_when_saving_the_actions_fails`):

```python
async def test_prepare_writes_the_preset_then_the_task_file(work_dir) -> None  # file equals render(...); edited actions saved
async def test_prepare_puts_the_old_sandbox_json_back_when_the_save_fails(work_dir) -> None  # b"old" before and after; reason task-file-unwritable; run folder removed
async def test_prepare_removes_the_new_sandbox_json_when_the_save_fails(work_dir) -> None
async def test_prepare_refuses_and_removes_the_run_folder_when_the_sandbox_json_cannot_be_written(work_dir) -> None  # reason sandbox-file-unwritable
async def test_prepare_writes_nothing_for_a_file_answer(work_dir) -> None  # switches None: the folder's file is untouched byte for byte
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -n 0 tests/unit/test_tui_settings.py tests/unit/test_tui_prepare.py -x --tb=short`
Expected: FAIL (`TypeError: unexpected keyword argument 'preset'`)

- [ ] **Step 3: Implement** per the Interfaces block.

- [ ] **Step 4: Run the tests**

Run: `make test`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/alr/tui/settings.py src/alr/tui/prepare.py tests/unit/test_tui_settings.py tests/unit/test_tui_prepare.py
git commit -m "Carry a sandbox preset in the view's settings and write it on Start"
```

---

### Task 4: The permissions dialog

**Files:**
- Modify: `src/alr/tui/dialogs.py` (`PermissionChoice`, `PermissionsDialog`)
- Test: `tests/unit/test_tui_dialogs.py`

**Interfaces:**
- Consumes: Task 1; Task 3's `RunSettings` fields (the dialog takes a `RunSettings` and returns a `PermissionChoice`).
- Produces:
  - `@dataclass(frozen=True) class PermissionChoice`: `permissions: Permissions`, `sandbox_file: str`, `preset: Preset | None`, `switches: Switches | None`.
  - `PermissionsDialog(settings: RunSettings)`. Widgets and ids: `RadioSet` with `RadioButton` ids `file` (label `Use the settings file {path}`; when it is shown is the note below), `tight` (`Tight sandbox   no web, no Docker, keys hidden`), `large` (`Large sandbox   web and Docker allowed`), `skip` (`Skip permissions (no sandbox)`); three `Checkbox`es ids `web` (`Sessions may reach the web`), `docker` (`Sessions may run Docker  (runs outside the sandbox with your full access)`), `hide` (`Hide credential folders (~/.ssh, ~/.aws, ~/.gnupg, ~/.config/gh, ~/.netrc, ~/.claude)`); a `Static` id `preview`; a `Static` id `note`; `OK` and `Cancel` buttons as today. The `Input` path field goes.
  - Note on the `file` radio: it is shown when a file answered (a folder file or `--sandbox-settings`), which is `settings.permissions is SANDBOX and settings.switches is None and settings.sandbox_file`, **or** when the folder has a `sandbox.json` on disk (`folder_sandbox(Path(settings.tasks_file).parent)`) even though a preset is the current answer, since the user may go back to the file. Its label shows that path.
  - Pre-selection: `file` when a file answered; `tight`/`large` when `settings.preset` is set; `skip` when `permissions is SKIP`; else `tight`. Choosing `tight`/`large` sets the checkboxes to `switches_for(...)`; the checkboxes are `disabled` when `file` or `skip` is selected.
  - Preview: for a preset, `render(current switches)`; for `file`, the file's text as read (`Path.read_text`, errors replaced), with the header `{path} (as it is)`; for `skip`, empty. Note text: preset → `Written to {sandbox_file} when you press Start. Cancel or Quit discards it.` plus ` Replaces the file there.` when a folder file exists; file → `Nothing is written. Pick a preset to replace it.`; skip → empty.
  - OK with `file` → `PermissionChoice(SANDBOX, settings.sandbox_file or the folder file, None, None)` after `check_sandbox(path)` as today (error shown in `#error`); with a preset → `PermissionChoice(SANDBOX, preset_path(settings.tasks_file) if settings.tasks_file else "", preset, Switches(from the checkboxes))`; with `skip` → `PermissionChoice(SKIP, "", None, None)`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_tui_dialogs.py`, using the existing `host`/`record` pattern

```python
async def test_permissions_dialog_preselects_tight_without_a_file() -> None
async def test_permissions_dialog_preselects_the_file_when_the_folder_has_one(tmp_path) -> None  # radio #file present, selected; checkboxes disabled; preview holds the file text
async def test_permissions_dialog_hides_the_file_radio_without_a_file() -> None
async def test_permissions_dialog_preselects_the_flags_preset() -> None  # settings.preset LARGE → #large selected, checkboxes all on
async def test_permissions_dialog_preselects_skip_after_yolo() -> None
async def test_choosing_large_sets_every_switch_on_and_shows_its_document() -> None  # preview == render(Large)
async def test_moving_a_switch_changes_the_preview_and_the_result() -> None  # tight + web → switches.web True; preview has the web list
async def test_ok_with_a_preset_returns_the_folder_path_and_switches(tmp_path) -> None
async def test_ok_with_the_file_returns_the_file_and_no_switches(tmp_path) -> None
async def test_ok_with_the_file_keeps_the_user_on_the_step_when_it_is_invalid(tmp_path) -> None  # replaces test_permissions_dialog_bad_settings_file_keeps_the_user_on_the_step
async def test_the_note_says_the_file_is_written_on_start_and_replaced(tmp_path) -> None
async def test_permissions_dialog_skip_returns_skip() -> None  # kept
```

Delete `test_enter_in_the_sandbox_field_submits` and `test_permissions_dialog_sandbox_returns_the_file` (the field is gone); update the `PermissionsDialog("")` call in the Esc parametrization to `PermissionsDialog(RunSettings())`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -n 0 tests/unit/test_tui_dialogs.py -x --tb=short`
Expected: FAIL

- [ ] **Step 3: Implement** per the Interfaces block. Keep each handler a named method (`on_radio_set_changed`, `on_checkbox_changed`, `_refresh_preview`, `_accept`); no inline callbacks.

- [ ] **Step 4: Run the tests**

Run: `make test`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/alr/tui/dialogs.py tests/unit/test_tui_dialogs.py
git commit -m "Rebuild the permissions dialog around Tight and Large presets with a preview"
```

---

### Task 5: Wire the view and the CLI together

**Files:**
- Modify: `src/alr/tui/app.py` (`ask_folder`, `ask_permissions`, `_permissions_label`, `_menu` for `sandbox`), `src/alr/cli.py` (`_open_view`, `_permissions`)
- Test: `tests/unit/test_tui_app.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: Tasks 3 and 4.
- Produces:
  - `_open_view(args, tasks_file, answer: Answer)`: `RunSettings(..., sandbox_file=answer.sandbox_file, permissions=..., preset=Preset(args.sandbox) if args.sandbox else None, switches=answer.switches)`. With a preset and no `-d`, `sandbox_file` is `""` until the folder is chosen.
  - `ask_folder`: after the task file is set, when `settings.switches` is not None, `settings.sandbox_file = preset_path(check.tasks_file)`; the existing "folder file answers an UNSET question" branch stays.
  - `ask_permissions`: `PermissionsDialog(self.settings)`; on a choice, copy `permissions`, `sandbox_file`, `preset`, `switches`.
  - `_permissions_label(settings: RunSettings) -> str` returns `f"Permissions: {settings.permissions_text()}"`.
  - The `x` viewer (`_menu("sandbox")`) opens `settings.sandbox_file` as today; before Start with a preset the file may not exist yet, and the viewer's existing "no sandbox file" text covers it.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_tui_app.py` (follow `test_permissions_label_shows_the_choice_and_applies_the_dialog` and `test_launch_asks_for_permissions_then_actions_in_order`):

```python
async def test_launch_does_not_ask_permissions_when_the_folder_has_a_file(work_dir) -> None  # unchanged behavior, pinned
async def test_launch_does_not_ask_permissions_with_a_preset_flag(work_dir) -> None  # strip "Permissions: sandbox (large)"
async def test_a_preset_flag_without_a_folder_settles_the_path_when_the_folder_is_chosen(work_dir) -> None  # Review Focus 5
async def test_p_reopens_the_dialog_preselected_to_the_current_answer(work_dir) -> None
async def test_choosing_tight_over_the_folders_file_shows_adjusted_when_a_switch_moves(work_dir) -> None  # strip "Permissions: sandbox (tight, adjusted)"
async def test_start_with_a_preset_writes_the_folder_file(work_dir) -> None  # after the fake run, sandbox.json == render(...)
```

`tests/unit/test_cli.py`: `test_the_view_refuses_a_preset_with_another_permission_flag` (exit 2 before the view opens), `test_the_view_gets_the_preset_from_the_flag` (inspect the `RunSettings` handed to `run_app`, as the existing view tests do).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -n 0 tests/unit/test_tui_app.py tests/unit/test_cli.py -x --tb=short`
Expected: FAIL

- [ ] **Step 3: Implement** per the Interfaces block.

- [ ] **Step 4: Run the tests**

Run: `make test`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/alr/tui/app.py src/alr/cli.py tests/unit/test_tui_app.py tests/unit/test_cli.py
git commit -m "Wire sandbox presets through the view: flags, folder pick, dialog, and strip"
```

---

### Task 6: End-to-end tests

**Files:**
- Modify: `tests/e2e/test_sandbox.py`

**Interfaces:**
- Consumes: the built wheel's `--sandbox` flag (Task 2) and the e2e helpers `run_with_flags`, `assert_sandboxed`-style parsing, `fake_claude_records`, `run_dry_run`.

The e2e tests are black-box: they share nothing with the unit tests and must not import `alr`. Copy the expected Tight document into the test as a literal (`TIGHT_DOC`), parsed for comparison.

- [ ] **Step 1: Write the tests**

```python
def test_sandbox_tight_writes_the_folder_file_and_passes_it_to_claude(tmp_path, fake_claude) -> None:
    run = run_with_flags(tmp_path, "--sandbox", "tight")
    folder_file = run.tasks_path.parent / "sandbox.json"
    assert run.res.code == 0, run.res
    assert json.loads(folder_file.read_text()) == TIGHT_DOC
    assert folder_file.read_text().endswith("\n") and "  \"sandbox\"" in folder_file.read_text()  # indent 2
    argv = run.records[0]["argv"]
    assert argv[:4] == ["--print", "--permission-mode", "acceptEdits", "--settings"]
    assert json.loads(argv[4]) == TIGHT_DOC

def test_sandbox_large_replaces_the_folders_existing_file(tmp_path, fake_claude) -> None  # write "{}" first; after: LARGE_DOC with the web list and docker
def test_sandbox_preset_with_yolo_exits_2(tmp_path, fake_claude) -> None  # stderr "error: --sandbox cannot be used with --sandbox-settings or --yolo"; no file written; claude never ran
def test_sandbox_preset_with_sandbox_settings_exits_2(tmp_path, fake_claude) -> None
def test_sandbox_preset_dry_run_writes_nothing(tmp_path) -> None  # stdout line "sandbox settings: <folder>/sandbox.json (tight preset; written on a real run)"
def test_sandbox_preset_refused_run_leaves_no_file(tmp_path, fake_claude) -> None  # a failed task in tasks.yaml: exit 3, no sandbox.json, no logs/
```

- [ ] **Step 2: Lint the test file**

Run: `uv run ruff format tests/e2e/test_sandbox.py && uv run ruff check tests/e2e/test_sandbox.py`
Expected: clean. (The e2e suite itself runs in Task 8 from the main session.)

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_sandbox.py
git commit -m "Cover --sandbox tight|large end to end"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `docs/design/01_requirements.md`, `docs/design/03_system_architecture.md`, `docs/design/02_architectural_decisions.md`

- [ ] **Step 1: README**: add the `--sandbox <preset>` row after `--sandbox-settings` (`Sandbox sessions with a preset, tight or large; written to <folder>/sandbox.json at start, replacing an existing file`); rewrite the "A real run needs one of ..." paragraph to name the three answers and that any two exit 2; add a short "Sandbox presets" subsection under the flags: the two presets, the three switches (view only), the two lists, the on-Start write and its note, and that the folder's file is alr's to replace; add the `sandbox-file-unwritable` row to the refusal table (`The preset's sandbox.json could not be written to the run folder`); update the quick-start comment to show `alr -d .local/feature --sandbox tight`.
- [ ] **Step 2: CHANGELOG** under `## [Unreleased]`: `### Added`: `--sandbox tight|large` and what it writes; the view's permissions dialog with presets, switches, and preview; the refusal reason `sandbox-file-unwritable`. `### Changed`: the permissions dialog's path field is gone; a folder's `sandbox.json` is offered as "Use the settings file" and is replaced by a preset on Start.
- [ ] **Step 3: Design docs**: `01_requirements.md`: flag table row; functional requirement 17 (the permissions setting now offers the file, Tight, Large, or skip); refusal row in the success criteria; the non-goal "Editing `sandbox.json` in the view" gets `(alr writes it from a preset; the viewer stays read-only)`. `03_system_architecture.md`: `engine/presets.py` in the module list; the startup sequence's save step mentions the preset write and rollback; the view's dialog description. `02_architectural_decisions.md`: ADR-027 **Status** → `Accepted`, and note that the web list, not `*`, was taken; bump frontmatter `version`, `date`, `notes`; Decision Summary row status.
- [ ] **Step 4: Check links and lint**: `uv run ruff check .` (no Python changed, should be clean) and read each edited section once.
- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md docs/design
git commit -m "Document sandbox presets and accept ADR-027"
```

---

### Task 8: Build and whole-branch review (main session)

- [ ] **Step 1:** `./build/build.sh` from the main session. Expected: the wheel builds and every e2e test passes, Task 6's included.
- [ ] **Step 2:** Fix anything the build turns up on the branch, re-run until green.
- [ ] **Step 3:** Adversarial whole-branch review per the project's review rule (fresh reviewers, reproduce every finding), then address findings.
- [ ] **Step 4:** Hand the branch to the owner for the PR; nobody but the owner pushes.

The wiki (Permissions-and-the-sandbox, Command-line-reference, Reading-the-screen, Your-first-run) is a separate repository and is updated after the release, with the screenshots the owner still has to retake.
