"""The meta package's own authoring facade: run MetaAgent output as a generated Search.

A generated package imports its authoring vocabulary from here (this module
is one of ``engine.generated_definitions.AUTHORING_FACADES``) and from
``engine.builtin.aibuildai``, which is where the ``SearchOutput`` contract a
generated top-level Search returns actually lives -- the same Output every
built-in package's ``explore()`` selects, so MetaSearch adopts it as its own
without a Meta-specific wrapper. This module itself activates a published
package, checks its exported ``SEARCH_TYPE`` against that contract, and
builds its Search invocation."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING
from pydantic import TypeAdapter
from engine.builtin.aibuildai.io import SearchOutput
from engine.builtin.meta.agents.meta.agent import MetaAgent  # noqa: F401
from engine.builtin.meta.agents.meta.io import MetaAgentInput, MetaAgentOutput  # noqa: F401

if TYPE_CHECKING:
    from types import ModuleType
    from engine.durable_execution import DurableExecution
    from engine.search.base import Search


def _package_dir(agent: "DurableExecution") -> Path:
    return Path(agent.artifacts_dir) / "search"


def _module_name(agent: "DurableExecution", run_home: str) -> str:
    """The module name one generated package takes for this run.

    The run home's own directory name already ends in the Run ID, so this
    is unique per run without asking the run for a second identity."""
    return f"aibuildai_meta_{Path(run_home).name}_{agent.path.replace('/', '_')}"


def _make_search_invocation(
    search_type: "type[Search]",
    payload: dict[str, object],
) -> "Search":
    """Build the generated Search invocation from one submitted Input payload.

    The package check and load_meta_search both call this, so a Search that
    cannot be built from its payload fails the check, not the run. Action-time
    topology is supplied only when the generated Search is called.
    """
    return search_type(
        input=TypeAdapter(search_type.input_type()).validate_python(payload),
    )


def _validate_search_type(module: "ModuleType") -> "type[Search]":
    from engine.builtin.aibuildai.search import AIBuildAISearch, Search

    search_type = getattr(module, "SEARCH_TYPE", None)
    if search_type is None:
        raise AssertionError(f"{module.__name__} does not export SEARCH_TYPE")
    if not (
        isinstance(search_type, type)
        and issubclass(search_type, Search)
        and not inspect.isabstract(search_type)
    ):
        raise AssertionError(
            f"SEARCH_TYPE is not a concrete Search subclass: {search_type}"
        )
    if issubclass(search_type, AIBuildAISearch):
        raise TypeError(
            "generated SEARCH_TYPE must be a plain Search, not AIBuildAISearch"
        )
    from engine.durable_execution import _action_spec

    if _action_spec(search_type, "run").success is not SearchOutput:
        raise TypeError("generated SEARCH_TYPE must return SearchOutput")
    return search_type


def load_meta_search(agent: "MetaAgent", output: MetaAgentOutput) -> "Search":
    """Load one generated package, publish it, and make its Search invocation.

    ``output`` is the settled Output the caller already holds from the
    MetaAgent Action it awaited, so nothing here reopens the journal to
    find what that Action produced."""
    if type(agent) is not MetaAgent:
        raise AssertionError(
            f"load_meta_search accepts only the built-in MetaAgent, got {type(agent).__name__}"
        )
    if agent._record is None:
        raise AssertionError("load_meta_search needs a recorded MetaAgent")
    package_dir = _package_dir(agent)
    module_name = _module_name(agent, agent.input.run_home)
    from engine.generated_definitions import (
        activate_generated_definition,
        publish_generated_definition,
    )

    run_home = Path(agent.input.run_home)
    package_relpath = package_dir.resolve().relative_to(run_home.resolve()).as_posix()
    module = activate_generated_definition(
        run_home, module_name=module_name, package_relpath=package_relpath
    )
    search_type = _validate_search_type(module)
    invocation = _make_search_invocation(search_type, output.input_payload)
    publish_generated_definition(module_name, package_relpath)
    return invocation


# The Meta public facade: the MetaAgent vocabulary and the recursive-Meta
# handoff. ``SearchOutput`` -- what a generated top-level Search returns -- is
# re-exported from ``engine.builtin.aibuildai``, not here; ``ExecutionCapability``
# is universal invocation vocabulary and lives on ``engine.capability``.
__all__ = [
    "MetaAgent",
    "MetaAgentInput",
    "MetaAgentOutput",
    "load_meta_search",
]
