import json
import re

import pytest

from alr import tasks
from alr.errors import TaskFileError
from alr.tasks import (
    COMPLETED,
    PENDING,
    RUN_SCOPE,
    Action,
    Shared,
    Task,
    TaskList,
)

_WITH_SHARED = "shared:{}\ntasks:\n  - {{id: 1, name: a, prompt: p}}\n"
VALID = "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p}\n"


@pytest.mark.parametrize(
    ("task", "want"),
    [
        pytest.param(
            Task(id=1, name="First task", prompt="Do it."),
            "1: First task\n\nDo it.",
            id="exact format",
        ),
        pytest.param(
            Task(id=7, name="Second task", prompt="Objective:\nDo the second thing.\n"),
            "7: Second task\n\nObjective:\nDo the second thing.",
            id="trailing newline of a block-scalar prompt is trimmed",
        ),
        pytest.param(
            Task(id=2, name="Indented", prompt="  Step one\nStep two\n"),
            "2: Indented\n\n  Step one\nStep two",
            id="interior indentation of the prompt's first line is preserved",
        ),
    ],
)
def test_task_string(task: Task, want: str) -> None:
    assert task.text() == want


def test_parse_tasks_valid() -> None:
    text = """shared:\n  prompt: shared\ntasks:
  - id: 1
    name: First task
    prompt: |
      Objective:
      Do the first thing.

      Verification:
      - Command: go test ./...
    state: pending
  - id: 7
    name: Second task
    prompt: Do the second thing.
    state: completed
"""

    got = tasks.parse_tasks(text)

    want = TaskList(
        shared=Shared(prompt="shared"),
        tasks=[
            Task(
                id=1,
                name="First task",
                prompt=(
                    "Objective:\nDo the first thing.\n\n"
                    "Verification:\n- Command: go test ./...\n"
                ),
                state=PENDING,
            ),
            Task(
                id=7,
                name="Second task",
                prompt="Do the second thing.",
                state=COMPLETED,
            ),
        ],
    )
    assert got == want, "block-scalar prompts must keep their newlines verbatim"


def test_parse_tasks_preserves_file_order() -> None:
    text = """shared:\n  prompt: shared\ntasks:
  - {id: 30, name: c, prompt: p, state: pending}
  - {id: 10, name: a, prompt: p, state: completed}
  - {id: 20, name: b, prompt: p, state: failed}
"""

    got = tasks.parse_tasks(text)

    ids = [task.id for task in got.tasks]
    assert ids == [30, 10, 20], "IDs are identity, not order"


def test_parse_tasks_ignores_unknown_fields() -> None:
    text = """shared:\n  prompt: shared\ntasks:
  - id: 1
    name: Only task
    agent: go software engineer
    checkpoint: ""
    prompt: Do it.
    state: pending
"""

    got = tasks.parse_tasks(text)

    assert got == TaskList(
        shared=Shared(prompt="shared"),
        tasks=[Task(id=1, name="Only task", prompt="Do it.", state=PENDING)],
    )


def test_parse_tasks_max_id() -> None:
    got = tasks.parse_tasks(
        "shared:\n  prompt: shared\ntasks:\n  - {id: 32767, name: a, prompt: p, state: pending}\n"
    )
    assert got.tasks[0].id == 32767


# Literal strings on purpose: they are the on-disk YAML contract, and renaming
# one breaks every existing task file.
@pytest.mark.parametrize("state", ["pending", "completed", "failed"])
def test_parse_tasks_accepts_every_valid_state(state: str) -> None:
    got = tasks.parse_tasks(
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: "
        + state
        + "}\n"
    )
    assert got.tasks[0].state == state


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p}\n",
            id="state omitted",
        ),
        pytest.param(
            'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: ""}\n',
            id="state empty string",
        ),
    ],
)
def test_parse_tasks_empty_state_defaults_to_pending(text: str) -> None:
    # A task file does not have to spell out `state: pending` for new work.
    got = tasks.parse_tasks(text)
    assert got.tasks[0].state == PENDING


def test_parse_tasks_trims_the_shared_prompt() -> None:
    # The shared prompt is sent to Claude trimmed, so stray blank lines must not reach it.
    got = tasks.parse_tasks(
        "shared:\n  prompt: |\n\n    Be careful.\n\n"
        "tasks:\n  - {id: 1, name: a, prompt: p}\n"
    )
    assert got.shared.prompt == "Be careful."


