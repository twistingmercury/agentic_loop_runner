"""Fixtures for the black-box tests."""

import os
import sys
import subprocess
from pathlib import Path

import pytest
from e2e_helpers import find_alr

FAKE_CLAUDE_SOURCE = Path(__file__).parent / "testdata" / "fake_claude.py"


def pytest_sessionstart(session: pytest.Session) -> None:
    """Stop at once, with one message, when alr is missing rather than fail every test."""
    find_alr()


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
def outside(tmp_path: Path) -> Path:
    """A folder next to the repository, so outside its work tree."""
    path = tmp_path.resolve() / "outside"
    path.mkdir()
    return path


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a git repository holding one commit, and make it the working directory.

    The commit ignores run/, where tests keep the task file as a --commit task or --commit run
    requires, and the folder exists so a run can save the file there.
    """
    # Symlinks resolved, so the path compares equal to the one git reports.
    repo = tmp_path.resolve() / "repo"
    repo.mkdir()
    _isolate_git(monkeypatch, repo)
    monkeypatch.chdir(repo)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repo / ".gitignore").write_text("run/\n", encoding="utf-8")
    for args in (
        ["init", "--quiet", "--initial-branch=main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
        ["add", "seed.txt", ".gitignore"],
        ["commit", "--quiet", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True)

    (repo / "run").mkdir()
    return repo


def _isolate_git(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """Keep the developer's git config and any enclosing repository out of the test."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repo.parent))
