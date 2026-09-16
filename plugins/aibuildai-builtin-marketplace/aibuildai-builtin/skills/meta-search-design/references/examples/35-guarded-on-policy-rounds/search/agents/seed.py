"""The role that supervises the seed model, scores it, and freezes what the rounds sample on."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SeedInput, SeedOutput
from .policy import SEED_POLICY


class SeedAgent(Agent[SeedInput, SeedOutput]):
    """Prepare the supervised data, train and score the seed, and name the prompt set the rounds sample."""

    name = "seed"
    policy = SEED_POLICY
    prompt_template = "agent/seed.j2"
