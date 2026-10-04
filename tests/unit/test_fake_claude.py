"""The fake claude the other tests rely on, checked on its own."""

import json
import os
import shutil
import subprocess
from pathlib import Path

STREAM_ARGS = [
    "--print",
    "--output-format",
    "stream-json",
    "--verbose",
    "--dangerously-skip-permissions",
]


def run_stream_fake(fake_claude: Path, **env: str) -> list[str]:
    """Run the fake claude in stream mode with extra environment; return its stdout lines."""
    claude = shutil.which("claude")
    assert claude == str(fake_claude / "claude")

    done = subprocess.run(
        [claude, *STREAM_ARGS],
        env={**os.environ, **env},
        input=b"prompt\n",
        stdout=subprocess.PIPE,
        check=True,
    )
    return done.stdout.decode("utf-8").removesuffix("\n").split("\n")


def test_fake_claude_stream_json(fake_claude: Path) -> None:
    lines = run_stream_fake(fake_claude)
    assert lines

    events = [json.loads(line) for line in lines]
    assert all(isinstance(event, dict) for event in events)

    last = events[-1]
    assert last["type"] == "result"
    assert last["result"] == '{"state":"completed","error":""}'


def test_fake_claude_stream_json_big_event(fake_claude: Path) -> None:
    lines = run_stream_fake(fake_claude, FAKE_CLAUDE_BIG_EVENT="200000")

    for line in lines:
        json.loads(line)

    assert max(len(line) for line in lines) >= 200000


def test_fake_claude_records_args(fake_claude: Path, tmp_path: Path) -> None:
    args_file = tmp_path / "args"
    run_stream_fake(fake_claude, FAKE_CLAUDE_ARGS=str(args_file))

    assert args_file.read_text(encoding="utf-8") == " ".join(STREAM_ARGS) + "\n"


def test_fake_claude_records_each_call_as_json(
    fake_claude: Path, tmp_path: Path
) -> None:
    record_file = tmp_path / "record"
    run_stream_fake(fake_claude, FAKECLAUDE_RECORD_FILE=str(record_file))
    run_stream_fake(fake_claude, FAKECLAUDE_RECORD_FILE=str(record_file))

    records = [json.loads(line) for line in record_file.read_text().splitlines()]
    assert records == [{"argv": STREAM_ARGS, "stdin": "prompt\n"}] * 2


def test_fake_claude_plain_output(fake_claude: Path) -> None:
    def run(**env: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["claude", "--print"],
            env={**os.environ, **env},
            input=b"prompt\n",
            capture_output=True,
        )

    assert run().stdout == b'{"state":"completed","error":""}\n'
    assert run(FAKE_CLAUDE_OUTPUT="").stdout == b""

    failed = run(FAKECLAUDE_EXIT_CODE="3", FAKECLAUDE_STDERR_MSG="boom")
    assert (failed.returncode, failed.stdout, failed.stderr) == (3, b"", b"boom\n")
