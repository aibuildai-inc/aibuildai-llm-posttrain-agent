"""Refresh the Web display and record the run clock at a fixed cadence."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable

from engine.durable_execution import latest_run_state, run_clock
from engine.event.events import RunClockHeartbeat
from engine.builtin.aibuildai.agents.submitter.agent import (
    SubmitterAgent,
    SubmitterStatus,
)

if TYPE_CHECKING:
    from engine.builtin.aibuildai.search import AIBuildAISearch

HEARTBEAT_PERIOD_S: float = 1.0  # public: the Web display reuses this cadence
# An abrupt death can lose up to one interval plus the wakeup delay of the
# tick that would have recorded it.
SETTLEMENT_PERIOD_S: float = 5.0
_SUBMITTER_UPDATE_PERIOD_S: float = 300.0
_logger = logging.getLogger(__name__)

__all__ = [
    "run_heartbeat",
]


def _live_submitter() -> "SubmitterAgent | None":
    """The run's Submitter, if this run has one."""
    for unit in latest_run_state().walk_units():
        if isinstance(unit, SubmitterAgent):
            return unit
    return None


def _report_to_submitter(submitter: "SubmitterAgent") -> None:
    """Tell the Submitter what the run around it is doing.

    The product lifecycle owns these three facts and already owns this role's
    status and finish notices, so it reports them here. The Agent asks the run
    for none of them: a concrete Definition that could read the journal, the
    clock and the product root would be the privileged path this product does
    not grant."""
    run_state = latest_run_state()
    submitter._observe(
        SubmitterStatus(
            versions_pushed=run_state.versions_used,
            own_remaining_s=run_clock().effective_remaining_s(submitter),
            exploration_finished=run_state.aibuildai_search.exploration_finished,
        )
    )


async def _send_submitter_status(submitter: "SubmitterAgent", search_path: str) -> None:
    run_state = latest_run_state()
    clock = run_clock()
    elapsed = clock.run_elapsed_s()
    remaining = clock.exploration_remaining_s()
    time_part = f"{int(elapsed // 60)} min elapsed"
    if remaining < float("inf"):
        time_part += f", {int(remaining // 60)} min remaining"
    # A generic observer derives what it tells the Submitter from the journal
    # itself: how much work the run started under its root and how much of it
    # has ended. Nothing is asked of the algorithm.
    started = ended = 0
    for record in run_state.records.values():
        if record.parent_path != search_path:
            continue
        started += len(record.actions)
        ended += len(record.actions) - len(record.active_actions)
    lines = [
        f"[AIBuildAISearch update, {time_part}]",
        f"Work under this search: {started} Actions started, {ended} finished.",
        "The search is still active.",
    ]
    await submitter.add_runtime_message("\n".join(lines))


async def run_heartbeat(
    search: "AIBuildAISearch",
    *,
    sleep: Callable = asyncio.sleep,
) -> None:
    """Refresh the render clock, settle the run clock, then render."""
    run_state = latest_run_state()
    from engine.builtin.aibuildai.search import product_runtime

    display = product_runtime().display
    clock = run_clock()
    settled_at = clock.run_elapsed_s()
    last_submitter_update = clock.run_elapsed_s()
    submitter: "SubmitterAgent | None" = None
    next_tick = time.monotonic() + HEARTBEAT_PERIOD_S
    try:
        while True:
            await sleep(max(0.0, next_tick - time.monotonic()))
            next_tick += HEARTBEAT_PERIOD_S
            elapsed = clock.run_elapsed_s()
            now = time.time()
            if elapsed - settled_at >= SETTLEMENT_PERIOD_S:
                search.record_event(
                    RunClockHeartbeat,
                    None,
                    elapsed_s=elapsed,
                )
                settled_at = elapsed
            try:
                if submitter is None:
                    submitter = _live_submitter()
                if submitter is not None:
                    # Every tick: the gate this feeds asks about a moving run,
                    # so a report on the 300 s message cadence would answer it
                    # from a view five minutes old.
                    _report_to_submitter(submitter)
                if elapsed - last_submitter_update >= _SUBMITTER_UPDATE_PERIOD_S:
                    if submitter is not None and run_state.search.record.active_actions:
                        await _send_submitter_status(submitter, search.path)
                    last_submitter_update = elapsed
                display.render(
                    run_state,
                    now_relative=elapsed,
                    now_absolute=now,
                )
            except Exception:  # noqa: BLE001 — render is non-essential observability; a failure must never abort the run (see below)
                # Rendering is non-essential observability; a failure here must NEVER abort the
                # whole pipeline. A real run died this way: a worker-thread tree
                # mutation raced walk_units and the RuntimeError propagated out
                # of the heartbeat, through asyncio.run, into cli's except branch
                # — failing a run whose work was already done. Log and keep
                # ticking. CancelledError is BaseException, so cancellation still
                # propagates through the outer handler.
                _logger.exception("heartbeat render failed; continuing")
    except asyncio.CancelledError:
        # asyncio contract: a task that catches CancelledError must re-raise
        # after cleanup so the cancellation propagates to the parent. We have
        # no cleanup here (the next iteration's await sleep is what raised).
        raise
