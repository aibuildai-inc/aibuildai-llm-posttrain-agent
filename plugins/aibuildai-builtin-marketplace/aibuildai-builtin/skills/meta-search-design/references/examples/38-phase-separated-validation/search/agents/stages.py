from dataclasses import dataclass
from typing import ClassVar

from pydantic import ConfigDict

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import Agent, AgentInput, RolePolicy


@dataclass(frozen=True)
class PreparationInput(AgentInput):
    objective: str
    public_data_dir: str


class PreparationOutput(SuccessfulOutput):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_ready: bool
    environment_manifest: str
    reason: str


@dataclass(frozen=True)
class ValidationInput(AgentInput):
    objective: str
    public_data_dir: str
    environment_manifest: str


class ValidationOutput(SuccessfulOutput):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_patch_validated: bool
    output_dir: str
    reason: str


class PreparationAgent(Agent[PreparationInput, PreparationOutput]):
    name: ClassVar[str] = "phase_preparation"
    prompt_template = "agent/preparation.j2"
    policy = RolePolicy(
        tools=("Read", "Glob", "Grep", "Write", "Edit", "Bash", "StructuredOutput"),
        task_environment=True,
    )


class ValidationAgent(Agent[ValidationInput, ValidationOutput]):
    name: ClassVar[str] = "phase_validation"
    prompt_template = "agent/validation.j2"
    policy = RolePolicy(
        tools=("Read", "Glob", "Grep", "Write", "Edit", "Bash", "StructuredOutput"),
        task_environment=True,
    )
