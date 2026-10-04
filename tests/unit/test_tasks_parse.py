import pytest

from alr import tasks
from alr.errors import TaskFileError
from alr.tasks import COMPLETED, FAILED, PENDING, Gate, Task, TaskList

VALID = "tasks:\n  - {id: 1, name: a, prompt: p}\n"

TASK_LEVEL_GATES_ERROR = (
    "tasks[0] (id 3): gates: gates are set once for the whole file now, "
    "as a top-level gates: list; see the HOWTO"
)
DURATION_HINT = "must be a duration string such as 90s or 10m"


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
        pytest.param(
            Task(id=3, name="No prompt", prompt=""),
            "3: No prompt",
            id="empty prompt",
        ),
    ],
)
def test_task_string(task: Task, want: str) -> None:
    assert task.text() == want


def test_parse_tasks_valid() -> None:
    text = """tasks:
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
        ]
    )
    assert got == want, "block-scalar prompts must keep their newlines verbatim"


def test_parse_tasks_preserves_file_order() -> None:
    text = """tasks:
  - {id: 30, name: c, prompt: p, state: pending}
  - {id: 10, name: a, prompt: p, state: completed}
  - {id: 20, name: b, prompt: p, state: failed}
"""

    got = tasks.parse_tasks(text)

    ids = [task.id for task in got.tasks]
    assert ids == [30, 10, 20], "IDs are identity, not order"


def test_parse_tasks_ignores_unknown_fields() -> None:
    text = """tasks:
  - id: 1
    name: Only task
    agent: go software engineer
    checkpoint: ""
    prompt: Do it.
    state: pending
