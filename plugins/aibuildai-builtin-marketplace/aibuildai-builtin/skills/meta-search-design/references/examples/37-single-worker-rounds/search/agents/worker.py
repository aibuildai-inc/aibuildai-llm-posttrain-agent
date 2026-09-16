"""The one role: it prepares data, trains, evaluates, and decides its own next step, round after round."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import RoundOutput, WorkerInput
from .policy import WORKER_POLICY


class WorkerAgent(Agent[WorkerInput, RoundOutput]):
    """Do one whole post-training round on the card and report the best checkpoint it measured."""

    name = "worker"
    policy = WORKER_POLICY
    prompt_template = "agent/worker.j2"
