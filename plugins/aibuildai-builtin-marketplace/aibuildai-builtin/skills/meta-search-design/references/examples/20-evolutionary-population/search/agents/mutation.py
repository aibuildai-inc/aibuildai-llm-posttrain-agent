"""The role that mutates one parent into bounded offspring."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import MutationInput, MutationOutput
from .policy import MUTATION_POLICY


class MutationAgent(Agent[MutationInput, MutationOutput]):
    """Change one declared part of the parent's configuration; never redesign it whole."""

    name = "mutation"
    policy = MUTATION_POLICY
    prompt_template = "agent/mutation.j2"
