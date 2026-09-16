"""The role that solves one base-case section directly, with no further split."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SectionSolverInput, SectionSolverOutput
from .policy import SOLVER_POLICY


class SectionSolverAgent(Agent[SectionSolverInput, SectionSolverOutput]):
    """Read a small section and write its summary directly."""

    name = "section_solver"
    policy = SOLVER_POLICY
    prompt_template = "agent/solver.j2"
