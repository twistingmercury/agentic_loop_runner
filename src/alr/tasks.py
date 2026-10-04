"""The task file: parse, validate, save."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from alr.durations import parse_duration
from alr.errors import TaskFileError, quote

PENDING = "pending"
FAILED = "failed"
COMPLETED = "completed"

_STATES = (PENDING, COMPLETED, FAILED)
_DURATION_HINT = "must be a duration string such as 90s or 10m"
_ID_ERROR = "id: must be a positive integer no greater than 32767"
_MAX_ID = 32767

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

# YAML reads these as line breaks and folds them in every style but a
# double-quoted one, which escapes them.
_UNICODE_BREAKS = frozenset("\x85\u2028\u2029")


class _TaskDumper(yaml.SafeDumper):
    """Writes the task file with lists indented under their key and multi-line text as blocks."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # PyYAML leaves a list under a key unindented; the task file indents it.
        super().increase_indent(flow, False)

    def analyze_scalar(self, scalar: str) -> yaml.emitter.ScalarAnalysis:
        analysis = super().analyze_scalar(scalar)
        if _UNICODE_BREAKS.intersection(scalar):
            analysis.allow_flow_plain = analysis.allow_block_plain = False
            analysis.allow_single_quoted = analysis.allow_block = False
            return analysis

        # PyYAML counts a tab as a special character that only a double-quoted
        # string may hold, but a block scalar keeps it as it is.
        if "\t" in scalar and not analysis.allow_block:
            analysis.allow_block = (
                super().analyze_scalar(scalar.replace("\t", "x")).allow_block
            )

        return analysis


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar(_STR, value, style=style)


_TaskDumper.add_representer(str, _represent_str)


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
class TaskList:
    """A whole task file.

    `gates` is None when the file has no gates key (not decided yet) and a
    list, possibly empty, when it has one (decided), so a save can keep the
    difference.
    """

    tasks: list[Task]
    gates: list[Gate] | None = None

    def gate_list(self) -> list[Gate]:
        """The file's gates, empty when the key is absent."""
        if self.gates is None:
            return []

        return self.gates


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

    seq, gates_node = _root_nodes(doc)

    # Gates are checked before the tasks so a file with an error in each
    # reports the same one whatever order its keys are in.
    task_list = TaskList(tasks=[], gates=_decode_gates(gates_node))
    ids: dict[int, int] = {}
    names: dict[str, int] = {}
    for i, el in enumerate(seq.value):
        task, where = _parse_task(i, el)
        _check_duplicate(ids, names, i, task, where)
        task_list.tasks.append(task)

    return task_list


def _check_duplicate(
    ids: dict[int, int], names: dict[str, int], i: int, task: Task, where: str
) -> None:
    """Reject a task whose id or name repeats an earlier one, recording both.

    Names compare ignoring case and surrounding whitespace.
    """
    if task.id in ids:
        raise TaskFileError(f"{where}: id: duplicates tasks[{ids[task.id]}]")

    ids[task.id] = i
    name = task.name.lower().strip()
    if name in names:
        raise TaskFileError(
            f"{where}: name: duplicates the name of tasks[{names[name]}]"
        )

    names[name] = i


def _root_nodes(doc: yaml.Node | None) -> tuple[yaml.SequenceNode, yaml.Node | None]:
    """Return the non-empty sequence under the single tasks key and the gates node.

    The gates node is None when the file has no gates key.
    """
    if not isinstance(doc, yaml.MappingNode):
        raise TaskFileError("tasks: is required")

    seq: yaml.Node | None = None
    gates: yaml.Node | None = None
    for key, value in doc.value:
        if key.value == "tasks":
            if seq is not None:
                raise TaskFileError("tasks: duplicate key")

            seq = value

        if key.value == "gates":
            if gates is not None:
                raise TaskFileError("gates: duplicate key")

            gates = value

    if seq is None:
        raise TaskFileError("tasks: is required")

    if not isinstance(seq, yaml.SequenceNode):
        raise TaskFileError("tasks: must be a sequence")

    if not seq.value:
        raise TaskFileError("tasks: must contain at least one task")

    return seq, gates


def _decode_gates(node: yaml.Node | None) -> list[Gate] | None:
    """Return a list, not None, for `gates: []` so a save writes the key back."""
    if node is None:
        return None

    return _check_gates(node)


def _parse_task(i: int, el: yaml.Node) -> tuple[Task, str]:
    """Check element i of the tasks sequence and build its task.

    Also returns the task's location, tasks[<i>] (id <id>), for the caller's
    own errors.
    """
    where = f"tasks[{i}]"
    if not isinstance(el, yaml.MappingNode):
        raise TaskFileError(f"{where}: must be a mapping")

    fields = _task_fields(where, el)
    task_id = _task_id(where, fields)
    where = f"{where} (id {task_id})"
    _check_string_fields(where, fields)

    # An error rather than a quietly dropped key: dropping it would leave an
    # old file running every task with no checks at all.
    if "gates" in fields:
        raise TaskFileError(
            f"{where}: gates: gates are set once for the whole file now, "
            "as a top-level gates: list; see the HOWTO"
        )

    return _decode_task(task_id, fields), where


