"""The reducer: combine every package review into one report."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ReviewReducerInput, ReviewReducerOutput
from .policy import REDUCER_POLICY


class ReviewReducerAgent(Agent[ReviewReducerInput, ReviewReducerOutput]):
    """Read every mapped package and write the one combined report."""

    name = "review_reducer"
    policy = REDUCER_POLICY
    prompt_template = "agent/reducer.j2"
