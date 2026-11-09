"""The dry-run checks: findings, headings, and the Finish and Completion criteria rules."""

from alr.plain.dryrun_checks import (
    COMPLETION_CRITERIA,
    FINISH_BLOCK,
    Finding,
    check_task_list,
    headings,
)
from alr.tasks import COMPLETED, PENDING, Shared, Task, TaskList

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
    """A task list with one task per prompt, numbered from 1."""
    tasks = [
        Task(id=number, name=f"Task {number}", prompt=prompt, state=state)
        for number, prompt in enumerate(prompts, start=1)
    ]
    return TaskList(shared=Shared(prompt="shared"), tasks=tasks)


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