def test_parse_tasks_does_not_alter_name_or_prompt() -> None:
    # Trimming is for validation only; the stored values stay as written.
    got = tasks.parse_tasks(
        'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: " Build ", prompt: "  do it  ", state: pending}\n'
    )
    assert got.tasks[0].name == " Build "
    assert got.tasks[0].prompt == "  do it  "


ID_RANGE_ERROR = "tasks[0]: id: must be a positive integer no greater than 32767"
STATE_ERROR = "tasks[0] (id 1): state: must be pending, completed, or failed, got "

ERROR_CASES = [
    # File-level errors never name a task index or id.
    (
        "malformed yaml",
        "shared:\n  prompt: shared\ntasks:\n  - id: 1\n   name: bad indent\n",
        "failed to parse yaml tasks",
    ),
    ("empty input", "", "shared: is required"),
    ("missing tasks key", "shared:\n  prompt: shared\n", "tasks: is required"),
    # The top level holds only shared and tasks.
    (
        "missing shared key",
        "tasks:\n  - {id: 1, name: a, prompt: p}\n",
        "shared: is required",
    ),
    (
        "duplicate shared key",
        VALID + "shared:\n  prompt: again\n",
        "shared: duplicate key",
    ),
    (
        "top-level gates",
        VALID + "gates: []\n",
        "gates: gates are set under shared now, as shared.after_task",
    ),
    (
        "unknown top-level key",
        VALID + "items: []\n",
        "items: unknown key; a task file has only shared and tasks",
    ),
    # shared
    ("null shared", _WITH_SHARED.format(""), "shared: must be a mapping"),
    (
        "unknown shared key",
        _WITH_SHARED.format(" {prompt: p, tone: dry}"),
        "shared.tone: unknown key; shared has only prompt, after_task, and after_run",
    ),
    (
        "missing shared prompt",
        _WITH_SHARED.format(" {after_task: []}"),
        "shared.prompt: is required",
    ),
    (
        "null shared prompt",
        _WITH_SHARED.format(" {prompt: ~}"),
        "shared.prompt: must be a string",
    ),
    (
        "empty shared prompt",
        _WITH_SHARED.format(' {prompt: ""}'),
        "shared.prompt: must not be empty or whitespace",
    ),
    (
        "whitespace-only shared prompt",
        _WITH_SHARED.format(' {prompt: " \\t\\n "}'),
        "shared.prompt: must not be empty or whitespace",
    ),
    (
        "shared error reported before task error",
        "tasks:\n  - {id: 1, prompt: p}\nshared: {prompt: ' '}\n",
        "shared.prompt: must not be empty or whitespace",
    ),
    ("null tasks", "shared:\n  prompt: shared\ntasks:\n", "tasks: must be a sequence"),
    (
        "empty tasks sequence",
        "shared:\n  prompt: shared\ntasks: []\n",
        "tasks: must contain at least one task",
    ),
    # Tags outside the core set are rejected anywhere in the document.
    (
        "python object tag",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: !!python/object:os.system a, prompt: p}\n",
        "line 4: tag !!python/object:os.system is not allowed",
    ),
    (
        "custom tag on an element",
        VALID + "  - !foo {id: 2, name: b, prompt: p}\n",
        "line 5: tag !foo is not allowed",
    ),
    # Every element must be a mapping, even among valid tasks.
    ("null element", VALID + "  - null\n", "tasks[1]: must be a mapping"),
    ("empty mapping element", VALID + "  - {}\n", "tasks[1]: id: is required"),
    ("scalar element", VALID + "  - just a string\n", "tasks[1]: must be a mapping"),
    (
        "invalid first element before a valid one",
        "shared:\n  prompt: shared\ntasks:\n  - ~\n  - {id: 1, name: a, prompt: p}\n",
        "tasks[0]: must be a mapping",
    ),
    # id
    (
        "missing id",
        "shared:\n  prompt: shared\ntasks:\n  - {name: a, prompt: p}\n",
        "tasks[0]: id: is required",
    ),
    (
        "quoted id",
        'shared:\n  prompt: shared\ntasks:\n  - {id: "1", name: a, prompt: p}\n',
        "tasks[0]: id: must be a positive integer",
    ),
    (
        "zero id",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 0, name: a, prompt: p}\n",
        "tasks[0]: id: must be a positive integer",
    ),
    (
        "id overflows int16",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 32768, name: a, prompt: p}\n",
        ID_RANGE_ERROR,
    ),
    (
        "duplicate id",
        VALID + "  - {id: 1, name: b, prompt: p}\n",
        "tasks[1] (id 1): id: duplicates tasks[0]",
    ),
    (
        "aliased task repeats its id",
        "shared:\n  prompt: shared\ntasks:\n  - &t {id: 1, name: a, prompt: p}\n  - *t\n",
        "tasks[1] (id 1): id: duplicates tasks[0]",
    ),
    (
        "duplicate id key within a task",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, id: 2, name: a, prompt: p}\n",
        "tasks[0]: id: duplicate key",
    ),
    # name
    (
        "missing name",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, prompt: p}\n",
        "tasks[0] (id 1): name: is required",
    ),
    (
        "null name",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: ~, prompt: p}\n",
        "tasks[0] (id 1): name: must be a string",
    ),
    (
        "empty name",
        'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: "", prompt: p}\n',
        "tasks[0] (id 1): name: must not be empty or whitespace",
    ),
    (
        "whitespace-only name",
        'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: " \\t ", prompt: p}\n',
        "tasks[0] (id 1): name: must not be empty or whitespace",
    ),
    (
        "duplicate name",
        VALID + "  - {id: 2, name: a, prompt: p}\n",
        "tasks[1] (id 2): name: duplicates the name of tasks[0]",
    ),
    (
        "duplicate name differing only by case",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: Build, prompt: p}\n"
        "  - {id: 2, name: bUILD, prompt: p}\n",
        "tasks[1] (id 2): name: duplicates the name of tasks[0]",
    ),
    (
        "duplicate name differing only by surrounding whitespace",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: Build, prompt: p}\n"
        '  - {id: 2, name: "  build\\t", prompt: p}\n',
        "tasks[1] (id 2): name: duplicates the name of tasks[0]",
    ),
    # prompt
    (
        "missing prompt",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a}\n",
        "tasks[0] (id 1): prompt: is required",
    ),
    (
        "null prompt",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: ~}\n",
        "tasks[0] (id 1): prompt: must be a string",
    ),
    (
        "empty prompt",
        'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: ""}\n',
        "tasks[0] (id 1): prompt: must not be empty or whitespace",
    ),
    (
        "whitespace-only prompt",
        'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: " \\t\\n "}\n',
        "tasks[0] (id 1): prompt: must not be empty or whitespace",
    ),
    # state
    (
        "null state",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: ~}\n",
        "tasks[0] (id 1): state: must be a string",
    ),
    (
        "padded state",
        'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: " pending "}\n',
        'state: must be pending, completed, or failed, got " pending "',
    ),
    (
        "capitalized state",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: Completed}\n",
        STATE_ERROR + '"Completed"',
    ),
    (
        "invalid state on a later task",
        VALID + "  - {id: 2, name: b, prompt: p, state: done}\n",
        'tasks[1] (id 2): state: must be pending, completed, or failed, got "done"',
    ),
    # Plain values YAML reads as booleans or numbers are not strings; they must be quoted.
    *[
        (
            f"unquoted {value} as a name",
            f"shared:\n  prompt: shared\ntasks:\n  - id: 1\n    name: {value}\n    prompt: do it\n",
            "tasks[0] (id 1): name: must be a string",
        )
        for value in ("yes", "12:30")
    ],
    # A merge key would otherwise be dropped without a word, losing what it merged in.
    (
        "merge key",
        "shared:\n  prompt: shared\ntasks:\n  - &base {id: 1, name: a, prompt: p}\n"
        "  - {<<: *base, id: 2, name: b}\n",
        "tag !!merge is not allowed",
    ),
    # An explicit int tag can sit on text that is not a number at all.
    (
        "int tag on empty text",
        'shared:\n  prompt: shared\ntasks:\n  - {id: !!int "", name: a, prompt: p}\n',
        ID_RANGE_ERROR,
    ),
    # error
    (
        "non-string error",
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: failed, error: [x]}\n",
        "tasks[0] (id 1): error: must be a string",
    ),
]


