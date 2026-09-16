"""The one reader of budget facts, and the public shapes it fills.

Two questions are asked of the same accounting: what the whole exploration
has left, and what one execution the caller owns has left. Both answers come
from here, so an adapter -- the Search's durable step today, an Agent tool
later -- never carries a formula of its own. The reader is internal: it reads
the Clock, the run state, and the execution records that generated code never
sees.

It reads only. It never derives a third budget: the exploration budget and one
identity's own limits are the whole set, and neither is folded into the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from engine.durable_execution import DurableExecution
    from engine.run_state import RunState
    from infra.util.clock import Clock


class BudgetSnapshot(BaseModel):
    """One durable observation of the run's exploration budget.

    The wall-clock fields are seconds of exploration time: the window opens
    when exploration starts (Setup is outside it) and closes when exploration
    finishes (the Finalizer and later product work are outside it). The cost
    fields are the tracked LLM USD of the Agent invocations whose recorded
    scope belongs to the exploration. That scope is decided when an
    invocation is created: a child of the run root created while the window
    is open belongs to the exploration, and deeper children inherit their
    owner's scope. So Setup and its verifier, the run-level Router before
    it, and the Submitter created before the window are outside, as are the
    Finalizer and the final writeup after it, while a per-execution Router
    created inside the exploration is counted -- the same class can be inside
    the budget in one invocation and outside it in another.
    ``cost_limit_usd`` is None when no global cost limit is configured, and
    then ``cost_remaining_usd`` is None too. Remaining values are clamped at
    zero."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wall_clock_limit_s: float
    wall_clock_used_s: float
    wall_clock_remaining_s: float
    cost_limit_usd: float | None
    cost_used_usd: float
    cost_remaining_usd: float | None


class ExecutionBudgetSnapshot(BaseModel):
    """One durable observation of what the reader asked about, and nothing else.

    Two scopes fill this shape, and a reader always gets the one it named.
    Naming an Action answers about that occurrence: the cap it was started
    with, the time its own Attempts really ran, and the charges made for it.
    Naming an identity answers about its whole life: the lifetime totals it
    is configured with, the sum of its Action spans, and its whole bill.
    Neither scope is ever mixed with the other, and neither is guessed from
    what happens to be running.

    Time is owned work, so the local budget is not the whole answer: a child
    cannot outlive an ancestor, and ``wall_clock_effective_remaining_s`` is
    what survives the exploration window and every unpaused ancestor
    remainder.

    Cost has no effective remainder: no ancestor cap reaches the scope asked
    about and no descendant bill spends it. The one other cost limit is the
    global exploration budget, which stops NEW exploration work rather than
    this Agent, and ``cost_charges_exploration_budget`` says whether this
    work counts toward it. Read that budget with the exploration snapshot.

    ``None`` means that dimension has no finite limit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wall_clock_local_limit_s: float | None
    wall_clock_local_used_s: float
    wall_clock_local_remaining_s: float | None
    wall_clock_effective_remaining_s: float | None

    cost_local_cap_usd: float | None
    cost_local_used_usd: float
    cost_local_remaining_usd: float | None

    cost_charges_exploration_budget: bool


def _finite(value: float) -> float | None:
    """Say "no finite limit" the way the public models say it."""
    return None if value == float("inf") else value


class BudgetReader:
    """Read budget facts from the run's own accounting. Never writes."""

    def __init__(self, clock: "Clock", run_state: "RunState") -> None:
        self._clock = clock
        self._run_state = run_state

    def exploration(self) -> BudgetSnapshot:
        """The whole exploration's limit, used, and remaining, in both dimensions."""
        started_s, _ = self._run_state.exploration_window()
        if started_s is None:
            raise AssertionError("the exploration budget starts with the exploration")
        record = self._run_state.require_run_config()
        limit_s = record.run_budget_s
        used_s = self._clock.exploration_elapsed_s()
        return BudgetSnapshot(
            wall_clock_limit_s=limit_s,
            wall_clock_used_s=used_s,
            wall_clock_remaining_s=max(0.0, limit_s - used_s),
            cost_limit_usd=record.cost_budget_usd,
            cost_used_usd=self._run_state.exploration_cost_used_usd(),
            cost_remaining_usd=self._run_state.exploration_cost_remaining_usd(),
        )

    def execution(
        self, target: "DurableExecution", ordinal: int | None = None
    ) -> ExecutionBudgetSnapshot:
        """What one exact Action has used, or what one whole identity has.

        ``ordinal`` names the occurrence and answers about it alone: its own
        cap, its own Attempts' time, its own charges. With no ordinal the
        answer is the identity's roll-up over every Action it has run, read
        against the lifetime totals its configuration gives it. A caller that
        means one Action carries that occurrence here; nothing is inferred
        from which Action happens to be running, so a restored execution --
        a rendered frame, an earlier revision, a Web row -- answers exactly
        as the live one does."""
        record = target.record
        if ordinal is None:
            limit_s = target.identity_wall_clock_seconds()
            used_s = self._clock.identity_elapsed_s(target)
            remaining_s = self._clock.identity_remaining_s(target)
            cap_usd = target.identity_cost_cap_usd()
        else:
            capability = record.action(ordinal).capability
            limit_s = capability.wall_clock_seconds
            used_s = self._clock.local_elapsed_s(target, ordinal)
            remaining_s = self._clock.local_remaining_s(target, ordinal)
            cap_usd = capability.cost_cap_usd
        used_usd = self._run_state.own_cost_usd(record.path, ordinal)
        return ExecutionBudgetSnapshot(
            wall_clock_local_limit_s=limit_s,
            wall_clock_local_used_s=used_s,
            wall_clock_local_remaining_s=_finite(remaining_s),
            wall_clock_effective_remaining_s=_finite(
                self._clock.effective_remaining_s(target, ordinal)
            ),
            cost_local_cap_usd=cap_usd,
            cost_local_used_usd=used_usd,
            cost_local_remaining_usd=(
                None if cap_usd is None else max(0.0, cap_usd - used_usd)
            ),
            cost_charges_exploration_budget=record.charges_exploration_budget,
        )
