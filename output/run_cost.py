"""Run cost: the single authoritative derivation used by the Web view and report."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.work_unit.agent.base import Agent
from output.run_helpers import cost_includes_in_flight_estimate
from infra.util.formatting import canonical_model_name

if TYPE_CHECKING:
    from engine.run_state import RunState


def aggregate_cost_by_model(agents: "Iterable[Agent]") -> tuple[dict[str, float], dict[str, int]]:
    """Sum per-model cost / turns across agents. Each BaseAgent carries cost.cost_by_model (priced-per-model live, reconciled to SDK truth on TurnComplete). Shared by the live Cost by Model panel and the Final Summary.

    Keys are canonicalized (``canonical_model_name``) so a model's aliased request id (``claude-haiku-4-5``) and the dated snapshot the SDK reports back (``claude-haiku-4-5-20251001``) collapse to ONE row instead of two that both display as 'haiku'."""
    cost_by_model: dict[str, float] = {}
    turns_by_model: dict[str, int] = {}
    for a in agents:
        for m, v in a.cost.cost_by_model.items():
            k = canonical_model_name(m)
            cost_by_model[k] = cost_by_model.get(k, 0.0) + v
        for m, n in a.cost.turns_by_model.items():
            k = canonical_model_name(m)
            turns_by_model[k] = turns_by_model.get(k, 0) + n
    return cost_by_model, turns_by_model


@dataclass(frozen=True)
class RunCost:
    """The run's single authoritative cost decomposition. Every cost surface — masthead header, GLOBAL footer, ``cost`` tab, Final Summary — derives from THIS, so they cannot disagree (a cross-surface mismatch, in either direction).

    ``work_by_model`` / ``work_total`` are the tracked agents' own LLM spend, the per-model breakdown and its sum. The subagent router is a standard tracked Agent (``kind == "llm_agent"``), so its model-selection spend is one of these work rows — settled by the SDK through its own ``cost`` like every other Agent.

    ``unattributable`` is ``work_total - sum(work_by_model.values())``: real SDK-billed dollars with NO per-model row. On the Anthropic-native endpoint the SDK's authoritative per-invocation costUSD captures hidden side-queries (e.g. ``tool_use_summary_generation``) that the per-message stream never attributes to a model, so an agent legitimately settles with ``total_cost_usd > sum(cost_by_model)`` (Cost.settle_turn, the Anthropic trust branch branch). The figure is surfaced as its own ``cost``-tab line so the per-model rows plus it reconcile to ``work_total``; it is NOT asserted to zero — that former hard guard fired on EVERY Web refresh of such a run and killed it. On a clean run / third-party gateway (cost settled locally per model) the gap is ~0 and no line shows. The gap is non-negative in practice: the gateway path settles per-model and headline from the SAME locally-priced amounts (gap 0 by construction), and the Anthropic SDK's per-invocation costUSD is >= the sum of its per-model costUSD rows (the side-query is extra billed spend, never an over-count of a model row).

    ``headline_total == work_total`` — the one figure the masthead, footer, and the ``cost`` tab's ``total`` row all show."""
    work_by_model: dict[str, float]
    work_total: float
    headline_total: float
    turns_by_model: dict[str, int]
    unattributable: float
    is_estimate: bool


def compute_run_cost(run_state: "RunState") -> RunCost:
    """Derive the run's cost ONCE; every surface reads this instead of re-summing its own total (which drifted). The router is a standard ``llm_agent`` unit, so its spend is in ``agents`` here like any other agent."""
    agents = [u for u in run_state.walk_units() if isinstance(u, Agent)]
    work_by_model, turns_by_model = aggregate_cost_by_model(agents)
    work_total = sum(a.cost.total_cost_usd for a in agents)
    headline_total = work_total
    # The per-model WORK breakdown can sum to LESS than the work total: the
    # Anthropic SDK's per-invocation costUSD captures hidden side-queries (e.g.
    # tool_use_summary_generation) that never surface as their own per-model row
    # (Cost.settle_turn). That difference is the run's Unattributable
    # spend — real billed dollars with no model attribution — surfaced as an
    # explicit `cost`-tab line rather than asserted to zero (the former hard guard
    # fired on every refresh of such a run and killed it).
    unattributable = work_total - sum(work_by_model.values())
    return RunCost(
        work_by_model=work_by_model,
        work_total=work_total,
        headline_total=headline_total,
        turns_by_model=turns_by_model,
        unattributable=unattributable,
        is_estimate=cost_includes_in_flight_estimate(agents),
    )