def _task_fields(where: str, el: yaml.MappingNode) -> dict[str, yaml.Node]:
    """Map each key of the mapping to its value node, rejecting a repeated key."""
    fields: dict[str, yaml.Node] = {}
    for key_node, value in el.value:
        key = _key_text(key_node)
        if key in fields:
            raise TaskFileError(f"{where}: {key}: duplicate key")

        fields[key] = value

    return fields


def _key_text(key_node: yaml.Node) -> str:
    """A key's text; a sequence or mapping used as a key reads as empty, as in Go."""
    if not isinstance(key_node, yaml.ScalarNode):
        return ""

    return key_node.value


def _task_id(where: str, fields: dict[str, yaml.Node]) -> int:
    """Read the id, checking the node's tag so only a YAML integer is accepted."""
    node = fields.get("id")
    if node is None:
        raise TaskFileError(f"{where}: id: is required")

    if not isinstance(node, yaml.ScalarNode) or node.tag != _INT:
        raise TaskFileError(f"{where}: {_ID_ERROR}")

    task_id = _int_value(node)
    if task_id is None or not 0 < task_id <= _MAX_ID:
        raise TaskFileError(f"{where}: {_ID_ERROR}")

    return task_id


def _int_value(node: yaml.ScalarNode) -> int | None:
    """Convert an int-tagged node as PyYAML's safe constructor does; None if it cannot."""
    # An explicit !!int tag can sit on any text, so the conversion can fail.
    try:
        return yaml.SafeLoader("").construct_yaml_int(node)
    except (ValueError, IndexError):
        return None


def _check_string_fields(where: str, fields: dict[str, yaml.Node]) -> None:
    """Check the string fields in a fixed order.

    The first error reported must not depend on key order in the file.
    """
    for field in ("name", "prompt", "state", "error"):
        _check_string_field(where, field, fields.get(field))


def _check_string_field(where: str, field: str, node: yaml.Node | None) -> None:
    required = field in ("name", "prompt")
    if node is None and required:
        raise TaskFileError(f"{where}: {field}: is required")

    if node is None:
        return

    if not _is_string(node):
        raise TaskFileError(f"{where}: {field}: must be a string")

    if required and node.value.strip() == "":
        raise TaskFileError(f"{where}: {field}: must not be empty or whitespace")

    if field == "state" and node.value != "" and node.value not in _STATES:
        raise TaskFileError(
            f"{where}: state: must be pending, completed, or failed, "
            f"got {quote(node.value)}"
        )


def _is_string(node: yaml.Node) -> bool:
    return isinstance(node, yaml.ScalarNode) and node.tag == _STR


def _check_gates(node: yaml.Node) -> list[Gate]:
    """Check the file's gates sequence and build its gates.

    A malformed gate must reject the file: reading only what is recognized
    would drop the rest, and the next save would then erase it.
    """
    if not isinstance(node, yaml.SequenceNode):
        raise TaskFileError("gates: must be a sequence")

    return [_check_gate(f"gates[{j}]", el) for j, el in enumerate(node.value)]


def _check_gate(where: str, el: yaml.Node) -> Gate:
    """Check one gate, allowing only the cmd and timeout keys.

    A misspelled or invented option is an error instead of a gate that quietly
    does less than its author meant.
    """
    if not isinstance(el, yaml.MappingNode):
        raise TaskFileError(f"{where}: must be a mapping")

    fields = _task_fields(where, el)
    for key in fields:
        if key not in ("cmd", "timeout"):
            raise TaskFileError(
                f"{where}: {key}: unknown key; a gate has only cmd and timeout"
            )

    cmd = fields.get("cmd")
    if cmd is None:
        raise TaskFileError(f"{where}: cmd: is required")

    if not _is_string(cmd):
        raise TaskFileError(f"{where}: cmd: must be a string")

    if cmd.value.strip() == "":
        raise TaskFileError(f"{where}: cmd: must not be empty or whitespace")

    return Gate(
        cmd=cmd.value, timeout=_check_gate_timeout(where, fields.get("timeout"))
    )


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
        default_flow_style=False,
        indent=2,
        width=2**31 - 1,
    )
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    try:
        _write_file(tmp, text)
        tmp.replace(target)
    except OSError as exc:
        _remove(tmp)
        raise TaskFileError(
            f"failed to save tasks to {quote(str(target))}: {exc.strerror}"
        ) from exc


def _document(task_list: TaskList) -> dict[str, object]:
    """Lay the task list out as it is written: gates above tasks, empty optional keys left out."""
    doc: dict[str, object] = {}
    if task_list.gates is not None:
        doc["gates"] = [_gate_entry(gate) for gate in task_list.gates]

    doc["tasks"] = [_task_entry(task) for task in task_list.tasks]
    return doc


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


def _remove(path: Path) -> None:
    """Remove a leftover temporary file or empty directory; failure is ignored."""
    for remove in (path.unlink, path.rmdir):
        try:
            remove()
        except OSError:
            continue

        return


def _decode_task(task_id: int, fields: dict[str, yaml.Node]) -> Task:
    """Build the task from its checked fields, stored exactly as written."""
    state = fields.get("state")
    error = fields.get("error")
    return Task(
        id=task_id,
        name=fields["name"].value,
        prompt=fields["prompt"].value,
        state=(state.value if state is not None else "") or PENDING,
        error=error.value if error is not None else "",
    )


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
