"""One candidate: train the assigned approach and report its score."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import CandidateInput, CandidateOutput
from .policy import CANDIDATE_POLICY


class CandidateAgent(Agent[CandidateInput, CandidateOutput]):
    """Train one model with the assigned approach, save it, and score it."""

    name = "candidate"
    policy = CANDIDATE_POLICY
    prompt_template = "agent/candidate.j2"
