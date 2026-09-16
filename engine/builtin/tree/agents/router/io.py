"""Input and Output records for the router Agent family."""

from __future__ import annotations

from dataclasses import dataclass
from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from engine.base import WorkflowBaseModel
from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput


class RouterPriorResult(WorkflowBaseModel):
    """One prior Composite fact shown to an execution-level Router."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    parent_paths: tuple[str, ...]
    status: str
    metric: float | None
    # The revision proposal that produced this Composite, when one did. It is
    # the Reviser's own accepted Output, carried here because the Search held
    # it; a Composite started from a fresh design has none.
    reviser_output: JsonValue | None


class RouterExecutionContext(WorkflowBaseModel):
    """Frozen execution facts supplied to one execution-level Router."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    parent_paths: tuple[str, ...]
    status: str
    input: dict[str, JsonValue]
    prior_results: tuple[RouterPriorResult, ...]


@dataclass(frozen=True)
class RouterInput(AgentInput):
    task_name: str
    readme: str
    # The raw task folder is the Router's directory grant, so this is the task
    # statement whether the Router runs before Setup or once per design. Its
    # output can only choose models; it cannot pass task files to routed roles.
    provider: str
    mode: str
    execution: "RouterExecutionContext | None"
    # The exact Agent roles selected by the run's final llm.auto.roles config.
    # The prompt renders its role menu from THIS tuple, and the verifier reads
    # the same tuple, so both require the same complete model map.
    routed_roles: "tuple[str, ...]"
    # The models the router may choose from (llm.auto.allowed_models): None
    # means no restriction (the prompt keeps its provider-derived "available
    # models" list); a non-empty tuple is rendered into the prompt as the
    # explicit menu the router must choose every role's model from.
    allowed_models: "tuple[str, ...] | None"


class RouterOutput(SuccessfulOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "description": "Complete per-role model assignment for this run or execution. Every routed "
            "role needs a model. The Router does not route itself."
        }
    )
    reasoning: str = Field(
        default="",
        description="One short paragraph: the cost/quality tradeoff behind this assignment.",
    )
    models: tuple[tuple[str, str], ...] = Field(
        description="One model id for every routed role in RouterInput.routed_roles."
    )

    @field_validator(
        "models",
        mode="before",
        json_schema_input_type=dict[str, str],
    )
    @classmethod
    def _validate_models(cls, value: object) -> tuple[tuple[str, str], ...]:
        entries = tuple(value.items()) if isinstance(value, dict) else tuple(value)  # type: ignore[arg-type]
        roles = tuple(role for role, _model in entries)
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate Router model roles are not allowed")
        return entries

    @field_serializer("models")
    def _serialize_models(self, models: tuple[tuple[str, str], ...]) -> dict[str, str]:
        return dict(models)

    @model_validator(mode="after")
    def _routes_every_role(self, info: ValidationInfo) -> "RouterOutput":
        """Refuse a routing choice the run cannot use.

        A complete, in-menu, routable assignment is what this Output IS, not a
        separate thing to check after it exists: every fact the question needs
        -- the routed roles, the allowed menu, the endpoint provider -- is
        already on the Router's own Input, so the answer needs no file, no
        process, and no durable work. A partial, out-of-menu, or unknown-model
        answer is failed straight back into the same conversation.

        A persistence restore re-reads an Output the run already accepted and
        carries no input, so it stops at the context check."""
        if info.context is None:
            return self
        router_input = info.context.get("input")
        if not isinstance(router_input, RouterInput):
            raise ValueError("routed roles missing from validation context")
        from engine.builtin.tree.agents.router.routing import routing_decision_problem

        problem = routing_decision_problem(
            dict(self.models),
            router_input.routed_roles,
            allowed_models=list(router_input.allowed_models or ()) or None,
            provider=router_input.provider,
        )
        if problem is not None:
            raise ValueError(
                f"Router output rejected: {problem}. Re-emit StructuredOutput "
                "with one `models` entry for every routed role and no other "
                "roles."
            )
        return self