"""

    got = tasks.parse_tasks(text)

    assert got == TaskList(
        tasks=[Task(id=1, name="Only task", prompt="Do it.", state=PENDING)]
    )


def test_parse_tasks_max_id() -> None:
    got = tasks.parse_tasks(
        "tasks:\n  - {id: 32767, name: a, prompt: p, state: pending}\n"
    )
    assert got.tasks[0].id == 32767


def test_state_constants() -> None:
    # These strings are the on-disk YAML contract; renaming one breaks every
    # existing task file.
    assert PENDING == "pending"
    assert COMPLETED == "completed"
    assert FAILED == "failed"


@pytest.mark.parametrize("state", [PENDING, COMPLETED, FAILED])
def test_parse_tasks_accepts_every_valid_state(state: str) -> None:
    got = tasks.parse_tasks(
        "tasks:\n  - {id: 1, name: a, prompt: p, state: " + state + "}\n"
    )
    assert got.tasks[0].state == state


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("tasks:\n  - {id: 1, name: a, prompt: p}\n", id="state omitted"),
        pytest.param(
            'tasks:\n  - {id: 1, name: a, prompt: p, state: ""}\n',
            id="state empty string",
        ),
    ],
)
def test_parse_tasks_empty_state_defaults_to_pending(text: str) -> None:
    # A task file does not have to spell out `state: pending` for new work.
    got = tasks.parse_tasks(text)
    assert got.tasks[0].state == PENDING


def test_parse_tasks_does_not_alter_name_or_prompt() -> None:
    # Trimming is for validation only; the stored values stay as written.
    got = tasks.parse_tasks(
        'tasks:\n  - {id: 1, name: " Build ", prompt: "  do it  ", state: pending}\n'
    )
    assert got.tasks[0].name == " Build "
    assert got.tasks[0].prompt == "  do it  "


ID_RANGE_ERROR = "tasks[0]: id: must be a positive integer no greater than 32767"
STATE_ERROR = "tasks[0] (id 1): state: must be pending, completed, or failed, got "

ERROR_CASES = [
    # File-level errors never name a task index or id.
    (
        "malformed yaml",
        "tasks:\n  - id: 1\n   name: bad indent\n",
        "failed to parse yaml tasks",
    ),
    ("empty input", "", "tasks: is required"),
    ("comment-only input", "# nothing\n", "tasks: is required"),
    (
        "missing tasks key",
        "items:\n  - {id: 1, name: a, prompt: p}\n",
        "tasks: is required",
    ),
    ("root is a sequence", "- {id: 1, name: a, prompt: p}\n", "tasks: is required"),
    ("null tasks", "tasks:\n", "tasks: must be a sequence"),
    ("tilde tasks", "tasks: ~\n", "tasks: must be a sequence"),
    ("scalar tasks", "tasks: nope\n", "tasks: must be a sequence"),
    (
        "mapping tasks",
        "tasks: {id: 1, name: a, prompt: p}\n",
        "tasks: must be a sequence",
    ),
    ("empty tasks sequence", "tasks: []\n", "tasks: must contain at least one task"),
    (
        "duplicate tasks key",
        VALID + "tasks:\n  - {id: 2, name: b, prompt: p}\n",
        "tasks: duplicate key",
    ),
    # Tags outside the core set are rejected anywhere in the document.
    (
        "python object tag",
        "tasks:\n  - {id: 1, name: !!python/object:os.system a, prompt: p}\n",
        "line 2: tag !!python/object:os.system is not allowed",
    ),
    (
        "custom tag on an element",
        VALID + "  - !foo {id: 2, name: b, prompt: p}\n",
        "line 3: tag !foo is not allowed",
    ),
    (
        "custom tag on an unknown key",
        "tasks:\n  - {id: 1, name: a, prompt: p, extra: !foo x}\n",
        "tag !foo is not allowed",
    ),
    ("custom tag outside tasks", VALID + "meta: !foo x\n", "tag !foo is not allowed"),
    (
        "explicit binary tag",
        "tasks:\n  - {id: 1, name: !!binary YQ==, prompt: p}\n",
        "tag !!binary is not allowed",
    ),
    # Every element must be a mapping, even among valid tasks.
    ("bare dash element", VALID + "  -\n", "tasks[1]: must be a mapping"),
    ("null element", VALID + "  - null\n", "tasks[1]: must be a mapping"),
    ("tilde element", VALID + "  - ~\n", "tasks[1]: must be a mapping"),
    ("empty mapping element", VALID + "  - {}\n", "tasks[1]: id: is required"),
    ("empty sequence element", VALID + "  - []\n", "tasks[1]: must be a mapping"),
    ("empty string element", VALID + '  - ""\n', "tasks[1]: must be a mapping"),
    ("scalar element", VALID + "  - just a string\n", "tasks[1]: must be a mapping"),
    (
        "invalid first element before a valid one",
        "tasks:\n  - ~\n  - {id: 1, name: a, prompt: p}\n",
        "tasks[0]: must be a mapping",
    ),
    # id
    ("missing id", "tasks:\n  - {name: a, prompt: p}\n", "tasks[0]: id: is required"),
    ("null id", "tasks:\n  - {id: ~, name: a, prompt: p}\n", ID_RANGE_ERROR),
    (
        "float id",
        "tasks:\n  - {id: 1.5, name: a, prompt: p}\n",
        "tasks[0]: id: must be a positive integer",
    ),
    (
        "quoted id",
        'tasks:\n  - {id: "1", name: a, prompt: p}\n',
        "tasks[0]: id: must be a positive integer",
    ),
    (
        "word id",
        "tasks:\n  - {id: one, name: a, prompt: p}\n",
        "tasks[0]: id: must be a positive integer",
    ),
    (
        "zero id",
        "tasks:\n  - {id: 0, name: a, prompt: p}\n",
        "tasks[0]: id: must be a positive integer",
    ),
    (
        "negative id",
        "tasks:\n  - {id: -3, name: a, prompt: p}\n",
        "tasks[0]: id: must be a positive integer",
    ),
    (
        "id overflows int16",
        "tasks:\n  - {id: 32768, name: a, prompt: p}\n",
        ID_RANGE_ERROR,
    ),
    (
        "duplicate id",
        VALID + "  - {id: 1, name: b, prompt: p}\n",
        "tasks[1] (id 1): id: duplicates tasks[0]",
    ),
    (
        "aliased task repeats its id",
        "tasks:\n  - &t {id: 1, name: a, prompt: p}\n  - *t\n",
        "tasks[1] (id 1): id: duplicates tasks[0]",
    ),
    (
        "duplicate id key within a task",
        "tasks:\n  - {id: 1, id: 2, name: a, prompt: p}\n",
        "tasks[0]: id: duplicate key",
    ),
    # name
    (
        "missing name",
        "tasks:\n  - {id: 1, prompt: p}\n",
        "tasks[0] (id 1): name: is required",
    ),
    (
        "null name",
        "tasks:\n  - {id: 1, name: ~, prompt: p}\n",
        "tasks[0] (id 1): name: must be a string",
    ),
    (
        "integer name",
        "tasks:\n  - {id: 1, name: 42, prompt: p}\n",
        "tasks[0] (id 1): name: must be a string",
    ),
    (
        "sequence name",
        "tasks:\n  - {id: 1, name: [a], prompt: p}\n",
        "tasks[0] (id 1): name: must be a string",
    ),
    (
        "empty name",
        'tasks:\n  - {id: 1, name: "", prompt: p}\n',
        "tasks[0] (id 1): name: must not be empty or whitespace",
    ),
    (
        "whitespace-only name",
        'tasks:\n  - {id: 1, name: " \\t ", prompt: p}\n',
        "tasks[0] (id 1): name: must not be empty or whitespace",
    ),
    (
        "duplicate name",
        VALID + "  - {id: 2, name: a, prompt: p}\n",
        "tasks[1] (id 2): name: duplicates the name of tasks[0]",
    ),
    (
        "duplicate name differing only by case",
        "tasks:\n  - {id: 1, name: Build, prompt: p}\n"
        "  - {id: 2, name: bUILD, prompt: p}\n",
        "tasks[1] (id 2): name: duplicates the name of tasks[0]",
    ),
    (
        "duplicate name differing only by surrounding whitespace",
        "tasks:\n  - {id: 1, name: Build, prompt: p}\n"
        '  - {id: 2, name: "  build\\t", prompt: p}\n',
        "tasks[1] (id 2): name: duplicates the name of tasks[0]",
    ),
    # prompt
    (
        "missing prompt",
        "tasks:\n  - {id: 1, name: a}\n",
        "tasks[0] (id 1): prompt: is required",
    ),
    (
        "null prompt",
        "tasks:\n  - {id: 1, name: a, prompt: ~}\n",
        "tasks[0] (id 1): prompt: must be a string",
    ),
    (
        "mapping prompt",
        "tasks:\n  - {id: 1, name: a, prompt: {x: y}}\n",
        "tasks[0] (id 1): prompt: must be a string",
    ),
    (
        "empty prompt",
        'tasks:\n  - {id: 1, name: a, prompt: ""}\n',
        "tasks[0] (id 1): prompt: must not be empty or whitespace",
    ),
    (
        "whitespace-only prompt",
        'tasks:\n  - {id: 1, name: a, prompt: " \\t\\n "}\n',
        "tasks[0] (id 1): prompt: must not be empty or whitespace",
    ),
    (
        "blank prompt on a later task",
        VALID + '  - {id: 2, name: b, prompt: "  "}\n',
        "tasks[1] (id 2): prompt: must not be empty or whitespace",
    ),
    # state
    (
        "null state",
        "tasks:\n  - {id: 1, name: a, prompt: p, state: ~}\n",
        "tasks[0] (id 1): state: must be a string",
    ),
    (
        "state key with no value",
        "tasks:\n  - id: 1\n    name: a\n    prompt: p\n    state:\n",
        "tasks[0] (id 1): state: must be a string",
    ),
    (
        "whitespace-only state",
        'tasks:\n  - {id: 1, name: a, prompt: p, state: " "}\n',
        STATE_ERROR + '" "',
    ),
    (
        "padded state",
        'tasks:\n  - {id: 1, name: a, prompt: p, state: " pending "}\n',
        'state: must be pending, completed, or failed, got " pending "',
    ),
    (
        "capitalized state",
        "tasks:\n  - {id: 1, name: a, prompt: p, state: Completed}\n",
        STATE_ERROR + '"Completed"',
    ),
    (
        "upper-case state",
        "tasks:\n  - {id: 1, name: a, prompt: p, state: PENDING}\n",
        'got "PENDING"',
    ),
    (
        "abandoned state",
        "tasks:\n  - {id: 1, name: a, prompt: p, state: abandoned}\n",
        STATE_ERROR + '"abandoned"',
    ),
    (
        "state typo",
        "tasks:\n  - {id: 1, name: a, prompt: p, state: complete}\n",
        'got "complete"',
    ),
    (
        "boolean state",
        "tasks:\n  - {id: 1, name: a, prompt: p, state: true}\n",
        "tasks[0] (id 1): state: must be a string",
    ),
    (
        "invalid state on a later task",
        VALID + "  - {id: 2, name: b, prompt: p, state: done}\n",
        'tasks[1] (id 2): state: must be pending, completed, or failed, got "done"',
    ),
    # Elements are checked in file order, and within one element id, name,
    # prompt, state, then error.
    (
        "earlier element wins",
        "tasks:\n  - {id: 1, name: a, prompt: p, state: bogus}\n"
        '  - {id: 2, name: "", prompt: p}\n',
        "tasks[0] (id 1): state:",
    ),
    (
        "id before name",
        'tasks:\n  - {id: 0, name: "", prompt: p}\n',
        "tasks[0]: id:",
    ),
    (
        "name before prompt",
        'tasks:\n  - {id: 1, name: "", prompt: ""}\n',
        "tasks[0] (id 1): name:",
    ),
    (
        "prompt before state",
        'tasks:\n  - {id: 1, name: a, prompt: "", state: bogus}\n',
        "tasks[0] (id 1): prompt:",
    ),
    # Plain values YAML reads as booleans or numbers are not strings; they must be quoted.
    *[
        (
            f"unquoted {value} as a name",
            f"tasks:\n  - id: 1\n    name: {value}\n    prompt: do it\n",
            "tasks[0] (id 1): name: must be a string",
        )
        for value in ("yes", "no", "on", "off", "12:30", "1_000")
    ],
    # A merge key would otherwise be dropped without a word, losing what it merged in.
    (
        "merge key",
        "tasks:\n  - &base {id: 1, name: a, prompt: p}\n"
        "  - {<<: *base, id: 2, name: b}\n",
        "tag !!merge is not allowed",
    ),
    # An explicit int tag can sit on text that is not a number at all.
    (
        "int tag on empty text",
        'tasks:\n  - {id: !!int "", name: a, prompt: p}\n',
        ID_RANGE_ERROR,
    ),
    (
        "int tag on a word",
        "tasks:\n  - {id: !!int abc, name: a, prompt: p}\n",
        ID_RANGE_ERROR,
    ),
    # error
    (
        "non-string error",
        "tasks:\n  - {id: 1, name: a, prompt: p, state: failed, error: [x]}\n",
        "tasks[0] (id 1): error: must be a string",
    ),
]


@pytest.mark.parametrize(
    ("text", "want"),
    [pytest.param(text, want, id=name) for name, text, want in ERROR_CASES],
)
def test_parse_tasks_errors(text: str, want: str) -> None:
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert want in str(exc.value)


@pytest.mark.parametrize(
    "text",
    [
        "tasks:\n  - id: 1\n   name: bad indent\n",
        "tasks:\n  - {id: 1, name: !foo a, prompt: p}\n",
        "tasks: nope\n",
        "tasks: []\n",
    ],
)
def test_parse_tasks_file_errors_name_no_task(text: str) -> None:
    # Syntax, tag, and top-level errors do not invent a task index or id.
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert "tasks[" not in str(exc.value)
    assert "(id" not in str(exc.value)


def test_parse_tasks_accepts_explicit_core_tags() -> None:
    got = tasks.parse_tasks("tasks:\n  - {id: !!int 1, name: !!str 42, prompt: p}\n")
    assert got.tasks[0].name == "42"


def test_parse_tasks_reads_error_field() -> None:
    got = tasks.parse_tasks(
        "tasks:\n  - {id: 1, name: a, prompt: p, state: failed, error: boom}\n"
    )
    assert got.tasks[0].error == "boom"


def test_parse_tasks_reads_gates() -> None:
    text = """gates:
  - cmd: go test ./...
  - cmd: |
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

    want = [Gate(cmd="go test ./..."), Gate(cmd='test -z "$(gofmt -l .)"\n')]
    assert got.gate_list() == want, "cmd text must be stored unaltered, in file order"


