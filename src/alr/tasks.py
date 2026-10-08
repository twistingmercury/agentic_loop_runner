"""The task file: parse, validate, save."""

import contextlib
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from alr.durations import parse_duration
from alr.errors import TaskFileError, quote

PENDING = "pending"
FAILED = "failed"
COMPLETED = "completed"
# Only ever logged, never saved: in `run` mode a task whose gates passed is not
# complete until the one commit lands.
PASSED = "passed"


class RunResult(StrEnum):
    """How a run ended, as the run log and the exit code tell it."""

    COMPLETED = "completed"
    FAILED = "failed"
    COMMIT_FAILED = "commit_failed"
    STOPPED = "stopped"


_STATES = (PENDING, COMPLETED, FAILED)
_DURATION_HINT = "must be a duration string such as 90s or 10m"
_MAX_ID = 32767
_ID_ERROR = f"id: must be a positive integer no greater than {_MAX_ID}"

_TAG = "tag:yaml.org,2002:"
_STR = _TAG + "str"
_INT = _TAG + "int"

# Any other tag, explicit or custom, is rejected before anything is read. The
# merge tag is left out on purpose: a `<<` key would otherwise be dropped
# without a word, along with what it merges in.
_ALLOWED_TAGS = frozenset(
    _TAG + name
    for name in ("str", "int", "float", "bool", "null", "map", "seq", "timestamp")
)

# A YAML escape such as "\ud800" produces one, and text holding it cannot be
# written out as UTF-8.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")

# YAML reads these as line breaks and folds them in every style but a
# double-quoted one, which escapes them.
_UNICODE_BREAKS = frozenset("\x85\u2028\u2029")


@dataclass
class Gate:
    """One command alr runs itself after each task's session reports completed.

    It is never sent to the session.
    """

    cmd: str
    timeout: str = ""


@dataclass
class Task:
    id: int
    name: str
    prompt: str
    state: str = PENDING
    error: str = ""

    def text(self) -> str:
        """The task as it is sent to the session."""
        return f"{self.id}: {self.name}\n\n{self.prompt}".strip()


@dataclass
class Shared:
    """What every task in the file shares: the prompt and the gates.

    `gates` is None when the file has no gates key (not decided yet) and a
    list, possibly empty, when it has one (decided), so a save can keep the
    difference.
    """

    prompt: str
    gates: list[Gate] | None = None


@dataclass
class TaskList:
    """A whole task file."""

    shared: Shared
    tasks: list[Task]

    def gate_list(self) -> list[Gate]:
        """The file's gates, empty when the key is absent."""
        if self.shared.gates is None:
            return []

        return self.shared.gates


def parse_tasks(text: str) -> TaskList:
    """Parse and validate a task file.

    Any invalid element rejects the whole file; errors name the task as
    tasks[<index>] (id <id>) and the field.
    """
    try:
        doc = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise TaskFileError(f"failed to parse yaml tasks: {exc}") from exc

    if doc is not None:
        _check_tags(doc, set())

    shared_node, seq = _root_nodes(doc)

    # The shared block is checked before the tasks so a file with an error in
    # each reports the same one whatever order its keys are in.
    shared = _check_shared(shared_node)
    tasks: list[Task] = []
    ids: dict[int, int] = {}
    names: dict[str, int] = {}
    for i, el in enumerate(seq.value):
        task = _parse_task(i, el)
        _check_duplicate(ids, names, i, task)
        tasks.append(task)

    return TaskList(shared=shared, tasks=tasks)


def _where(i: int, task_id: int) -> str:
    return f"tasks[{i}] (id {task_id})"


def _check_duplicate(
    ids: dict[int, int], names: dict[str, int], i: int, task: Task
) -> None:
    """Reject a task whose id or name repeats an earlier one, recording both.

    Names compare ignoring case and surrounding whitespace.
    """
    where = _where(i, task.id)
    if task.id in ids:
        raise TaskFileError(f"{where}: id: duplicates tasks[{ids[task.id]}]")

    ids[task.id] = i
    name = task.name.lower().strip()
    if name in names:
        raise TaskFileError(
            f"{where}: name: duplicates the name of tasks[{names[name]}]"
        )

    names[name] = i


