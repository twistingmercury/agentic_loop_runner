"""The review session: its prompt and arguments, the three lines it prints, its log, and every way it fails."""

import io
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
from helpers import fake_claude_records, process_is_running, wait_for_file, write_file

from alr import review as review_module
from alr.errors import AlrError, ReviewError
from alr.review import review

TASKS_YAML = "shared:\n  prompt: shared\ntasks:\n  - {id: 1, name: a, prompt: p}\n"

# A blank line and an indented line, so a reply that is trimmed or tidied shows.
FINDINGS = (
    "task=1 | size | too big | five files\n\n  task=2 | rules | no path | missing\n"
)

HEADER_TIME = re.compile(r"time: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d")
LOG_NAME = re.compile(r"review-\d{8}T\d{6}\.log")


@pytest.fixture
def tasks_file(tmp_path: Path) -> str:
    """A run folder holding a task file, returned as the path the command line gives."""
    folder = tmp_path / "run"
    folder.mkdir()
    return write_file(folder / "tasks.yaml", TASKS_YAML)


@pytest.fixture
def sandbox_file(tmp_path: Path) -> str:
    """A sandbox settings file in a folder of its own, away from the run folder."""
    folder = tmp_path / "sandboxes"
    folder.mkdir()
    return write_file(folder / "tight.json", "{}")


def asking_line(tasks_file: str) -> str:
    return f"review: asking Claude to read {tasks_file} (this uses tokens)\n"


def logs_folder(tasks_file: str) -> Path:
    return Path(tasks_file).parent / "logs"


def review_log(tasks_file: str) -> Path:
    """The one file in the run folder's logs folder, which must be a review log."""
    [path] = sorted(logs_folder(tasks_file).iterdir())
    assert LOG_NAME.fullmatch(path.name), path.name
    return path


def recorded_call(record: Path) -> dict[str, Any]:
    """The one call the fake claude recorded."""
    [call] = fake_claude_records(record)
    return call


