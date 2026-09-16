"""The solver, decomposer, and combiner roles of the recursive divide-and-conquer Search."""

from __future__ import annotations

from .combiner import CombinerAgent
from .decomposer import DecomposerAgent
from .solver import SectionSolverAgent

__all__ = ["CombinerAgent", "DecomposerAgent", "SectionSolverAgent"]