def _root_nodes(doc: yaml.Node | None) -> tuple[yaml.Node, yaml.SequenceNode]:
    """Return the shared node and the non-empty sequence under the tasks key.

    Any other root key is an error: a typo or a top-level gates list left over
    from an older file must not be dropped, or a run would have no checks.
    """
    if not isinstance(doc, yaml.MappingNode):
        raise TaskFileError("shared: is required")

    roots: dict[str, yaml.Node] = {}
    for key_node, value in doc.value:
        key = _key_text(key_node)
        _check_root_key(key, roots)
        roots[key] = value

    shared = roots.get("shared")
    if shared is None:
        raise TaskFileError("shared: is required")

    seq = roots.get("tasks")
    if seq is None:
        raise TaskFileError("tasks: is required")

    if not isinstance(seq, yaml.SequenceNode):
        raise TaskFileError("tasks: must be a sequence")

    if not seq.value:
        raise TaskFileError("tasks: must contain at least one task")

    return shared, seq


def _check_root_key(key: str, seen: dict[str, yaml.Node]) -> None:
    if key == "gates":
        raise TaskFileError(
            "gates: gates are set under shared now, as shared.gates; see the HOWTO"
        )

    if key not in ("shared", "tasks"):
        raise TaskFileError(
            f"{key}: unknown key; a task file has only shared and tasks"
        )

    if key in seen:
        raise TaskFileError(f"{key}: duplicate key")


def _check_shared(node: yaml.Node) -> Shared:
    """Check the shared mapping and build it, with the prompt trimmed."""
    if not isinstance(node, yaml.MappingNode):
        raise TaskFileError("shared: must be a mapping")

    fields = _fields("shared", node)
    for key in fields:
        if key not in ("prompt", "gates"):
            raise TaskFileError(
                f"shared.{key}: unknown key; shared has only prompt and gates"
            )

    prompt = _text_node("shared.prompt", fields.get("prompt"), required=True)

    # `gates: []` stays an empty list, not None, so a save writes the key back.
    gates_node = fields.get("gates")
    gates = None if gates_node is None else _check_gates(gates_node)
    return Shared(prompt=prompt.strip(), gates=gates)


def _parse_task(i: int, el: yaml.Node) -> Task:
    """Check element i of the tasks sequence and build its task.

    The fields are checked in a fixed order, so the first error reported does
    not depend on key order in the file.
    """
    if not isinstance(el, yaml.MappingNode):
        raise TaskFileError(f"tasks[{i}]: must be a mapping")

    fields = _fields(f"tasks[{i}]", el)
    task_id = _task_id(i, fields)
    where = _where(i, task_id)
    name = _text(where, fields, "name", required=True)

    # The name becomes a commit subject and a one-line log field; splitlines
    # knows every Unicode line break, and drops a trailing one, hence the compare.
    if name.splitlines() != [name]:
        raise TaskFileError(f"{where}: name: must be a single line")

    prompt = _text(where, fields, "prompt", required=True)
    state = _text(where, fields, "state")
    if state != "" and state not in _STATES:
        raise TaskFileError(
            f"{where}: state: must be pending, completed, or failed, got {quote(state)}"
        )

    error = _text(where, fields, "error")

    # An error rather than a quietly dropped key: dropping it would leave an
    # old file running every task with no checks at all.
    if "gates" in fields:
        raise TaskFileError(
            f"{where}: gates: gates are set once for the whole file, "
            "as shared.gates; see the HOWTO"
        )

    return Task(
        id=task_id, name=name, prompt=prompt, state=state or PENDING, error=error
    )


def _fields(where: str, el: yaml.MappingNode) -> dict[str, yaml.Node]:
    """Map each key of the mapping to its value node, rejecting a repeated key."""
    fields: dict[str, yaml.Node] = {}
    for key_node, value in el.value:
        key = _key_text(key_node)
        if key in fields:
            raise TaskFileError(f"{where}: {key}: duplicate key")

        fields[key] = value

    return fields