def value_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("reply", "printed"),
    [
        (FINDINGS, FINDINGS),
        (FINDINGS.removesuffix("\n"), FINDINGS),
        # Only a missing final newline is made up; blank lines are the reply's own.
        (f"\n{FINDINGS}\n", f"\n{FINDINGS}\n"),
    ],
    ids=["trailing-newline", "no-newline", "blank-lines-around"],
)
def test_review_prints_the_three_lines_and_the_reply_as_it_came(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch, reply: str, printed: str
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", reply)
    out = io.StringIO()

    review(out, tasks_file, "", "")

    log = review_log(tasks_file)
    assert out.getvalue() == (
        asking_line(tasks_file) + printed + f"review log: {log}\n"
    )


@pytest.mark.usefixtures("fake_claude")
def test_review_writes_the_log_with_a_header_and_the_reply(
    tasks_file: str, sandbox_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", FINDINGS)

    review(io.StringIO(), tasks_file, sandbox_file, "")

    text = review_log(tasks_file).read_text(encoding="utf-8")
    header, _, body = text.partition("\n\n")
    time_line, task_line, sandbox_line = header.splitlines()
    assert HEADER_TIME.fullmatch(time_line), time_line
    assert task_line == f"task file: {tasks_file}"
    assert sandbox_line == f"sandbox file: {sandbox_file}"
    assert body == FINDINGS


@pytest.mark.usefixtures("fake_claude")
def test_review_writes_the_log_for_no_findings(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", "NO FINDINGS\n")
    out = io.StringIO()

    review(out, tasks_file, "", "")

    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert "sandbox file: none\n" in text
    assert text.endswith("\n\nNO FINDINGS\n")
    assert "\nNO FINDINGS\n" in out.getvalue()


@pytest.mark.usefixtures("fake_claude")
def test_review_creates_logs_at_mode_700_and_the_file_at_mode_600(
    tasks_file: str,
) -> None:
    assert not logs_folder(tasks_file).exists()

    review(io.StringIO(), tasks_file, "", "")

    assert stat.S_IMODE(logs_folder(tasks_file).stat().st_mode) == 0o700
    assert stat.S_IMODE(review_log(tasks_file).stat().st_mode) == 0o600


@pytest.mark.usefixtures("fake_claude")
def test_review_leaves_an_existing_logs_folder_and_its_files_alone(
    tasks_file: str,
) -> None:
    logs = logs_folder(tasks_file)
    logs.mkdir(mode=0o755)
    logs.chmod(0o755)
    earlier = logs / "20261110T090000"
    earlier.mkdir()

    review(io.StringIO(), tasks_file, "", "")

    assert stat.S_IMODE(logs.stat().st_mode) == 0o755
    assert earlier.is_dir()
    assert len(list(logs.glob("review-*.log"))) == 1


def test_review_prompt_names_the_skill_the_task_file_and_the_sandbox_file(
    record: Path, tasks_file: str, sandbox_file: str
) -> None:
    review(io.StringIO(), tasks_file, sandbox_file, "")

    prompt = recorded_call(record)["stdin"]
    assert "alr-task-review" in prompt
    assert tasks_file in prompt
    assert sandbox_file in prompt


def test_review_prompt_says_there_is_no_sandbox_file(
    record: Path, tasks_file: str
) -> None:
    review(io.StringIO(), tasks_file, "", "")

    prompt = recorded_call(record)["stdin"]
    assert "alr-task-review" in prompt
    assert tasks_file in prompt
    assert "There is no sandbox settings file" in prompt


def test_review_never_resumes_a_session_or_picks_a_model(
    record: Path, tasks_file: str
) -> None:
    review(io.StringIO(), tasks_file, "", "")

    argv = recorded_call(record)["argv"]
    assert argv[0] == "--print"
    forbidden = ("--resume", "--continue", "--model", "-r", "-c")
    assert [arg for arg in argv if arg.startswith(forbidden)] == []


def test_review_holds_the_session_to_reading_tools(
    record: Path, tasks_file: str
) -> None:
    review(io.StringIO(), tasks_file, "", "")

    argv = recorded_call(record)["argv"]
    # The skill needs the Skill tool; nothing here can edit a file or run a command.
    assert value_after(argv, "--tools") == "Read,Grep,Glob,Skill"
    assert value_after(argv, "--allowedTools") == "Read,Grep,Glob,Skill"
    # Never asks, so a tool outside the list is refused, not left waiting.
    assert value_after(argv, "--permission-mode") == "dontAsk"


def test_review_adds_the_run_folder_and_the_sandbox_folder_to_what_it_may_read(
    record: Path, tasks_file: str, sandbox_file: str
) -> None:
    review(io.StringIO(), tasks_file, sandbox_file, "")

    argv = recorded_call(record)["argv"]
    folders = argv[argv.index("--add-dir") + 1 :]
    assert folders == [str(Path(tasks_file).parent), str(Path(sandbox_file).parent)]


def test_review_adds_only_the_run_folder_when_there_is_no_sandbox_file(
    record: Path, tasks_file: str
) -> None:
    review(io.StringIO(), tasks_file, "", "")

    argv = recorded_call(record)["argv"]
    assert argv[argv.index("--add-dir") + 1 :] == [str(Path(tasks_file).parent)]


@pytest.mark.usefixtures("fake_claude")
def test_a_non_zero_exit_raises_and_logs_the_failure(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "3")
    monkeypatch.setenv("FAKECLAUDE_STDERR", "boom\n")
    # Whatever it said before failing is not a review.
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", FINDINGS)
    out = io.StringIO()

    with pytest.raises(ReviewError) as raised:
        review(out, tasks_file, "", "")

    failure = "review: claude exited with status 3: boom"
    assert str(raised.value) == failure
    assert raised.value.exit_code == 1
    assert out.getvalue() == asking_line(tasks_file)
    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith(f"\n\nerror: {failure}\n")
    assert "task=1" not in text


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize("reply", ["", "\n", " \n\n"])
def test_an_empty_reply_raises_and_logs_the_failure(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", reply)
    out = io.StringIO()

    with pytest.raises(ReviewError) as raised:
        review(out, tasks_file, "", "")

    failure = "review: claude replied with nothing"
    assert str(raised.value) == failure
    assert out.getvalue() == asking_line(tasks_file)
    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith(f"\n\nerror: {failure}\n")


@pytest.mark.usefixtures("fake_claude")
def test_a_time_out_stops_the_session_and_raises(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "ready"
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    monkeypatch.setenv("FAKECLAUDE_READY_FILE", str(ready))
    out = io.StringIO()

    with pytest.raises(ReviewError) as raised:
        review(out, tasks_file, "", "1s")

    failure = "review: claude did not finish within 1s"
    assert str(raised.value) == failure
    assert out.getvalue() == asking_line(tasks_file)
    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith(f"\n\nerror: {failure}\n")
    claude_pid = int(wait_for_file(ready))
    assert not process_is_running(claude_pid)


def test_a_missing_claude_raises_before_any_output_and_writes_no_log(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    out = io.StringIO()

    with pytest.raises(ReviewError) as raised:
        review(out, tasks_file, "", "")

    assert str(raised.value) == "review: claude is not on the PATH"
    assert isinstance(raised.value, AlrError)
    assert out.getvalue() == ""
    assert not logs_folder(tasks_file).exists()


def test_a_claude_that_cannot_start_raises_and_logs_the_failure(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    program = broken / "claude"
    program.write_text("#!/no/such/interpreter\n", encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(broken))
    out = io.StringIO()

    with pytest.raises(ReviewError) as raised:
        review(out, tasks_file, "", "")

    failure = "review: claude could not start: No such file or directory"
    assert str(raised.value) == failure
    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith(f"\n\nerror: {failure}\n")


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    ("action_timeout", "seconds"),
    [("", 600.0), ("90s", 90.0)],
    ids=["default", "override"],
)
def test_action_timeout_overrides_the_default_limit(
    tasks_file: str,
    monkeypatch: pytest.MonkeyPatch,
    action_timeout: str,
    seconds: float,
) -> None:
    limits: list[float] = []
    real_run = subprocess.run

    def run_noting_the_limit(*args: Any, **kwargs: Any) -> Any:
        limits.append(kwargs["timeout"])
        return real_run(*args, **kwargs)

    monkeypatch.setattr(review_module.subprocess, "run", run_noting_the_limit)

    review(io.StringIO(), tasks_file, "", action_timeout)

    assert limits == [seconds]
