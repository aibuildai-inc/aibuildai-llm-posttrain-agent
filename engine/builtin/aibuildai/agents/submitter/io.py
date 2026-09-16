"""Input and Output records for the submitter Agent family."""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput


@dataclass(frozen=True)
class ExternalScore:
    """One external oracle result for one design, as ADVISORY context.

    Never a ranking input. The run's own score program remains the only authority that orders designs; this is what a rate-limited external service said about one of them, so a later design can see where the local proxy and the real oracle disagreed.

    ``score`` is None when a submission was made but no result had returned yet. That is a different fact from "scored badly", and the roles that read this are told to treat it as such -- collapsing the two would invent a bad result out of a pending one.

    ``design_path`` is None when the submission did not come from a design at all. The SUBMITTER may build its own design -- retraining a method on the FULL data with no holdout is explicitly sanctioned, and nothing it builds is ever scored locally -- so a submission frequently has no design to name.
    """

    design_path: str | None
    score: float | None
    note: str


@dataclass(frozen=True)
class SubmitterInput(AgentInput):
    """Input for the SUBMITTER, which runs CONCURRENTLY with the search.

    It is deliberately given no winner: it starts before any design has finished and spends a rate-limited external budget as designs appear, because an external oracle can take tens of minutes to return a score and a run cannot afford to serialise several of those after the search ends. So it watches the run workspace tree (``run_workspace``) for scored design output rather than being handed one design.

    The Finalizer still owns the run's deliverable; this role owns only the external budget."""

    metric_name: str
    metric_direction: str
    max_versions: int


class SubmitterOutput(SuccessfulOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "description": inspect.cleandoc(
                """What the SUBMITTER did with the run's external submission budget.

        This role does not deliver -- the Finalizer owns the deliverable. The
        framework already records every pushed notebook version in the journal,
        and every external result lands in this role's own append-only record
        as it arrives. This Output adds only the one thing neither record can
        hold: the role's own reading of how the budget was spent."""
            )
        }
    )

    summary: str = Field(
        description=(
            "Short plain description of how you spent the budget: what you "
            "learned from any external scores that returned, and anything still "
            "pending when you finished."
        ),
    )
