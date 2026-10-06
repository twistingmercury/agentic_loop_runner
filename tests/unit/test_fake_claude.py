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


def test_fake_claude_stream_json_big_event(fake_claude: Path) -> None:
    lines = run_stream_fake(fake_claude, FAKECLAUDE_BIG_EVENT="200000")

    for line in lines:
        json.loads(line)

    assert max(len(line) for line in lines) >= 200000
