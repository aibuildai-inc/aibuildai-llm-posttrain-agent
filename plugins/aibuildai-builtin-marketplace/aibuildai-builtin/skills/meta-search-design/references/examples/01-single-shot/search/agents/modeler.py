"""The role that trains one model and reports its validation score."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ModelerInput, ModelerOutput
from .policy import MODELER_POLICY


class ModelerAgent(Agent[ModelerInput, ModelerOutput]):
    """Train one model on the task data, save it, and score it."""

    name = "modeler"
    policy = MODELER_POLICY
    prompt_template = "agent/modeler.j2"