def test_parse_tasks_reads_gate_timeout() -> None:
    text = """gates:
  - cmd: go test ./...
    timeout: 10m
  - cmd: echo hi
  - cmd: sleep 1
    timeout: 1h30m
tasks:
  - id: 1
    name: Gated
    prompt: p
"""

    got = tasks.parse_tasks(text)

    want = [
        Gate(cmd="go test ./...", timeout="10m"),
        Gate(cmd="echo hi"),
        Gate(cmd="sleep 1", timeout="1h30m"),
    ]
    assert got.gate_list() == want, "timeout text must be stored as written"


def test_parse_tasks_gates_absent_versus_empty() -> None:
    absent = tasks.parse_tasks(
        "tasks:\n  - id: 1\n    name: First\n    prompt: do it\n"
    )
    assert absent.gates is None, "no gates key means not decided"
    assert absent.gate_list() == []

    empty = tasks.parse_tasks(
        "gates: []\ntasks:\n  - id: 1\n    name: First\n    prompt: do it\n"
    )
    assert empty.gates is not None, "gates: [] means decided: none"
    assert empty.gates == []
    assert empty.gate_list() == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "tasks:\n  - id: 3\n    name: First\n    prompt: do it\n"
            "    gates:\n      - cmd: make test\n",
            id="list",
        ),
        pytest.param(
            "tasks:\n  - id: 3\n    name: First\n    prompt: do it\n    gates: []\n",
            id="empty",
        ),
    ],
)
def test_parse_tasks_task_level_gates_are_an_error(text: str) -> None:
    # An old file's per-task gates must stop the run rather than be dropped:
    # dropping them would run every task with no checks at all.
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert str(exc.value) == TASK_LEVEL_GATES_ERROR


