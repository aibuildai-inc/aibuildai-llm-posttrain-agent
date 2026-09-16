"""The shared attempt-output gate every role that writes a scored attempt runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict

from engine.durable_execution import action
from engine.execution_output import VerifierOutput
from engine.failure import Failure
from engine.work_unit.program.base import Program


@dataclass(frozen=True)
class AttemptVerifierInput:
    """The directory a role promised to fill with its scored attempt."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    attempt_dir: str


class AttemptVerifierProgram(Program[AttemptVerifierInput]):
    """Require that the role actually produced the attempt it says it produced.

    Every role that writes one owes the same thing, so one Program asks it: a
    Worker's attempt and an ensemble are both scored from a directory the
    role promised to fill, and a role that
    left it empty can still write it before leaving its session.

    What the attempt CONTAINS is not asked here. The task's own score program
    is the one authority on whether an attempt is worth anything, and it runs
    over this same directory straight after; a shape this Program guessed at
    from directory names would be a second, weaker opinion in front of it."""

    name: ClassVar[str] = "attempt_verifier"

    @action
    def run(self) -> VerifierOutput | Failure:
        attempt = Path(self.input.attempt_dir)
        if not attempt.is_dir() or not any(attempt.iterdir()):
            return VerifierOutput(
                passed=False,
                reason=(
                    f"the attempt directory {attempt} is missing or empty, so "
                    f"this work produced nothing to score. The run scores "
                    f"exactly that directory. Write your output to {attempt} "
                    "and call StructuredOutput again."
                ),
            )
        return VerifierOutput(passed=True, reason="")
