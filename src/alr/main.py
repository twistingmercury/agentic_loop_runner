"""Entry point for the alr command."""

import os
import sys
from typing import TextIO

from alr import cli


def main() -> None:
    code = cli.main()
    _flush(sys.stdout)
    _flush(sys.stderr)
    sys.exit(code)


def _flush(stream: TextIO) -> None:
    """Flush stream now, so a dead terminal cannot change the exit code later.

    Python exits 120 when flushing stdout or stderr fails at shutdown. When the
    flush fails here, the stream's descriptor is pointed at os.devnull so the
    flush at shutdown has nothing to lose.
    """
    try:
        stream.flush()
    except OSError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, stream.fileno())
        os.close(devnull)
