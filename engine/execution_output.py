"""The terminal Output shape shared by every durable execution."""

from __future__ import annotations

# The public Output surface.
__all__ = ["SuccessfulOutput", "VerifierOutput"]

from abc import ABC
from collections.abc import Mapping, Set

# Any is required by Pydantic's public copy and construct method signatures.
from typing import Any, Generic, Literal, Self, TypeVar, cast, final

from pydantic import ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from engine.base import WorkflowBaseModel


OutputFailureFlagT = TypeVar("OutputFailureFlagT", bound=bool, covariant=True)
_NO_UPDATE = object()


class ExecutionOutput(WorkflowBaseModel, Generic[OutputFailureFlagT], ABC):
    """One immutable terminal Output with a class-fixed failure flag."""

    model_config = ConfigDict(frozen=True)
    failed: OutputFailureFlagT

    @model_serializer(mode="wrap")
    def _serialize_business_payload(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, object]:
        payload = handler(self)
        if not isinstance(payload, dict):
            raise TypeError("ExecutionOutput must serialize to an object")
        serialized_failed = payload.pop("failed")
        if serialized_failed is not self.failed:
            raise AssertionError("ExecutionOutput serialized the wrong failed value")
        return payload

    @final
    def business_payload(self) -> dict[str, object]:
        """Return the Output without its execution-lifecycle field."""
        payload = self.model_dump(mode="json")
        if not isinstance(payload, dict):
            raise TypeError("ExecutionOutput must serialize to an object")
        return payload

    @final
    def terminal_payload(self) -> dict[str, object]:
        """Return the only payload shape stored as this execution's Output."""
        return {"failed": self.failed, **self.business_payload()}

    @final
    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None | object = _NO_UPDATE,
        deep: bool = False,
    ) -> Self:
        if update is not _NO_UPDATE:
            raise TypeError("ExecutionOutput.model_copy does not accept update")
        return super().model_copy(deep=deep)

    @classmethod
    @final
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        del _fields_set, values
        raise TypeError("ExecutionOutput.model_construct is disabled")

    @classmethod
    @final
    def construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        del _fields_set, values
        raise TypeError("ExecutionOutput.construct is disabled")

    @final
    def copy(
        self,
        *,
        include: Set[int | str] | Mapping[int | str, Any] | None = None,
        exclude: Set[int | str] | Mapping[int | str, Any] | None = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        del include, exclude, update, deep
        raise TypeError("ExecutionOutput.copy is disabled")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if cls.model_config.get("frozen") is not True:
            raise AssertionError(f"{cls.__name__} must remain frozen")


class SuccessfulOutput(ExecutionOutput[Literal[False]], ABC):
    """The common shape of a successful execution Output."""

    failed: Literal[False] = False

    @classmethod
    @final
    def business_json_schema(cls) -> dict[str, object]:
        """Return the schema an Agent fills, without the runtime flag."""
        schema = cls.model_json_schema()
        properties = cast(dict[str, object], schema["properties"])
        del properties["failed"]
        return schema


class VerifierOutput(SuccessfulOutput):
    """The verdict every completed verifier returns, whatever family it belongs to.

    A verifier is not a family: it is an ordinary Agent or Program whose sole
    Action is ``run`` and whose success Output is this. The producer that owns
    the candidate reads only these two fields, so the chain never asks what
    kind of WorkUnit gave the verdict."""

    passed: bool = Field(
        description=(
            "True only when the verified submission follows its role's stated "
            "requirements and agrees with the artifacts supplied for review. "
            "False when the submission is incomplete, unsupported, or differs "
            "from those requirements or artifacts."
        ),
    )
    reason: str = Field(
        description=(
            "One-paragraph justification of the verdict. On FAIL, the "
            "concrete changes the verified role must make before resubmitting."
        ),
    )
