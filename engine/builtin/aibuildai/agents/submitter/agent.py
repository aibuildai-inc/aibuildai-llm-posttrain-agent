"""SubmitterAgent: the role that spends an external submission budget.

It runs beside the search when ``submission.max_versions`` is above 1. It owns how a scarce budget of EXTERNAL submissions is spent across the designs the search produced. The Finalizer still owns the deliverable the user receives.

The framework still chooses the winner from the local score program; this role never re-picks it. What it does choose is where the submission budget goes, which is a different question -- the local score ranks designs, but only the external oracle scores them, and that oracle is rate limited.

The Finalizer's deliverable gate is unchanged: a run either hands the user something or fails.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from dataclasses import dataclass

from pydantic import PrivateAttr

from engine.work_unit.agent.base import Agent
from engine.builtin.aibuildai.agents.submitter.io import SubmitterInput, SubmitterOutput
from engine.work_unit.agent.policy import ALL_BUILTIN_TOOLS, RolePolicy

if TYPE_CHECKING:
    from config import AgentConfig

logger = logging.getLogger(__name__)

_KEEP_WAITING = (
    "Search is still active and unpushed version slots remain. Keep checking "
    "for designs. Call StructuredOutput after the explicit search-finished "
    "notice or after every version slot is used."
)

SUBMITTER_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
    task_environment=True,
)


@dataclass(frozen=True)
class SubmitterStatus:
    """What the product lifecycle tells the Submitter about the run around it.

    Three facts this role needs and cannot hold: the pushed-version count moves
    while it runs, its own remaining time moves with it, and the exploration
    ends outside it. They are not frozen into its Input, because they are not
    creation facts; they are observations, and the product lifecycle that
    already owns this role's status and finish notices reports them here."""

    versions_pushed: int
    own_remaining_s: float | None
    exploration_finished: bool


class SubmitterAgent(Agent[SubmitterInput, SubmitterOutput]):
    name: ClassVar[str] = "submitter"
    prompt_template = "agent/submitter.j2"
    policy = SUBMITTER_POLICY

    def prompt_facts(self) -> dict[str, object]:
        """Its own record file, and the Search tree the designs it reports on land in."""
        # Imported here, not at module scope: the product shell imports this
        # role, so the arrow back to it exists only while a run is served.
        from engine.builtin.aibuildai.search import product_runtime

        search = self.parent
        if search is None:
            raise AssertionError("the SUBMITTER runs under the product Search")
        return {
            "external_scores_path": product_runtime().run_paths.external_scores_path,
            "search_dir": search.directory,
        }

    @classmethod
    def enabled_in(cls, configs: "AgentConfig") -> bool:
        return configs.submitter_on

    # How much of its own budget this role keeps for finishing up once it stops
    # waiting for the Search.
    _FINALIZE_FRACTION: ClassVar[float] = 0.2

    # The product's latest report. Live, so it is never journaled: a
    # conversation gate replayed from a stale view of a moving run would answer
    # a question about a moment that has passed.
    _status: "SubmitterStatus | None" = PrivateAttr(default=None)

    def _observe(self, status: "SubmitterStatus") -> None:
        """Take the product lifecycle's latest report of the run around this role.

        Engine-internal, like every other member this role adds: a concrete
        Agent's public surface is the one its base defines, and this channel
        runs product -> role, never role -> run."""
        self._status = status

    def _unfinished_work_notice(self) -> str | None:
        """Keep the Submitter in session while the Search can still produce designs.

        This role's job is not one answer but a whole spend of the external
        submission budget, so a finished-sounding turn is not the same as a
        finished role. The journal owns the pushed count
        (``NotebookVersionPushed`` -> ``RunState.versions_used``), so this
        restates no number: it holds the Submitter in its session while the
        Search is still active, unpushed version slots remain, and its own time
        is not nearly gone. Nothing here judges what the model submitted."""
        status = self._status
        if status is None:
            # The product has not reported yet, so nothing says the search is
            # over. This role's job is to stay while the search can still
            # produce designs, and the product cancels it when that ends.
            return _KEEP_WAITING
        if status.versions_pushed > self.input.max_versions:
            raise AssertionError("pushed versions exceed this run's limit")
        budget_s = self.capability.wall_clock_seconds
        budget_nearly_gone = (
            budget_s is not None
            and status.own_remaining_s is not None
            and status.own_remaining_s <= budget_s * self._FINALIZE_FRACTION
        )
        if (
            status.exploration_finished
            or status.versions_pushed >= self.input.max_versions
            or budget_nearly_gone
        ):
            return None
        return _KEEP_WAITING
