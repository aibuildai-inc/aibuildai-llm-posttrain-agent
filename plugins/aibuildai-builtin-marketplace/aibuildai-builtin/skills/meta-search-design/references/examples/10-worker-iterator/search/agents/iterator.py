"""The role that reads every observation and chooses the next work item."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import IteratorInput, IteratorOutput
from .policy import ITERATOR_POLICY


class IteratorAgent(Agent[IteratorInput, IteratorOutput]):
    """Continue with one next work item, stop with a diagnosis, or fail."""

    name = "iterator"
    policy = ITERATOR_POLICY
    prompt_template = "agent/iterator.j2"
