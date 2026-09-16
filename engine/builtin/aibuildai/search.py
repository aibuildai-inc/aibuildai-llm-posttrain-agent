"""The product Search runs setup, exploration, delivery, and reporting."""

from __future__ import annotations

import abc
import asyncio
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast, final

from config import WorkUnitTimeConfigError
from infra.util.clock import Clock
from pydantic import BaseModel, ConfigDict, PrivateAttr, TypeAdapter, ValidationError

from engine.builtin.aibuildai.agents.finalizer.agent import FinalizerAgent
from engine.builtin.aibuildai.agents.finalizer.io import FinalizerInput, FinalizerOutput
from engine.builtin.aibuildai.agents.setup.agent import SetupAgent
from engine.builtin.aibuildai.agents.setup.io import SetupInput, SetupOutput
from engine.builtin.aibuildai.agents.submitter.agent import SubmitterAgent
from engine.builtin.aibuildai.agents.submitter.io import (
    ExternalScore,
    SubmitterInput,
    SubmitterOutput,
)
from engine.builtin.aibuildai.agents.writer.agent import WriterAgent
from engine.builtin.aibuildai.agents.writer.io import WriterInput
from engine.builtin.aibuildai.capability import ProductCapabilities, granted
from engine.builtin.aibuildai.io import (
    AIBuildAISearchRuntimeRecord,
    RunSuspendRequest,
    SearchOutput,
    SearchResult,
    deliverable_files,
    grading_files,
    public_files,
    submitter_files,
    task_data,
    task_folder_files,
)
from engine.builtin.aibuildai.programs.score import ScoreProgram
from engine.composite import Composite
from engine.durable_execution import (
    DurableExecution,
    Handle,
    RunTerminalFailure,
    _action_spec,
    action,
    action_output,
    bind_run_services,
    latest_run_state,
    record_events,
    run_clock,
    run_configs,
    run_host,
    run_program_environment,
    running_search,
)
from engine.event.base import Event
from engine.event.store import SearchApplication
from engine.event.events import (
    ActionAttemptStarted,
    ExecutionCreated,
    MetricContractRecorded,
    NotebookVersionPushed,
    RunSuspendRequested,
    SearchExplorationFinished,
    SearchExplorationStarted,
    SearchResultSelected,
)
from engine.failure import Failure, FailureKind
from engine.hooks import run_heartbeat
from engine.metric_contract import MetricContractInput
from engine.paths import RunPaths
from engine.search.base import Search, SearchInputT
from engine.status import TerminalStatus
from engine.work_unit.agent.base import Agent, AgentInput
from engine.work_unit.agent.policy import ModelRouter
from engine.work_unit.agent.prompt import search_template_for
from engine.work_unit.base import WorkUnit
from engine.work_unit.events import RateLimitPaused, RateLimitResumed

logger = logging.getLogger(__name__)

# Every built-in package names its own Search prompt the same way; the package
# name is what keeps two of them apart in the template map.
SEARCH_TEMPLATE = "search.j2"


class _RunScope:
    """The terminal result passed from the run body to its cleanup."""

    output: "SearchResult | Failure | None" = None


if TYPE_CHECKING:
    from engine.work_unit.agent.plugins import EnableConfig
    from engine.durable_execution import DurableExecution
    from engine.run_state import RunState
    from config import AgentConfig
    from engine.mcp import RunMcpClient
    from infra.host_resource.controller import HostResourceController
    from engine.program_environment import ProgramEnvironment
    from output.web.live_member import LiveWebMember
    from output.transcript import Transcript
    from infra.backends.spec import AgentSpec
    from typing import Callable


AgentT = TypeVar("AgentT", bound=Agent)

# Every Agent the framework itself composes into a run. A semantic reviewer is
# not listed: it reaches the run through the role that declares it, which is
# the one place that knows a role has one at all.
_HARNESS_AGENT_TYPES: tuple[type[Agent], ...] = (
    SetupAgent,
    SubmitterAgent,
    FinalizerAgent,
    WriterAgent,
)


@dataclass(frozen=True)
class ProductRuntime:
    """The product bindings of the run this process serves.

    They belong to the PROCESS, not to any execution object. An execution is
    journaled as its Input and its Capability and rebuilt from exactly those
    two, so the instance whose Action actually runs is not the one production
    assembly built: ``execute_durable`` takes it from a freshly restored
    RunState. Anything attached to the assembled instance is simply absent
    there, which is why these are read through :func:`product_runtime` and
    are members of no class."""

    run_paths: "RunPaths"
    product_capabilities: "ProductCapabilities"
    router: "ModelRouter | None"
    mcp_client: "RunMcpClient"
    display: "LiveWebMember"
    transcript_for: "Callable[[Agent], Transcript]"


_PRODUCT_RUNTIME: "ProductRuntime | None" = None


def product_runtime() -> ProductRuntime:
    """The product bindings of the run this process serves."""
    runtime = _PRODUCT_RUNTIME
    if runtime is None:
        raise AssertionError("the product runtime is not bound")
    return runtime