# Syntax, tag, and top-level errors do not invent a task index or id.
FILE_LEVEL_ERRORS = {
    "malformed yaml",
    "custom tag on an element",
    "null tasks",
    "empty tasks sequence",
}


@pytest.mark.parametrize(
    ("name", "text", "want"),
    [pytest.param(name, text, want, id=name) for name, text, want in ERROR_CASES],
)
def test_parse_tasks_errors(name: str, text: str, want: str) -> None:
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert want in str(exc.value)
    if name in FILE_LEVEL_ERRORS:
        assert "tasks[" not in str(exc.value)
        assert "(id" not in str(exc.value)


def test_parse_tasks_accepts_explicit_core_tags() -> None:
    got = tasks.parse_tasks(
        "shared:\n  prompt: shared\ntasks:\n  - {id: !!int 1, name: !!str 42, prompt: p}\n"
    )
    assert got.tasks[0].name == "42"


def test_parse_tasks_reads_error_field() -> None:
    got = tasks.parse_tasks(
        "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p, state: failed, error: boom}\n"
    )
    assert got.tasks[0].error == "boom"


def test_parse_tasks_reads_actions() -> None:
    text = """shared:
  prompt: shared
  after_task:
    - action: go test ./...
    - action: |
        test -z "$(gofmt -l .)"
tasks:
  - id: 1
    name: First
    prompt: p
  - id: 2
    name: Second
    prompt: p
"""

    got = tasks.parse_tasks(text)
    assert len(got.tasks) == 2

    want = [
        Action(command="go test ./..."),
        Action(command='test -z "$(gofmt -l .)"\n'),
    ]
    assert got.after_task_list() == want, (
        "action text must be stored unaltered, in file order"
    )


