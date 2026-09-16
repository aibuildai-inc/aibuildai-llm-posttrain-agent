"""Reject concrete Agent public members outside the Agent base contract."""

from __future__ import annotations

import importlib
import inspect
import symtable
import textwrap
from pathlib import Path

from engine.durable_execution import DURABLE_EXECUTION_TYPES
from engine.work_unit.agent.base import Agent

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_ROOT = REPO_ROOT / "engine" / "work_unit" / "agent"
# Every tree a concrete Agent may be defined in: the generic family itself and
# the built-in Definition packages. A role that lives anywhere else is loading
# from a source tree this check did not mean to inspect.
AGENT_TREES = (AGENT_ROOT, REPO_ROOT / "engine" / "builtin")
# The gate must never pass by inspecting nothing: this is the floor the shipped
# packages already clear, so an enumeration that silently finds no Agent fails.
MINIMUM_CONCRETE_AGENTS = 10


def _public_members(cls: type[object]) -> set[str]:
    source = textwrap.dedent(inspect.getsource(cls))
    source_file = inspect.getsourcefile(cls) or "<source>"
    root_table = symtable.symtable(source, source_file, "exec")
    class_tables = [
        table
        for table in root_table.get_children()
        if table.get_type() == "class" and table.get_name() == cls.__name__
    ]
    if len(class_tables) != 1:
        raise AssertionError(f"could not find the source scope for {cls.__name__}")
    return {
        symbol.get_name()
        for symbol in class_tables[0].get_symbols()
        if symbol.is_local() and not symbol.get_name().startswith("_")
    }


def _concrete_public_members(cls: type[Agent]) -> set[str]:
    members: set[str] = set()
    for base in cls.__mro__:
        if base is Agent:
            break
        metadata = getattr(base, "__pydantic_generic_metadata__", {})
        if metadata.get("origin") is not None:
            continue
        source_file = inspect.getsourcefile(base)
        if source_file is None or not any(
            Path(source_file).resolve().is_relative_to(tree) for tree in AGENT_TREES
        ):
            raise AssertionError(
                f"{base.__name__} public-member check loaded a different source tree"
            )
        members.update(_public_members(base))
    return members


def main() -> int:
    if Path(inspect.getfile(Agent)).resolve() != AGENT_ROOT / "base.py":
        raise AssertionError("Agent public-member check loaded a different source tree")
    for tree in AGENT_TREES:
        for path in tree.rglob("agent.py"):
            module = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
            importlib.import_module(module)

    allowed = _public_members(Agent)
    base_fields = set(Agent.model_fields)
    violations: list[str] = []
    concrete_count = 0
    for agent_type in DURABLE_EXECUTION_TYPES.values():
        if not issubclass(agent_type, Agent) or agent_type.__dict__.get(
            "_intermediate", False
        ):
            continue
        metadata = getattr(agent_type, "__pydantic_generic_metadata__", {})
        if metadata.get("origin") is not None:
            continue
        concrete_count += 1
        saved_fields = set(agent_type.model_fields) - base_fields
        # A declared @tool IS the sanctioned public surface: its name is what
        # the role's own model calls, so it can be neither private nor renamed.
        for name in sorted(
            _concrete_public_members(agent_type)
            - allowed
            - saved_fields
            - set(agent_type.tools)
        ):
            violations.append(f"{agent_type.__name__}.{name}")

    if concrete_count < MINIMUM_CONCRETE_AGENTS:
        print(
            f"only {concrete_count} concrete Agents were loaded, fewer than the "
            f"{MINIMUM_CONCRETE_AGENTS} this build ships: the enumeration above "
            "found nothing to check, so its silence proves nothing"
        )
        return 1
    if violations:
        print("Concrete Agents define public members outside Agent:")
        for violation in violations:
            print(violation)
        return 1
    print(
        f"{concrete_count} concrete Agents add no public members outside "
        "saved fields and declared tools"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
