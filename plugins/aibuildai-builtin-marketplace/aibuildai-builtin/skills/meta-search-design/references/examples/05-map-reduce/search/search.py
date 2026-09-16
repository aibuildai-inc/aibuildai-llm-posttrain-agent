"""Review every declared package independently, then reduce the findings once."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import (
    MappedPackage,
    PackageReviewInput,
    ReviewReducerInput,
)
from .agents.reducer import ReviewReducerAgent
from .agents.reviewer import PackageReviewerAgent
from .io import CodeReviewMapReduceInput


class CodeReviewMapReduceSearch(Search[CodeReviewMapReduceInput, SearchOutput]):
    """Every mapper covers a different package; all mappers must succeed."""

    async def explore(self) -> SearchOutput | Failure:
        packages = self.input.packages
        if len(set(packages)) != len(packages):
            return Failure(
                kind=FailureKind.PERMANENT,
                reason="The declared packages are not disjoint.",
            )

        handles = {
            package: await self.ctx.spawn(
                PackageReviewerAgent(
                    input=PackageReviewInput(
                        objective=self.input.objective,
                        repo_dir=self.input.repo_dir,
                        package_path=package,
                    ),
                ).run,
                "Start the assigned work.",
                capture_failure=True, read=(task_data(),),
                capability=ExecutionCapability(
                        wall_clock_seconds=self.input.reviewer_wall_clock_seconds,
                        gpus=0,
                    ),
            )
            for package in packages
        }
        await self.ctx.wait(set(handles.values()))

        mapped: list[MappedPackage] = []
        failed: list[str] = []
        for package in packages:
            outcome = await handles[package].result()
            if isinstance(outcome, Failure):
                failed.append(f"{package} ({outcome.reason})")
            else:
                mapped.append(
                    MappedPackage(
                        package_path=package,
                        defects=outcome.defects,
                        summary=outcome.summary,
                    )
                )

        if failed:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    f"{len(failed)} of {len(packages)} required packages failed: "
                    f"{', '.join(failed)}"
                ),
            )

        reducer = ReviewReducerAgent(
            input=ReviewReducerInput(
                objective=self.input.objective,
                declared_packages=packages,
                mapped_packages=tuple(mapped),
            ),
        )
        reducer_handle = await self.ctx.spawn(
            reducer.run, "Start the assigned work.",
            upstream=tuple(handles[package] for package in packages), read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.reducer_wall_clock_seconds, gpus=0),
        )
        reduced = await reducer_handle.result()

        return SearchOutput(
            output_dir=reduced.output_dir,
            score=-float(reduced.total_defects),
            components=(
                    ("total_defects", float(reduced.total_defects)),
                    ("blocker_count", float(reduced.blocker_count)),
                ),
        )
