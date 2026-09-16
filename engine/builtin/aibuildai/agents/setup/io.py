"""Input and Output records for the setup Agent family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput


@dataclass(frozen=True)
class SetupInput(AgentInput):
    # The user's whole task folder: the run's only input. Setup is the one
    # role that reads all of it, answers included; every other role reads only
    # the design data setup then declares.
    task_name: str
    # When true, the task folder ships no data: SETUP obtains the data + evaluation
    # spec itself (its system_instructions carry the how) and authors the whole
    # grading contract from scratch. False: the data is already in the task folder
    # and SETUP wraps it.
    setup_from_scratch: bool
    # The task statement for a from-scratch run, straight from run.task_prompt.
    # SETUP copies it verbatim into the run's README. Empty when
    # setup_from_scratch is false, where the statement lives in the task folder.
    task_prompt: str


class SetupOutput(SuccessfulOutput):
    """A successful Setup. Failure travels as the universal ``Failure``, so this
    record carries only the facts that cross the Setup -> run boundary and that
    the framework cannot derive itself: the metric contract, plus optional
    human-readable feedback. What designs read is not declared here -- the
    public dir itself is that record."""

    metric_name: str = Field(
        min_length=1,
        description=(
            "Name of the number your score.py prints, in the user's own "
            "vocabulary where they gave one, e.g. 'accuracy', 'wer', 'abs_error'."
        ),
    )
    metric_direction: Literal["max", "min"] = Field(
        description="Whether a higher ('max') or a lower ('min') score is better."
    )
    feedback: str = Field(
        default="",
        description=(
            "Notable events during environment setup: any data-file "
            "inconsistencies, package install failures resolved, unusual "
            "hardware findings (GPU model, available VRAM), or cases where "
            "the data layout differed from the task instruction. Empty if "
            "everything was uneventful."
        ),
    )


@dataclass(frozen=True)
class SetupReviewInput(AgentInput):
    """Input for the SETUP review -- the review of the run's whole grading contract before any design pays to be scored by it.

    SETUP's deterministic verifier proves the score program RUNS; this reviewer asks whether it scores the right thing. So it reads both what SETUP produced and what SETUP was asked to produce: the task folder is the user's whole task directory, answers included, because auditing a holdout means comparing it against what it was carved from -- this role audits the conditions of the run and never solves the task. The private dir holds the authored ``score.py`` and the holdout answers; the public dir is everything a design can reach, and the two must not overlap. The metric contract travels in the submission this review Action is given, not in a field here. ``task_prompt`` is the authoritative statement the artifacts must agree with; it is empty when the task folder carries the statement instead."""

    task_prompt: str
