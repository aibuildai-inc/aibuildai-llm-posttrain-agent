"""The role that writes the fixed training source and creates a diverse initial population."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import InitializerInput, InitializerOutput
from .policy import INITIALIZER_POLICY


class InitializerAgent(Agent[InitializerInput, InitializerOutput]):
    """Write train.py once, then propose a diverse initial population of configurations."""

    name = "initializer"
    policy = INITIALIZER_POLICY
    prompt_template = "agent/initializer.j2"
