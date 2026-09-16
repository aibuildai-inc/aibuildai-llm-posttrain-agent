"""MetaSearch: the product Search whose program is written by an Agent.

A MetaAgent researches the task, writes a small Definition package under one
generated child Search, and this package runs it. The child Search returns
the same :class:`SearchOutput` contract every built-in package's ``explore()``
selects, so MetaSearch adopts it as its own without a Meta-specific wrapper."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from engine.durable_execution import run_host
from engine.base import WorkflowBaseModel
from engine.builtin.aibuildai.io import SearchOutput, task_data
from engine.builtin.aibuildai.search import SEARCH_TEMPLATE, AIBuildAISearch
from engine.builtin.meta.agents.meta.agent import MetaAgent, MetaVerifierAgent
from engine.builtin.meta.agents.meta.io import (
    MetaAgentInput,
    MetaResourceContext,
    WorkUnitLimit,
)
from engine.builtin.meta.authoring import load_meta_search
from engine.capability import ExecutionCapability
from engine.failure import Failure, FailureKind
from engine.generated_definitions import GeneratedDefinitionError
from engine.metric_contract import MetricContractInput
from engine.paths import RunPaths

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.work_unit.base import WorkUnit


# --- What this package's own children may spend --------------------------
# This package resolves its own roles, from the run's configuration, once at
# startup. It is deliberately a copy of what every other package does: no
# shared layer answers for an arbitrary role name, so one package's children
# can never be named, reached, or re-priced from another. The question is
# always asked of a Definition class this package owns, never of a string.
def _capability(
    configs: "AgentConfig",
    unit_type: "type[WorkUnit]",
    *,
    expected_minutes: float | None = None,
    gpus: int | tuple[int, ...] = 0,
) -> ExecutionCapability:
    """What one child of this package may spend."""
    kind, name = unit_type.kind, unit_type.name
    if name is None:
        raise AssertionError(f"{unit_type.__name__} declares no configured name")
    time_config = configs.work_unit_time_for(kind, name)
    limit = configs.resources.work_unit.limit_for(kind, name)
    return ExecutionCapability(
        wall_clock_seconds=time_config.cap_seconds(
            expected_minutes=expected_minutes,
            pipeline_minutes=configs.run.budget.wall_clock_minutes,
        ),
        cpu_max_cores=limit.cpu_max_cores,
        memory_max_gb=float(limit.memory_max_gb),
        gpus=gpus,
        # File isolation is physical, so it is declared by the unit that does
        # the work, not by a container above it. None where the run did not ask
        # for it, so a unit never argues with an owner that did.
        retry=time_config.retry,
    )



class MetaSearchParameters(WorkflowBaseModel):
    """MetaSearch's own business parameters: the half a user writes under ``search.input``.

    The generated child Search declares its own parameters in its own Input.

    ``report`` decides whether the Meta author also writes and compiles the
    LaTeX design report (``report/paper.pdf``) and whether the required Meta
    review judges that report together with the package. On by default. Off,
    the package is judged alone and no document is required: for a run whose
    only deliverable is the trained artifact, the report costs 10-15 minutes
    of the Meta budget and adds a second way for a correct package to be
    rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report: bool = Field(
        default=True,
        description=(
            "write, compile, and review the Meta design report "
            "(report/paper.pdf) beside the generated package; false skips the "
            "document entirely and judges the package alone"
        ),
    )


class MetaSearchInput(MetaSearchParameters):
    """MetaSearch's complete immutable Input.

    The business parameters above are the user's. Everything below is resolved
    once by startup, so nothing this Search creates ever reads configuration or
    asks for a capability by role name."""

    meta: ExecutionCapability
    meta_review: ExecutionCapability
    exploration_wall_clock_minutes: float
    exploration_cost_budget_usd: float | None
    # The Meta review's own resource-grant context: a run fact settled once at
    # startup, never rediscovered from a live Search, config, or host.
    host_cpu_cores: float
    run_cpu_max_cores: float | None
    run_memory_max_gb: int | None
    effective_cpu_cores: float
    work_unit_limits: dict[str, WorkUnitLimit]
    available_resource_profiles: tuple[str, ...]


