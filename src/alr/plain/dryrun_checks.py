"""The dry run's second pass: deterministic warnings about what prompts and commands say."""

import re
from dataclasses import dataclass

from alr.tasks import COMPLETED, Task, TaskList

FINISH_BLOCK = "finish-block"
COMPLETION_CRITERIA = "completion-criteria"
EXCLUDED_COMMAND_WRAPPED = "excluded-command-wrapped"
INLINE_VARIABLE = "inline-variable"
TASK_FILE_MENTIONED = "task-file-mentioned"
NO_CHECK = "no-check"

FINISH_HEADING = "Finish:"
COMPLETION_CRITERIA_HEADING = "Completion criteria:"
NO_HEADING = "(no heading)"
MISSING = "missing"

# A heading starts at column 0 with a letter and ends with a colon.
_HEADING = re.compile(r"[A-Za-z].*:")


@dataclass(frozen=True)
class Finding:
    """One problem a rule found: where it is, which rule, and the text to show."""

    where: str
    rule: str
    text: str

    def line(self) -> str:
        """The warning line the dry run prints."""
        return f"warning: {self.where}: {self.rule}: {self.text}"


def headings(prompt: str) -> list[str]:
    """The heading lines of a prompt, in order, without line-ending or trailing spaces."""
    found: list[str] = []
    for raw_line in prompt.split("\n"):
        # A prompt written with CRLF line endings still has to match.
        heading = raw_line.rstrip("\r ")
        if _HEADING.fullmatch(heading):
            found.append(heading)

    return found


def check_task_list(
    task_list: TaskList, excluded_commands: list[str] | None
) -> list[Finding]:
    """Run the rules over a task list, in one fixed order.

    excluded_commands is the sandbox file's excludedCommands, or None when there
    is no sandbox file, which skips the rule that needs it. For now only the
    Finish and Completion criteria rules run.
    """
    findings: list[Finding] = []
    for task in task_list.tasks:
        if task.state == COMPLETED:
            continue

        findings.extend(_check_headings(task))

    return findings


def _check_headings(task: Task) -> list[Finding]:
    """Rules 1 and 2: the prompt ends with a Finish block and has Completion criteria."""
    where = f"task {task.id}"
    found = headings(task.prompt)
    findings: list[Finding] = []

    last_heading = found[-1] if found else NO_HEADING
    if last_heading != FINISH_HEADING:
        findings.append(Finding(where, FINISH_BLOCK, last_heading))

    if COMPLETION_CRITERIA_HEADING not in found:
        findings.append(Finding(where, COMPLETION_CRITERIA, MISSING))

    return findings
