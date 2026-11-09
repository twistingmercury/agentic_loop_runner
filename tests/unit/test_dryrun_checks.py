"""The dry-run checks: findings, headings, code spans, and the rules that read the text."""

from alr.plain.dryrun_checks import (
    COMPLETION_CRITERIA,
    FINISH_BLOCK,
    INLINE_VARIABLE,
    NO_CHECK,
    TASK_FILE_MENTIONED,
    Finding,
    check_task_list,
    code_spans,
    headings,
)
from alr.tasks import COMPLETED, PENDING, Action, Shared, Task, TaskList

CLEAN_PROMPT = """Objective:
Do the thing.

Scope and files:
- src/thing.py

Steps:
1. Write the test.
2. Make it pass.

Verification:
- make test

Completion criteria:
- The test passes.

Finish:
Reply in at most 10 lines. The last line is the result line.
"""


def task_list(*prompts: str, state: str = PENDING) -> TaskList:
    """A task list with one task per prompt, numbered from 1, checked by a shared action."""
    tasks = [
        Task(id=number, name=f"Task {number}", prompt=prompt, state=state)
        for number, prompt in enumerate(prompts, start=1)
    ]
    shared = Shared(prompt="shared", after_task=[Action(command="make test")])
    return TaskList(shared=shared, tasks=tasks)


def test_finding_line_format() -> None:
    finding = Finding("task 3", "finish-block", "(no heading)")

    assert finding.line() == "warning: task 3: finish-block: (no heading)"


def test_clean_prompt_has_no_findings() -> None:
    assert check_task_list(task_list(CLEAN_PROMPT), None) == []


def test_finish_block_warns_with_the_last_heading() -> None:
    prompt = CLEAN_PROMPT.split("Finish:")[0]

    findings = check_task_list(task_list(prompt), None)

    assert findings == [Finding("task 1", FINISH_BLOCK, "Completion criteria:")]


def test_finish_block_without_any_heading() -> None:
    findings = check_task_list(task_list("Just do it."), None)

    assert Finding("task 1", FINISH_BLOCK, "(no heading)") in findings


def test_completion_criteria_missing() -> None:
    prompt = CLEAN_PROMPT.replace("Completion criteria:", "Done when:")

    findings = check_task_list(task_list(prompt), None)

    assert findings == [Finding("task 1", COMPLETION_CRITERIA, "missing")]


def test_findings_come_in_rule_order_then_task_order() -> None:
    findings = check_task_list(task_list("one", CLEAN_PROMPT, "three"), None)

    assert findings == [
        Finding("task 1", FINISH_BLOCK, "(no heading)"),
        Finding("task 1", COMPLETION_CRITERIA, "missing"),
        Finding("task 3", FINISH_BLOCK, "(no heading)"),
        Finding("task 3", COMPLETION_CRITERIA, "missing"),
    ]


def test_crlf_prompt_finds_its_headings() -> None:
    crlf_prompt = CLEAN_PROMPT.replace("\n", "\r\n")

    assert check_task_list(task_list(crlf_prompt), None) == []


def test_trailing_blank_lines_keep_finish_last() -> None:
    padded_prompt = CLEAN_PROMPT + "\n\n   \n"

    assert check_task_list(task_list(padded_prompt), None) == []


def test_completed_task_is_skipped() -> None:
    findings = check_task_list(task_list("one", state=COMPLETED), None)

    assert findings == []


def test_headings_start_at_column_zero_and_end_with_a_colon() -> None:
    prompt = "Intro text\nFinish:\n  Indented:\n1. Numbered:\nScope and files:  \r\n"

    assert headings(prompt) == ["Finish:", "Scope and files:"]


def test_code_spans_inline_and_fenced() -> None:
    assert code_spans("run `a b` then `c`") == ["a b", "c"]

    fenced = "Run:\n```\nmake test\n\n./build/build.sh\n```\nDone."

    assert code_spans(fenced) == ["make test", "./build/build.sh"]


def test_code_spans_ignore_a_lone_backtick() -> None:
    assert code_spans("a ` b") == []


def test_code_spans_keep_text_after_an_unterminated_fence() -> None:
    assert code_spans("```\nthen `x` here") == ["x"]


def test_inline_variable_warns() -> None:
    prompt = "Run `UV_CACHE_DIR=.uv-cache uv run pytest` now."

    findings = check_task_list(task_list(CLEAN_PROMPT + prompt), None)

    expected = Finding(
        "task 1", INLINE_VARIABLE, "UV_CACHE_DIR=.uv-cache uv run pytest"
    )
    assert findings == [expected]


def test_inline_variable_silent_on_a_bare_assignment_and_a_plain_command() -> None:
    prompt = "Set `VAR=` and run `uv run pytest`."

    assert check_task_list(task_list(CLEAN_PROMPT + prompt), None) == []


