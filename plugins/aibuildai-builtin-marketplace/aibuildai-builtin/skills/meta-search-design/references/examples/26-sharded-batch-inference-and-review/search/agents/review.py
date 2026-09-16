"""The role that interprets merged shard evidence after coverage is validated."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ReviewInput, ReviewOutput
from .policy import REVIEW_POLICY


class InferenceReviewAgent(Agent[ReviewInput, ReviewOutput]):
    """Accept the round or explain why the next policy round should differ."""

    name = "inference_review"
    policy = REVIEW_POLICY
    prompt_template = "agent/review.j2"