UNKNOWN_KEY = "unknown key; a gate has only cmd and timeout"

GATE_ERROR_CASES = [
    ("null gates", "gates:", "gates: must be a sequence"),
    ("scalar gates", "gates: go test ./...", "gates: must be a sequence"),
    ("mapping gates", "gates: {cmd: go test ./...}", "gates: must be a sequence"),
    ("duplicate gates key", "gates: []\ngates: []", "gates: duplicate key"),
    ("string element", "gates: [go test ./...]", "gates[0]: must be a mapping"),
    ("missing cmd", "gates: [{}]", "gates[0]: cmd: is required"),
    ("timeout without cmd", "gates:\n  - timeout: 5m", "gates[0]: cmd: is required"),
    ("misspelled key", "gates: [{command: a}]", f"gates[0]: command: {UNKNOWN_KEY}"),
    ("extra key", "gates: [{cmd: a, retries: 3}]", f"gates[0]: retries: {UNKNOWN_KEY}"),
    ("duplicate cmd", "gates: [{cmd: a, cmd: b}]", "gates[0]: cmd: duplicate key"),
    ("integer cmd", "gates: [{cmd: 5}]", "gates[0]: cmd: must be a string"),
    ("null cmd", "gates: [{cmd: }]", "gates[0]: cmd: must be a string"),
    (
        "blank cmd",
        'gates: [{cmd: "  "}]',
        "gates[0]: cmd: must not be empty or whitespace",
    ),
    (
        "integer timeout",
        "gates: [{cmd: a, timeout: 30}]",
        f"gates[0]: timeout: {DURATION_HINT}",
    ),
    (
        "null timeout",
        "gates: [{cmd: a, timeout: }]",
        f"gates[0]: timeout: {DURATION_HINT}",
    ),
    (
        "sequence timeout",
        "gates: [{cmd: a, timeout: [10m]}]",
        f"gates[0]: timeout: {DURATION_HINT}",
    ),
    (
        "unparseable timeout",
        "gates: [{cmd: a, timeout: soon}]",
        f"gates[0]: timeout: {DURATION_HINT}",
    ),
    (
        "unitless timeout",
        'gates: [{cmd: a, timeout: "30"}]',
        f"gates[0]: timeout: {DURATION_HINT}",
    ),
    (
        "blank timeout",
        'gates: [{cmd: a, timeout: "  "}]',
        f"gates[0]: timeout: {DURATION_HINT}",
    ),
    (
        "zero timeout",
        "gates: [{cmd: a, timeout: 0s}]",
        "gates[0]: timeout: must be greater than zero",
    ),
    (
        "negative timeout",
        "gates: [{cmd: a, timeout: -5m}]",
        "gates[0]: timeout: must be greater than zero",
    ),
    (
        "duplicate timeout",
        "gates: [{cmd: a, timeout: 1s, timeout: 2s}]",
        "gates[0]: timeout: duplicate key",
    ),
    (
        "second gate invalid",
        "gates: [{cmd: a}, {cmd: ''}]",
        "gates[1]: cmd: must not be empty or whitespace",
    ),
]


