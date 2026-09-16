"""The role that splits one section range into smaller, ordered subsections."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import DecomposerInput, DecomposerOutput
from .policy import DECOMPOSER_POLICY


class DecomposerAgent(Agent[DecomposerInput, DecomposerOutput]):
    """Choose split points so every child is strictly smaller than the parent."""

    name = "decomposer"
    policy = DECOMPOSER_POLICY
    prompt_template = "agent/decomposer.j2"
