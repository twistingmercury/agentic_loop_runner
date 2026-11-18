"""Plain mode's output, kept from crashing a run that was meant to outlive its terminal."""

import errno
import io
import signal
import sys
from collections.abc import Buffer, Iterator
from contextlib import contextmanager
from typing import TextIO

# What a write to a terminal that has gone fails with.
_GONE = (errno.EIO, errno.EPIPE)


class _VanishingTerminal(io.FileIO):
    """A standard stream's file that stops writing once its terminal has gone.

    A write that fails because the terminal is gone is reported as done, and every
    later write is dropped. Any other failure is raised as before. The file
    descriptor belongs to the process, so closing this does not close it.
    """

    def __init__(self, descriptor: int) -> None:
        super().__init__(descriptor, "w", closefd=False)
        self._gone = False

    def write(self, data: Buffer) -> int:
        size = len(memoryview(data))
        if self._gone:
            return size

        try:
            return super().write(data)
        except OSError as exc:
            if exc.errno not in _GONE:
                raise

        self._gone = True
        return size


def _guarded(stream: TextIO) -> TextIO:
    stream.flush()
    raw = _VanishingTerminal(stream.fileno())
    return io.TextIOWrapper(
        io.BufferedWriter(raw),
        encoding=stream.encoding,
        errors=stream.errors,
        line_buffering=bool(stream.line_buffering),
    )


@contextmanager
def outliving_its_terminal() -> Iterator[None]:
    """Guard stdout and stderr for the length of the block when SIGHUP is ignored.

    SIGHUP is ignored when a person means the run to go on after the terminal
    closes (as `nohup` leaves it). Without the guard the next line the run prints
    to the closed terminal raises and ends the run. Streams that are not terminals
    (nohup sends them to a file) are left as they are.
    """
    if signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:
        yield
        return

    stdout, stderr = sys.stdout, sys.stderr
    if stdout.isatty():
        sys.stdout = _guarded(stdout)

    if stderr.isatty():
        sys.stderr = _guarded(stderr)

    try:
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout, sys.stderr = stdout, stderr
