"""The role that decides which bounded wave of assignments to run next."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import OrchestratorInput, OrchestratorOutput
from .policy import ORCHESTRATOR_POLICY


class OrchestratorAgent(Agent[OrchestratorInput, OrchestratorOutput]):
    """Inspect accumulated evidence and propose one wave at a time, or finalize."""

    name = "orchestrator"
    policy = ORCHESTRATOR_POLICY
    prompt_template = "agent/orchestrator.j2"
