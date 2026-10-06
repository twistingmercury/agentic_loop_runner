import errno
import os
import random
from pathlib import Path

import pytest

from alr import tasks
from alr.errors import TaskFileError, quote
from alr.tasks import COMPLETED, FAILED, PENDING, Gate, Shared, Task, TaskList

SHARED = Shared(prompt="shared")
PROMPT = 'Line one: with "quotes" and a colon\n\n  indented line\nhéllo — done\n'


def _saved(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("want", "existing"),
    [
        pytest.param(
            TaskList(
                shared=SHARED,
                tasks=[
                    Task(
                        id=1,
                        name="First",
                        prompt="p",
                        state=FAILED,
                        error="exit status 1",
                    )
                ],
            ),
            None,
            id="error text",
        ),
        pytest.param(
            TaskList(
                shared=SHARED,
                tasks=[
                    Task(
                        id=3,
                        name="Third",
                        prompt="Do the third thing.\nAcross\nmultiple lines.\n",
                        state=FAILED,
                    ),
                    Task(id=1, name="First", prompt="Do the first thing."),
                    Task(id=2, name="Second", prompt="Do the second.", state=COMPLETED),
                ],
            ),
            None,
            id="ids, multi-line prompts, states, and file order",
        ),
        pytest.param(
            TaskList(
                shared=SHARED,
                tasks=[Task(id=1, name="Only", prompt="p", state=COMPLETED)],
            ),
            "stale contents",
            id="over an existing file",
        ),
        pytest.param(
            TaskList(
                shared=Shared(
                    prompt="shared",
                    gates=[
                        Gate(cmd="go test ./..."),
                        Gate(cmd='echo one\necho "two"\n'),
                    ],
                ),
                tasks=[Task(id=1, name="First", prompt="p")],
            ),
            None,
            id="gates",
        ),
        pytest.param(
            TaskList(
                shared=Shared(prompt='Line one.\n\nLine two: with "quotes"'),
                tasks=[Task(id=1, name="First", prompt="p")],
            ),
            None,
            id="multi-line shared prompt",
        ),
    ],
)
def test_save_tasks_round_trip(
    tmp_path: Path, want: TaskList, existing: str | None
) -> None:
    path = tmp_path / "tasks.yaml"
    if existing is not None:
        path.write_text(existing, encoding="utf-8")

    tasks.save_tasks(str(path), want)

    assert not Path(f"{path}.tmp").exists()
    assert tasks.parse_tasks(_saved(path)) == want


def test_save_tasks_path_is_directory(tmp_path: Path) -> None:
    target = tmp_path / "tasks.yaml"
    target.mkdir(mode=0o700)

    with pytest.raises(TaskFileError) as excinfo:
        tasks.save_tasks(
            str(target),
            TaskList(shared=SHARED, tasks=[Task(id=1, name="a", prompt="p")]),
        )

    cause = excinfo.value.__cause__
    assert isinstance(cause, OSError)
    assert str(excinfo.value) == (
        f"failed to save tasks to {quote(str(target))}: {cause.strerror}"
    )
    assert target.is_dir()
    assert not Path(f"{target}.tmp").exists()


