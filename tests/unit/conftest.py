"""Fixtures for the unit tests."""

import os
import sys
from pathlib import Path

import pytest

FAKE_CLAUDE_SOURCE = Path(__file__).parent / "fakeclaude" / "fake_claude.py"


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a scriptable `claude` first on PATH and return the folder that holds it."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    # The interpreter running the tests, so the fake works whatever python3 is on PATH.
    script.write_text(
        f"#!{sys.executable}\n{FAKE_CLAUDE_SOURCE.read_text(encoding='utf-8')}",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    return bin_dir


@pytest.fixture
def record(fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use the fake claude and return the file it records its calls in."""
    path = tmp_path / "record.log"
    monkeypatch.setenv("FAKECLAUDE_RECORD_FILE", str(path))
    return path


@pytest.fixture
def work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a folder holding an empty run/ folder for task files, and make it the working directory."""
    # Symlinks resolved, so the path compares equal to the one a run reports.
    folder = tmp_path.resolve() / "work"
    (folder / "run").mkdir(parents=True)
    monkeypatch.chdir(folder)
    return folder
