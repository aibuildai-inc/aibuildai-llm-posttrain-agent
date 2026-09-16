"""The common failed Output of every durable execution."""

from __future__ import annotations

# The public Failure surface.
__all__ = ["Failure", "FailureKind"]

from enum import StrEnum

from typing import Literal

from pydantic import Field

from engine.execution_output import ExecutionOutput


class FailureKind(StrEnum):
    """The closed reason categories that change product behavior."""

    TIMEOUT = "timeout"
    UNEXPECTED = "unexpected"
    PERMANENT = "permanent"
    COST_LIMIT = "cost_limit"
    NO_OUTPUT = "no_output"
    SUBMISSION_REVIEW = "submission_review"
    CRASH = "crash"
    ARTIFACT_MISSING = "artifact_missing"
    MEMORY_CAP = "memory_cap"
    INFRA = "infra"
    GPU_UNUSED = "gpu_unused"
    RATE_LIMITED = "rate_limited"


class Failure(ExecutionOutput[Literal[True]]):
    """The terminal Output of one failed durable execution."""

    failed: Literal[True] = True
    kind: FailureKind
    reason: str = Field(min_length=1)
    code: str | None = None
