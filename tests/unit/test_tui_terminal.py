"""The view's terminal output must not freeze alr when the terminal goes away."""

import io
import threading

from textual.drivers import linux_driver

from alr.tui import terminal
from alr.tui.terminal import HangupSafeWriterThread


class _DeadTerminal(io.StringIO):
    """A terminal that has gone: every write fails the way a hung-up pty does."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    def write(self, text: str) -> int:
        self.writes += 1
        raise OSError(5, "Input/output error")

    def flush(self) -> None:
        raise OSError(5, "Input/output error")


class _LiveTerminal(io.StringIO):
    """A terminal that is there: it keeps what it is given."""


def _write_from_a_thread(writer: HangupSafeWriterThread, count: int) -> bool:
    """Write count items from a thread; return whether all of them were accepted in time."""

    def write_all() -> None:
        for number in range(count):
            writer.write(f"line {number}")

    thread = threading.Thread(target=write_all, daemon=True)
    thread.start()
    thread.join(5)
    return not thread.is_alive()


def test_a_dead_terminal_never_blocks_the_writers() -> None:
    writer = HangupSafeWriterThread(_DeadTerminal())
    writer.start()

    # Far more than the queue holds: a writer thread that died on the first
    # error would leave the sender stuck on the full queue.
    assert _write_from_a_thread(writer, 500)

    writer.stop()
    assert not writer.is_alive()


def test_a_dead_terminal_is_written_to_once_then_left_alone() -> None:
    terminal_gone = _DeadTerminal()
    writer = HangupSafeWriterThread(terminal_gone)
    writer.start()

    assert _write_from_a_thread(writer, 100)
    writer.stop()

    assert terminal_gone.writes == 1


def test_a_live_terminal_gets_every_write_in_order() -> None:
    live = _LiveTerminal()
    writer = HangupSafeWriterThread(live)
    writer.start()

    writer.write("a")
    writer.write("b")
    writer.stop()

    assert live.getvalue() == "ab"


def test_the_view_uses_the_hangup_safe_writer() -> None:
    terminal.use_hangup_safe_writer()

    assert linux_driver.WriterThread is HangupSafeWriterThread
