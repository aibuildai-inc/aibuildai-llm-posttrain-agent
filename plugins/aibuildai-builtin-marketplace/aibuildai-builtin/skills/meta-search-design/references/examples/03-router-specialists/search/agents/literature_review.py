"""The specialist for tasks about prior work: papers, methods, comparisons."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SpecialistInput, SpecialistOutput
from .policy import SPECIALIST_POLICY


class LiteratureReviewAgent(Agent[SpecialistInput, SpecialistOutput]):
    """Survey prior work relevant to the objective; report what it establishes."""

    name = "literature_review"
    policy = SPECIALIST_POLICY
    prompt_template = "agent/literature_review.j2"
