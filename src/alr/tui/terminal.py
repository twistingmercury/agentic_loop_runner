"""The view's terminal output, kept from freezing alr when the terminal goes away."""

from textual.drivers import linux_driver
from textual.drivers._writer_thread import WriterThread


class HangupSafeWriterThread(WriterThread):
    """Textual's output thread, except that a terminal that has gone is not fatal.

    Textual's own thread ends at the first write error. Nothing then empties its
    queue, which holds only a few writes, so the next write from the view blocks
    for good and so does shutting the view down: alr can no longer be stopped.
    That happens when the terminal closes while SIGHUP is ignored (as `nohup`
    leaves it) and the run is meant to go on without it. This thread notes that
    the terminal is gone, stops writing, and keeps taking what the view sends.
    """

    def run(self) -> None:
        terminal_gone = False
        while True:
            text = self._queue.get()
            if text is None:
                break

            if terminal_gone:
                continue

            terminal_gone = not self._write(text)

        self._flush()

    def _write(self, text: str) -> bool:
        """Write text and flush at a break in the output; False once the terminal is gone."""
        try:
            self._file.write(text)
            if self._queue.qsize() == 0:
                self._file.flush()
        except (OSError, ValueError):
            return False

        return True

    def _flush(self) -> None:
        try:
            self._file.flush()
        except (OSError, ValueError):
            return


def use_hangup_safe_writer() -> None:
    """Make Textual's Linux driver write through HangupSafeWriterThread.

    The driver builds its output thread by the name WriterThread in its own
    module, with no way to hand it another, so the name is replaced there.
    """
    setattr(linux_driver, "WriterThread", HangupSafeWriterThread)
