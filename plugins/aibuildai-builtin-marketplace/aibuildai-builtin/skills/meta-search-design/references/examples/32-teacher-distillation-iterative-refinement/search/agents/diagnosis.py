"""The role that reads per-category scores and targets the next generation round."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import DiagnosisInput, DiagnosisOutput
from .policy import DIAGNOSIS_POLICY


class DiagnosisAgent(Agent[DiagnosisInput, DiagnosisOutput]):
    """Read the weakest categories and the checkpoint's own outputs; propose targeted seeds."""

    name = "diagnosis"
    policy = DIAGNOSIS_POLICY
    prompt_template = "agent/diagnosis.j2"
