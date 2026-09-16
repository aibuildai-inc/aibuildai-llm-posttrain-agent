"""The worker and diagnosis roles of the teacher-distillation Search."""

from __future__ import annotations

from .diagnosis import DiagnosisAgent
from .worker import DistillWorkerAgent

__all__ = ["DistillWorkerAgent", "DiagnosisAgent"]
