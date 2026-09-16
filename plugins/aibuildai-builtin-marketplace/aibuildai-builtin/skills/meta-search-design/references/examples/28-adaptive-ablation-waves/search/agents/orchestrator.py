"""The role that decides which bounded wave of cells to run next."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import OrchestratorInput, OrchestratorOutput
from .policy import ORCHESTRATOR_POLICY


class OrchestratorAgent(Agent[OrchestratorInput, OrchestratorOutput]):
    """Fix the source in wave 1, then propose one wave at a time from the ledger."""

    name = "orchestrator"
    policy = ORCHESTRATOR_POLICY
    prompt_template = "agent/orchestrator.j2"