def test_inline_variable_also_checks_the_shared_prompt() -> None:
    tasks = task_list(CLEAN_PROMPT)
    tasks.shared.prompt = "Use `A=1 make test`."

    findings = check_task_list(tasks, None)

    assert findings == [Finding("shared prompt", INLINE_VARIABLE, "A=1 make test")]


def test_task_file_mentioned_in_a_task_prompt() -> None:
    mention = "Edit tasks.yaml when done.\n"
    tasks = task_list(
        CLEAN_PROMPT, CLEAN_PROMPT.replace("Finish:", mention + "\nFinish:")
    )

    findings = check_task_list(tasks, None)

    assert findings == [
        Finding("task 2", TASK_FILE_MENTIONED, "Edit tasks.yaml when done.")
    ]


def test_task_file_mentioned_in_an_after_task_action() -> None:
    tasks = task_list(CLEAN_PROMPT)
    tasks.shared.after_task = [Action(command="git add tasks.yaml")]

    findings = check_task_list(tasks, None)

    assert findings == [
        Finding("after_task", TASK_FILE_MENTIONED, "git add tasks.yaml")
    ]


def test_task_file_mentioned_in_an_after_run_action() -> None:
    tasks = task_list(CLEAN_PROMPT)
    tasks.shared.after_run = [Action(command="git add tasks.yaml\ngit commit")]

    findings = check_task_list(tasks, None)

    assert findings == [Finding("after_run", TASK_FILE_MENTIONED, "git add tasks.yaml")]


def test_task_file_mentioned_in_a_tasks_own_action() -> None:
    tasks = task_list(CLEAN_PROMPT, CLEAN_PROMPT, CLEAN_PROMPT)
    tasks.tasks[2].actions = [Action(command="cat tasks.yaml")]

    findings = check_task_list(tasks, None)

    assert findings == [Finding("task 3", TASK_FILE_MENTIONED, "cat tasks.yaml")]


def test_shared_prompt_may_mention_the_task_file() -> None:
    tasks = task_list(CLEAN_PROMPT)
    tasks.shared.prompt = "Never edit or commit `tasks.yaml`."

    assert check_task_list(tasks, None) == []


def test_no_check_when_after_task_is_absent() -> None:
    tasks = task_list(CLEAN_PROMPT)
    tasks.shared.after_task = None

    findings = check_task_list(tasks, None)

    text = "no actions of its own and no after_task"
    assert findings == [Finding("task 1", NO_CHECK, text)]


def test_no_check_silent_for_an_empty_after_task_list() -> None:
    tasks = task_list(CLEAN_PROMPT)
    tasks.shared.after_task = []

    assert check_task_list(tasks, None) == []


def test_no_check_silent_for_a_task_with_actions() -> None:
    tasks = task_list(CLEAN_PROMPT)
    tasks.shared.after_task = None
    tasks.tasks[0].actions = [Action(command="make test")]

    assert check_task_list(tasks, None) == []


def test_all_completed_still_checks_shared_and_actions() -> None:
    tasks = task_list("Edit tasks.yaml", state=COMPLETED)
    tasks.shared.prompt = "Use `A=1 make test`."
    tasks.shared.after_task = [Action(command="cat tasks.yaml")]
    tasks.tasks[0].actions = [Action(command="cat tasks.yaml")]

    findings = check_task_list(tasks, None)

    assert findings == [
        Finding("shared prompt", INLINE_VARIABLE, "A=1 make test"),
        Finding("after_task", TASK_FILE_MENTIONED, "cat tasks.yaml"),
    ]


def test_findings_come_out_in_a_fixed_order() -> None:
    bad_prompt = "Run `A=1 make test` and edit tasks.yaml."
    tasks = task_list(bad_prompt, CLEAN_PROMPT)
    tasks.shared.prompt = "Use `B=2 make test`."
    tasks.shared.after_task = [Action(command="cat tasks.yaml")]
    tasks.shared.after_run = [Action(command="cat tasks.yaml")]
    tasks.tasks[0].actions = [Action(command="cat tasks.yaml")]

    findings = check_task_list(tasks, None)

    assert findings == [
        Finding("shared prompt", INLINE_VARIABLE, "B=2 make test"),
        Finding("task 1", FINISH_BLOCK, "(no heading)"),
        Finding("task 1", COMPLETION_CRITERIA, "missing"),
        Finding("task 1", INLINE_VARIABLE, "A=1 make test"),
        Finding("task 1", TASK_FILE_MENTIONED, bad_prompt),
        Finding("task 1", TASK_FILE_MENTIONED, "cat tasks.yaml"),
        Finding("after_task", TASK_FILE_MENTIONED, "cat tasks.yaml"),
        Finding("after_run", TASK_FILE_MENTIONED, "cat tasks.yaml"),
    ]


def test_no_check_comes_last_within_a_task() -> None:
    tasks = task_list("one")
    tasks.shared.after_task = None

    findings = check_task_list(tasks, None)

    assert [finding.rule for finding in findings] == [
        FINISH_BLOCK,
        COMPLETION_CRITERIA,
        NO_CHECK,
    ]
