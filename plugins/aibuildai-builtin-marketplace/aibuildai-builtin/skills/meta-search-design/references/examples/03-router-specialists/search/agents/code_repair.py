"""The specialist for tasks about a broken pipeline: diagnose and fix it."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SpecialistInput, SpecialistOutput
from .policy import SPECIALIST_POLICY


class CodeRepairAgent(Agent[SpecialistInput, SpecialistOutput]):
    """Find the defect in the task's code and leave a working fix."""

    name = "code_repair"
    policy = SPECIALIST_POLICY
    prompt_template = "agent/code_repair.j2"
