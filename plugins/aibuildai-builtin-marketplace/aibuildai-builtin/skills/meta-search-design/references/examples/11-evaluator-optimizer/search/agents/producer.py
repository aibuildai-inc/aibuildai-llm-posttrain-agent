"""The role that produces or revises the one candidate lineage."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ProducerInput, ProducerOutput
from .policy import PRODUCER_POLICY


class ProducerAgent(Agent[ProducerInput, ProducerOutput]):
    """Produce the first version, or revise the previous version under feedback."""

    name = "producer"
    policy = PRODUCER_POLICY
    prompt_template = "agent/producer.j2"
