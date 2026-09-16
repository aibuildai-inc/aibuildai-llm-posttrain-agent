"""The role that checks the change and is the only role allowed to finish."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SpecialistInput, SpecialistOutput
from .policy import REVIEW_POLICY


class ReviewAgent(Agent[SpecialistInput, SpecialistOutput]):
    """Check the change against the objective; finish it or send it back."""

    name = "review"
    policy = REVIEW_POLICY
    prompt_template = "agent/review.j2"
