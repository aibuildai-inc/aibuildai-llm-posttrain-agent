from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class WorkUnitLimit:
    """One kind's resource ceiling."""

    cpu_max_cores: float
    memory_max_gb: int


@dataclass(frozen=True)
class MetaResourceContext:
    """Authoritative runtime/resource grant facts for the Meta review.

    Resolved once, beside every other fact ``MetaAgentInput`` carries, so the
    reviewer reads a fixed grant rather than asking the run anything."""

    host_cpu_cores: float
    run_cpu_max_cores: float | None
    run_memory_max_gb: int | None
    effective_cpu_cores: float
    work_unit_limits: dict[str, WorkUnitLimit] = field(default_factory=dict)
    available_resource_profiles: tuple[str, ...] = ()
    visible_gpu_count: int = 0
    wall_clock_budget_minutes: float = 0.0
    cost_budget_usd: float | None = None


@dataclass(frozen=True)
class MetaAgentInput(AgentInput):
    """Business Input for one MetaAgent invocation.

    Everything here is frozen by the Search that spawns the invocation, out of
    that Search's own immutable Input: ``MetaSearch`` for the top-level call,
    and a generated child Search out of its own authored Input for a runtime
    replan. The Agent, and the review it composes, read only this Input and
    never the run they are part of."""

    metric_direction: str
    # The static exploration limits, settled in the run record. Authoring and
    # review spend from the same budget, so the generated Search must read the
    # live remainder with ``self.budget.snapshot()`` instead of assuming these.
    exploration_wall_clock_minutes: float
    exploration_cost_budget_usd: float | None
    # The run home this invocation writes its package under. A run-level fact
    # frozen at the spawn site, so the Agent names its own package without
    # asking the run where it lives.
    run_home: str
    resources: MetaResourceContext
    # Whether this invocation writes the design report. Read from the root
    # MetaSearch's own Input (``search.input.report``), so a runtime replan
    # invoked by a generated child Search inherits the run-level choice.
    report: bool = True


class MetaAgentOutput(SuccessfulOutput):
    """What MetaAgent hands back: the generated Search's Input payload.

    The research report is not here. MetaAgent writes ``report/paper.tex`` and
    compiles ``report/paper.pdf`` itself, in a directory beside the package, so
    the only thing this Output has to carry is the machine fact the product
    consumes -- the payload the generated Search is built from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_payload: dict[str, object] = Field(default_factory=dict)


@dataclass(frozen=True)
class MetaReviewInput(AgentInput):
    """The stable side of one package review: where the package is, and the resource grant facts it must fit. The submission itself, and any runtime request the author was given, arrive as this reviewer's own first user turn rather than as typed fields here."""

    package_dir: str
    agent_path: str
    owning_search_workspace: str
    metric_direction: str
    resources: MetaResourceContext
    # Whether the author was asked for a design report at all; when False the
    # reviewer judges the package alone (search.input.report on the run).
    report: bool = True
