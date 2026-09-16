"""Domain-declared status words rendered by output surfaces: Action occurrence states and the whole pipeline's outcome."""

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.durable_execution import ActionRecord


class StatusState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @classmethod
    def of_action(cls, action: "ActionRecord") -> "StatusState":
        """Derive the display state of one exact Action occurrence.

        A presentation over the Action's own facts; no identity stores it."""
        if action.output is not None:
            return cls.FAILED if action.output.get("failed") else cls.DONE
        if action.attempts:
            return cls.RUNNING
        return cls.PENDING


class TerminalStatus(str, Enum):
    """The declared outcome of the whole pipeline."""

    COMPLETED = "completed"
    EMPTY = "empty"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    FAILED_UNEXPECTED = "failed_unexpected"

    @property
    def display_word(self) -> str:
        if self is TerminalStatus.FAILED_UNEXPECTED:
            return "UNEXPECTED"
        return self.value.upper()