@pytest.mark.parametrize(
    ("gates", "want"),
    [pytest.param(gates, want, id=name) for name, gates, want in GATE_ERROR_CASES],
)
def test_parse_tasks_gate_errors(gates: str, want: str) -> None:
    text = gates + "\ntasks:\n  - id: 1\n    name: a\n    prompt: p\n"
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert str(exc.value) == want


def test_parse_tasks_gate_error_reported_before_task_error() -> None:
    # Gates are checked before the tasks so a file with an error in each always
    # reports the same one, whatever order the keys are in.
    text = "tasks:\n  - id: 1\n    prompt: p\ngates: [{}]\n"
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert str(exc.value) == "gates[0]: cmd: is required"


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        pytest.param("10m", 600.0, id="minutes"),
        pytest.param("1h30m", 5400.0, id="compound"),
        pytest.param("200ms", 0.2, id="milliseconds"),
    ],
)
def test_parse_timeout(text: str, seconds: float) -> None:
    assert tasks.parse_timeout(text) == pytest.approx(seconds)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        pytest.param("30", DURATION_HINT, id="unitless"),
        pytest.param(" ", DURATION_HINT, id="blank"),
        pytest.param("", DURATION_HINT, id="empty"),
        pytest.param("soon", DURATION_HINT, id="words"),
        pytest.param("0s", "must be greater than zero", id="zero"),
        pytest.param("-1m", "must be greater than zero", id="negative"),
    ],
)
def test_parse_timeout_errors(text: str, want: str) -> None:
    with pytest.raises(ValueError) as exc:
        tasks.parse_timeout(text)

    assert str(exc.value) == want


