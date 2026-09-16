"""One modeler invocation becomes the Search result."""

from __future__ import annotations

from engine.failure import Failure
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import ModelerInput
from .agents.modeler import ModelerAgent
from .io import SingleShotSearchInput


class SingleShotSearch(Search[SingleShotSearchInput, SearchOutput]):
    """Adapt the Search Input to one role, run it once, adapt its Output back."""

    async def explore(self) -> SearchOutput | Failure:
        modeler = ModelerAgent(
            input=ModelerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
            ),
        )
        handle = await self.ctx.spawn(
            modeler.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.wall_clock_seconds, gpus=0),
        )
        output = await handle.result()
        return SearchOutput(
            output_dir=output.output_dir,
            score=output.score,
        )
