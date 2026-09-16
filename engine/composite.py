"""The Composite family: one durable orchestration class over child executions.

A Composite is an ordinary public class. It owns a typed Input, exposes typed
Actions, and gets a durable identity, ownership, replay, and cancellation from
the shared base, and nothing else: no override point, no resource claim, and
no runtime state of its own. Its class ``module:qualname`` is its durable type.
"""

from __future__ import annotations

__all__ = ["Composite"]

import re
from typing import ClassVar, Generic, TypeVar


from engine.capability import ExecutionCapability
from engine.durable_execution import DurableExecution

CompositeInputT = TypeVar("CompositeInputT", covariant=True)


class Composite(DurableExecution[CompositeInputT], Generic[CompositeInputT]):
    """One orchestration over durable children, itself a durable identity.

    Declare Actions with ``@action``; ``run`` is the conventional one::

        class EvaluateCandidate(Composite[CandidateInput]):
            @action
            async def run(self) -> CandidateOutput:
                ...

    A Composite owns no resources, so an Action on it declares none: a caller
    cannot put a GPU, memory, CPU, sandbox, or retry policy on a container and
    have it bind every WorkUnit below. Declare what is used on those units."""

    _intermediate: ClassVar[bool] = True

    @classmethod
    def _check_action_capability(cls, capability: ExecutionCapability) -> None:
        if capability != ExecutionCapability():
            raise ValueError(
                f"{cls.__name__} only contains work and spends nothing itself, "
                "so its Action declares no capability. Declare what is used "
                "on the WorkUnits inside it."
            )

    @classmethod
    def uid_name(cls) -> str:
        # The class's own name in path spelling, so every path, log line, and
        # Web card says what this Composite does: ``CoderCandidate`` numbers
        # its siblings as ``coder_candidate_N``.
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", cls.__name__).lower()
