"""The review session: its prompt and arguments, the three lines it prints, its log, and every way it fails."""

import asyncio
import io
import json
import os
import re
import signal
import stat
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import pytest
from helpers import (
    fake_claude_records,
    process_is_running,
    wait_for_exit,
    wait_for_file,
    write_file,
)

from alr import review as review_module
from alr.engine.stop import Stop
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


@pytest.fixture
def one_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every review start in the same second."""
    moment = datetime(2026, 11, 10, 14, 38, 22, tzinfo=timezone.utc)
    monkeypatch.setattr(review_module, "_now", lambda: moment)


@pytest.fixture
def unwritable_logs(tasks_file: str, locked_folder: None) -> Iterator[Path]:
    """A logs folder that exists but cannot be written, restored so pytest can clean up."""
    logs = logs_folder(tasks_file)
    logs.mkdir()
    logs.chmod(0o500)
    yield logs
    logs.chmod(0o700)


def run_review(
    out: TextIO,
    tasks_file: str,
    sandbox_file: str,
    action_timeout: str,
    stop: Stop,
) -> None:
    asyncio.run(review(out, tasks_file, sandbox_file, action_timeout, stop))


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

    run_review(out, tasks_file, "", "", Stop())

    log = review_log(tasks_file)
    assert out.getvalue() == (
        asking_line(tasks_file) + printed + f"review log: {log}\n"
    )


@pytest.mark.usefixtures("fake_claude")
def test_review_writes_the_log_with_a_header_and_the_reply(
    tasks_file: str, sandbox_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", FINDINGS)

    run_review(io.StringIO(), tasks_file, sandbox_file, "", Stop())

    text = review_log(tasks_file).read_text(encoding="utf-8")
    header, _, body = text.partition("\n\n")
    time_line, task_line, sandbox_line = header.splitlines()
    assert HEADER_TIME.fullmatch(time_line), time_line
    assert task_line == f"task file: {json.dumps(tasks_file)}"
    assert sandbox_line == f"sandbox file: {json.dumps(sandbox_file)}"
    assert body == FINDINGS


@pytest.mark.usefixtures("fake_claude")
def test_review_writes_the_log_for_no_findings(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", "NO FINDINGS\n")
    out = io.StringIO()

    run_review(out, tasks_file, "", "", Stop())

    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert "sandbox file: none\n" in text
    assert text.endswith("\n\nNO FINDINGS\n")
    assert "\nNO FINDINGS\n" in out.getvalue()


@pytest.mark.usefixtures("fake_claude")
def test_review_creates_logs_at_mode_700_and_the_file_at_mode_600(
    tasks_file: str,
) -> None:
    assert not logs_folder(tasks_file).exists()

    run_review(io.StringIO(), tasks_file, "", "", Stop())

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

    run_review(io.StringIO(), tasks_file, "", "", Stop())

    assert stat.S_IMODE(logs.stat().st_mode) == 0o755
    assert earlier.is_dir()
    assert len(list(logs.glob("review-*.log"))) == 1


def test_review_prompt_names_the_skill_the_task_file_and_the_sandbox_file(
    record: Path, tasks_file: str, sandbox_file: str
) -> None:
    run_review(io.StringIO(), tasks_file, sandbox_file, "", Stop())

    prompt = recorded_call(record)["stdin"]
    assert "alr-task-review" in prompt
    assert tasks_file in prompt
    assert sandbox_file in prompt


def test_review_prompt_says_there_is_no_sandbox_file(
    record: Path, tasks_file: str
) -> None:
    run_review(io.StringIO(), tasks_file, "", "", Stop())

    prompt = recorded_call(record)["stdin"]
    assert "alr-task-review" in prompt
    assert tasks_file in prompt
    assert "There is no sandbox settings file" in prompt


def test_review_never_resumes_a_session_or_picks_a_model(
    record: Path, tasks_file: str
) -> None:
    run_review(io.StringIO(), tasks_file, "", "", Stop())

    argv = recorded_call(record)["argv"]
    assert argv[0] == "--print"
    forbidden = ("--resume", "--continue", "--model", "-r", "-c")
    assert [arg for arg in argv if arg.startswith(forbidden)] == []


def test_review_holds_the_session_to_reading_tools(
    record: Path, tasks_file: str
) -> None:
    run_review(io.StringIO(), tasks_file, "", "", Stop())

    argv = recorded_call(record)["argv"]
    # The skill needs the Skill tool; nothing here can edit a file or run a command.
    assert value_after(argv, "--tools") == "Read,Grep,Glob,Skill"
    assert value_after(argv, "--allowedTools") == "Read,Grep,Glob,Skill"
    # Never asks, so a tool outside the list is refused, not left waiting.
    assert value_after(argv, "--permission-mode") == "dontAsk"


def test_review_adds_the_run_folder_and_the_sandbox_folder_to_what_it_may_read(
    record: Path, tasks_file: str, sandbox_file: str
) -> None:
    run_review(io.StringIO(), tasks_file, sandbox_file, "", Stop())

    argv = recorded_call(record)["argv"]
    folders = argv[argv.index("--add-dir") + 1 :]
    assert folders == [str(Path(tasks_file).parent), str(Path(sandbox_file).parent)]


def test_review_adds_only_the_run_folder_when_there_is_no_sandbox_file(
    record: Path, tasks_file: str
) -> None:
    run_review(io.StringIO(), tasks_file, "", "", Stop())

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
        run_review(out, tasks_file, "", "", Stop())

    failure = "review: claude failed (exit status 3): boom"
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
        run_review(out, tasks_file, "", "", Stop())

    failure = "review: claude replied with nothing"
    assert str(raised.value) == failure
    assert out.getvalue() == asking_line(tasks_file)
    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith(f"\n\nerror: {failure}\n")


@pytest.mark.usefixtures("fake_claude")
def test_a_clean_exit_with_no_reply_keeps_the_last_stderr_line(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", "")
    monkeypatch.setenv("FAKECLAUDE_STDERR", "first line\nrate limited\n")

    with pytest.raises(ReviewError) as raised:
        run_review(io.StringIO(), tasks_file, "", "", Stop())

    failure = "review: claude replied with nothing: rate limited"
    assert str(raised.value) == failure
    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith(f"\n\nerror: {failure}\n")


@pytest.mark.usefixtures("fake_claude", "unwritable_logs")
def test_the_log_warning_comes_after_stdout_is_flushed(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class NoteFlush(io.StringIO):
        def flush(self) -> None:
            events.append("stdout flushed")

    class NoteWarning(io.StringIO):
        def write(self, text: str, /) -> int:
            if text.startswith("warning:"):
                events.append("warning written")

            return super().write(text)

    monkeypatch.setattr(sys, "stdout", NoteFlush())
    monkeypatch.setattr(sys, "stderr", NoteWarning())

    run_review(io.StringIO(), tasks_file, "", "", Stop())

    assert events == ["stdout flushed", "warning written"]


@pytest.mark.usefixtures("fake_claude")
def test_a_time_out_stops_the_session_and_raises(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "ready"
    monkeypatch.setenv("FAKECLAUDE_BLOCK", "1")
    monkeypatch.setenv("FAKECLAUDE_READY_FILE", str(ready))
    out = io.StringIO()

    with pytest.raises(ReviewError) as raised:
        run_review(out, tasks_file, "", "1s", Stop())

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
        run_review(out, tasks_file, "", "", Stop())

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
        run_review(out, tasks_file, "", "", Stop())

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
    real_parse_duration = review_module.parse_duration

    def parse_noting_the_limit(text: str) -> float:
        limits.append(real_parse_duration(text))
        return limits[-1]

    monkeypatch.setattr(review_module, "parse_duration", parse_noting_the_limit)

    run_review(io.StringIO(), tasks_file, "", action_timeout, Stop())

    assert limits == [seconds]


@pytest.mark.usefixtures("fake_claude", "one_second")
def test_two_reviews_in_the_same_second_get_different_log_names(
    tasks_file: str,
) -> None:
    run_review(io.StringIO(), tasks_file, "", "", Stop())
    second = io.StringIO()

    run_review(second, tasks_file, "", "", Stop())

    names = sorted(path.name for path in logs_folder(tasks_file).iterdir())
    assert names == ["review-20261110T143822-1.log", "review-20261110T143822.log"]
    second_log = logs_folder(tasks_file) / "review-20261110T143822-1.log"
    assert second.getvalue().endswith(f"review log: {second_log}\n")


@pytest.mark.usefixtures("fake_claude", "one_second")
def test_a_third_review_in_the_same_second_gets_a_two_suffix(
    tasks_file: str,
) -> None:
    for _ in range(3):
        run_review(io.StringIO(), tasks_file, "", "", Stop())

    names = sorted(path.name for path in logs_folder(tasks_file).iterdir())
    assert names == [
        "review-20261110T143822-1.log",
        "review-20261110T143822-2.log",
        "review-20261110T143822.log",
    ]


@pytest.mark.usefixtures("fake_claude", "one_second")
def test_an_existing_log_is_never_replaced_and_new_logs_are_mode_600(
    tasks_file: str,
) -> None:
    logs = logs_folder(tasks_file)
    logs.mkdir()
    taken = logs / "review-20261110T143822.log"
    taken.write_text("mine\n", encoding="utf-8")
    taken.chmod(0o644)

    run_review(io.StringIO(), tasks_file, "", "", Stop())

    assert taken.read_text(encoding="utf-8") == "mine\n"
    assert stat.S_IMODE(taken.stat().st_mode) == 0o644
    new_log = logs_folder(tasks_file) / "review-20261110T143822-1.log"
    assert stat.S_IMODE(new_log.stat().st_mode) == 0o600


@pytest.mark.usefixtures("fake_claude", "unwritable_logs")
def test_the_reply_is_printed_when_the_log_cannot_be_written(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", FINDINGS)
    out = io.StringIO()

    run_review(out, tasks_file, "", "", Stop())

    assert out.getvalue() == asking_line(tasks_file) + FINDINGS


@pytest.mark.usefixtures("fake_claude", "one_second")
def test_the_reply_is_printed_before_the_log_is_written(tasks_file: str) -> None:
    class NoteLogs(io.StringIO):
        """Notes whether the logs folder existed when each piece was printed."""

        def __init__(self) -> None:
            super().__init__()
            self.logs_seen: list[bool] = []

        def write(self, text: str, /) -> int:
            self.logs_seen.append(logs_folder(tasks_file).exists())
            return super().write(text)

    out = NoteLogs()

    run_review(out, tasks_file, "", "", Stop())

    # The asking line, the reply, then the log line: only the last follows the log.
    assert out.logs_seen == [False, False, True]


@pytest.mark.usefixtures("fake_claude", "unwritable_logs")
def test_a_log_write_failure_prints_a_warning_and_changes_no_result(
    tasks_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    out = io.StringIO()

    run_review(out, tasks_file, "", "", Stop())

    assert "review log:" not in out.getvalue()
    stderr = capsys.readouterr().err
    warning = re.compile(
        rf"warning: review log: cannot write {re.escape(str(logs_folder(tasks_file)))}"
        r"/review-\d{8}T\d{6}\.log: Permission denied\n"
    )
    assert warning.fullmatch(stderr), stderr


@pytest.mark.usefixtures("fake_claude", "unwritable_logs")
def test_a_failed_review_keeps_its_error_when_the_log_cannot_be_written(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "3")
    monkeypatch.setenv("FAKECLAUDE_STDERR", "boom\n")

    with pytest.raises(ReviewError) as raised:
        run_review(io.StringIO(), tasks_file, "", "", Stop())

    assert str(raised.value) == "review: claude failed (exit status 3): boom"
    assert "warning: review log: cannot write" in capsys.readouterr().err


@pytest.mark.usefixtures("fake_claude", "locked_folder")
def test_a_logs_folder_that_cannot_be_made_gets_the_warning_too(
    tasks_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run_folder = Path(tasks_file).parent
    run_folder.chmod(0o500)
    out = io.StringIO()
    try:
        run_review(out, tasks_file, "", "", Stop())
    finally:
        run_folder.chmod(0o700)

    assert "review log:" not in out.getvalue()
    stderr = capsys.readouterr().err
    assert stderr.startswith("warning: review log: cannot write ")
    assert stderr.endswith(": Permission denied\n")


def test_a_missing_skill_raises_before_the_review_line_and_starts_no_claude(
    record: Path, tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    out = io.StringIO()

    with pytest.raises(ReviewError) as raised:
        run_review(out, tasks_file, "", "", Stop())

    skills = config / "skills"
    assert str(raised.value) == (
        f"review: the alr-task-review skill is not installed in {skills}"
    )
    assert out.getvalue() == ""
    assert not logs_folder(tasks_file).exists()
    assert fake_claude_records(record) == []


@pytest.mark.usefixtures("fake_claude")
def test_a_reply_the_stream_cannot_encode_prints_with_a_replacement_and_still_logs(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", "task=1 | size \u2014 too big\n")
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="ascii", write_through=True)

    run_review(out, tasks_file, "", "", Stop())

    log = review_log(tasks_file)
    assert raw.getvalue().decode("ascii") == (
        asking_line(tasks_file) + "task=1 | size ? too big\n" + f"review log: {log}\n"
    )
    # The log is utf-8, so the reply is kept whole there.
    assert "size \u2014 too big" in log.read_text(encoding="utf-8")


def test_a_path_with_a_line_break_is_quoted_in_the_prompt_and_the_header(
    record: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "odd\nname"
    folder.mkdir()
    odd_tasks = write_file(folder / "tasks.yaml", TASKS_YAML)

    run_review(io.StringIO(), odd_tasks, "", "", Stop())

    quoted = json.dumps(odd_tasks)
    prompt = recorded_call(record)["stdin"]
    assert f"alr task file {quoted}." in prompt
    assert odd_tasks not in prompt
    header = review_log(odd_tasks).read_text(encoding="utf-8").split("\n\n")[0]
    assert header.splitlines()[1] == f"task file: {quoted}"
    assert len(header.splitlines()) == 3


@pytest.mark.usefixtures("fake_claude")
def test_the_last_stderr_line_is_the_reason(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_EXIT_CODE", "1")
    monkeypatch.setenv("FAKECLAUDE_STDERR", "first line\nsecond line\n")

    with pytest.raises(ReviewError) as raised:
        run_review(io.StringIO(), tasks_file, "", "", Stop())

    assert str(raised.value) == "review: claude failed (exit status 1): second line"


@pytest.mark.usefixtures("fake_claude")
def test_invalid_utf8_in_the_reply_is_replaced(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", "bad \udcff byte\n")
    out = io.StringIO()

    run_review(out, tasks_file, "", "", Stop())

    assert "bad \ufffd byte\n" in out.getvalue()
    assert "bad \ufffd byte" in review_log(tasks_file).read_text(encoding="utf-8")


def test_a_skills_folder_without_skill_md_fails_before_the_review_line(
    record: Path, tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "cfg"
    (config / "skills" / "alr-task-review").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    out = io.StringIO()

    with pytest.raises(ReviewError) as raised:
        run_review(out, tasks_file, "", "", Stop())

    assert str(raised.value) == (
        f"review: the alr-task-review skill is not installed in {config / 'skills'}"
    )
    assert out.getvalue() == ""
    assert not logs_folder(tasks_file).exists()
    assert fake_claude_records(record) == []


def test_the_argv_denies_mcp_tools(record: Path, tasks_file: str) -> None:
    run_review(io.StringIO(), tasks_file, "", "", Stop())

    argv = recorded_call(record)["argv"]
    # --tools does not cover MCP tools, so they are denied by name pattern.
    assert value_after(argv, "--disallowedTools") == "mcp__*"
    # --add-dir takes a list, so it stays last and keeps the flag out of its folders.
    assert argv.index("--disallowedTools") < argv.index("--add-dir")


def install_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Put a claude that runs the Python source body first on PATH.

    For the tests the shared fake cannot script: it has no way to start a child
    that keeps the output open, to report its process group, or to print megabytes.
    """
    folder = tmp_path / "scripted"
    folder.mkdir()
    program = folder / "claude"
    program.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:{os.environ.get('PATH', '')}")


