"""Run one substrate-neutral Program declaration and report its outcome."""

from __future__ import annotations

import abc
from typing import Callable

from infra.exec.outcome import ExecOutcome
from infra.exec.spec import ProgramExecSpec


class Executor(abc.ABC):
    """Program execution boundary; the adapter owns physical placement."""

    @abc.abstractmethod
    async def run_program(
        self,
        spec: ProgramExecSpec,
        *,
        owner_path: str,
        remaining_s: Callable[[], float],
    ) -> ExecOutcome:
        """Place, run, and release one Program attempt."""
        ...