def test_save_tasks_target_dir_not_writable(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("running as root: directory permissions are not enforced")

    ro_dir = tmp_path / "ro"
    ro_dir.mkdir(mode=0o500)
    path = ro_dir / "tasks.yaml"

    try:
        with pytest.raises(TaskFileError) as excinfo:
            tasks.save_tasks(
                str(path),
                TaskList(shared=SHARED, tasks=[Task(id=1, name="a", prompt="p")]),
            )
    finally:
        ro_dir.chmod(0o700)

    assert str(excinfo.value) == (
        f"failed to save tasks to {quote(str(path))}: {os.strerror(errno.EACCES)}"
    )
    assert not Path(f"{path}.tmp").exists()


def test_save_tasks_failed_save_leaves_existing_file_untouched(tmp_path: Path) -> None:
    # The file itself stays writable; only the temporary file's name is taken,
    # by a directory. A save that wrote to the target directly would succeed
    # and replace the contents, so this passes only when the write goes to a
    # sibling file first. A read-only directory would not do: the release
    # build runs the tests as root, which ignores directory permissions.
    path = tmp_path / "tasks.yaml"
    original = TaskList(shared=SHARED, tasks=[Task(id=1, name="Original", prompt="p")])
    tasks.save_tasks(str(path), original)
    before = path.read_bytes()
    Path(f"{path}.tmp").mkdir(mode=0o700)

    changed = TaskList(
        shared=SHARED,
        tasks=[Task(id=1, name="Changed", prompt="p", state=FAILED, error="boom")],
    )
    with pytest.raises(TaskFileError) as excinfo:
        tasks.save_tasks(str(path), changed)

    assert str(excinfo.value) == (
        f"failed to save tasks to {quote(str(path))}: {os.strerror(errno.EISDIR)}"
    )
    assert path.read_bytes() == before


def test_save_tasks_writes_two_space_indent(tmp_path: Path) -> None:
    # People edit the task file by hand between runs, so its layout is part of
    # what a save promises; a round-trip through parse_tasks cannot see
    # indentation.
    path = tmp_path / "tasks.yaml"
    task_list = TaskList(
        shared=Shared(
            prompt="Be careful.",
            gates=[Gate(cmd="go test ./...", timeout="90s"), Gate(cmd="echo one")],
        ),
        tasks=[
            Task(id=1, name="First", prompt="p", state=PENDING),
            Task(id=2, name="Second", prompt="q", state=FAILED, error="boom"),
        ],
    )

    tasks.save_tasks(str(path), task_list)

    saved = _saved(path)
    assert tasks.parse_tasks(saved) == task_list
    assert saved.startswith(
        "shared:\n  prompt: Be careful.\n  gates:\n"
        "    - cmd: go test ./...\n      timeout: 90s\n"
    )
    assert "\n    - cmd: echo one\ntasks:\n  - id: 1\n    name: First\n" in saved
    assert saved.endswith(
        "\n  - id: 2\n    name: Second\n    prompt: q\n    state: failed\n    error: boom\n"
    )


@pytest.mark.parametrize(
    ("text", "present", "absent"),
    [
        pytest.param(
            "shared:\n  prompt: shared\ntasks:\n  - id: 1\n    name: First\n    prompt: do it\n    state: pending\n",
            "tasks:",
            "gates",
            id="absent",
        ),
        pytest.param(
            "shared:\n  prompt: shared\n  gates: []\ntasks:\n  - id: 1\n    name: First\n    prompt: do it\n    state: pending\n",
            "  gates: []\n",
            "error:",
            id="empty",
        ),
        pytest.param(
            "shared:\n  prompt: shared\n  gates:\n    - cmd: make test\n      timeout: 10m\ntasks:\n  - id: 1\n    name: First\n    prompt: do it\n    state: pending\n",
            "  gates:\n    - cmd: make test\n      timeout: 10m\n",
            "gates: []",
            id="set",
        ),
    ],
)
def test_save_tasks_keeps_gates_absent_empty_or_set(
    tmp_path: Path, text: str, present: str, absent: str
) -> None:
    path = tmp_path / "tasks.yaml"
    task_list = tasks.parse_tasks(text)

    tasks.save_tasks(str(path), task_list)

    saved = _saved(path)
    assert tasks.parse_tasks(saved) == task_list
    assert present in saved
    assert absent not in saved


@pytest.mark.parametrize(
    "prompt",
    [
        pytest.param(PROMPT, id="plain"),
        pytest.param("a\tb\nc\n", id="tab"),
        pytest.param("\tleading tab\nsecond line\n", id="leading tab"),
    ],
)
def test_save_tasks_writes_multi_line_prompt_as_block(
    tmp_path: Path, prompt: str
) -> None:
    path = tmp_path / "tasks.yaml"
    tasks.save_tasks(
        str(path), TaskList(shared=SHARED, tasks=[Task(id=1, name="a", prompt=prompt)])
    )

    saved = _saved(path)
    assert "prompt: |" in saved
    assert "\\n" not in saved
    assert tasks.parse_tasks(saved).tasks[0].prompt == prompt


def test_save_tasks_writes_multi_line_shared_prompt_as_block(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    shared = Shared(prompt="one\ntwo")

    tasks.save_tasks(
        str(path),
        TaskList(shared=shared, tasks=[Task(id=1, name="a", prompt="p")]),
    )

    assert _saved(path).startswith("shared:\n  prompt: |-\n    one\n    two\ntasks:")


@pytest.mark.parametrize(
    "name",
    ["yes", "true", "~", "0o17", "12:30", "null", "1e3", "- dash", "a: b", "#hash"],
)
def test_save_tasks_keeps_awkward_names_as_strings(tmp_path: Path, name: str) -> None:
    path = tmp_path / "tasks.yaml"
    tasks.save_tasks(
        str(path), TaskList(shared=SHARED, tasks=[Task(id=1, name=name, prompt="p")])
    )

    assert tasks.parse_tasks(_saved(path)).tasks[0].name == name


def _round_trips(tmp_path: Path, name: str, prompt: str) -> tuple[str, str]:
    path = tmp_path / "tasks.yaml"
    tasks.save_tasks(
        str(path), TaskList(shared=SHARED, tasks=[Task(id=1, name=name, prompt=prompt)])
    )
    got = tasks.parse_tasks(_saved(path)).tasks[0]
    return got.name, got.prompt


@pytest.mark.parametrize("char", ["\x85", "\u2028", "\u2029"])
@pytest.mark.parametrize("template", ["a{}b", "x{}", "line one\n{}line two\n"])
def test_save_tasks_round_trips_unicode_line_breaks(
    tmp_path: Path, char: str, template: str
) -> None:
    # YAML reads these as line breaks and folds them, so they must be escaped.
    text = template.format(char)

    assert _round_trips(tmp_path, text, text) == (text, text)


def test_save_tasks_round_trips_random_strings(tmp_path: Path) -> None:
    alphabet = list("abcXYZ019  \t\n\n\r:#-'\"\x85é")
    rng = random.Random(0)
    for _ in range(3000):
        text = "".join(rng.choices(alphabet, k=rng.randint(1, 12)))
        if not text.strip():
            continue

        assert _round_trips(tmp_path, text, text) == (text, text), repr(text)