def _key_text(key_node: yaml.Node) -> str:
    """A key's text; a sequence or mapping used as a key reads as empty."""
    if not isinstance(key_node, yaml.ScalarNode):
        return ""

    return key_node.value


def _task_id(i: int, fields: dict[str, yaml.Node]) -> int:
    """Read the id; only a YAML integer from 1 to 32767 is accepted."""
    node = fields.get("id")
    if node is None:
        raise TaskFileError(f"tasks[{i}]: id: is required")

    task_id = _int_value(node)
    if task_id is None or not 0 < task_id <= _MAX_ID:
        raise TaskFileError(f"tasks[{i}]: {_ID_ERROR}")

    return task_id


def _int_value(node: yaml.Node) -> int | None:
    """Convert an int-tagged scalar as PyYAML's safe constructor does.

    None for any other node, and for text an explicit !!int tag cannot read.
    """
    if not isinstance(node, yaml.ScalarNode) or node.tag != _INT:
        return None

    try:
        return yaml.SafeLoader("").construct_yaml_int(node)
    except (ValueError, IndexError):
        return None


def _text(
    where: str, fields: dict[str, yaml.Node], name: str, *, required: bool = False
) -> str:
    """Read a string field; "" when the key is absent and not required."""
    return _text_node(f"{where}: {name}", fields.get(name), required=required)


def _text_node(label: str, node: yaml.Node | None, *, required: bool = False) -> str:
    """Read a string node, naming it by label in errors; "" when it is absent and not required."""
    if node is None and required:
        raise TaskFileError(f"{label}: is required")

    if node is None:
        return ""

    if not _is_string(node):
        raise TaskFileError(f"{label}: must be a string")

    if required and node.value.strip() == "":
        raise TaskFileError(f"{label}: must not be empty or whitespace")

    if _LONE_SURROGATE.search(node.value):
        raise TaskFileError(f"{label}: must not contain a lone surrogate")

    return node.value


def _is_string(node: yaml.Node) -> bool:
    return isinstance(node, yaml.ScalarNode) and node.tag == _STR


def _check_gates(node: yaml.Node) -> list[Gate]:
    """Check the file's gates sequence and build its gates.

    A malformed gate must reject the file: reading only what is recognized
    would drop the rest, and the next save would then erase it.
    """
    if not isinstance(node, yaml.SequenceNode):
        raise TaskFileError("shared.gates: must be a sequence")

    return [_check_gate(f"shared.gates[{j}]", el) for j, el in enumerate(node.value)]


def _check_gate(where: str, el: yaml.Node) -> Gate:
    """Check one gate, allowing only the cmd and timeout keys.

    A misspelled or invented option is an error instead of a gate that quietly
    does less than its author meant.
    """
    if not isinstance(el, yaml.MappingNode):
        raise TaskFileError(f"{where}: must be a mapping")

    fields = _fields(where, el)
    for key in fields:
        if key not in ("cmd", "timeout"):
            raise TaskFileError(
                f"{where}: {key}: unknown key; a gate has only cmd and timeout"
            )

    cmd = _text(where, fields, "cmd", required=True)
    return Gate(cmd=cmd, timeout=_check_gate_timeout(where, fields.get("timeout")))


def _check_gate_timeout(where: str, node: yaml.Node | None) -> str:
    """Check a gate's timeout and return its text as written.

    The tag is checked as well as the text because `timeout: 30` is a YAML
    integer, and a bare number has no unit to read. The run parses the text
    again when the gate runs.
    """
    if node is None:
        return ""

    if not _is_string(node):
        raise TaskFileError(f"{where}: timeout: {_DURATION_HINT}")

    try:
        parse_timeout(node.value)
    except ValueError as exc:
        raise TaskFileError(f"{where}: timeout: {exc}") from exc

    return node.value


