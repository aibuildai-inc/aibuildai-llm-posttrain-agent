"""The role that performs exactly one assigned task and returns one typed result."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import WorkerInput, WorkerOutput
from .policy import WORKER_POLICY


class WorkerAgent(Agent[WorkerInput, WorkerOutput]):
    """Answer one assignment's task_objective under its assigned role; do not redesign the study."""

    name = "worker"
    policy = WORKER_POLICY
    prompt_template = "agent/worker.j2"
