"""The role that trains the initial model and every low-learning-rate continuation, and scores each."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import RoundOutput, WorkerInput
from .policy import WORKER_POLICY


class PrecisionWorkerAgent(Agent[WorkerInput, RoundOutput]):
    """Run the initial SFT, or one continuation from the best checkpoint on a fix dataset, and evaluate it."""

    name = "precision_worker"
    policy = WORKER_POLICY
    prompt_template = "agent/worker.j2"
