"""Solve a section range directly at the base case, else split, recurse, and combine."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.combiner import CombinerAgent
from .agents.decomposer import DecomposerAgent
from .agents.io import ChildSummary, CombinerInput, DecomposerInput, SectionSolverInput, Subsection
from .agents.solver import SectionSolverAgent
from .io import RecursiveDivideSearchInput


def _is_base_case(measure: int, depth_remaining: int, base_case_max_sections: int) -> bool:
    return measure <= base_case_max_sections or depth_remaining <= 0


def _validate_subsections(
    subsections: tuple[Subsection, ...],
    parent_start: int,
    parent_end: int,
    max_children: int,
) -> tuple[Subsection, ...] | Failure:
    if not 1 <= len(subsections) <= max_children:
        return Failure(
            kind=FailureKind.PERMANENT,
            reason=f"decomposer returned {len(subsections)} subsections; the bound is 1 to {max_children}",
        )
    ids = {subsection.subsection_id for subsection in subsections}
    if len(ids) != len(subsections):
        return Failure(kind=FailureKind.PERMANENT, reason="decomposer returned duplicate subsection_id values")

    required = sorted((s for s in subsections if s.required), key=lambda s: s.section_start)
    for subsection in subsections:
        measure = subsection.section_end - subsection.section_start
        if measure <= 0 or subsection.section_start < parent_start or subsection.section_end > parent_end:
            return Failure(
                kind=FailureKind.PERMANENT,
                reason=f"subsection {subsection.subsection_id} is not inside [{parent_start}, {parent_end})",
            )
        if measure >= parent_end - parent_start:
            return Failure(
                kind=FailureKind.PERMANENT,
                reason=f"subsection {subsection.subsection_id} is not strictly smaller than its parent",
            )
    if not required:
        return Failure(kind=FailureKind.PERMANENT, reason="decomposer marked no subsection as required")
    if required[0].section_start != parent_start or required[-1].section_end != parent_end:
        return Failure(kind=FailureKind.PERMANENT, reason="required subsections do not cover the parent range")
    for left, right in zip(required, required[1:], strict=True):
        if left.section_end != right.section_start:
            return Failure(kind=FailureKind.PERMANENT, reason="required subsections are not contiguous")
    return subsections


class RecursiveDivideSearch(Search[RecursiveDivideSearchInput, SearchOutput]):
    """One section range, solved directly or split into recursively solved subsections."""

    async def explore(self) -> SearchOutput | Failure:
        section_start = self.input.section_start
        section_end = self.input.section_end
        measure = section_end - section_start
        if measure <= 0:
            return Failure(kind=FailureKind.PERMANENT, reason="section_end must be greater than section_start")

        if _is_base_case(measure, self.input.depth_remaining, self.input.base_case_max_sections):
            solver = SectionSolverAgent(
                input=SectionSolverInput(
                    objective=self.input.objective,
                    document_path=self.input.document_path,
                    section_start=section_start,
                    section_end=section_end,
                ),
            )
            solver_handle = await self.ctx.spawn(
                solver.run,
                "Solve this section.",
                read=(task_data(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.solver_wall_clock_seconds,
                    gpus=0,
                ),
            )
            solved = await solver_handle.result()
            return SearchOutput(
                output_dir=solved.output_dir,
                score=solved.score,
            )

        decomposer = DecomposerAgent(
            input=DecomposerInput(
                objective=self.input.objective,
                document_path=self.input.document_path,
                section_start=section_start,
                section_end=section_end,
                max_children=self.input.max_children,
                depth_remaining=self.input.depth_remaining,
            ),
        )
        decomposer_handle = await self.ctx.spawn(
            decomposer.run,
            "Decompose this section.",
            read=(task_data(),),
            capability=ExecutionCapability(
                wall_clock_seconds=self.input.decomposer_wall_clock_seconds,
                gpus=0,
            ),
        )
        decomposition = await decomposer_handle.result()

        subsections = _validate_subsections(
            decomposition.subsections, section_start, section_end, self.input.max_children
        )
        if isinstance(subsections, Failure):
            return subsections

        children = tuple(
            RecursiveDivideSearch(
                input=RecursiveDivideSearchInput(
                    objective=self.input.objective,
                    document_path=self.input.document_path,
                    section_start=subsection.section_start,
                    section_end=subsection.section_end,
                    depth_remaining=self.input.depth_remaining - 1,
                    max_children=self.input.max_children,
                    base_case_max_sections=self.input.base_case_max_sections,
                    solver_wall_clock_seconds=self.input.solver_wall_clock_seconds,
                    decomposer_wall_clock_seconds=self.input.decomposer_wall_clock_seconds,
                    combiner_wall_clock_seconds=self.input.combiner_wall_clock_seconds,
                    child_search_wall_clock_seconds=self.input.child_search_wall_clock_seconds,
                ),
            )
            for subsection in subsections
        )

        handles = {
            subsection.subsection_id: await self.ctx.spawn(
                child.run,
                upstream=(decomposer_handle,),
                capture_failure=True,
                read=(task_data(),),
                # A child Search orchestrates: it holds the span its own
                # subtree must finish in, and names no card, because it opens
                # no process to put one in.
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.child_search_wall_clock_seconds,
                ),
            )
            for subsection, child in zip(subsections, children, strict=True)
        }
        await self.ctx.wait(set(handles.values()))

        child_summaries: list[ChildSummary] = []
        for subsection in sorted(subsections, key=lambda item: item.order_key):
            outcome = await handles[subsection.subsection_id].result()
            if isinstance(outcome, Failure):
                if subsection.required:
                    return outcome
                continue
            child_summaries.append(
                ChildSummary(
                    subsection_id=subsection.subsection_id,
                    section_start=subsection.section_start,
                    section_end=subsection.section_end,
                    output_dir=outcome.output_dir,
                    score=outcome.score,
                )
            )

        combiner = CombinerAgent(
            input=CombinerInput(
                objective=self.input.objective,
                document_path=self.input.document_path,
                section_start=section_start,
                section_end=section_end,
                combination_plan=decomposition.combination_plan,
                child_summaries=tuple(child_summaries),
            ),
        )
        combined_handle = await self.ctx.spawn(
            combiner.run, "Combine the child summaries.", upstream=(decomposer_handle, *handles.values()),
            read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.combiner_wall_clock_seconds, gpus=0),
        )
        combined = await combined_handle.result()

        return SearchOutput(
            output_dir=combined.output_dir,
            score=combined.score,
        )
