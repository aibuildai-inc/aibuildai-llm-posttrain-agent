"""Pure helpers shared by the Web view and the run report."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal
from engine.work_unit.agent.base import Agent
from engine.builtin.aibuildai.agents.finalizer.agent import FinalizerAgent
from engine.run_state import RunState
from engine.builtin.aibuildai.agents.finalizer.io import FinalizerOutput
from engine.durable_execution import action_output
from engine.status import StatusState
from output.status_presentation import presentation_for


def agent_status(agent: Agent) -> StatusState:
    """The state one Agent shows: its open Action, else its latest settled one.

    A presentation over the Agent's Action records; nothing stores it."""
    actions = agent.record.actions
    if not actions:
        return StatusState.PENDING
    active = agent.record.active_actions
    return StatusState.of_action(active[0] if active else actions[-1])

_ROLE_ORDER = {
    # The Units panel sorts rows by this order, so a composite's Coder leads, its
    # verifier reads right after it, and then come the composite's own subprocess
    # units (trainer, score program): the Coder is the work a reader looks at
    # first, and those results only make sense beside it.
    # router: routing runs before any per-execution work, so it leads
    # everywhere.  A negative value guarantees it precedes coder=0.
    "router": -1,
    "coder": 0,
    "trainer": 0.7,  # Training Program
    "score_program": 0.95,
    "coder_review": 0.5,  # right after its coder
    "judge": 1,
    "reviser": 2,
    "reviser_review": 2.1,
    "selector": 3,
    "designer": 4,
    "designer_review": 4.1,
    "setup": 5,
    "aggregator": 7,
    "finalizer": 7.15,
    "writer": 7.2,
    "writer_review": 7.3,
}


def _status_label_for_agent(agent: Agent) -> tuple[str, str, str]:
    """(status_label, status_color_token, status_symbol) for an LLM agent. Every state uses the shared status presentation. The symbol is the shared dot (`◉`), so a unit's Status cell matches a composite's tree-dot and summary."""
    pres = presentation_for(agent_status(agent))
    return (pres.label, pres.token, pres.symbol)


def cost_includes_in_flight_estimate(agents: Iterable[Agent]) -> bool:
    """The displayed cumulative cost is a provisional estimate iff a RUNNING agent carries a non-zero live-priced cost the SDK has not reconciled yet. That live cost is priced ONCE per turn (message_id), so it is a lower bound the SDK reconciles UP (or leaves unchanged) at TurnComplete — NOT the old ~2x over-count that reconciled DOWN and made the displayed total shrink. Look ONLY at RUNNING agents' own spend: a settled earlier agent's exact cost must not be marked an estimate just because a later agent merely went RUNNING at $0. Shared by the header (compute_header) and the Cost by Model panel (compute_cost) so both surfaces apply the same `est` marker to the same number, so the Cost by Model table cannot miss the header's marker."""
    return any(
        a.cost.total_cost_usd > 0.0
        for a in agents
        if agent_status(a) is StatusState.RUNNING
    )


def _ctx_pct(tokens: int, window: int | None) -> float | None:
    # window None == context window unknown: return None so the cell shows the
    # raw used-token count instead of a usage bar with a fabricated denominator.
    if window is None:
        return None
    return tokens / window * 100 if tokens > 0 and window > 0 else 0.0


def _resolve_metric_direction(run_state: RunState) -> Literal["max", "min"]:
    """The recorded direction, with a display-only startup placeholder."""
    contract = run_state.metric_contract
    return contract.metric_direction if contract is not None else "max"


def _resolve_metric_name(run_state: RunState) -> str:
    """The recorded metric name, with a display-only startup placeholder."""
    contract = run_state.metric_contract
    return contract.metric_name if contract is not None else "score"


def declared_delivered_paths(run_state: "RunState | None") -> tuple[str, ...]:
    """The files the run handed back, as the finalizer itself declared them.

    The finalizer names every file it wrote, and its own gate has already refused a declaration that names nothing, names a file that is missing, names a file outside the deliverable dir, or names an empty one. So the declaration IS the delivery, and reading it is how a surface reports what the run shipped.

    Asking the deliverable dir instead -- "does it hold anything" -- answers a wider question than the one being asked. A stray note or a leftover log answers it just as well as a real result, so a run that delivered nothing reads as a run that delivered. Every surface reads this one function, so the exit report and the live RUN tab cannot disagree.

    Empty when no finalizer output exists: not started, still running, or failed. Nothing was declared, so nothing was delivered.
    """
    if run_state is None:
        return ()
    finalizers = run_state.work_units_of_type(run_state.search, FinalizerAgent)
    if len(finalizers) > 1:
        raise AssertionError("a Search may have only one Finalizer")
    if not finalizers:
        return ()
    finalizer = finalizers[0]
    output = None if not finalizer.record.actions else action_output(finalizer.record, 1)
    if output is None or output.failed:
        return ()
    if not isinstance(output, FinalizerOutput):
        raise AssertionError(
            f"the finalizer unit holds {type(output).__name__}, so what the "
            f"run delivered cannot be read"
        )
    return output.delivery.relative_paths
