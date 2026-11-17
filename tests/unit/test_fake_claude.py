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


def run_stream_fake(
    fake_claude: Path, extra_args: list[str] | None = None, **env: str
) -> list[str]:
    """Run the fake claude in stream mode with extra environment; return its stdout lines."""
    claude = shutil.which("claude")
    assert claude == str(fake_claude / "claude")

    done = subprocess.run(
        [claude, *STREAM_ARGS, *(extra_args or [])],
        env={**os.environ, **env},
        input=b"prompt\n",
        stdout=subprocess.PIPE,
        check=True,
    )
    return done.stdout.decode("utf-8").removesuffix("\n").split("\n")


def run_plain_fake(fake_claude: Path, args: list[str], **env: str) -> tuple[int, str]:
    """Run the fake claude in plain mode; return its exit code and decoded stdout."""
    claude = shutil.which("claude")
    assert claude == str(fake_claude / "claude")

    done = subprocess.run(
        [claude, *args],
        env={**os.environ, **env},
        input=b"prompt\n",
        stdout=subprocess.PIPE,
    )
    return done.returncode, done.stdout.decode("utf-8")


def test_fake_claude_stream_json_big_event(fake_claude: Path) -> None:
    lines = run_stream_fake(fake_claude, FAKECLAUDE_BIG_EVENT="200000")

    for line in lines:
        json.loads(line)

    assert max(len(line) for line in lines) >= 200000


def test_fake_claude_resume_output_replaces_output_only_on_resume(
    fake_claude: Path,
) -> None:
    line = '{"state":"completed","error":""}'
    env = {"FAKECLAUDE_OUTPUT": "", "FAKECLAUDE_RESUME_OUTPUT": line}

    _, first_stdout = run_plain_fake(fake_claude, ["--print"], **env)
    _, resumed_stdout = run_plain_fake(fake_claude, ["--resume", "x"], **env)

    assert first_stdout == ""
    assert resumed_stdout == line


def test_fake_claude_resume_exit_code_replaces_exit_code_only_on_resume(
    fake_claude: Path,
) -> None:
    env = {"FAKECLAUDE_RESUME_EXIT_CODE": "3"}

    first_code, _ = run_plain_fake(fake_claude, ["--print"], **env)
    resumed_code, _ = run_plain_fake(fake_claude, ["--resume", "x"], **env)

    assert first_code == 0
    assert resumed_code == 3


def test_fake_claude_resume_falls_back_to_the_plain_variables(
    fake_claude: Path,
) -> None:
    code, stdout = run_plain_fake(fake_claude, ["--resume", "x"], FAKECLAUDE_OUTPUT="x")

    assert code == 0
    assert stdout == "x"


def test_fake_claude_stream_mode_honors_resume_output(fake_claude: Path) -> None:
    lines = run_stream_fake(
        fake_claude,
        ["--resume", "x"],
        FAKECLAUDE_OUTPUT="plain",
        FAKECLAUDE_RESUME_OUTPUT="resumed",
    )

    result = json.loads(lines[-1])
    assert result["type"] == "result"
    assert result["result"] == "resumed"
