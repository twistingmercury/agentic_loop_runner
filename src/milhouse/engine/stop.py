"""Which signal stopped the run, so the exit code and the run log can say so."""

import signal
from dataclasses import dataclass


@dataclass
class Stop:
    """The signal that asked the run to stop; SIGINT until one arrives."""

    signum: int = signal.SIGINT

    def exit_code(self) -> int:
        """The shell's code for a process ended by this signal."""
        return 128 + self.signum