def test_parse_tasks_reads_action_timeout() -> None:
    text = """shared:
  prompt: shared
  after_task:
    - action: go test ./...
      timeout: 10m
    - action: echo hi
    - action: sleep 1
      timeout: 1h30m
tasks:
  - id: 1
    name: First
    prompt: p
"""

    got = tasks.parse_tasks(text)

    want = [
        Action(command="go test ./...", timeout="10m"),
        Action(command="echo hi"),
        Action(command="sleep 1", timeout="1h30m"),
    ]
    assert got.after_task_list() == want, "timeout text must be stored as written"


def test_parse_tasks_actions_absent_versus_empty() -> None:
    absent = tasks.parse_tasks(
        "shared:\n  prompt: shared\ntasks:\n  - id: 1\n    name: First\n    prompt: do it\n"
    )
    assert absent.shared.after_task is None, "no actions key means not decided"
    assert absent.after_task_list() == []

    empty = tasks.parse_tasks(
        "shared:\n  prompt: shared\n  after_task: []\ntasks:\n  - id: 1\n    name: First\n    prompt: do it\n"
    )
    assert empty.shared.after_task is not None, "after_task: [] means decided: none"
    assert empty.shared.after_task == []
    assert empty.after_task_list() == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "shared:\n  prompt: shared\ntasks:\n  - id: 3\n    name: First\n    prompt: do it\n"
            "    gates:\n      - cmd: make test\n",
            id="list",
        ),
        pytest.param(
            "shared:\n  prompt: shared\ntasks:\n  - id: 3\n    name: First\n    prompt: do it\n    gates: []\n",
            id="empty",
        ),
    ],
)
def test_parse_tasks_task_level_gates_are_an_error(text: str) -> None:
    # An old file's per-task gates must stop the run rather than be dropped:
    # dropping them would run every task with no checks at all.
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert "tasks[0] (id 3): gates" in str(exc.value)
    assert "shared.after_task" in str(exc.value)


@pytest.mark.parametrize("key", ["after_task", "after_run"])
@pytest.mark.parametrize("value", ["[{action: make test}]", "[]"])
def test_parse_tasks_task_level_actions_are_an_error(key: str, value: str) -> None:
    # Like a per-task gates: dropping it would silently run the task without
    # the actions its author expected, and the next save would erase the key.
    text = (
        "shared:\n  prompt: shared\ntasks:\n  - id: 3\n    name: First\n"
        f"    prompt: do it\n    {key}: {value}\n"
    )

    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert f"tasks[0] (id 3): {key}" in str(exc.value)
    assert "once for the whole file, under shared" in str(exc.value)


