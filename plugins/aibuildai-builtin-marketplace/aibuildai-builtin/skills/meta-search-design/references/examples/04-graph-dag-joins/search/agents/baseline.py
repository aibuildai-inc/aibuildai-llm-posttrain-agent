"""Operation B: train a quick default model, independent of every other role."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import BaselineInput, BaselineOutput
from .policy import BASELINE_POLICY


class BaselineAgent(Agent[BaselineInput, BaselineOutput]):
    """Train one default model and report its score and feature weights."""

    name = "baseline"
    policy = BASELINE_POLICY
    prompt_template = "agent/baseline.j2"
