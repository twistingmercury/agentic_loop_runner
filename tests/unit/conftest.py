"""Fixtures for the unit tests."""

import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from alr.config import create_folder
from alr.skillinstall import sync

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


@pytest.fixture(autouse=True)
def home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Point the home folder at a temporary one that already holds the config folder.

    No test may touch the real ~/.local/alr. The folder is created up front so
    a real run goes straight to its work instead of asking to set up; fresh_home
    is the one without it. The bundled skills are synced up front too, so no
    real run prints an Added line for them.

    This uses its own MonkeyPatch, not the monkeypatch fixture: an autouse
    fixture that shared it would change when a test's own patches are undone.
    """
    folder = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(folder))
        patch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        create_folder(folder)
        sync(folder)
        yield folder


@pytest.fixture
def fresh_home(home: Path) -> Path:
    """The temporary home as a machine that never ran alr: no ~/.local at all."""
    shutil.rmtree(home / ".local")
    return home


@pytest.fixture
def locked_folder() -> Iterator[None]:
    """Skip as root, which can write anywhere; the tests restore their own modes."""
    if os.geteuid() == 0:
        pytest.skip("root can write to any folder")

    yield
