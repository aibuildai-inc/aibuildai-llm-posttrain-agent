"""The five authored roles of the router and specialists Search."""

from __future__ import annotations

from .code_repair import CodeRepairAgent
from .data_analysis import DataAnalysisAgent
from .literature_review import LiteratureReviewAgent
from .router import RouterAgent
from .synthesis import SynthesisAgent

__all__ = [
    "CodeRepairAgent",
    "DataAnalysisAgent",
    "LiteratureReviewAgent",
    "RouterAgent",
    "SynthesisAgent",
]
