"""The role that serves the teacher, grows the corpus, trains the student, and scores it."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import RoundOutput, WorkerInput
from .policy import WORKER_POLICY


class DistillWorkerAgent(Agent[WorkerInput, RoundOutput]):
    """Serve the teacher for the round's seeds, append to the corpus, train the student, evaluate per category."""

    name = "distill_worker"
    policy = WORKER_POLICY
    prompt_template = "agent/worker.j2"
