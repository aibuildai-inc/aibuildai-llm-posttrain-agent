from pydantic import BaseModel, ConfigDict

from engine.builtin.aibuildai import SearchOutput, task_data
from engine.capability import ExecutionCapability
from engine.failure import Failure, FailureKind
from engine.search import Search

from .agents.stages import (
    PreparationAgent,
    PreparationInput,
    ValidationAgent,
    ValidationInput,
)


class PhaseValidationInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str
    public_data_dir: str


class PhaseValidationSearch(Search[PhaseValidationInput, SearchOutput]):
    async def explore(self) -> SearchOutput | Failure:
        preparation = PreparationAgent(
            input=PreparationInput(
                objective=self.input.objective,
                public_data_dir=self.input.public_data_dir,
            ),
        )
        prepared_handle = await self.ctx.spawn(
            preparation.run,
            "Prepare the environment without loading the model.",
            capability=ExecutionCapability(wall_clock_seconds=600, gpus=0),
            read=(task_data(),),
        )
        prepared = await prepared_handle.result()
        if not prepared.environment_ready:
            return Failure(kind=FailureKind.UNEXPECTED, reason=prepared.reason)

        validator = ValidationAgent(
            input=ValidationInput(
                objective=self.input.objective,
                public_data_dir=self.input.public_data_dir,
                environment_manifest=prepared.environment_manifest,
            ),
        )
        validated_handle = await self.ctx.spawn(
            validator.run,
            "Validate an actual model-generated patch in the prepared environment.",
            capability=ExecutionCapability(wall_clock_seconds=900, gpus=1),
            upstream=(prepared_handle,),
            read=(task_data(), prepared_handle.files()),
        )
        validated = await validated_handle.result()
        if not validated.model_patch_validated:
            return Failure(kind=FailureKind.UNEXPECTED, reason=validated.reason)
        return SearchOutput(output_dir=validated.output_dir, score=1.0)
