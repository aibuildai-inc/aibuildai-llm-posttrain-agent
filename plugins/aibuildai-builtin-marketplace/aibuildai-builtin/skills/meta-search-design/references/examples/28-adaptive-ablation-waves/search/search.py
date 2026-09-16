"""Plan a wave, run its cells in parallel, extend the ledger, repeat or finalize."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.analyst import AnalystAgent
from .agents.io import AnalystInput, Cell, CellRecord, OrchestratorInput
from .agents.orchestrator import OrchestratorAgent
from .io import AdaptiveAblationSearchInput
from .programs.cell import CellInput, CellProgram


class AdaptiveAblationSearch(Search[AdaptiveAblationSearchInput, SearchOutput]):
    """The study adapts between waves; no cell adapts while it runs."""

    def _validate_wave(self, wave: int, cells: tuple[Cell, ...], known: set[str]) -> str | None:
        if len(cells) > self.input.max_cells_per_wave:
            return f"wave {wave} proposes {len(cells)} cells; at most {self.input.max_cells_per_wave}"
        names = [cell.name for cell in cells]
        if len(set(names)) != len(names) or known & set(names):
            return f"wave {wave} reuses a cell name"
        if wave == 1 and not any(cell.name == "baseline" and cell.parent is None for cell in cells):
            return "wave 1 has no baseline cell"
        for cell in cells:
            if cell.parent is not None and cell.parent not in known and cell.parent not in names:
                return f"cell {cell.name} names unknown parent {cell.parent}"
        return None

    async def explore(self) -> SearchOutput | Failure:
        ledger: list[CellRecord] = []
        outputs: dict[str, tuple[str, float]] = {}
        source_dir: str | None = None
        python_path = ""
        metric_name = ""
        orchestrator = OrchestratorAgent(
            input=OrchestratorInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                max_cells=self.input.max_cells_per_wave,
            ),
        )
        request = "Design wave 1, starting from a baseline cell."
        measured: tuple[object, ...] = ()

        for wave in range(1, self.input.max_waves + 1):
            orchestrator_handle = await self.ctx.spawn(
                orchestrator.run,
                f"{request} Wave {wave}; {self.input.max_waves - wave} wave(s) may follow. At most {self.input.max_cells_per_wave} cells.",
                upstream=measured,
                read=(task_data(), *(h.files() for h in measured),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.orchestrator_wall_clock_seconds,
                    gpus=0,
                ),
            )
            plan = await orchestrator_handle.result()
            source_dir, python_path, metric_name = (
                plan.source_dir,
                plan.python_path,
                plan.metric_name,
            )
            if not plan.next_wave:
                break
            if source_dir is None:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason="an executable wave has no source_dir",
                )
            problem = self._validate_wave(
                wave, plan.next_wave, set(outputs) | {r.cell.name for r in ledger}
            )
            if problem is not None:
                return Failure(kind=FailureKind.UNEXPECTED, reason=problem)

            handles = [
                await self.ctx.spawn(
                    CellProgram(
                        input=CellInput(
                            cell_name=cell.name,
                            config_json=cell.config_json,
                            source_dir=source_dir,
                            python_path=python_path,
                            data_dir=self.input.data_dir,
                            metric_name=metric_name,
                        ),
                    ).run,
                    upstream=(orchestrator_handle,),
                    capture_failure=True,
                    read=(task_data(), orchestrator_handle.files(),),
                    capability=ExecutionCapability(
                        wall_clock_seconds=self.input.cell_wall_clock_seconds,
                        gpus=1,
                    ),
                )
                for cell in plan.next_wave
            ]
            await self.ctx.wait(handles)
            measured = tuple(handles)
            for cell, handle in zip(plan.next_wave, handles, strict=True):
                outcome = await handle.result()
                if isinstance(outcome, Failure):
                    ledger.append(CellRecord(cell=cell, wave=wave, metric=None, failure_reason=outcome.reason))
                else:
                    outputs[cell.name] = (outcome.output_dir, outcome.metric)
                    ledger.append(CellRecord(cell=cell, wave=wave, metric=outcome.metric))

            request = "Design the next wave from the measurements now in the run."

        if not outputs:
            return Failure(kind=FailureKind.NO_OUTPUT, reason="no ablation cell reached a checkpoint")
        analyst = AnalystAgent(
            input=AnalystInput(
                objective=self.input.objective,
                metric_name=metric_name,
                ledger=tuple(ledger),
            ),
        )
        analyst_handle = await self.ctx.spawn(
            analyst.run, "Start the assigned work.", upstream=(orchestrator_handle,),
            read=(task_data(), orchestrator_handle.files(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.analyst_wall_clock_seconds, gpus=0),
        )
        analysis = await analyst_handle.result()
        if analysis.best_cell not in outputs:
            return Failure(
                kind=FailureKind.UNEXPECTED,
                reason=f"analyst named {analysis.best_cell}, which produced no checkpoint",
            )
        output_dir, metric = outputs[analysis.best_cell]
        return SearchOutput(
            output_dir=output_dir,
            score=metric,
            components=((metric_name, metric),),
        )
