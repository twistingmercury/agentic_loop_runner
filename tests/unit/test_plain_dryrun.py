"""The dry run: validate the task file and report on it, without running anything."""

import io
import os
from pathlib import Path

import pytest
from helpers import write_file

from alr.errors import FailedTasksError, RefusedError
from alr.plain.dryrun import dry_run
from alr.plain.dryrun_checks import check_task_list
from alr.tasks import parse_tasks

# What a dry run with no sandbox settings file prints where the sandbox line goes.
SKIPPED_LINE = "sandbox rules skipped: no sandbox settings file\n"

# Every task prompt in a fixture whose dry-run output is asserted exactly ends
# like this, so none of those fixtures draws a warning that the assertion does
# not expect.
PROMPT_ENDING = """

      Completion criteria:
      - it is done

      Finish:
      Reply in one line."""


def _task(task_id: int, name: str, request: str, extra: str = "") -> str:
    """One task entry, with a prompt that passes every dry-run rule."""
    return (
        f"  - id: {task_id}\n"
        f"    name: {name}\n"
        f"    prompt: |\n"
        f"      {request}{PROMPT_ENDING}\n"
        f"{extra}"
    )


VALID_TASKS_YAML = (
    "shared:\n  prompt: shared\n  after_task: []\ntasks:\n"
    + _task(1, "First task", "Do the first thing.")
    + _task(2, "Second task", "Do the second thing.")
)
ACTIONS_YAML = (
    """shared:
  prompt: shared
  after_task:
    - action: go test ./...
      timeout: 30s
    - action: |
        echo one
        echo two
    - action: make lint
tasks:
"""
    + _task(1, "First", "Do the first thing.")
    + _task(2, "Second", "Do the second thing.")
    + _task(3, "Third", "Do the third thing.")
)


async def _dry_run(
    tasks_file: str,
    action_timeout: str = "",
    sandbox_file: str = "",
    sandbox_name: str = "",
    added_on_real_run: bool = False,
    strict: bool = False,
) -> str:
    out = io.StringIO()
    await dry_run(
        out,
        tasks_file,
        action_timeout,
        sandbox_file,
        sandbox_name,
        added_on_real_run,
        strict,
    )
    return out.getvalue()


async def test_dry_run_valid_file(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    assert await _dry_run(tasks_file) == (
        "   ID  STATE      NAME\n"
        "   --  ---------  ----\n"
        "    1  PENDING    First task\n"
        "    2  PENDING    Second task\n"
        f"{SKIPPED_LINE}"
        f"{tasks_file} is valid\n"
    )


async def test_dry_run_invalid_file(tmp_path: Path) -> None:
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: bogus}\n",
    )

    out = io.StringIO()
    with pytest.raises(RefusedError, match="state") as caught:
        await dry_run(out, tasks_file, "", "")

    assert caught.value.reason == "invalid-task-file"
    assert "failed to start loop runner" not in str(caught.value)
    assert str(caught.value).count("failed to parse tasks yaml") == 1
    assert out.getvalue() == ""


FAILED_TASKS_YAML = """shared:
  prompt: shared
tasks:
  - id: 1
    name: First task
    prompt: Do the first thing.
    state: completed
  - id: 2
    name: Second task
    prompt: Do the second thing.
    state: failed
    error: boom
  - id: 100
    name: Third task
    prompt: Do the third thing.
"""
FAILED_TASKS_REPORT = (
    "Some tasks failed previous runs:\n"
    "    ID  STATE      NAME\n"
    "   ---  ---------  ----\n"
    "✅   1  \033[92mCOMPLETED\033[0m  First task\n"
    "❌   2  \033[1;91mFAILED   \033[0m  Second task  \033[1;91m← Needs review!\033[0m\n"
    "   100  PENDING    Third task\n"
)


