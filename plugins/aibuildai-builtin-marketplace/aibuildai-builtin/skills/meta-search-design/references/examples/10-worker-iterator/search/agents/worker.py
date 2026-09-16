"""The role that performs exactly one bounded diagnostic action."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import WorkerInput, WorkerOutput
from .policy import WORKER_POLICY


class WorkerAgent(Agent[WorkerInput, WorkerOutput]):
    """Perform the assigned work item and report one typed observation."""

    name = "worker"
    policy = WORKER_POLICY
    prompt_template = "agent/worker.j2"