ACTION_ERROR_CASES = [
    ("null after_task", "after_task:", "shared.after_task:"),
    ("string element", "after_task: [go test ./...]", "shared.after_task[0]:"),
    ("missing action", "after_task: [{}]", "shared.after_task[0]: action"),
    (
        "misspelled key",
        "after_task: [{command: a}]",
        "shared.after_task[0]: command",
    ),
    ("null action", "after_task: [{action: }]", "shared.after_task[0]: action"),
    (
        "blank action",
        'after_task: [{action: "  "}]',
        "shared.after_task[0]: action",
    ),
    (
        "integer timeout",
        "after_task: [{action: a, timeout: 30}]",
        "shared.after_task[0]: timeout",
    ),
    (
        "unparseable timeout",
        "after_task: [{action: a, timeout: soon}]",
        "shared.after_task[0]: timeout",
    ),
    (
        "zero timeout",
        "after_task: [{action: a, timeout: 0s}]",
        "shared.after_task[0]: timeout",
    ),
    (
        "second action invalid",
        "after_task: [{action: a}, {action: ''}]",
        "shared.after_task[1]: action",
    ),
]


@pytest.mark.parametrize(
    ("snippet", "want"),
    [
        pytest.param(snippet, want, id=name)
        for name, snippet, want in ACTION_ERROR_CASES
    ],
)
def test_parse_tasks_action_errors(snippet: str, want: str) -> None:
    # `want` is the location and field the user must fix, not the hint after it.
    text = (
        f"shared:\n  prompt: shared\n  {snippet}\n"
        "tasks:\n  - id: 1\n    name: a\n    prompt: p\n"
    )
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert want in str(exc.value)


# The message is a bare problem with no field or value in it (callers add
# those), so the keyword tells the two failures apart.
@pytest.mark.parametrize(
    ("text", "want"),
    [
        pytest.param("30", "duration", id="unitless"),
        pytest.param("0s", "zero", id="zero"),
    ],
)
def test_parse_timeout_errors(text: str, want: str) -> None:
    with pytest.raises(ValueError) as exc:
        tasks.parse_timeout(text)

    assert want in str(exc.value)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        pytest.param("0x1F", 31, id="hex"),
    ],
)
def test_parse_tasks_reads_ids_as_pyyaml_does(text: str, want: int) -> None:
    got = tasks.parse_tasks(
        "shared:\n  prompt: shared\ntasks:\n  - {id: "
        + text
        + ", name: a, prompt: p}\n"
    )
    assert got.tasks[0].id == want


@pytest.mark.parametrize(
    ("text", "where"),
    [
        pytest.param(
            'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: "a\\ud800", prompt: p}\n',
            "tasks[0] (id 1): name",
            id="name",
        ),
        pytest.param(
            'shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: "\\udfff"}\n',
            "tasks[0] (id 1): prompt",
            id="prompt",
        ),
        pytest.param(
            'shared: {prompt: s, after_task: [{action: "echo \\ud800"}]}\ntasks:\n  - {id: 1, name: a, prompt: p}\n',
            "shared.after_task[0]: action",
            id="action cmd",
        ),
        pytest.param(
            'shared: {prompt: "\\ud800"}\ntasks:\n  - {id: 1, name: a, prompt: p}\n',
            "shared.prompt",
            id="shared prompt",
        ),
    ],
)
def test_parse_tasks_rejects_a_lone_surrogate(text: str, where: str) -> None:
    # Text holding one cannot be written out as UTF-8, so it must not get in.
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert where in str(exc.value)


@pytest.mark.parametrize(
    "brk",
    [
        "\n",
        "\r",
        "\r\n",
        "\x85",
        "\u2028",
        "\u2029",
        "\x0b",
        "\x0c",
        "\x1c",
        "\x1d",
        "\x1e",
        "\n\n",
    ],
)
def test_a_task_name_with_a_line_break_is_rejected(brk: str) -> None:
    # json.dumps writes the break as an escape: a raw one inside a quoted YAML
    # scalar is folded to a space and would never reach the check.
    name = json.dumps(f"one{brk}two")
    text = (
        f"shared: {{prompt: s}}\ntasks:\n  - id: 7\n    name: {name}\n    prompt: p\n"
    )

    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert str(exc.value) == "tasks[0] (id 7): name: must be a single line"


