"""The role that supervises the parent, scores it, and lays out what the judge is measured on."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ParentInput, ParentOutput
from .policy import PARENT_POLICY


class ParentAgent(Agent[ParentInput, ParentOutput]):
    """Fine-tune and score the parent, write the prompt set, and build the graded calibration pairs."""

    name = "parent"
    policy = PARENT_POLICY
    prompt_template = "agent/parent.j2"
