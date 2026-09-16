"""The terminal action: run the fixed train.py once under a path's decisions and read its metric."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import TrialInput, TrialOutput
from .policy import TRIAL_POLICY


class TrialAgent(Agent[TrialInput, TrialOutput]):
    """Write the decisions as config.json, run train.py once, and report the metric it wrote."""

    name = "trial"
    policy = TRIAL_POLICY
    prompt_template = "agent/trial.j2"