class AIBuildAISearch(Search[SearchInputT, SearchResult], abc.ABC):
    """One run lifecycle and the live methods its executions may call.

    This is the AIBuildAI product's own Search shell: it fixes the run config
    as the Input and the delivered files as the Output, and it adds Setup,
    grading, composite admission, result selection, the Finalizer, delivery, and the
    Web display. Linear and Tree are built-in product searches; a plain
    ``Search[I, S]`` inherits none of it."""

    _intermediate: ClassVar[bool] = True

    # Workflow-local values; result selection lives in the journal.
    _heartbeat_task: "asyncio.Task[None] | None" = PrivateAttr(default=None)
    _setup_handle: "Handle[SetupOutput | Failure] | None" = PrivateAttr(default=None)
    _submitter_handle: "Handle[SubmitterOutput] | None" = PrivateAttr(default=None)

    def _new_family_record(self) -> AIBuildAISearchRuntimeRecord:
        return AIBuildAISearchRuntimeRecord()

    @property
    def _runtime(self) -> AIBuildAISearchRuntimeRecord:
        runtime = self.record.family_state
        if not isinstance(runtime, AIBuildAISearchRuntimeRecord):
            raise AssertionError("AIBuildAISearch has no product runtime record")
        return runtime

    # --- What each Search method declares ----------------------------------
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    description: ClassVar[str]
    agent_types: ClassVar[tuple[type[Agent], ...]] = ()
    # Every non-Agent WorkUnit definition this Search invokes as a child Action.
    # Startup validates the complete declared set before any paid work starts.
    unit_types: ClassVar[tuple[type[WorkUnit], ...]] = ()

    prompt_template: ClassVar[str]
    # The half of this package's Input a user writes under ``search.input``.
    # ``build_input`` completes the rest from resolved configuration.
    parameters_type: ClassVar[type[BaseModel]]

    # The Agents that receive a method-owned root MCP server. Startup validation
    # reads this declaration before any agent launch; the method's custom Agent
    # launch path supplies the corresponding server at runtime.
    mcp_server_agents: ClassVar[tuple[type[Agent], ...]] = ()

    @classmethod
    def uid_name(cls) -> str:
        return "search"

    @property
    def exploration_finished(self) -> bool:
        return self._runtime.exploration_finished_at_s is not None

    @property
    def exploration_started_at_s(self) -> float | None:
        return self._runtime.exploration_started_at_s

    @property
    def exploration_finished_at_s(self) -> float | None:
        return self._runtime.exploration_finished_at_s

    @property
    def exploration_cost_usd(self) -> float | None:
        return self._runtime.exploration_cost_usd

    @property
    def run_suspend_requested(self) -> RunSuspendRequest | None:
        return self._runtime.run_suspend_requested

    @property
    def selected_output(self) -> SearchOutput | None:
        """The result this run delivers if its exploration ends now."""
        return self._runtime.selected_output

    @final
    def select(self, output: SearchOutput) -> None:
        """Save the result used when exploration returns None or reaches its time limit.

        Each call replaces the previous choice without ending exploration."""
        self.record_event(SearchResultSelected, self, output=output)

    def accept_selected(self, output: SearchOutput) -> None:
        """Apply one recorded selection."""
        self._runtime.selected_output = output

    # --- The algorithm hook ------------------------------------------------
    @abstractmethod
    async def explore(  # pyright: ignore[reportIncompatibleMethodOverride] -- the product shell owns run(), so its algorithm hook takes the frozen metric contract the shell established; the generic parameterless hook is never called on this family.
        self, metric_contract: MetricContractInput
    ) -> "SearchOutput | Failure | None":
        """Return the final result, None to use the saved choice, or Failure.

        A returned result replaces the saved choice. A returned Failure wins.
        Pause and recoverable errors leave the run unfinished."""

    # --- Registration and construction -------------------------------------
    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if inspect.isabstract(cls):
            return
        if cls.prompt_template != SEARCH_TEMPLATE:
            raise AssertionError(
                f"{cls.__name__}.prompt_template must be {SEARCH_TEMPLATE!r}: "
                "a package's own prompt is keyed under the package name"
            )
        if not cls.agent_types:
            raise AssertionError(
                f"{cls.__name__} must declare the Agent classes it uses"
            )
        declared_agent_set = set(cls.agent_types)
        if not set(cls.mcp_server_agents) <= declared_agent_set:
            raise AssertionError(
                f"{cls.__name__}.mcp_server_agents must be a subset of agent_types"
            )
        harness = {agent_type.name for agent_type in _HARNESS_AGENT_TYPES}
        reviewers = {
            producer.semantic_reviewer()
            for producer in _HARNESS_AGENT_TYPES + cls.agent_types
            if producer.semantic_reviewer() is not None
        }
        for agent_type in cls.agent_types:
            if agent_type.name in harness:
                raise AssertionError(
                    f"{cls.__name__}.agent_types is only the algorithm command "
                    f"set; harness Agent {agent_type.name!r} is added by Search"
                )
            if agent_type in reviewers:
                raise AssertionError(
                    f"{cls.__name__}.agent_types lists {agent_type.__name__}, "
                    "which the role it reviews already composes"
                )
            if agent_type.policy is None:
                raise AssertionError(
                    f"{cls.__name__} declares {agent_type.__name__} "
                    "without a RolePolicy"
                )
        # One name, one Agent, across everything this run can compose --
        # the reviewers each role brings with it included, because the
        # active set keys on name and a repeat there would silently
        # replace one role with another. Every set is walked as a
        # sequence, where a repeat is still visible; a name-keyed dict
        # would have collapsed it.
        declared: set[str] = set()
        composed = _HARNESS_AGENT_TYPES + cls.agent_types
        composed += tuple(
            reviewer
            for producer in composed
            if (reviewer := producer.semantic_reviewer()) is not None
        )
        for agent_type in composed:
            if agent_type.name in declared:
                raise AssertionError(
                    f"{cls.__name__} declares two Agents named {agent_type.name!r}"
                )
            declared.add(agent_type.name)
        input_owners: dict[type[object], type[Agent]] = {}
        for agent_type in composed:
            input_type = agent_type.input_type()
            # A reviewer with no stable review context declares no business
            # Input at all, and several may do so; that is not two roles
            # sharing one identity.
            if input_type is AgentInput:
                continue
            prior = input_owners.get(input_type)
            if prior is not None and prior is not agent_type:
                raise AssertionError(
                    f"{prior.__name__} and {agent_type.__name__} use the same "
                    f"Input {input_type.__name__}"
                )
            input_owners[input_type] = agent_type

    @final
    def bind_runtime(
        self,
        *,
        configs: "AgentConfig",
        run_paths: RunPaths,
        product_capabilities: ProductCapabilities,
        router: "ModelRouter | None",
        mcp_client: "RunMcpClient",
        run_state: "RunState",
        clock: "Clock",
        store: SearchApplication,
        display: "LiveWebMember",
        transcript_for: "Callable[[Agent], Transcript]",
        host: "HostResourceController",
        program_environment: "ProgramEnvironment",
    ) -> None:
        """Bind this process's run, once. Production assembly is the one caller.

        Nothing is attached to this instance. An execution is journaled as its
        Input and its Capability and REBUILT from exactly those two, so the
        object whose Action actually runs is not this one -- ``execute_durable``
        takes it from a freshly restored RunState. A binding kept on ``self``
        would be absent there. Both records below therefore belong to the
        process that hosts the run, and both are read through a function."""
        global _PRODUCT_RUNTIME
        if run_state.search is not self:
            raise AssertionError("runtime must bind the restored Search root")
        _PRODUCT_RUNTIME = ProductRuntime(
            run_paths=run_paths,
            product_capabilities=product_capabilities,
            router=router,
            mcp_client=mcp_client,
            display=display,
            transcript_for=transcript_for,
        )
        bind_run_services(
            search=self,
            configs=configs,
            run_state=run_state,
            store=store,
            clock=clock,
            host=host,
            program_environment=program_environment,
        )
        # Outside the exploration window the global budget constrains nothing,
        # but every MCP adapter needs a FINITE timeout (a remote HTTP/SSE
        # session turns it into a timedelta, and infinity overflows it), so
        # the unconstrained phases fall back to the whole settled Run Budget
        # as the finite operation ceiling.
        run_budget_s = configs.run.budget.wall_clock_minutes * 60
        mcp_client._bind_clock(
            lambda: min(clock.exploration_remaining_s(), run_budget_s)
        )
        mcp_client._bind_kaggle_version_recorder(self.record_version_used)
        mcp_client._bind_host_resources(host)

    def terminal_status(
        self, decided: "SearchResult | Failure | None" = None
    ) -> TerminalStatus:
        """What one terminal Output means: the decided one, or the recorded one."""
        output = self._run_output() if decided is None else decided
        if isinstance(output, Failure):
            return (
                TerminalStatus.FAILED_UNEXPECTED
                if output.kind is FailureKind.UNEXPECTED
                else TerminalStatus.FAILED
            )
        if output is not None:
            return TerminalStatus.COMPLETED
        if self.run_suspend_requested is not None:
            return TerminalStatus.INTERRUPTED
        return TerminalStatus.EMPTY

    def open_process(self) -> None:
        self._runtime.run_suspend_requested = None

    def request_suspend(self, *, reason: str, ts: float) -> None:
        self._runtime.run_suspend_requested = RunSuspendRequest(
            since_s=ts, reason=reason
        )

    def start_exploration(self, *, ts: float) -> None:
        """Open the exploration budget window: the Run Budget and the global cost limit count from here."""
        if self._runtime.exploration_started_at_s is not None:
            raise AssertionError("Search exploration can start only once")
        self._runtime.exploration_started_at_s = ts

    def finish_exploration(
        self,
        *,
        ts: float,
        exploration_cost_usd: float,
    ) -> None:
        if self.exploration_finished:
            raise AssertionError("Search exploration can finish only once")
        if self._runtime.exploration_started_at_s is None:
            raise AssertionError("Search exploration must start before it finishes")
        self._runtime.exploration_finished_at_s = ts
        self._runtime.exploration_cost_usd = exploration_cost_usd

    @final
    def _record_exploration_finished(self) -> None:
        """Durably close the exploration budget window, whatever ended explore.

        Every way out of the explore body -- a returned result, a handled stop,
        the wall-clock boundary, a propagated child Failure, a host capability
        failure, an unexpected error -- passes through here, so a run with a recorded result
        never holds an open window whose elapsed keeps growing. Only a suspend
        (cancellation) leaves the window open: that run resumes and its
        exploration continues."""
        from engine.durable_execution import record_durable_event

        record_durable_event(SearchExplorationFinished, self)

    # --- The contract a Search method overrides ----------------------------
    @classmethod
    def check_config(cls, configs: "AgentConfig") -> None:
        """Refuse a config this Search type cannot honour. The base refuses none."""

    @classmethod
    def prepare_router(
        cls, configs: "AgentConfig", *, enable: "EnableConfig"
    ) -> "ModelRouter | None":
        """This package's own model Router, or None when it declares no Router.

        Routing is one concrete role's job, not a shared service: a package
        that declares no Router returns None here, and startup then refuses a
        configuration that asks for routing instead of quietly ignoring it."""
        del configs, enable
        return None

    # --- Config rules and the Agents this method declares ------------------
    @classmethod
    @final
    # Any: the mapping a package's own Input model validates back into typed
    # fields, so the value's real type is that package's parameters_type.
    def parameters(cls, configs: "AgentConfig") -> dict[str, Any]:
        """Validate ``search.input`` against this package's own parameters.

        ``search.kind`` selects the package; ``search.input`` is that package's
        own business half, so its ``parameters_type`` is the sole authority for
        which parameters exist, their types, defaults, and validation."""
        try:
            parameters = TypeAdapter(cls.parameters_type).validate_python(
                configs.search.input
            )
        except ValidationError as exc:
            details = "; ".join(
                f"search.input.{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            )
            raise ValueError(
                f"invalid search.input for search.kind="
                f"{configs.search.kind!r}: {details}"
            ) from exc
        return parameters.model_dump()

    @classmethod
    @abstractmethod
    def build_input(cls, configs: "AgentConfig", run_paths: RunPaths) -> BaseModel:
        """Construct this package's COMPLETE immutable Input.

        The user's validated parameters plus every value the package's children
        need resolved once: each child's capability and any budget rule it
        applies to a typed runtime fact. Startup calls this at the composition
        boundary and the result is exactly what a fresh run journals as the
        root Search's Input, so no later stage reads configuration again."""

    @classmethod
    @final
    def validate_config(cls, configs: "AgentConfig") -> None:
        """Check every config rule this package declares, before the run home exists.

        Assembly builds the one canonical typed root Input later, through
        ``build_input``, once the run's paths are real."""
        cls.parameters(configs)

        auto = configs.llm.auto
        if auto is not None:
            active = cls.active_agent_types(configs)
            roles = active if auto.roles == "all" else auto.roles
            invalid = sorted(set(roles) - set(active))
            if invalid:
                raise ValueError(
                    "llm.auto.roles contains Agent role(s) that cannot take part "
                    f"in routing for search.kind={configs.search.kind!r}: {invalid}"
                )
            auto.roles = sorted(roles)

        from engine.work_unit.agent.spec import (
            validate_extension_tool_conflicts,
        )

        # Every verifier.enable key is answered by the role it names, through
        # the same predicate that composes the reviewer at runtime. So a key
        # can be wrong in exactly two ways and both are said here: it names a
        # role that composes no reviewer at all, or it asks for the opposite of
        # what that role decides. Nothing here holds a list of reviewable roles
        # to go stale against the build.
        producers = {
            agent_type.name: agent_type
            for agent_type in _HARNESS_AGENT_TYPES + cls.agent_types
            if agent_type.semantic_reviewer() is not None
        }
        for role, wanted in sorted(configs.verifier.enable.items()):
            producer = producers.get(role)
            if producer is None:
                raise ValueError(
                    f"verifier.enable names role {role!r}, which composes no "
                    f"reviewer; search.kind={configs.search.kind!r} reviews "
                    f"{sorted(producers)}"
                )
            if producer.composes_reviewer(configs) is not wanted:
                raise ValueError(
                    f"verifier.enable[{role!r}]={wanted} but {role!r} decides "
                    "otherwise for this config: its review is not optional"
                )

        active_agents = cls.active_agent_types(configs)
        identities: set[tuple[str, str]] = {
            ("llm_agent", role) for role in active_agents
        }
        identities.add((ScoreProgram.kind, ScoreProgram.name))
        for unit_type in cls.unit_types:
            identities.add((unit_type.kind, unit_type.name_segment()))
        run_limits = configs.resources.run
        for kind, name in sorted(identities):
            configs.work_unit_time_for(kind, name)
            limit = configs.resources.work_unit.limit_for(kind, name)
            for field, run_value in (
                ("cpu_max_cores", run_limits.cpu_max_cores),
                ("memory_max_gb", run_limits.memory_max_gb),
            ):
                declared = getattr(limit, field)
                if run_value is not None and declared > run_value:
                    raise ValueError(
                        "resources.work_unit limit for "
                        f"kind={kind!r}, name={name!r}: {field}={declared} "
                        f"widens the run ceiling resources.run.{field}={run_value}; "
                        "a WorkUnit capability may only keep or narrow the run "
                        "ceiling"
                    )

        cls.check_config(configs)

        owner = f"search.kind={configs.search.kind!r}"
        for agent_type in cls.mcp_server_agents:
            validate_extension_tool_conflicts(
                f"{owner} Agent {agent_type.name!r}",
                has_skills=False,
                has_sub_agents=False,
                has_mcp_servers=True,
                disallowed_tools=configs.disallowed_tools,
                disallowed_source="config.disallowed_tools",
            )

    @classmethod
    @final
    def declared_role_names(cls) -> tuple[str, ...]:
        """Every Agent role name this package can compose, reviewers included.

        Read off the package's own declarations, so the starter config budgets
        exactly the roles a package has and cannot go stale against it. This is
        a class-level question with no configuration in it: which of these roles
        a given run actually composes is ``active_agent_types``."""
        declared = _HARNESS_AGENT_TYPES + cls.agent_types
        names = [agent_type.name for agent_type in declared]
        names += [
            reviewer.name
            for agent_type in declared
            if (reviewer := agent_type.semantic_reviewer()) is not None
        ]
        return tuple(dict.fromkeys(names))

    @classmethod
    @final
    def declared_unit_names(cls) -> tuple[str, ...]:
        """Every non-Agent WorkUnit name this package can run."""
        names = [ScoreProgram.name, *(unit.name_segment() for unit in cls.unit_types)]
        return tuple(dict.fromkeys(names))

    @classmethod
    @final
    def active_agent_types(cls, configs: "AgentConfig") -> dict[str, type[Agent]]:
        """Return the Agent classes this configured run composes.

        Every declared Agent answers for itself whether its role runs, so this
        is the whole activation authority: a role that can be switched off --
        the Router, the Submitter, the Writer -- states the switch beside the
        role, and nothing here knows what any of them is for.

        A reviewer arrives with the role that declares it. That is why no
        Search lists one: the producer already names the reviewer it composes,
        and a second list here could only disagree with it."""
        active: dict[str, type[Agent]] = {}
        for agent_type in _HARNESS_AGENT_TYPES + cls.agent_types:
            if not agent_type.enabled_in(configs):
                continue
            active[agent_type.name] = agent_type
            if agent_type.composes_reviewer(configs):
                reviewer = agent_type.semantic_reviewer()
                if reviewer is None:
                    raise AssertionError(f"{agent_type.name} composes no reviewer")
                active[reviewer.name] = reviewer
        return active

    @final
    @staticmethod
    def worth_ensembling(
        scores: "tuple[float, ...]", metric_contract: MetricContractInput
    ) -> bool:
        """Whether these member scores are worth paying an Aggregator for.

        One rule, shared by every shipped package with an ensemble stage: no
        member is not worth combining, and neither is a population whose every
        member measured zero on a max-direction metric, which is what a run
        where nothing worked looks like."""
        return bool(scores) and not (
            metric_contract.metric_direction == "max"
            and all(abs(score) <= 1e-12 for score in scores)
        )

    @final
    def external_scores(self) -> tuple[ExternalScore, ...]:
        """Read the external scores that a new role may receive."""
        if not run_configs().submission.share_external_scores:
            return ()
        submitters = latest_run_state().work_units_of_type(self, SubmitterAgent)
        if len(submitters) != 1:
            raise AssertionError("external score sharing requires a Submitter")
        file = Path(product_runtime().run_paths.external_scores_path)
        if not file.is_file():
            return ()
        out: list[ExternalScore] = []
        for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                # A null design path marks work the Submitter built itself. A
                # null score marks a result that is still pending.
                raw_design = row["design_path"]
                if raw_design is None:
                    design_path = None
                elif isinstance(raw_design, str):
                    design = latest_run_state().records.get(raw_design)
                    if design is None:
                        raise ValueError(f"unknown design path {raw_design!r}")
                    design_path = design.path
                else:
                    raise TypeError("design_path must be a string or null")
                raw_score = row.get("score")
                score = None if raw_score is None else float(raw_score)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                logger.warning("external scores: skipping unparseable line in %s", file)
                continue
            out.append(
                ExternalScore(
                    design_path=design_path,
                    score=score,
                    note=str(row.get("note") or ""),
                )
            )
        return tuple(out)

    # --- Running Agents ----------------------------------------------------
    @final
    async def prepare_agent(self, agent: Agent) -> "AgentSpec":
        """Return one prepared Agent run's backend spec."""
        from engine.work_unit.agent.spec import compute_agent_spec

        if not isinstance(agent, Agent):
            raise AssertionError(f"Agent driver received {type(agent).__name__}")
        if agent._action.output is not None:
            raise AssertionError("cached Agent Output must return before its driver")
        spec = await compute_agent_spec(
            agent,
            search_template=search_template_for(type(self)),
            cgroups=agent.cgroups,
            device_indices=agent.device_indices,
            timeout_s=run_clock().effective_remaining_s(agent),
        )

        transcript = product_runtime().transcript_for(agent)
        if not transcript.described:
            transcript.start(
                system_prompt=spec.instructions,
                input_payload=agent.input.json_dict(),
            )

        return spec

    @property
    def setup_handle(self) -> "Handle[SetupOutput | Failure]":
        """The Setup Action that established the grading contract, so its consumers name that exact Action as upstream."""
        if self._setup_handle is None:
            raise AssertionError("Setup has not established the grading contract yet")
        return self._setup_handle

    # --- Grading -----------------------------------------------------------
    @final
    def prepare_grading(self, setup_output: SetupOutput) -> None:
        """Record the metric contract Setup's accepted Output declares.

        That the frozen files exist is not re-asked here. SETUP's own accepted verifier owns that gate and has already run the score program over them; a second check after acceptance can only disagree with the owner."""
        from engine.durable_execution import record_durable_event

        record_durable_event(
            MetricContractRecorded,
            None,
            metric_name=setup_output.metric_name,
            metric_direction=setup_output.metric_direction,
        )

    # --- Recording Events --------------------------------------------------
    @final
    def record_event(
        self,
        event_type: type[Event],
        execution: DurableExecution | None,
        **facts: object,
    ) -> Event:
        """Append and fold one fact at this task's current durable boundary."""
        return record_events(((event_type, execution, facts),))[0]

    def _after_event_saved(
        self,
        event: object,
        execution: "DurableExecution | None",
        *,
        event_ts: float,
        now: float,
    ) -> None:
        """Update views only after one Event is in the Event store."""
        if latest_run_state() is None:
            return
        if isinstance(event, (RateLimitPaused, RateLimitResumed)):
            if not isinstance(execution, Agent):
                raise AssertionError("rate-limit timing requires an Agent target")
            run_clock().refresh(execution)
        elif isinstance(event, ActionAttemptStarted):
            if execution is None:
                raise AssertionError("execution timing requires a target")
            run_clock().refresh(execution)
        if isinstance(execution, Agent):
            transcript = product_runtime().transcript_for(execution)
            transcript.apply_event(event, event_ts)
        if isinstance(event, ExecutionCreated):
            created = latest_run_state().execution_for(
                event.uid if event.target is None else f"{event.target}/{event.uid}"
            )
            if isinstance(created, Agent):
                product_runtime().transcript_for(created)
            if isinstance(created, Composite):
                # Every Composite owns a directory from the moment it exists.
                # The record does no filesystem work, so the directory is made
                # here, on the state the fold just produced. Nothing is put in
                # it: a Composite is a bounded operation, and what one kind of
                # operation needs beside its code is that operation's own
                # business, arranged by the callback that runs it.
                Path(latest_run_state().execution_directory(created.path)).mkdir(
                    parents=True, exist_ok=True
                )
        product_runtime().display.render(
            latest_run_state(),
            now_relative=event_ts,
            now_absolute=now,
        )

    @final
    def record_version_used(self) -> None:
        """Record one Kaggle notebook version the relay reports as pushed."""
        self.record_event(NotebookVersionPushed, None)

    # --- Budget, admissions, and stopping ----------------------------------
    @final
    @final
    def grant_retry(self, work_unit: WorkUnit, failure: Failure) -> bool:
        """Re-open one terminally failed unit for another run, and say whether it was.

        Two questions, and both must answer yes. The declared quota is this ACTION's own: ``capability.retry`` was resolved by whoever started this Action and journaled on it, so deciding a retry reads no role-name config, an authored identity fails and retries without an outer YAML row, and a later Action on the same identity arrives with its own untouched allowance instead of whatever earlier calls spent. The permission is its owner's, and only its owner's: ``may_retry`` is asked of the ONE execution this unit hangs under, and a parent with no policy of its own does not object, so the question stops there. An external root has no parent and uses its own base method. That is what keeps early stopping a policy BETWEEN composites -- a composite already running is left to finish, its interior and its retries included, exactly as the soft wall leaves it -- and it is why a unit inside a composite is bounded by its quota alone.

        Granting it is one recorded fact, and the fold does the rest: the Failure moves into the unit's earlier runs, and the unit is left un-started with its frozen Input, ready to run again."""
        if work_unit.run_number - 1 >= work_unit.capability.retry:
            return False
        parent = work_unit.parent
        retry_owner = work_unit if parent is None else parent
        refusal = retry_owner.may_retry(failure)
        if refusal is not None:
            logger.info(
                "no retry for %s: %s",
                work_unit.step_name,
                refusal,
            )
            return False
        return True

    @final
    def cap_stop_reason(self) -> "str | None":
        """Why the run's own cost or wall-clock ceiling stops new work.

        The cost side stops at the START of the budget's finalize band, not at the ceiling. Both are the same rule the wall-clock side already follows: launching is allowed only while the run can still PAY for what it launches, so the last slice of the budget is reserved for finishing what is already in flight.

        Stopping at the ceiling instead cost a user a whole run. A long-running agent's resource preparation clamps its cap to whatever the run has left, so a launch admitted at $4.99 of $5.00 handed a fresh 30-minute worker a $0.0097 cap. No agent can buy a model turn for a cent -- the first priced event of its first turn was already 22 times that -- so the launch could only ever spend money and then be killed for spending it. The two questions have to be one: a run that cannot fund a unit does not admit one."""
        budget = run_configs().run.budget
        admission_frac = 1.0 - budget.finalize_threshold_pct / 100.0
        cost_cap = budget.cost_usd
        if cost_cap is not None:
            spent = latest_run_state().exploration_cost_used_usd()
            if spent >= cost_cap * admission_frac:
                return (
                    f"${spent:.2f} of the ${cost_cap:.2f} exploration cost ceiling "
                    f"is spent, leaving less than the {budget.finalize_threshold_pct:g}% "
                    "reserved to finish work already running, so no new work was "
                    "launched (soft cap; in-flight work still finalizes)"
                )
        pipeline_s = budget.wall_clock_minutes * 60
        if run_clock().exploration_elapsed_s() >= pipeline_s * admission_frac:
            return (
                f"{run_clock().exploration_elapsed_s() / 60.0:.1f} min of the "
                f"{budget.wall_clock_minutes:g} min exploration wall-clock budget "
                f"is spent, leaving less than the {budget.finalize_threshold_pct:g}% "
                "reserved to finish work already running, so no new work was "
                "launched (soft cap; in-flight work still finishes)"
            )
        return None

    @final
    def suspend(self, reason: str) -> None:
        """Record one suspend request and cancel the live pipeline task."""
        pipeline_task = self._pipeline_task
        if latest_run_state().search._run_output() is not None:
            # Nothing left to suspend: the terminal Output is recorded and
            # only the display's own quit gate remains. Release that gate as
            # its owner, so an interrupt there returns through the normal
            # path; asking the display to quit would call this method again.
            return
        if (
            pipeline_task is not None
            and pipeline_task is not asyncio.current_task()
            and not pipeline_task.cancelling()
        ):
            pipeline_task.cancel()
            joiner = running_search()._joiner
            if joiner is None:
                raise AssertionError("suspend requires a joining supervisor")
            # JOIN: a suspended root records no Output, so a resumed run's
            # polling handle would wait forever; wake the joiner only once
            # the root task has fully unwound.
            pipeline_task.add_done_callback(
                lambda _: (
                    None
                    if latest_run_state().search._run_output() is not None
                    else joiner.cancel()
                )
            )
        if latest_run_state().aibuildai_search.run_suspend_requested is None:
            self.record_event(RunSuspendRequested, None, reason=reason)

    # --- The run lifecycle: start, shut down, record the result ------------
    @action
    async def run(self) -> SearchResult | Failure:
        """The fixed AIBuildAI product lifecycle around one concrete algorithm.

        Setup freezes the grading contract, the optional Submitter watches the
        run beside the exploration, the exploration budget window opens, the
        concrete ``explore`` hook runs, the Finalizer turns what it selected
        into the user's deliverable, and the optional whole-run Writer reports
        on the finished run. A concrete built-in implements only the hook."""
        # Establish this Search's level in the run's resource tree before any
        # descendant can be placed below it.
        run_host().claim_level(self.path)
        async with self._run_scope() as scope:
            if self._run_output() is None:
                router = product_runtime().router
                if router is not None:
                    await router.decide_run_models(self)
                configs = run_configs()
                capabilities = product_runtime().product_capabilities
                await run_program_environment().prepare(
                    lambda: run_clock().effective_remaining_s(self)
                )
                setup = SetupAgent(
                    input=SetupInput(
                        task_name=configs.run.task_name,
                        setup_from_scratch=configs.run.setup_from_scratch,
                        task_prompt=configs.run.task_prompt or "",
                        reviewer_capability=capabilities.setup_review,
                    ),
                )
                # Setup's own output gate proves the score module on the
                # baseline attempt and sends a failure back into the same
                # conversation, so one call establishes the whole contract.
                setup_handle: Handle[SetupOutput | Failure] = await self.ctx.spawn(
                    setup.run,
                    "Start the assigned work.",
                    read=(task_folder_files(),),
                    write=(public_files(), grading_files()),
                    capture_failure=True,
                    capability=capabilities.setup.model_copy(
                        update={"gpus": run_host().gpu_universe()}
                    ),
                )
                setup_output = await setup_handle.result()
                if isinstance(setup_output, Failure):
                    raise RunTerminalFailure(setup.path, setup_output)
                self._setup_handle = setup_handle
                self.prepare_grading(setup_output)
                contract = latest_run_state().metric_contract
                if contract is None:
                    raise AssertionError(
                        "grading preparation did not record its metric contract"
                    )
                metric_contract = MetricContractInput(
                    metric_name=contract.metric_name,
                    metric_direction=contract.metric_direction,
                )
                submitter: Handle[SubmitterOutput] | None = None
                submitter_agent: SubmitterAgent | None = None
                if run_configs().submitter_on:
                    submitter_agent = SubmitterAgent(
                        input=SubmitterInput(
                            metric_name=metric_contract.metric_name,
                            metric_direction=metric_contract.metric_direction,
                            max_versions=configs.submission.max_versions,
                        ),
                    )
                    submitter = await self.ctx.spawn(
                        submitter_agent.run,
                        "Start the assigned work.",
                        upstream=(setup_handle,),
                        read=(task_data(), self.files()),
                        write=(submitter_files(),),
                        capability=granted(capabilities.submitter, "submitter"),
                    )
                    self._submitter_handle = submitter
                from engine.durable_execution import record_durable_events

                # The exploration budget window opens here: the Run Budget and
                # the cost cap count nothing before this fact, so Setup stays
                # outside them, and a resumed process replays the committed
                # boundary instead of opening a second window.
                record_durable_events(((SearchExplorationStarted, self, {}),))
                exploration_timeout = asyncio.timeout(
                    run_clock().exploration_remaining_s()
                )
                refused: Failure | None = None
                boundary: Failure | None = None
                try:
                    async with exploration_timeout:
                        returned = await self.explore(metric_contract)
                        if isinstance(returned, SearchOutput):
                            self.select(returned)
                        else:
                            refused = returned
                except TimeoutError:
                    if not exploration_timeout.expired():
                        raise
                    boundary = Failure(
                        kind=FailureKind.TIMEOUT,
                        reason="exploration reached its wall-clock boundary",
                    )
                stopped = refused or boundary
                if stopped is not None:
                    logger.info("Search exploration stopped: %s", stopped)
                    await self.ctx._cancel_started_children(stopped)
                self._record_exploration_finished()
                if submitter is not None and submitter_agent is not None:
                    # Submission is product harness work outside the
                    # exploration budget, so its wind-down grace is its own
                    # remaining Local Budget, never the exploration remainder.
                    submitter_grace = run_clock().local_remaining_s(submitter_agent)
                    await self.ctx.cancel(
                        submitter,
                        Failure(
                            kind=FailureKind.TIMEOUT,
                            reason=(
                                "the Submitter did not finish before delivery cleanup "
                                "had to start, so the run went on without its report; "
                                "any submissions it made still stand"
                            ),
                        ),
                        grace_s=submitter_grace,
                    )
                if latest_run_state().metric_contract is None:
                    raise AssertionError(
                        "grading must be prepared before it is finalized"
                    )
                # The finished transaction includes selections from child workflows.
                selected = None if refused is not None else self.selected_output
                if selected is None:
                    scope.output = stopped or Failure(
                        kind=FailureKind.NO_OUTPUT,
                        reason="nothing was scored, so the run has no result",
                    )
                else:
                    finalizer_records = latest_run_state().work_units_of_type(
                        self, FinalizerAgent
                    )
                    finalizer_was_complete = any(
                        len(unit.record.actions) > len(unit.record.active_actions)
                        for unit in finalizer_records
                    )
                    delivered = await (
                        await self.ctx.spawn(
                            FinalizerAgent(
                                input=FinalizerInput(
                                    output=selected,
                                    deliverable_dir=product_runtime().run_paths.deliverable_dir,
                                ),
                            ).run,
                            "Start the assigned work.",
                            read=(task_data(), self.files(
                                Path(selected.output_dir).relative_to(self.directory).as_posix(),
                                links=True,
                            )),
                            write=(deliverable_files(),),
                            capability=capabilities.finalizer,
                        )
                    ).result()
                    if not isinstance(delivered, FinalizerOutput):
                        raise AssertionError(
                            "a successful Finalizer has the wrong Output type"
                        )
                    if finalizer_was_complete:
                        finalizer_records = latest_run_state().work_units_of_type(
                            self, FinalizerAgent
                        )
                        if len(finalizer_records) != 1:
                            raise AssertionError("Search requires one Finalizer")
                        await self._recheck_delivered_output(
                            finalizer_records[0], delivered
                        )
                    scope.output = SearchResult(
                        output=selected, delivery=delivered.delivery
                    )
                    if stopped is not None:
                        logger.info(
                            "The run preserved a valid final deliverable "
                            "after the Search exploration stopped: %s",
                            stopped,
                        )
                # The paper is about a finished run, so a run that delivered
                # nothing does not pay for one.
                if isinstance(scope.output, SearchResult) and run_configs().writer.enable:
                    await self.drive_writeup()
        output = self._run_output() or scope.output
        if output is None:
            raise AssertionError("AIBuildAISearch ended without an Output")
        return cast("SearchResult | Failure", output)

    async def _recheck_delivered_output(
        self, finalizer: Agent, output: BaseModel
    ) -> None:
        """Prove a resumed run's deliverable is still on disk.

        A resume returns the Finalizer's recorded Output without running it
        again, so the files that Output names were written in an earlier
        process and nothing has looked at them since. The Finalizer is asked
        for the very invocations that cleared them then, so there is one owner
        of what checking a delivery means, and they run here as ordinary
        durable work rather than as a gate behaving differently in a second
        phase."""
        if not isinstance(output, FinalizerOutput) or not isinstance(
            finalizer.input, FinalizerInput
        ):
            raise AssertionError("the delivery recheck requires the Finalizer")
        verdict = await finalizer._verify(self.ctx, output)
        if verdict is not None:
            reason, _ = verdict
            raise RuntimeError(
                f"{type(finalizer).name} boundary verification failed: {reason}"
            )

    @asynccontextmanager
    @final
    async def _run_scope(self) -> AsyncIterator[_RunScope]:
        """Keep the Search runtime ready while it and its children run."""
        pipeline_task = asyncio.current_task()
        if pipeline_task is None:
            raise AssertionError("Search.run requires an asyncio task")
        if self._pipeline_task is not None:
            raise AssertionError("Search.run cannot run twice in one process")
        self._pipeline_task = pipeline_task
        # This run IS the interruptible operation, until the display reaches
        # its own quit gate and disarms this. The callback carries the reason,
        # so the terminal key and the Web Workspace both interrupt this one
        # operation and the journal records which of them did.
        product_runtime().display.set_suspend_callback(self.suspend)

        product_runtime().display.render(
            latest_run_state(),
            now_relative=run_clock().run_elapsed_s(),
            now_absolute=time.time(),
        )
        completion_message = ""
        terminal: TerminalStatus | None = None
        body_started = False
        scope = _RunScope()
        async with product_runtime().display:
            self._heartbeat_task = asyncio.create_task(run_heartbeat(self))
            try:
                product_runtime().mcp_client.kaggle_versions_used = latest_run_state().versions_used
                async with self._checked_mcp_client():
                    body_started = True
                    yield scope
                terminal = latest_run_state().aibuildai_search.terminal_status(
                    scope.output
                )
                # The banner leads with the run's OWN cap when one stopped new
                # work, and puts the honest end-of-run tally behind it (issue
                # The actual figure behind the soft-cap overshoot, not
                # just the configured cap), then the results pointer. Why a
                # search method stopped exploring is its own business and is
                # never a product fact.
                completion_message = f"Run files saved under: {latest_run_state().run_home}"
                cap_reason = self.cap_stop_reason()
                if cap_reason is not None:
                    tally = (
                        f"final exploration tally: "
                        f"${latest_run_state().exploration_cost_used_usd():.2f} spent, "
                        f"{run_clock().exploration_elapsed_s() / 60.0:.1f} min explored. "
                    )
                    completion_message = (
                        f"Stopped: {cap_reason}. {tally}{completion_message}"
                    )
                if isinstance(scope.output, Failure):
                    completion_message = scope.output.reason
            except (KeyboardInterrupt, asyncio.CancelledError):
                # A suspend request (Ctrl-C, SIGTERM, Web Workspace Pause) or
                # a recoverable Failure recorded anywhere in the run cancels
                # this pipeline task. Keep the Search resumable and let
                # cancellation reach the joining supervisor.
                request = latest_run_state().aibuildai_search.run_suspend_requested
                failed = latest_run_state().latest_recoverable_failure()
                terminal = TerminalStatus.INTERRUPTED
                completion_message = (
                    request.reason
                    if request is not None
                    else failed[1].reason
                    if failed is not None
                    else "Cancelled by user"
                )
                raise
            except RunTerminalFailure as exc:
                terminal = (
                    TerminalStatus.FAILED_UNEXPECTED
                    if exc.failure.kind is FailureKind.UNEXPECTED
                    else TerminalStatus.FAILED
                )
                # The origin belongs INSIDE the recorded Failure: the reason the
                # root Action persists is the reason the CLI headlines, so the
                # sentence exists once.
                # An Output is immutable and ExecutionOutput.model_copy REFUSES
                # update= by design, so the annotated Failure is built rather
                # than copied. Calling model_copy(update=) here raised TypeError
                # from inside the handler that exists to record why the run
                # ended, which replaced every terminal cause with the
                # TypeError itself and lost the diagnosis.
                scope.output = Failure(
                    kind=exc.failure.kind,
                    reason=(
                        f"required execution {exc.origin_path} made the run "
                        f"terminal: {exc.failure.reason}"
                    ),
                    code=exc.failure.code,
                )
                completion_message = scope.output.reason
            except WorkUnitTimeConfigError as exc:
                # This is a user config failure, not an internal crash.
                terminal = TerminalStatus.FAILED
                completion_message = str(exc)
                scope.output = Failure(
                    kind=FailureKind.PERMANENT, reason=completion_message
                )
            finally:
                await self._shutdown_runtime()
                # A terminal is decided only on the paths above. Anything
                # else unwinding through here is an unknown exception on its
                # way to the recoverable boundary: it records one Failure
                # fact at its origin and ends the epoch, so nothing terminal
                # is recorded and no durable cleanup runs.
                if terminal is not None and terminal is not TerminalStatus.INTERRUPTED:
                    submitters = latest_run_state().work_units_of_type(self, SubmitterAgent)
                    if len(submitters) > 1:
                        raise AssertionError("a Search may have only one Submitter")
                    handle = self._submitter_handle
                    for submitter in submitters:
                        # The Submitter answers one call, whose Handle the
                        # Search kept when it started it.
                        if submitter.record.active_actions and handle is not None:
                            await self.ctx.cancel(
                                handle,
                                Failure(
                                    kind=FailureKind.TIMEOUT,
                                    reason="the Search ended before the Submitter could finish",
                                ),
                                grace_s=0.0,
                            )
                if (
                    terminal is not None
                    and terminal is not TerminalStatus.INTERRUPTED
                    and isinstance(scope.output, Failure)
                ):
                    # A failed run stops the work it started; a delivered one
                    # has nothing left running to stop.
                    await self.ctx._cancel_started_children(scope.output)
                if terminal is not None:
                    log_terminal = (
                        logger.info
                        if terminal
                        in (TerminalStatus.COMPLETED, TerminalStatus.INTERRUPTED)
                        else logger.error
                    )
                    log_terminal(
                        "Search %s: %s",
                        terminal.value,
                        completion_message,
                    )
        if not body_started:
            # Startup saved the terminal result before the Search body could start.
            # The context must still yield once so DurableExecution can return it.
            yield scope

    @final
    async def _shutdown_runtime(self) -> None:
        """Stop tasks and release the run resources."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        run_host().release()

    # --- MCP startup --------------------------------------------------------
    @asynccontextmanager
    async def _checked_mcp_client(self) -> AsyncIterator[None]:
        """Hold the run's one live MCP client open for this body.

        Resume starts the shared client before DBOS recovers workflows, so an
        already-active client is entered by someone else and this only borrows
        it. A startup failure raises ``McpStartupError`` out of the client
        itself, carrying the concrete per-server statuses the CLI reports."""
        mcp_client = product_runtime().mcp_client
        if mcp_client.active:
            yield
            return
        async with mcp_client:
            yield

    # --- Writing the paper -------------------------------------------------
    @final
    async def write_paper(self) -> "str | None":
        """Run a saved WriterAgent, or add an external root when none was saved.

        Returns the path of the paper that Writer execution actually owns, so
        the caller reports the one original rather than a fixed run-global
        address the product no longer writes."""
        output = self._run_output()
        if output is None or output.failed:
            raise AssertionError("write-paper requires a DONE Search")
        if not isinstance(output, SearchOutput):
            raise AssertionError("DONE Search has the wrong Output type")
        task = asyncio.current_task()
        if task is None:
            raise AssertionError("write-paper requires an asyncio task")
        self._pipeline_task = task

        # write-paper runs on a Search that is ALREADY terminal, so the
        # display cannot read liveness from run state: this operation arms
        # its own interrupt, and Ctrl-C (key or signal) cancels this task.
        # The display disarms it when its quit gate begins.
        def stop_writeup(reason: str) -> None:
            del reason
            task.cancel()

        product_runtime().display.set_suspend_callback(stop_writeup)
        from engine.durable_execution import durable_runtime

        # Events flow the moment the MCP client starts, so the durable services
        # must be live before the first record, not first inside drive_writeup.
        async with durable_runtime(self):
            async with product_runtime().display:
                self._heartbeat_task = asyncio.create_task(run_heartbeat(self))
                try:
                    product_runtime().mcp_client.kaggle_versions_used = latest_run_state().versions_used
                    async with self._checked_mcp_client():
                        return await self.drive_writeup()
                finally:
                    await self._shutdown_runtime()

    @final
    async def drive_writeup(self) -> "str | None":
        """Run the writer stage on a finished run.

        Returns the accepted paper's own path under the Writer's artifacts, or
        None when no paper was produced. The Writer checks, compiles, reviews,
        and keeps its paper.
        """
        run_state = latest_run_state()

        writer_message = "Write the final paper about the whole run."
        writer_input = WriterInput(
            instruction=writer_message,
            metric_contract=run_state.metric_contract,
            reviewer_capability=product_runtime().product_capabilities.writer_review,
        )
        if self._run_output() is None:
            writer = WriterAgent(
                input=writer_input,
            )
            result = await (
                await self.ctx.spawn(
                    writer.run,
                    writer_message,
                    # A paper about the run is written FROM the run: every
                    # producer's own directory, the reviews, each execution's
                    # trace, and what the run handed back. This one is a
                    # genuinely broad read job, said as a broad reference.
                    read=(task_data(), self.files(), deliverable_files()),
                    capture_failure=True,
                    capability=granted(
                    product_runtime().product_capabilities.writer, "writer"
                ),
                )
            ).result()
        else:
            from dbos import DBOS
            from engine.durable_execution import execute_durable

            writers = tuple(
                cast(WriterAgent, unit)
                for unit in run_state.walk_units()
                if isinstance(unit, WriterAgent)
                and isinstance(unit.input, WriterInput)
            )
            if len(writers) > 1:
                raise AssertionError("a run may have only one final Writer")
            if writers:
                writer = writers[0]
            else:
                # A root Writer added after the run: the paper is about the
                # whole run, so its upstream is the run's own settled Action.
                root, _ = latest_run_state().create_root(
                    WriterAgent(input=writer_input).run,
                    writer_message,
                    capability=granted(
                        product_runtime().product_capabilities.writer, "writer"
                    ),
                    upstream=tuple(
                        (self.path, settled.ordinal)
                        for settled in self.record.actions
                        if settled.output is not None
                    ),
                    # The same paper about the same run, so the same reads the
                    # ordinary path above declares. A Writer born through the
                    # other door with empty grants could open none of what it
                    # is asked to write about.
                    read=(task_data(), self.files(), deliverable_files()),
                )
                writer = cast(WriterAgent, root)
            settled_result = action_output(writer.record, 1)
            if settled_result is not None:
                result = settled_result
            else:
                action_record = writer.record.action(1)
                handle = await DBOS.start_workflow_async(
                    execute_durable,
                    type(writer).type_key(),
                    writer.path,
                    action_record.ordinal,
                    action_record.method,
                    action_record.request,
                    run_state.version,
                    True,
                )
                result = _action_spec(WriterAgent, "run").output_adapter.validate_python(
                    (await handle.get_result())["output"]
                )
        if result.failed is True:
            logger.warning(
                "writer draft %s failed (%s); no paper produced",
                writer.path,
                result.reason,
            )
            return None
        return f"{writer.artifacts_dir}/{type(writer).required_pdf}"
