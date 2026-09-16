"""The export role, and the fresh validation role spawned once per environment profile."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ExportInput, ExportOutput, ValidationInput, ValidationOutput
from .policy import EXPORT_POLICY, VALIDATION_POLICY


class ExportAgent(Agent[ExportInput, ExportOutput]):
    """Run export.py once under the frozen plan and report the artifact and its digest."""

    name = "export"
    policy = EXPORT_POLICY
    prompt_template = "agent/export.j2"


class ValidationAgent(Agent[ValidationInput, ValidationOutput]):
    """Run validate.py on the one exported artifact under one environment profile."""

    name = "validation"
    policy = VALIDATION_POLICY
    prompt_template = "agent/validation.j2"