class MetaSearch(AIBuildAISearch[MetaSearchInput]):
    description = """An Agent writes the search program, and the run then executes the program it wrote."""
    __doc__ = description

    prompt_template = SEARCH_TEMPLATE
    agent_types = (MetaAgent,)
    unit_types = ()
    parameters_type = MetaSearchParameters

    @classmethod
    def build_input(cls, configs: "AgentConfig", run_paths: RunPaths) -> MetaSearchInput:
        """Validate the user's ``search.input`` and complete this package's Input."""
        del run_paths
        host_cpu_cores = float(os.cpu_count() or 1)
        run_limits = configs.resources.run
        run_cpu_max_cores = run_limits.cpu_max_cores
        run_memory_max_gb = run_limits.memory_max_gb
        effective_cpu_cores = (
            min(host_cpu_cores, run_cpu_max_cores)
            if run_cpu_max_cores is not None
            else host_cpu_cores
        )
        work_unit_limits = {
            name: WorkUnitLimit(
                cpu_max_cores=limit.cpu_max_cores, memory_max_gb=limit.memory_max_gb
            )
            for kind, name in (
                ("llm_agent", MetaAgent.name),
                ("program", "training"),
            )
            for limit in (configs.resources.work_unit.limit_for(kind, name),)
        }
        return MetaSearchInput(
            **cls.parameters(configs),
            meta=_capability(configs, MetaAgent),
            meta_review=_capability(configs, MetaVerifierAgent),
            exploration_wall_clock_minutes=configs.run.budget.wall_clock_minutes,
            exploration_cost_budget_usd=configs.run.budget.cost_usd,
            host_cpu_cores=host_cpu_cores,
            run_cpu_max_cores=run_cpu_max_cores,
            run_memory_max_gb=run_memory_max_gb,
            effective_cpu_cores=effective_cpu_cores,
            work_unit_limits=work_unit_limits,
            available_resource_profiles=tuple(sorted(cls.active_agent_types(configs))),
        )

    # --- The algorithm ------------------------------------------------------
    async def explore(self, metric_contract: MetricContractInput) -> "SearchOutput | Failure | None":
        """Author one Search, then run it: MetaSearch's whole algorithm.

        Every fact MetaAgent and its required review need is frozen onto its
        own ``MetaAgentInput`` right here, at this spawn site, out of this
        Search's own immutable Input and its own live capability -- never out
        of configuration, run state, or the host controller."""
        meta_input = MetaAgentInput(
            metric_direction=metric_contract.metric_direction,
            exploration_wall_clock_minutes=self.input.exploration_wall_clock_minutes,
            exploration_cost_budget_usd=self.input.exploration_cost_budget_usd,
            run_home=self.run_home,
            resources=MetaResourceContext(
                host_cpu_cores=self.input.host_cpu_cores,
                run_cpu_max_cores=self.input.run_cpu_max_cores,
                run_memory_max_gb=self.input.run_memory_max_gb,
                effective_cpu_cores=self.input.effective_cpu_cores,
                work_unit_limits=self.input.work_unit_limits,
                available_resource_profiles=self.input.available_resource_profiles,
                visible_gpu_count=run_host().host_visible_gpu_count(),
                wall_clock_budget_minutes=self.input.exploration_wall_clock_minutes,
                cost_budget_usd=self.input.exploration_cost_budget_usd,
            ),
            report=self.input.report,
            reviewer_capability=self.input.meta_review,
        )
        meta = MetaAgent(
            input=meta_input,
        )
        meta_handle = await self.ctx.spawn(
            meta.run,
            "Start the assigned work.",
            # It designs a Search FROM this run: the task data it must design
            # against, and this Search's own tree, where a replan reads the
            # evidence finished executions left instead of a copy of it.
            read=(task_data(), self.files()),
            capability=self.input.meta.model_copy(
                update={"gpus": run_host().gpu_universe()}
            ),
        )
        meta_output = await meta_handle.result()
        try:
            child = load_meta_search(meta, meta_output)
        except GeneratedDefinitionError as exc:
            return Failure(kind=FailureKind.PERMANENT, reason=str(exc))
        child_handle = await self.ctx.spawn(
            child.run,
            upstream=(meta_handle,),
            # A generated Search starts no process of its own, so its Action
            # spends nothing; every unit inside it declares its own. Written
            # out because that is what the package contract asks of the
            # packages this one loads.
            capability=ExecutionCapability(),
        )
        output = await child_handle.result()
        if not isinstance(output, SearchOutput):
            raise AssertionError(
                f"the generated Search returned {type(output).__name__}, not SearchOutput"
            )
        return output


SEARCH_TYPE = MetaSearch
