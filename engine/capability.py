"""One Action's recorded limits.

An ``ExecutionCapability`` belongs to one Action invocation, not to the
identity that answers it. The same Trainer may answer ``train`` with eight
cards and ``evaluate`` with one; those are two Actions with two capabilities,
and neither is a fact about the Trainer. A limit follows the thing it
actually limits, so these fields do not share one owner:

``wall_clock_seconds`` and ``cost_cap_usd`` bound THIS Action. Waiting for a
durable child counts against the waiting Action, because that Action owns the
unfinished orchestration span; a child's own bill and its own clock are its
own. ``retry`` is this Action's Attempt allowance: a later Action on the same
identity gets its own, and Attempts of one Action never reset its elapsed
time, its spend, or its cards.

``cpu_max_cores`` and ``memory_max_gb`` are instantaneous physical ceilings on
this Action's own process level. They do NOT narrow through the ownership
tree: a Manager Action holding 2 cores may start a Worker Action holding 16.
The Run cgroup root is the only aggregate authority, so several Actions may
declare more than the Run in total without a static refusal, and a single
Action larger than a finite Run ceiling is structurally impossible and fails
early.

``gpus`` is written by the author in one of two forms and is recorded in only
one. ``gpus=(0, 2)`` places the Action on exactly those physical cards and
bypasses selection; ``gpus=2`` asks the selector for that many distinct cards;
``gpus=0`` or ``None`` asks for none. Whichever form is written, the Action's
recorded fact is always the exact tuple of NVML physical indices, resolved
once before the Action starts, and reused unchanged by every Attempt and by
Resume. Cards are not owned: two Actions may legally share one card.

Declarative, not a reservation. The kernel cgroup tree stays the physical
backstop.
"""

from __future__ import annotations

# The public capability surface.
__all__ = ["ExecutionCapability"]

import math

from pydantic import BaseModel, ConfigDict, field_validator


class ExecutionCapability(BaseModel):
    """Immutable resource declaration for one Action invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wall_clock_seconds: float | None = None
    cost_cap_usd: float | None = None
    cpu_max_cores: float | None = None
    memory_max_gb: float | None = None
    gpus: int | tuple[int, ...] | None = None
    retry: int = 0

    def model_copy(  # pyright: ignore[reportIncompatibleMethodOverride] -- narrows the update mapping on purpose.
        self,
        *,
        update: "dict[str, object] | None" = None,
        deep: bool = False,
    ) -> "ExecutionCapability":
        """Copy with changes, refusing a field this capability does not have.

        Pydantic applies ``update`` without validating it, so a key that is not
        a field becomes a silent attribute and the real field keeps its old
        value. That is how a renamed limit disappears: the copy looks right,
        the Action starts, and the work runs with no card."""
        if update:
            unknown = sorted(set(update) - set(type(self).model_fields))
            if unknown:
                raise ValueError(
                    f"ExecutionCapability has no field {', '.join(unknown)}; "
                    f"its fields are {', '.join(type(self).model_fields)}"
                )
        return super().model_copy(update=update, deep=deep)

    @classmethod
    def model_construct(
        cls,
        _fields_set: "set[str] | None" = None,
        **values: object,
    ) -> "ExecutionCapability":
        """Refused: it skips validation, so it is the other way a limit vanishes."""
        del _fields_set, values
        raise TypeError("ExecutionCapability.model_construct is disabled")

    @property
    def gpu_indices(self) -> tuple[int, ...]:
        """The exact physical cards this Action holds.

        Reading this refuses an unresolved count, so a caller can never treat
        "two cards, host's choice" as if it were a placement."""
        if self.gpus is None:
            return ()
        if isinstance(self.gpus, int):
            raise AssertionError(
                f"gpus={self.gpus} is still a request for that many cards; a "
                "started Action carries the exact physical indices instead"
            )
        return self.gpus

    @field_validator("wall_clock_seconds")
    @classmethod
    def _wall_clock_positive(cls, v: float | None) -> float | None:
        if v is not None and (not math.isfinite(v) or v <= 0):
            raise ValueError(f"wall_clock_seconds must be finite and > 0, got {v}")
        return v

    @field_validator("cost_cap_usd")
    @classmethod
    def _cost_cap_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"cost_cap_usd must be > 0, got {v}")
        return v

    @field_validator("cpu_max_cores")
    @classmethod
    def _cpu_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"cpu_max_cores must be > 0, got {v}")
        return v

    @field_validator("memory_max_gb")
    @classmethod
    def _memory_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"memory_max_gb must be > 0, got {v}")
        return v

    @field_validator("gpus")
    @classmethod
    def _gpus_well_formed(
        cls, v: int | tuple[int, ...] | None
    ) -> int | tuple[int, ...] | None:
        if v is None:
            return v
        if isinstance(v, int):
            if v < 0:
                raise ValueError(f"gpus must ask for >= 0 cards, got {v}")
            return v
        if any(index < 0 for index in v):
            raise ValueError(f"gpus names a negative physical index: {v}")
        if len(set(v)) != len(v):
            raise ValueError(f"gpus names the same physical card twice: {v}")
        return v

    @field_validator("retry")
    @classmethod
    def _retry_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"retry must be >= 0, got {v}")
        return v


def level_ceilings(cap: ExecutionCapability) -> dict[str, int | None]:
    """One Action's own physical ceilings as resource-tree arguments."""
    return {
        "memory_max_bytes": (
            None if cap.memory_max_gb is None else int(cap.memory_max_gb * 1024**3)
        ),
        "cpu_max_millicores": (
            None if cap.cpu_max_cores is None else int(cap.cpu_max_cores * 1000)
        ),
    }