@pytest.mark.parametrize(
    "char",
    # The ends of the rule's three ranges: \x00-\x08, \x0a-\x1f, \x7f-\x9f. The
    # line feed that starts the second is a line break, so it is pinned with the
    # others above and answers with the single-line message.
    ["\0", "\x01", "\x07", "\x08", "\x1b", "\x1f", "\x7f", "\x80", "\x9f"],
)
def test_a_task_name_with_a_control_character_is_rejected(char: str) -> None:
    # Control characters would garble the one-line fields of the run log.
    name = json.dumps(f"a{char}b")
    text = (
        f"shared: {{prompt: s}}\ntasks:\n  - id: 7\n    name: {name}\n    prompt: p\n"
    )

    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert str(exc.value) == (
        "tasks[0] (id 7): name: must not contain control characters"
    )


def test_a_task_name_with_a_tab_or_punctuation_loads() -> None:
    name = json.dumps("fix\tthe 'thing': (a/b) #1 \u00e9")
    text = (
        f"shared: {{prompt: s}}\ntasks:\n  - id: 7\n    name: {name}\n    prompt: p\n"
    )

    assert tasks.parse_tasks(text).tasks[0].name == "fix\tthe 'thing': (a/b) #1 \u00e9"


@pytest.mark.parametrize("char", ["\t", " ", "\xa0"])
def test_a_task_name_with_spacing_just_outside_the_control_range_loads(
    char: str,
) -> None:
    # The tab sits between the rejected \x08 and the line feed, the space just
    # above \x1f, and the no-break space just past the C1 block.
    name = json.dumps(f"a{char}b")
    text = (
        f"shared: {{prompt: s}}\ntasks:\n  - id: 7\n    name: {name}\n    prompt: p\n"
    )

    assert tasks.parse_tasks(text).tasks[0].name == f"a{char}b"


@pytest.mark.parametrize("brk", ["\n", "\x85", "\u2028"])
def test_a_task_name_with_a_trailing_line_break_is_rejected(brk: str) -> None:
    # str.splitlines drops a trailing break, so "one\n" alone looks like one line.
    name = json.dumps(f"one{brk}")
    text = (
        f"shared: {{prompt: s}}\ntasks:\n  - id: 7\n    name: {name}\n    prompt: p\n"
    )

    with pytest.raises(TaskFileError, match="name: must be a single line"):
        tasks.parse_tasks(text)


def _file(shared_extra: str) -> str:
    return (
        "shared:\n  prompt: shared\n"
        + shared_extra
        + "tasks:\n  - id: 1\n    name: First\n    prompt: p\n"
    )


def test_after_task_and_after_run_parse() -> None:
    task_list = tasks.parse_tasks(
        _file(
            """  after_task:
    - action: ./build.sh
      timeout: 5m
  after_run:
    - action: git push --dry-run
"""
        )
    )

    assert task_list.shared.after_task == [Action("./build.sh", "5m")]
    assert task_list.shared.after_run == [Action("git push --dry-run")]
    assert task_list.after_task_list() == [Action("./build.sh", "5m")]
    assert task_list.after_run_list() == [Action("git push --dry-run")]


def test_absent_action_lists_are_none_and_read_as_empty() -> None:
    task_list = tasks.parse_tasks(_file(""))

    assert task_list.shared.after_task is None
    assert task_list.shared.after_run is None
    assert task_list.after_task_list() == []
    assert task_list.after_run_list() == []


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        (
            "  gates:\n    - cmd: x\n",
            "shared.gates: renamed to shared.after_task",
        ),
        (
            "  after_task:\n    - cmd: x\n",
            "shared.after_task[0]: cmd: renamed to action",
        ),
        (
            "  after_task:\n    - action: x\n      retry: 3\n",
            "shared.after_task[0]: retry: unknown key; an action has only action and timeout",
        ),
        (
            "  after_run:\n    - action: x\n      timeout: 30\n",
            "shared.after_run[0]: timeout: must be a duration string such as 90s or 10m",
        ),
        (
            "  gates: []\n  after_task: []\n",
            "shared.gates: renamed to shared.after_task",
        ),
    ],
)
def test_old_and_bad_action_keys_are_refused(snippet: str, message: str) -> None:
    with pytest.raises(TaskFileError, match=re.escape(message)):
        tasks.parse_tasks(_file(snippet))


def test_run_scope_is_a_task_with_id_zero() -> None:
    assert RUN_SCOPE == Task(id=0, name="after the run", prompt="")
