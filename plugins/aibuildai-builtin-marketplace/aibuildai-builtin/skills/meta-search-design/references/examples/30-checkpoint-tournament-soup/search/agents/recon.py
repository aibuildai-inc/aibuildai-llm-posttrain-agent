"""The role that reads the eval mechanism and freezes the first data policy."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ReconInput, ReconOutput
from .policy import RECON_POLICY


class ReconAgent(Agent[ReconInput, ReconOutput]):
    """Read the eval script, prepare the training data once, and set the training config."""

    name = "recon"
    policy = RECON_POLICY
    prompt_template = "agent/recon.j2"
