"""Shared run base model.

Leaf module: the common WorkflowBaseModel base class. It imports nothing else in engine, so every record module can build on it without cycles.
"""

from __future__ import annotations

import types

from pydantic import BaseModel, ConfigDict


# Build-agnostic callable type for pydantic's ``ignored_types``. In a source
# checkout ``type(_model_method_probe)`` is ``types.FunctionType``; once a module
# is Cython-compiled its module-level functions (and the methods of every
# pydantic model defined in a compiled module) become the Cython
# ``cython_function_or_method`` type, which is a single type shared across all
# compiled modules. pydantic decides "this class attribute is a method, not an
# un-annotated field" by isinstance against the model's ``ignored_types`` — and a
# Cython-compiled method is NOT a ``types.FunctionType``, so without this entry
# every pydantic model that defines a method fails to build (``PydanticUserError``
# "non-annotated attribute") the moment its module is imported as a native
# ``.so``. Including the probe type is a no-op in source mode (it just equals
# ``FunctionType``, already listed).
def _model_method_probe() -> None: ...


_CYTHON_METHOD_TYPE = type(_model_method_probe)

IGNORED_MODEL_ATTR_TYPES = (
    types.FunctionType,
    property,
    classmethod,
    staticmethod,
    _CYTHON_METHOD_TYPE,
)


class WorkflowBaseModel(BaseModel):
    model_config = ConfigDict(
        ignored_types=IGNORED_MODEL_ATTR_TYPES,
        extra="forbid",
        # Durable executions may own concrete Pydantic WorkUnits.
        # arbitrary_types_allowed is also kept for the other arbitrary field
        # types (Cost and event value objects) those models carry.
        arbitrary_types_allowed=True,
    )

