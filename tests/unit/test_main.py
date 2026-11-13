"""The alr entry point: flush both streams, then exit with the code."""

import io
import os

import pytest

from alr import cli
from alr import main as entry


class RecordingStream(io.StringIO):
    """A stream that notes in a shared list when it is flushed."""

    def __init__(self, name: str, events: list[str]) -> None:
        super().__init__()
        self.name = name
        self.events = events

    def flush(self) -> None:
        self.events.append(f"{self.name} flushed")


class DeadTerminal(io.StringIO):
    """A stream whose flush fails, as on a terminal that hung up; fileno is a real descriptor."""

    def __init__(self, descriptor: int) -> None:
        super().__init__()
        self.descriptor = descriptor

    def flush(self) -> None:
        raise OSError("the terminal is gone")

    def fileno(self) -> int:
        return self.descriptor


def test_main_flushes_stdout_and_stderr_then_exits_with_the_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(cli, "main", lambda: 7)
    monkeypatch.setattr("sys.stdout", RecordingStream("stdout", events))
    monkeypatch.setattr("sys.stderr", RecordingStream("stderr", events))

    with pytest.raises(SystemExit) as exited:
        entry.main()

    assert exited.value.code == 7
    assert events == ["stdout flushed", "stderr flushed"]


def test_a_flush_that_fails_points_the_descriptor_at_devnull() -> None:
    read_end, write_end = os.pipe()
    os.set_blocking(write_end, False)
    try:
        entry._flush(DeadTerminal(write_end))
        # The descriptor now leads to devnull, so this write must not reach the pipe.
        os.write(write_end, b"lost")

        # The pipe has no writer left and nothing in it: end of file.
        assert os.read(read_end, 16) == b""
    finally:
        os.close(read_end)
        os.close(write_end)
