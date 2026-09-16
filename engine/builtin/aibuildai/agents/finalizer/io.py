"""Input and Output records for the finalizer Agent family."""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from pydantic import ConfigDict

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput
from engine.builtin.aibuildai.io import DeliveryRecord, SearchOutput


@dataclass(frozen=True)
class FinalizerInput(AgentInput):
    """Input for the FINALIZER. The Search has already selected its Output; this role reads it and writes the deliverable."""

    output: SearchOutput
    # The directory this role writes the deliverable into, frozen at the spawn
    # site by the product shell that owns the run's paths.
    deliverable_dir: str


class FinalizerOutput(SuccessfulOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "description": inspect.cleandoc(
                """What the FINALIZER handed back at the end of the run.

        The result is already selected by the framework, from the scores the
        task's own score program produced. This role only turns the selected
        result's output into the deliverable the run's README asks for, written
        into the run's deliverable directory."""
            )
        }
    )

    delivery: DeliveryRecord
