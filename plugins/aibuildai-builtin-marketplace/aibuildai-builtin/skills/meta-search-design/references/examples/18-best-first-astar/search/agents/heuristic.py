"""The role that estimates h: compute a lineage still needs to reach the target."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import HeuristicInput, HeuristicOutput
from .policy import HEURISTIC_POLICY


class HeuristicAgent(Agent[HeuristicInput, HeuristicOutput]):
    """Estimate remaining compute for one child that missed the target."""

    name = "heuristic"
    policy = HEURISTIC_POLICY
    prompt_template = "agent/heuristic.j2"
