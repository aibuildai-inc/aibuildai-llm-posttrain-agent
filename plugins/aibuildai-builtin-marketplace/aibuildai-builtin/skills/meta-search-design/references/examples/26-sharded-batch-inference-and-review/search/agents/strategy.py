"""The role that freezes one inference policy before any shard starts."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import StrategyInput, StrategyOutput
from .policy import STRATEGY_POLICY


class InferenceStrategyAgent(Agent[StrategyInput, StrategyOutput]):
    """Choose model, prompt, and decoding profiles from the offered closed sets."""

    name = "inference_strategy"
    policy = STRATEGY_POLICY
    prompt_template = "agent/strategy.j2"
