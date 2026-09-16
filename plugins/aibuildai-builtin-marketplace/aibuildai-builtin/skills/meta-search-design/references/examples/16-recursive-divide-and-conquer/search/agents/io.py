"""Typed Input and Output of the solver, decomposer, and combiner roles."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class Subsection(BaseModel):
    """One child problem the decomposer produced; stays attached to the parent's range."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subsection_id: str = Field(description="Unique id of this subsection within its parent's decomposition.")
    section_start: int = Field(ge=0, description="First section index of this subsection, inclusive.")
    section_end: int = Field(gt=0, description="Last section index of this subsection, exclusive.")
    required: bool = Field(description="False only when the parent range is already covered without it.")
    order_key: str = Field(description="Sort key restoring reading order when the combiner joins summaries.")


class ChildSummary(BaseModel):
    """One recursive child's result, ready for the combiner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subsection_id: str = Field(description="The Subsection this summary answers.")
    section_start: int = Field(ge=0, description="First section index this summary covers, inclusive.")
    section_end: int = Field(gt=0, description="Last section index this summary covers, exclusive.")
    output_dir: str = Field(description="Absolute artifacts directory holding the child's written summary.")
    score: float = Field(ge=0.0, le=1.0, description="The child's self-assessed coverage completeness.")


@dataclasses.dataclass(frozen=True)
class SectionSolverInput(AgentInput):
    objective: str
    document_path: str
    section_start: int
    section_end: int


class SectionSolverOutput(SuccessfulOutput):
    """One directly solved section: its written summary and self-assessed coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the written summary.")
    summary_path: str = Field(description="Absolute path of the written summary file, inside output_dir.")
    score: float = Field(ge=0.0, le=1.0, description="Self-assessed completeness of this section's coverage.")


@dataclasses.dataclass(frozen=True)
class DecomposerInput(AgentInput):
    objective: str
    document_path: str
    section_start: int
    section_end: int
    max_children: int
    depth_remaining: int


class DecomposerOutput(SuccessfulOutput):
    """The subsections one parent range splits into, and how to recombine them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subsections: tuple[Subsection, ...] = Field(description="Bounded, disjoint, strictly smaller child ranges.")
    combination_plan: str = Field(description="How the combiner should join the child summaries in order.")


@dataclasses.dataclass(frozen=True)
class CombinerInput(AgentInput):
    objective: str
    document_path: str
    section_start: int
    section_end: int
    combination_plan: str
    child_summaries: tuple[ChildSummary, ...]


class CombinerOutput(SuccessfulOutput):
    """The joined summary for one parent range, built from its children's summaries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the written combined summary.")
    summary_path: str = Field(description="Absolute path of the written combined summary, inside output_dir.")
    score: float = Field(ge=0.0, le=1.0, description="Self-assessed completeness of the combined coverage.")
