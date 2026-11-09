"""The dry run's second pass: deterministic warnings about what prompts and commands say."""

import re
from dataclasses import dataclass

from alr.tasks import COMPLETED, Action, Task, TaskList

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
NO_CHECK_TEXT = "no actions of its own and no after_task"

# A heading starts at column 0 with a letter and ends with a colon.
_HEADING = re.compile(r"[A-Za-z].*:")

# A fenced block runs from a line of three backticks to the next such line. An
# unterminated fence matches nothing, so it cannot swallow the text after it.
_FENCED_BLOCK = re.compile(
    r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.MULTILINE | re.DOTALL
)

# Single backticks around text on one line; a lone backtick matches nothing.
_INLINE_SPAN = re.compile(r"`([^`\n]+)`")

# NAME=value followed by a command, the shape of an inline variable.
_INLINE_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*\s+\S")

TASK_FILE_NAME = "tasks.yaml"


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
    is no sandbox file, which skips the rule that needs it. For now rules 1, 2,
    4, 5, and 6 run.

    The order is fixed so one file always prints the same lines: the shared
    prompt, then each task in file order, then the after_task actions, then the
    after_run actions.
    """
    findings: list[Finding] = []
    findings.extend(_check_inline_variables("shared prompt", task_list.shared.prompt))

    for task in task_list.tasks:
        if task.state == COMPLETED:
            continue

        findings.extend(_check_task(task_list, task))

    after_task = task_list.after_task_list()
    findings.extend(_check_task_file_in_actions("after_task", after_task))

    after_run = task_list.after_run_list()
    findings.extend(_check_task_file_in_actions("after_run", after_run))

    return findings


def code_spans(text: str) -> list[str]:
    """The code spans of a text: each non-blank line of a fenced block, then each inline span."""
    spans: list[str] = []
    for block in _FENCED_BLOCK.finditer(text):
        for raw_line in block.group(1).split("\n"):
            line = raw_line.strip()
            if line:
                spans.append(line)

    # Fenced blocks come out first, so their backticks are not read again as inline spans.
    prose = _FENCED_BLOCK.sub("\n", text)
    for inline in _INLINE_SPAN.finditer(prose):
        span = inline.group(1).strip()
        if span:
            spans.append(span)

    return spans


def _first_line(text: str) -> str:
    """The text cut to its first line, without surrounding spaces."""
    return text.split("\n")[0].strip()


def _check_task(task_list: TaskList, task: Task) -> list[Finding]:
    """Every task rule, in rule order: 1, 2, 4, 5, 6."""
    where = f"task {task.id}"
    findings = _check_headings(task)
    findings.extend(_check_inline_variables(where, task.prompt))
    findings.extend(_check_task_file_in_prompt(where, task.prompt))
    findings.extend(_check_task_file_in_actions(where, task.actions))
    findings.extend(_check_no_check(task_list, task))

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


def _check_inline_variables(where: str, prompt: str) -> list[Finding]:
    """Rule 4: a code span shaped NAME=value command, which only works in a shell."""
    findings: list[Finding] = []
    for span in code_spans(prompt):
        if _INLINE_VARIABLE.match(span):
            findings.append(Finding(where, INLINE_VARIABLE, _first_line(span)))

    return findings


def _check_task_file_in_prompt(where: str, prompt: str) -> list[Finding]:
    """Rule 5 for a task prompt: it names the task file."""
    if TASK_FILE_NAME not in prompt:
        return []

    # A prompt's first line is just its first heading, so quote the line that names the file.
    for line in prompt.split("\n"):
        if TASK_FILE_NAME in line:
            return [Finding(where, TASK_FILE_MENTIONED, line.strip())]

    return []


def _check_task_file_in_actions(where: str, actions: list[Action]) -> list[Finding]:
    """Rule 5 for action commands: a command names the task file instead of $ALR_TASK_FILE."""
    findings: list[Finding] = []
    for action in actions:
        if TASK_FILE_NAME in action.command:
            text = _first_line(action.command)
            findings.append(Finding(where, TASK_FILE_MENTIONED, text))

    return findings


def _check_no_check(task_list: TaskList, task: Task) -> list[Finding]:
    """Rule 6: nothing checks the task. An explicit empty after_task is a decision."""
    if task.actions:
        return []

    if task_list.shared.after_task is not None:
        return []

    return [Finding(f"task {task.id}", NO_CHECK, NO_CHECK_TEXT)]