@pytest.mark.usefixtures("fake_claude")
@pytest.mark.parametrize(
    "stop_signal",
    [signal.SIGINT, signal.SIGTERM, signal.SIGHUP],
    ids=["sigint", "sigterm", "sighup"],
)
def test_a_stop_writes_a_stopped_by_line_to_the_log(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch, stop_signal: signal.Signals
) -> None:
    async def cancelled(*args: Any, **kwargs: Any) -> str:
        raise asyncio.CancelledError

    monkeypatch.setattr(review_module, "_session", cancelled)
    out = io.StringIO()

    with pytest.raises(asyncio.CancelledError):
        run_review(out, tasks_file, "", "", Stop(signum=stop_signal))

    assert out.getvalue() == asking_line(tasks_file)
    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith(f"\n\nstopped by {stop_signal.name}\n")


@pytest.mark.usefixtures("fake_claude")
def test_a_stop_after_the_reply_still_writes_the_log(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKECLAUDE_OUTPUT", FINDINGS)

    async def stopped_after_the_reply() -> None:
        class CancelOnReply(io.StringIO):
            def write(self, text: str, /) -> int:
                if text == FINDINGS:
                    task.cancel()
                    raise asyncio.CancelledError

                return super().write(text)

        task = asyncio.ensure_future(
            review(CancelOnReply(), tasks_file, "", "", Stop(signum=signal.SIGTERM))
        )
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stopped_after_the_reply())

    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.splitlines()[-1] == "stopped by SIGTERM"


