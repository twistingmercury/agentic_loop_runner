"""What a full-screen run is set up with, and whether it can start yet."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from alr.engine.commit import CommitMode
from alr.engine.sandbox import bypass_args, sandbox_args
from alr.tasks import Gate


class Permissions(StrEnum):
    """How sessions run; UNSET until the user chooses, so nobody runs unsandboxed by accident."""

    UNSET = "unset"
    SANDBOX = "sandbox"
    SKIP = "skip"


@dataclass
class RunSettings:
    """The values the flags and the dialogs fill, and the run is started from."""

    tasks_file: str = ""
    sandbox_file: str = ""
    permissions: Permissions = Permissions.UNSET
    commit_mode: CommitMode = CommitMode.NONE
    logging: bool = True

    reset_failed: bool = False
    """--reset-failed: failed tasks go back to pending when the file is first read."""

    gate_timeout: str = ""
    gates: list[Gate] | None = None
    """Gates edited in the view; None when they were not touched."""

    def missing(self) -> str | None:
        """Say what stops a run from starting, or None when nothing does."""
        if not self.tasks_file:
            return "choose a run folder"

        if self.permissions is Permissions.UNSET:
            return "choose how sessions run"

        if self.permissions is Permissions.SANDBOX and not self.sandbox_file:
            return "choose a sandbox settings file"

        return None

    def session_args(self) -> list[str]:
        """Return the Claude flags for the chosen permissions; the settings file is read here."""
        if self.permissions is Permissions.SKIP:
            return bypass_args()

        return sandbox_args(self.sandbox_file)

    def log_folder(self) -> Path:
        """Where the run's log folder goes: beside the task file."""
        return Path(self.tasks_file).parent / "logs"