@pytest.mark.parametrize("value", ["yes", "no", "on", "off", "12:30", "1_000"])
def test_parse_tasks_reads_quoted_yaml_11_scalars_as_names(value: str) -> None:
    text = f'tasks:\n  - id: 1\n    name: "{value}"\n    prompt: do it\n'
    assert tasks.parse_tasks(text).tasks[0].name == value


@pytest.mark.parametrize(
    ("text", "want"),
    [
        pytest.param("0x1F", 31, id="hex"),
        pytest.param("0b11", 3, id="binary"),
        pytest.param("0_7", 7, id="octal with underscore"),
        pytest.param("1_0", 10, id="underscore"),
        pytest.param("+5", 5, id="plus sign"),
        pytest.param("1:30", 90, id="base 60"),
    ],
)
def test_parse_tasks_reads_ids_as_pyyaml_does(text: str, want: int) -> None:
    got = tasks.parse_tasks("tasks:\n  - {id: " + text + ", name: a, prompt: p}\n")
    assert got.tasks[0].id == want


@pytest.mark.parametrize(
    ("text", "where"),
    [
        pytest.param(
            'tasks:\n  - {id: 1, name: "a\\ud800", prompt: p}\n',
            "tasks[0] (id 1): name",
            id="name",
        ),
        pytest.param(
            'tasks:\n  - {id: 1, name: a, prompt: "\\udfff"}\n',
            "tasks[0] (id 1): prompt",
            id="prompt",
        ),
        pytest.param(
            'gates: [{cmd: "echo \\ud800"}]\ntasks:\n  - {id: 1, name: a, prompt: p}\n',
            "gates[0]: cmd",
            id="gate cmd",
        ),
    ],
)
def test_parse_tasks_rejects_a_lone_surrogate(text: str, where: str) -> None:
    # Text holding one cannot be written out as UTF-8, so it must not get in.
    with pytest.raises(TaskFileError) as exc:
        tasks.parse_tasks(text)

    assert str(exc.value) == f"{where}: must not contain a lone surrogate"