def test_a_cancel_while_the_child_is_starting_leaves_no_claude_running(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "claude.pid"
    monkeypatch.setenv("PID_FILE", str(pid_file))
    install_claude(
        tmp_path,
        monkeypatch,
        "import os, time\n"
        "with open(os.environ['PID_FILE'], 'w') as file:\n"
        "    file.write(str(os.getpid()))\n"
        "time.sleep(8)\n",
    )

    async def cancelled_during_the_spawn() -> None:
        task = asyncio.ensure_future(
            review(io.StringIO(), tasks_file, "", "", Stop(signum=signal.SIGINT))
        )
        # One turn of the loop: the spawn has been asked for and has not returned.
        await asyncio.sleep(0)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled_during_the_spawn())

    # A claude left running writes its pid file within moments of starting,
    # which is after the cancel; without this wait the check below would
    # almost always find no file and pass whatever alr did.
    time.sleep(0.5)
    if pid_file.exists():
        wait_for_exit(int(pid_file.read_text()))

    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.splitlines()[-1] == "stopped by SIGINT"


def test_a_signal_death_reads_like_a_task_session(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_claude(
        tmp_path,
        monkeypatch,
        "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n",
    )

    with pytest.raises(ReviewError) as raised:
        run_review(io.StringIO(), tasks_file, "", "", Stop())

    assert str(raised.value) == "review: claude failed (signal: SIGKILL)"
    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith("\n\nerror: review: claude failed (signal: SIGKILL)\n")


def test_the_session_starts_in_its_own_process_group(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    groups = tmp_path / "groups"
    monkeypatch.setenv("GROUPS_FILE", str(groups))
    install_claude(
        tmp_path,
        monkeypatch,
        "import os\n"
        "with open(os.environ['GROUPS_FILE'], 'w') as file:\n"
        "    file.write(f'{os.getpid()} {os.getpgrp()}')\n"
        "print('NO FINDINGS')\n",
    )

    run_review(io.StringIO(), tasks_file, "", "", Stop())

    pid, group = (int(number) for number in groups.read_text().split())
    # It leads a group of its own, and that group is not alr's.
    assert group == pid
    assert group != os.getpgrp()


def test_a_background_child_holding_the_output_does_not_turn_a_finished_review_into_a_time_out(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("CHILD_PID_FILE", str(child_pid_file))
    # The child inherits the session's output and outlives the session by far.
    install_claude(
        tmp_path,
        monkeypatch,
        "import os, subprocess\n"
        "child = subprocess.Popen(['sleep', '20'])\n"
        "with open(os.environ['CHILD_PID_FILE'], 'w') as file:\n"
        "    file.write(str(child.pid))\n"
        "print('the reply')\n",
    )
    out = io.StringIO()

    # A limit far under the child's 20 seconds: waiting for the output to close would hit it.
    run_review(out, tasks_file, "", "5s", Stop())

    assert "the reply\n" in out.getvalue()
    wait_for_exit(int(child_pid_file.read_text()))


def test_a_failed_session_leaves_no_background_child(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("CHILD_PID_FILE", str(child_pid_file))
    install_claude(
        tmp_path,
        monkeypatch,
        "import os, subprocess, sys\n"
        "child = subprocess.Popen(['sleep', '20'])\n"
        "with open(os.environ['CHILD_PID_FILE'], 'w') as file:\n"
        "    file.write(str(child.pid))\n"
        "sys.exit(3)\n",
    )

    with pytest.raises(ReviewError):
        run_review(io.StringIO(), tasks_file, "", "", Stop())

    wait_for_exit(int(child_pid_file.read_text()))


@pytest.mark.usefixtures("fake_claude")
def test_an_unexpected_error_in_the_session_logs_the_fallback_line(
    tasks_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(review_module, "_session", broken)

    with pytest.raises(RuntimeError):
        run_review(io.StringIO(), tasks_file, "", "", Stop())

    text = review_log(tasks_file).read_text(encoding="utf-8")
    assert text.endswith("\n\nerror: the review ended without a result\n")


def test_a_review_that_ends_near_its_limit_is_not_a_time_out(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("CHILD_PID_FILE", str(child_pid_file))
    # The reply comes at 0.7 of the limit; the child holds the output open, so
    # the drain after the exit would carry the review past the limit.
    install_claude(
        tmp_path,
        monkeypatch,
        "import os, subprocess, time\n"
        "child = subprocess.Popen(['sleep', '20'])\n"
        "with open(os.environ['CHILD_PID_FILE'], 'w') as file:\n"
        "    file.write(str(child.pid))\n"
        "time.sleep(0.7)\n"
        "print('the reply')\n",
    )
    out = io.StringIO()

    run_review(out, tasks_file, "", "1s", Stop())

    assert "the reply\n" in out.getvalue()
    wait_for_exit(int(child_pid_file.read_text()))


def test_a_large_reply_is_returned_whole(
    tasks_file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    line = f"{'x' * 120}\n"
    lines = 32768
    install_claude(
        tmp_path,
        monkeypatch,
        f"import sys\nfor number in range({lines}):\n"
        f"    sys.stdout.write(f'{{number:07d}} {line[:-1]}\\n')\n",
    )
    reply = "".join(f"{number:07d} {line}" for number in range(lines))
    out = io.StringIO()

    run_review(out, tasks_file, "", "", Stop())

    # Four megabytes, all of it, from the first line to the last.
    assert len(reply) > 4_000_000
    assert out.getvalue() == asking_line(tasks_file) + reply + (
        f"review log: {review_log(tasks_file)}\n"
    )
    assert review_log(tasks_file).read_text(encoding="utf-8").endswith(f"\n\n{reply}")
