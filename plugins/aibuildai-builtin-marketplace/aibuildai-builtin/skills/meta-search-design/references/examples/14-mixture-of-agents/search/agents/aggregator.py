"""The role that reads the full candidate slate and picks the winner."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import AggregatorInput, AggregatorOutput
from .policy import AGGREGATOR_POLICY


class AggregatorAgent(Agent[AggregatorInput, AggregatorOutput]):
    """Choose one candidate against the rubric; never the last one by default."""

    name = "aggregator"
    policy = AGGREGATOR_POLICY
    prompt_template = "agent/aggregator.j2"