async def test_dry_run_refuses_failed_tasks_after_showing_them(
    tmp_path: Path,
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", FAILED_TASKS_YAML)

    out = io.StringIO()
    with pytest.raises(FailedTasksError) as caught:
        await dry_run(out, tasks_file, "", "")

    assert caught.value.reason == "failed-tasks"
    assert out.getvalue() == FAILED_TASKS_REPORT
    assert Path(tasks_file).read_text(encoding="utf-8") == FAILED_TASKS_YAML, (
        "the tasks file must be unchanged"
    )


# The shared lists are listed once for the file, not once per task.
@pytest.mark.parametrize(
    ("override", "want"),
    [
        pytest.param(
            "",
            "after_task: go test ./...: 30s (action)\n"
            "after_task: echo one: 10m (default)\n"
            "after_task: make lint: 10m (default)\n",
            id="action and default",
        ),
        pytest.param(
            "2m",
            "after_task: go test ./...: 2m (flag)\n"
            "after_task: echo one: 2m (flag)\n"
            "after_task: make lint: 2m (flag)\n",
            id="flag overrides every action",
        ),
    ],
)
async def test_dry_run_lists_effective_action_timeouts(
    tmp_path: Path, override: str, want: str
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", ACTIONS_YAML)

    got = await _dry_run(tasks_file, action_timeout=override)

    assert "NAME\n" in got, "the task table comes first"
    assert got.endswith(f"Third\n{want}{SKIPPED_LINE}{tasks_file} is valid\n"), (
        "action lines sit between the table and the valid line"
    )


TASK_ACTIONS_YAML = (
    """shared:
  prompt: shared
  after_task:
    - action: echo one
tasks:
"""
    + _task(
        1,
        "First",
        "Do the first thing.",
        extra="    actions:\n      - action: uv run pytest\n        timeout: 15m\n",
    )
    # A completed task's prompt is skipped by every rule, so "p" stays clean.
    + """  - id: 2
    name: Done
    prompt: p
    state: completed
    actions:
      - action: markdownlint README.md
"""
)


@pytest.mark.parametrize(
    ("override", "want"),
    [
        pytest.param(
            "",
            "task 1 actions: uv run pytest: 15m (action)\n"
            "task 2 actions: markdownlint README.md: 10m (default)\n"
            "after_task: echo one: 10m (default)\n",
            id="no flag",
        ),
        pytest.param(
            "2m",
            "task 1 actions: uv run pytest: 2m (flag)\n"
            "task 2 actions: markdownlint README.md: 2m (flag)\n"
            "after_task: echo one: 2m (flag)\n",
            id="flag overrides every line",
        ),
    ],
)
async def test_dry_run_lists_a_tasks_own_action_limits_first(
    tmp_path: Path, override: str, want: str
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", TASK_ACTIONS_YAML)

    got = await _dry_run(tasks_file, action_timeout=override)

    assert got.endswith(f"Done\n{want}{SKIPPED_LINE}{tasks_file} is valid\n"), (
        "a completed task's actions are listed too: the dry run reports the file, not the run"
    )


AFTER_RUN_YAML = (
    "shared:\n  prompt: shared\n  after_task:\n    - action: make lint\n"
    "  after_run:\n    - action: git push --dry-run\n      timeout: 1m\n"
    "    - action: echo done\n"
    "tasks:\n" + _task(1, "First", "Do the first thing.")
)
# An explicit empty after_task says the missing check is meant.
NO_ACTIONS_YAML = "shared:\n  prompt: shared\n  after_task: []\ntasks:\n" + _task(
    1, "First", "Do the first thing."
)


async def test_dry_run_lists_after_run_actions_after_the_after_task_ones(
    tmp_path: Path,
) -> None:
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        AFTER_RUN_YAML,
    )

    got = await _dry_run(tasks_file)

    assert got.endswith(
        "First\nafter_task: make lint: 10m (default)\n"
        "after_run: git push --dry-run: 1m (action)\n"
        "after_run: echo done: 10m (default)\n"
        f"{SKIPPED_LINE}{tasks_file} is valid\n"
    )


async def test_dry_run_no_actions_prints_no_action_lines(tmp_path: Path) -> None:
    tasks_file = write_file(
        tmp_path / "tasks.yaml",
        NO_ACTIONS_YAML,
    )

    got = await _dry_run(tasks_file, action_timeout="2m")

    # The prefix, not the bare word: the temporary path holds this test's name.
    assert "after_task: " not in got
    assert "after_run: " not in got
    assert got.endswith(f"First\n{SKIPPED_LINE}{tasks_file} is valid\n")


async def test_dry_run_names_sandbox_settings(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    settings = write_file(tmp_path / "sandbox.json", "{}")

    assert await _dry_run(tasks_file, sandbox_file=settings) == (
        "   ID  STATE      NAME\n"
        "   --  ---------  ----\n"
        "    1  PENDING    First task\n"
        "    2  PENDING    Second task\n"
        f"sandbox settings: {settings}\n"
        f"{tasks_file} is valid\n"
    )


async def test_dry_run_refuses_a_log_folder_that_is_a_file(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    (tmp_path / "logs").write_text("a file", encoding="utf-8")

    out = io.StringIO()
    with pytest.raises(RefusedError) as caught:
        await dry_run(out, tasks_file, "", "")

    assert caught.value.reason == "log-unwritable"
    assert out.getvalue() == ""


async def test_dry_run_refuses_failed_tasks_before_an_unwritable_log_folder(
    tmp_path: Path,
) -> None:
    # A real run opens its log after the failed-tasks check, so it never gets
    # to say log-unwritable for a file it refuses first.
    tasks_file = write_file(tmp_path / "tasks.yaml", FAILED_TASKS_YAML)
    (tmp_path / "logs").write_text("a file", encoding="utf-8")

    out = io.StringIO()
    with pytest.raises(FailedTasksError):
        await dry_run(out, tasks_file, "", "")

    assert out.getvalue() == FAILED_TASKS_REPORT


async def test_dry_run_refuses_a_read_only_log_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    (tmp_path / "logs").mkdir()
    access = os.access

    def deny_logs(path: str | Path, mode: int, **kwargs: bool) -> bool:
        # chmod 500 would not stop root, so the refusal is made here instead.
        denied = Path(path).name == "logs" and mode & os.W_OK
        return not denied and access(path, mode, **kwargs)

    monkeypatch.setattr(os, "access", deny_logs)

    out = io.StringIO()
    with pytest.raises(RefusedError) as caught:
        await dry_run(out, tasks_file, "", "")

    assert caught.value.reason == "log-unwritable"
    assert out.getvalue() == ""
    assert list((tmp_path / "logs").iterdir()) == []


async def test_dry_run_creates_no_log_folder(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    await _dry_run(tasks_file)

    assert not (tmp_path / "logs").exists()


async def test_dry_run_names_a_sandbox_picked_by_name(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    (tmp_path / "sandboxes").mkdir()
    path = write_file(tmp_path / "sandboxes" / "large.json", "{}")

    got = await _dry_run(tasks_file, sandbox_file=path, sandbox_name="large")

    assert f"sandbox settings: {path} (large)\n" in got
    assert got.endswith(f"{tasks_file} is valid\n")


async def test_dry_run_says_a_missing_shipped_sandbox_is_added_on_a_real_run(
    tmp_path: Path,
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    path = str(tmp_path / "sandboxes" / "tight.json")

    got = await _dry_run(
        tasks_file, sandbox_file=path, sandbox_name="tight", added_on_real_run=True
    )

    assert f"sandbox settings: {path} (tight; added on a real run)\n" in got
    assert not (tmp_path / "sandboxes").exists()


async def test_dry_run_writes_nothing_at_the_sandbox_path(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    path = tmp_path / "sandbox.json"
    path.write_text("{}", encoding="utf-8")

    await _dry_run(tasks_file, sandbox_file=str(path), sandbox_name="large")

    assert path.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    "tasks_yaml",
    [
        pytest.param(VALID_TASKS_YAML, id="valid"),
        pytest.param(ACTIONS_YAML, id="shared actions"),
        pytest.param(TASK_ACTIONS_YAML, id="a task's own actions"),
        pytest.param(AFTER_RUN_YAML, id="after_run"),
        pytest.param(NO_ACTIONS_YAML, id="no actions"),
    ],
)
def test_dry_run_fixtures_pass_every_rule(tasks_yaml: str) -> None:
    # The tests above pin the dry run's exact output, so their fixtures must not
    # draw a warning the assertions do not expect.
    parsed = parse_tasks(tasks_yaml)

    assert check_task_list(parsed, None) == []


# No Finish block, so rule 1 warns on task 1.
NO_FINISH_YAML = (
    "shared:\n  prompt: shared\n  after_task: []\ntasks:\n"
    "  - id: 1\n    name: First\n    prompt: |\n"
    "      Do the first thing.\n\n      Completion criteria:\n      - done\n"
)
WRAPPED_BUILD_YAML = (
    "shared:\n  prompt: shared\n  after_task: []\ntasks:\n"
    "  - id: 1\n    name: First\n    prompt: |\n"
    "      Run `./build/build.sh > build.log` to check.\n"
    f"{PROMPT_ENDING}\n"
)


async def test_warnings_print_before_the_sandbox_line_and_valid(
    tmp_path: Path,
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", NO_FINISH_YAML)
    settings = write_file(tmp_path / "sandbox.json", "{}")

    got = await _dry_run(tasks_file, sandbox_file=settings)

    assert got.splitlines()[-3:] == [
        "warning: task 1: finish-block: Completion criteria:",
        f"sandbox settings: {settings}",
        f"{tasks_file} is valid",
    ]


async def test_no_sandbox_prints_the_skipped_line(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    got = await _dry_run(tasks_file, sandbox_file="")

    assert got.endswith(f"Second task\n{SKIPPED_LINE}{tasks_file} is valid\n")
    assert "sandbox settings:" not in got


async def test_the_skipped_line_follows_the_warnings(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", NO_FINISH_YAML)

    got = await _dry_run(tasks_file)

    assert got.endswith(
        "warning: task 1: finish-block: Completion criteria:\n"
        f"{SKIPPED_LINE}{tasks_file} is valid\n"
    )


async def test_excluded_commands_feed_rule_3(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", WRAPPED_BUILD_YAML)
    settings = write_file(
        tmp_path / "sandbox.json",
        '{"sandbox": {"excludedCommands": ["./build/build.sh"]}}',
    )

    got = await _dry_run(tasks_file, sandbox_file=settings)

    assert (
        "warning: task 1: excluded-command-wrapped: ./build/build.sh > build.log\n"
        in got
    )


async def test_no_sandbox_file_leaves_rule_3_off(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", WRAPPED_BUILD_YAML)

    got = await _dry_run(tasks_file)

    assert "excluded-command-wrapped" not in got


@pytest.mark.parametrize(
    "settings_json",
    [
        pytest.param("{}", id="no sandbox key"),
        pytest.param('{"sandbox": {}}', id="no excludedCommands"),
        pytest.param('{"sandbox": {"excludedCommands": "*"}}', id="not a list"),
        pytest.param('{"sandbox": {"excludedCommands": [1, null]}}', id="not strings"),
    ],
)
async def test_a_settings_file_without_excluded_commands_means_none(
    tmp_path: Path, settings_json: str
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", WRAPPED_BUILD_YAML)
    settings = write_file(tmp_path / "sandbox.json", settings_json)

    got = await _dry_run(tasks_file, sandbox_file=settings)

    assert "excluded-command-wrapped" not in got
    assert got.endswith(f"sandbox settings: {settings}\n{tasks_file} is valid\n")


async def test_a_clean_file_prints_no_warning(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)
    settings = write_file(tmp_path / "sandbox.json", "{}")

    got = await _dry_run(tasks_file, sandbox_file=settings)

    assert "warning:" not in got


# Two tasks with no Finish block, so rule 1 warns twice.
TWO_WARNINGS_YAML = NO_FINISH_YAML + (
    "  - id: 2\n    name: Second\n    prompt: |\n"
    "      Do the second thing.\n\n      Completion criteria:\n      - done\n"
)


async def test_strict_with_warnings_refuses(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", TWO_WARNINGS_YAML)
    out = io.StringIO()

    with pytest.raises(RefusedError) as raised:
        await dry_run(out, tasks_file, "", "", strict=True)

    assert raised.value.reason == "task-file-warnings"
    assert raised.value.message == "2 warnings"
    assert out.getvalue().count("warning: task ") == 2
    assert "is valid" not in out.getvalue()


async def test_strict_with_one_warning_says_warning(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", NO_FINISH_YAML)

    with pytest.raises(RefusedError) as raised:
        await _dry_run(tasks_file, strict=True)

    assert raised.value.reason == "task-file-warnings"
    assert raised.value.message == "1 warning"


async def test_strict_prints_the_sandbox_line_before_it_refuses(
    tmp_path: Path,
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", NO_FINISH_YAML)
    out = io.StringIO()

    with pytest.raises(RefusedError):
        await dry_run(out, tasks_file, "", "", strict=True)

    assert out.getvalue().endswith(
        f"warning: task 1: finish-block: Completion criteria:\n{SKIPPED_LINE}"
    )


async def test_strict_clean_file_passes(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    got = await _dry_run(tasks_file, strict=True)

    assert got.endswith(f"{tasks_file} is valid\n")


async def test_warnings_without_strict_still_pass(tmp_path: Path) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", TWO_WARNINGS_YAML)

    got = await _dry_run(tasks_file)

    assert got.endswith(f"{tasks_file} is valid\n")


@pytest.mark.parametrize("strict", [False, True], ids=["lenient", "strict"])
async def test_dry_run_returns_the_warning_count_zero_for_a_clean_file(
    tmp_path: Path, strict: bool
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", VALID_TASKS_YAML)

    count = await dry_run(io.StringIO(), tasks_file, "", "", strict=strict)

    assert count == 0


@pytest.mark.parametrize(
    ("tasks_yaml", "want"),
    [(NO_FINISH_YAML, 1), (TWO_WARNINGS_YAML, 2)],
    ids=["one", "two"],
)
async def test_dry_run_returns_the_warning_count(
    tmp_path: Path, tasks_yaml: str, want: int
) -> None:
    tasks_file = write_file(tmp_path / "tasks.yaml", tasks_yaml)
    out = io.StringIO()

    count = await dry_run(out, tasks_file, "", "")

    # The count is the number of warning lines the dry run printed.
    assert count == want
    assert out.getvalue().count("warning: task ") == want
