"""Stage A: read the task and data, decide the plan."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import PlannerInput, PlannerOutput
from .policy import PLANNER_POLICY


class PlannerAgent(Agent[PlannerInput, PlannerOutput]):
    """Inspect the data and write one complete modeling plan."""

    name = "planner"
    policy = PLANNER_POLICY
    prompt_template = "agent/planner.j2"