def parse_timeout(text: str) -> float:
    """Parse a gate timeout into seconds.

    The task file and the --gate-timeout flag both use this, so one set of
    rules applies. The error text is the bare problem; callers prefix where
    the value came from.
    """
    try:
        seconds = parse_duration(text)
    except ValueError as exc:
        raise ValueError(_DURATION_HINT) from exc

    if seconds <= 0:
        raise ValueError("must be greater than zero")

    return seconds


def _check_tags(node: yaml.Node, seen: set[int]) -> None:
    """Reject any node carrying a tag outside the allowed set."""
    # An alias is the same node again, and an anchor can point back into itself.
    if id(node) in seen:
        return

    seen.add(id(node))
    if node.tag not in _ALLOWED_TAGS:
        raise TaskFileError(
            f"line {node.start_mark.line + 1}: tag {_short_tag(node.tag)} is not allowed"
        )

    for child in _children(node):
        _check_tags(child, seen)


def _short_tag(tag: str) -> str:
    if tag.startswith(_TAG):
        return "!!" + tag.removeprefix(_TAG)

    return tag


def _children(node: yaml.Node) -> list[yaml.Node]:
    if isinstance(node, yaml.SequenceNode):
        return node.value

    if isinstance(node, yaml.MappingNode):
        return [child for pair in node.value for child in pair]

    return []


def save_tasks(path: str, task_list: TaskList) -> None:
    """Write the task file as YAML.

    The text goes to a temporary file that is renamed over path, so a failed
    save leaves the original file untouched.
    """
    text = yaml.dump(
        _document(task_list),
        Dumper=_TaskDumper,
        sort_keys=False,
        allow_unicode=True,
        width=float("inf"),
    )
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    try:
        _write_file(tmp, text)
        tmp.replace(target)
    except OSError as exc:
        # Only the temporary file is alr's to remove.
        with contextlib.suppress(OSError):
            tmp.unlink()

        raise TaskFileError(
            f"failed to save tasks to {quote(str(target))}: {exc.strerror}"
        ) from exc


class _TaskDumper(yaml.SafeDumper):
    """Writes the task file with lists indented under their key and multi-line text as blocks."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # PyYAML leaves a list under a key unindented; the task file indents it.
        super().increase_indent(flow, False)

    def analyze_scalar(self, scalar: str) -> yaml.emitter.ScalarAnalysis:
        analysis = super().analyze_scalar(scalar)
        # PyYAML counts a tab as a special character that only a double-quoted
        # string may hold, but a block scalar keeps it as it is.
        if "\t" in scalar and not analysis.allow_block:
            analysis.allow_block = (
                super().analyze_scalar(scalar.replace("\t", "x")).allow_block
            )

        return analysis


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = None
    if _UNICODE_BREAKS.intersection(value):
        style = '"'
    elif "\n" in value:
        style = "|"

    return dumper.represent_scalar(_STR, value, style=style)


_TaskDumper.add_representer(str, _represent_str)


def _document(task_list: TaskList) -> dict[str, object]:
    """Lay the task list out as it is written: shared above tasks, empty optional keys left out."""
    shared: dict[str, object] = {"prompt": task_list.shared.prompt}
    if task_list.shared.gates is not None:
        shared["gates"] = [_gate_entry(gate) for gate in task_list.shared.gates]

    return {
        "shared": shared,
        "tasks": [_task_entry(task) for task in task_list.tasks],
    }


def _gate_entry(gate: Gate) -> dict[str, str]:
    entry = {"cmd": gate.cmd}
    if gate.timeout:
        entry["timeout"] = gate.timeout

    return entry


def _task_entry(task: Task) -> dict[str, int | str]:
    entry: dict[str, int | str] = {
        "id": task.id,
        "name": task.name,
        "prompt": task.prompt,
        "state": task.state,
    }
    if task.error:
        entry["error"] = task.error

    return entry


def _write_file(path: Path, text: str) -> None:
    # The task file can hold prompts and commands, so only its owner may read it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)
