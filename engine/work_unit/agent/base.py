"""Concrete LLM agents as durable WorkUnits."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field, fields as dataclass_fields, is_dataclass
from pathlib import Path
from collections.abc import Callable
from typing import (
    Any,
    TYPE_CHECKING,
    ClassVar,
    Coroutine,
    Final,
    Generic,
    Literal,
    TypeVar,
    cast,
    final,
    get_type_hints,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    ValidationError,
)

from engine.capability import ExecutionCapability
from engine.durable_execution import (
    ActionInvocation,
    ActionMethodT,
    ActionRecord,
    ExecutionContext,
    FileRef,
    FileRoot,
    RunSuspending,
    _action_spec,
    _action_target,
    _declared_request,
    action,
    latest_run_state,
    record_durable_event,
    run_clock,
    run_configs,
    running_product_search,
)
from engine.work_unit.events import (
    AgentSpecBuilt,
    BackendLog,
    InvocationStarted,
    RateLimitPaused,
    RateLimitResumed,
    ReasoningDelta,
    SessionReplaced,
    SessionStarted,
    StructuredOutputRejected,
    TextDelta,
    ToolUseEnd,
    ToolUseStart,
    TurnComplete,
    UsageDelta,
)
from engine.cost import Cost, InvocationResult, Usage
from engine.failure import Failure, FailureKind
from engine.execution_output import SuccessfulOutput, VerifierOutput
from engine.work_unit.agent.policy import RolePolicy
from engine.work_unit.base import (
    _RuntimeValue,
    WorkUnit,
    WorkUnitRuntimeRecord,
)
from infra.backends.claude.errors import AgentError, BackendError, PermanentError
from infra.backends.claude.conversation import Conversation
from infra.backends.claude.event_adapter import ConversationObserver
from infra.backends.one_shot import rejected_resubmit
from infra.cost_observation import local_rates_by_model

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.event.base import Event
    from engine.run_state import RunState
    from infra.backends.spec import AgentSpec


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentInput:
    """Base for every role's immutable business input.

    ``input.json`` is a view of the same immutable value carried by the creation fact. Each Agent family's own Input and Output records live beside that Agent in its ``io.py``. ``json_dict()`` feeds the transcript's ``input.json`` and the Agent Input values block; prompt templates read the same immutable Input object directly.
    """

    __pydantic_config__ = ConfigDict(extra="forbid")

    # What this role's own semantic review may spend, when this run composes
    # one, and None when it does not. It lives HERE, on the identity Input,
    # because that is the only creation fact the journal keeps: an execution is
    # recorded as its Input plus its Capability and rebuilt from exactly those
    # two, so a grant carried anywhere else is silently None on the instance
    # that actually runs the Action. Keyword-only so a role's own business
    # fields keep their order.
    reviewer_capability: "ExecutionCapability | None" = field(
        default=None, kw_only=True
    )

    def __post_init__(self) -> None:
        """Validate and normalize every field into its immutable declared type."""
        hints = get_type_hints(type(self), include_extras=True)
        for data_field in dataclass_fields(self):
            value = TypeAdapter(hints[data_field.name]).validate_python(
                getattr(self, data_field.name)
            )
            object.__setattr__(self, data_field.name, value)

    def json_dict(self) -> dict[str, object]:
        """Return every business Input field as a plain JSON value.

        The reviewer grant is not one: it is a resource fact, like the role's
        own ``capability``, which this view has never carried either. The
        journal records the whole Input regardless, through the Input type's
        own adapter, so leaving it out here changes what a role READS and
        nothing about what the run keeps."""
        out: dict[str, object] = {}
        hints = get_type_hints(type(self), include_extras=True)
        for data_field in dataclass_fields(self):
            if data_field.name == "reviewer_capability":
                continue
            out[data_field.name] = TypeAdapter(hints[data_field.name]).dump_python(
                getattr(self, data_field.name), mode="json"
            )
        return out


AgentInputT = TypeVar("AgentInputT", bound=AgentInput)
AgentSuccessT = TypeVar("AgentSuccessT", bound=SuccessfulOutput)


class CostLimitError(Exception):
    """The current cost crossed this Agent's hard cap."""


def backend_failure_kind(exc: BaseException) -> FailureKind:
    """Map one backend failure to the product failure kind.

    One answer for every Agent. What a failed Agent means to whoever started it
    is that caller's own concern, and nothing here needs to know which Agents
    are verifying another one: a verifier chain reports its entry's Failure
    and goes on, because a verifier that failed gave no verdict."""
    if isinstance(exc, PermanentError):
        if exc.reason is PermanentError.Reason.RETRY_CEILING:
            return FailureKind.RATE_LIMITED
        return FailureKind.PERMANENT
    if isinstance(exc, AgentError):
        return FailureKind.MEMORY_CAP if exc.oom_killed else FailureKind.INFRA
    if isinstance(exc, BackendError):
        return FailureKind.UNEXPECTED
    raise AssertionError(f"{type(exc).__name__} is not a backend failure")


class AgentRuntimeRecord(WorkUnitRuntimeRecord):
    """Conversation facts owned only by Agent."""

    session_id: str | None = None
    cost: Cost = Field(default_factory=Cost)
    resolved_model: str | None = None
    structured_output_payload: dict[str, object] | None = None
    # How many provider requests have completed since this conversation segment
    # began. A rejected candidate opens the next segment, whose owed
    # message is that segment's own durable step argument.
    segment_turns: int = 0


class _AgentObserver(ConversationObserver):
    """The durable Agent's side of the Conversation seam: each fact the Conversation reports is journaled as this Agent's existing Event, with the engine-owned facts (the catalog rates Cost may select) attached first. Every pause and resume is the Conversation's own fact: it was told the open pause at construction, so nothing here infers or suppresses provider recovery."""

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent

    def _record(self, event_type: type["Event"], **facts: object) -> None:
        running_product_search().record_event(event_type, self._agent, **facts)

    def invocation_started(self) -> None:
        # The Action this send belongs to is settled here, while it is the one
        # running, and travels on the fact. A fold has records and no running
        # coroutine to ask.
        self._record(InvocationStarted, action=self._agent.ctx.ordinal)

    def session_started(self, *, session_id: str, model: str, cwd: str, tools: tuple[str, ...], mcp_server_names: tuple[str, ...], launcher_path: str | None) -> None:
        self._record(SessionStarted, session_id=session_id, model=model, cwd=cwd, tools=tools, mcp_server_names=mcp_server_names, launcher_path=launcher_path)

    def session_replaced(self, *, session_id: str) -> None:
        self._record(SessionReplaced, session_id=session_id)

    def text_delta(self, text: str, *, parent_tool_use_id: str | None) -> None:
        self._record(TextDelta, text=text, parent_tool_use_id=parent_tool_use_id)

    def reasoning_delta(self, text: str, *, parent_tool_use_id: str | None) -> None:
        self._record(ReasoningDelta, text=text, parent_tool_use_id=parent_tool_use_id)

    def tool_use_start(self, *, tool_use_id: str, name: str, input: dict, parent_tool_use_id: str | None) -> None:
        self._record(ToolUseStart, tool_use_id=tool_use_id, name=name, input=input, parent_tool_use_id=parent_tool_use_id)

    def tool_use_end(self, *, tool_use_id: str, output: str | list | None, is_error: bool, parent_tool_use_id: str | None) -> None:
        self._record(ToolUseEnd, tool_use_id=tool_use_id, output=output, is_error=is_error, parent_tool_use_id=parent_tool_use_id)

    def usage_delta(self, *, input_tokens: int, output_tokens: int, cached_input_tokens: int, cache_creation_input_tokens: int | None, web_search_requests: int, speed: str, model: str, cumulative: bool, message_id: str | None, parent_tool_use_id: str | None) -> None:
        agent = self._agent
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cached_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens or 0,
            web_search_requests=web_search_requests,
            speed=speed,
        )
        # A repeated snapshot of one call's usage is not a second fact.
        if not agent.cost.has_usage_snapshot(usage, cumulative=cumulative, message_id=message_id):
            self._record(
                UsageDelta,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
                model=model,
                cumulative=cumulative,
                message_id=message_id,
                web_search_requests=web_search_requests,
                speed=speed,
                local_rates_by_model=local_rates_by_model((model,), agent.resolved_model),
                parent_tool_use_id=parent_tool_use_id,
            )
        agent._enforce_cost_ceiling()

    def turn_complete(self, *, duration_s: float | None, duration_api_s: float | None, cost_usd: float | None, stop_reason: str, num_turns: int, usage_by_model: dict[str, dict[str, float]] | None, structured_output: dict[str, Any] | None) -> None:
        agent = self._agent
        self._record(
            TurnComplete,
            duration_s=duration_s,
            duration_api_s=duration_api_s,
            cost_usd=cost_usd,
            stop_reason=stop_reason,
            num_turns=num_turns,
            usage_by_model=usage_by_model,
            local_rates_by_model=local_rates_by_model(usage_by_model or (), agent.resolved_model),
            structured_output=structured_output,
        )
        agent._enforce_cost_ceiling()

    def backend_log(self, *, level: Literal["debug", "info", "warn", "error"], source: str, text: str) -> None:
        self._record(BackendLog, level=level, source=source, text=text)

    def rate_limit_paused(self) -> None:
        self._record(RateLimitPaused)

    def rate_limit_resumed(self) -> None:
        self._record(RateLimitResumed)


_TOOL_MARK: Final = "__aibuildai_tool__"


def tool(method: ActionMethodT) -> ActionMethodT:
    """Publish one ordinary async method to this role's own model as a standard MCP tool.

    The method name is the tool name, its docstring is the description the model reads, the
    one annotated business request beside ``self``, when there is one, is its declared input,
    and its annotated result is what the model is handed back. That result is any type Pydantic
    can serialize -- ``str``, ``dict[str, int]``, a model -- because a tool is NOT an Action and
    does not carry an Action's durable success ABI. Only a declared method whose value is
    journalled as one execution's outcome owes ``SuccessfulOutput``; this value is read by a
    model inside one conversation and never becomes an Action's recorded result.
    The body runs on this Agent's Action Workflow side, outside the opaque conversation Step, so
    it may start real child Actions through ``self.ctx`` and hand back the typed result they
    settled with. A returned ``Failure`` is how the method tells the model its work failed, and
    that reason reaches the model as this tool's own error. Any other exception raised on this
    side is a defect, not an answer: it ends the Action and recovers the ordinary durable way,
    rather than telling the model a broken framework is a tool it can try again. What the model
    sends is a separate matter and is refused on the model's own side: ``engine.mcp.tool_server``
    validates the arguments against this method's published schema and answers an invalid call
    as this tool's error, and it also answers any failure of the crossing itself that way, so a
    dead crossing still reads to the model as a failed tool. It is not an Action: a direct Python
    call stays an ordinary method call. A declaration that is not an ordinary async method with an
    annotated result is refused here, at the door, rather than published and left to fail on the
    model's first call."""
    if (isinstance(method, (classmethod, staticmethod)) or not inspect.iscoroutinefunction(method)
            or "return" not in getattr(method, "__annotations__", {})):
        raise AssertionError(f"@tool {getattr(method, '__name__', method)!r} must be an ordinary async method with an annotated result")
    setattr(method, _TOOL_MARK, True)
    # The guard above is a TypeGuard, so it narrows the declared method away.
    return cast(ActionMethodT, method)


class Agent(
    WorkUnit[AgentInputT],
    Generic[AgentInputT, AgentSuccessT],
):
    """One persisted LLM execution with its role behavior and observations."""

    kind: ClassVar[Literal["llm_agent"]] = "llm_agent"
    session_id: ClassVar[_RuntimeValue[str | None]] = _RuntimeValue("session_id")
    cost: ClassVar[_RuntimeValue[Cost]] = _RuntimeValue("cost")
    resolved_model: ClassVar[_RuntimeValue[str | None]] = _RuntimeValue(
        "resolved_model"
    )
    structured_output_payload: ClassVar[_RuntimeValue[dict[str, object] | None]] = (
        _RuntimeValue("structured_output_payload")
    )
    segment_turns: ClassVar[_RuntimeValue[int]] = _RuntimeValue("segment_turns")
    name: ClassVar[str]
    prompt_template: ClassVar[str | None] = None

    max_turns: ClassVar[int | None] = None
    _intermediate: ClassVar[bool] = True
    policy: ClassVar[RolePolicy | None] = None

    @property
    @final
    def reviewer_capability(self) -> "ExecutionCapability | None":
        """What this role's own semantic review may spend, or None for no review.

        Read from this role's own immutable Input, which is the fact the run
        journals and rebuilds an execution from."""
        return self.input.reviewer_capability

    # The directory this role's session opens in, when the role owns one that
    # is not its own scratch. The launch boundary asks the role; it holds
    # no list of which roles are special.
    def launch_directory(self) -> str | None:
        return None

    def reviewer_reads(self) -> "tuple[FileRef, ...]":
        """Resources this role's own reviewers read and this role does not.

        A reviewer is granted what its producer could touch, which is right
        whenever the review is of the producer's own work. A role whose
        reviewer audits the producer AGAINST material the producer must never
        see answers here, and the producer stays without it."""
        return ()

    def prompt_facts(self) -> dict[str, object]:
        """Facts only this role's own package can name, for its own template.

        Generic launch renders the run-neutral facts and this Action's granted
        locations. A role whose prompt must name one product resource -- the
        grading dir it writes, the record file it appends to -- answers here,
        so the resource stays named by the package that owns it."""
        return {}

    @property
    def _runtime(self) -> AgentRuntimeRecord:
        runtime = super()._runtime
        if not isinstance(runtime, AgentRuntimeRecord):
            raise AssertionError(f"{self.path!r} has no Agent runtime record")
        return runtime

    def _new_family_record(self) -> AgentRuntimeRecord:
        return AgentRuntimeRecord()

    @property
    def scratch_dir(self) -> str:
        return f"{self.directory}/state/scratch"

    @property
    def artifacts_dir(self) -> str:
        return f"{self.directory}/state/artifacts"

    def artifacts_dir_at(self, run_state: "RunState", ordinal: int, attempt: int) -> str:
        """One Agent identity keeps one workspace across every Action.

        Its files are the identity's, not one Action's, so every occurrence
        answers the same address on purpose."""
        del ordinal, attempt
        return f"{run_state.execution_directory(self.path)}/state/artifacts"

    required_pdf: ClassVar[str | None] = None
    # Every @tool this role publishes to its own model, settled once at class
    # formation: the name, the adapter for its one declared request or None,
    # and the adapter for its one declared success type. A declaration that is
    # not an ordinary async method with zero or one typed request and one
    # declared success type never reaches a model, because settling it here
    # raises while the class is still being built.
    tools: ClassVar[dict[str, tuple[TypeAdapter | None, TypeAdapter]]] = {}

    # --- Live-run state, bound while one Action owns the conversation.
    # PrivateAttrs, never fields: process state, not journaled facts.
    _agent_spec: "AgentSpec | None" = PrivateAttr(default=None)
    _live_conversation: "Conversation | None" = PrivateAttr(default=None)
    _session_ready: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    # The raw terminal candidate of the last completed turn, and what the
    # gates have since cleared. Not the same fact: the schema, verifier, and
    # reviewer run at the top of the next pass, so a fault that ends that
    # turn leaves work nobody examined. Only ``_verified_output`` is keepable.
    _submitted_output: "dict[str, object] | None" = PrivateAttr(default=None)
    _verified_output: "BaseModel | None" = PrivateAttr(default=None)
    # True from a re-entry that found a provider request still open until the
    # first send of this pass recovered it.
    _recovering: bool = PrivateAttr(default=False)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if not cls._is_concrete_definition():
            return
        if inspect.isabstract(cls):
            raise AssertionError(
                f"{cls.__name__} is abstract; a shared Agent base declares "
                "_intermediate: ClassVar[bool] = True"
            )
        if not isinstance(cls.policy, RolePolicy):
            raise AssertionError(f"{cls.__name__} must declare a RolePolicy")
        if cls.prompt_template is None:
            raise AssertionError(f"{cls.__name__} must declare prompt_template")
        if not cls.prompt_template.startswith(
            "agent/"
        ) or not cls.prompt_template.endswith(".j2"):
            raise AssertionError(
                f"{cls.__name__}.prompt_template must name a .j2 file under agent/"
            )
        input_type = cls.model_fields["input"].annotation
        if not (
            isinstance(input_type, type)
            and issubclass(input_type, AgentInput)
            and is_dataclass(input_type)
        ):
            raise AssertionError(
                f"{cls.__name__}.Input must be a frozen AgentInput dataclass"
            )
        if cls.required_pdf is not None and (
            Path(cls.required_pdf).is_absolute()
            or ".." in Path(cls.required_pdf).parts
        ):
            raise AssertionError(
                f"{cls.__name__}.required_pdf must be a path under the role's "
                "own artifacts, which every role can write"
            )
        cls.tools = {
            name: (
                None if (r := _declared_request(cls, name, f)) is None else TypeAdapter(r[1]),
                TypeAdapter(get_type_hints(f)["return"]),
            )
            for name in dir(cls)
            if getattr((m := inspect.getattr_static(cls, name, None)), _TOOL_MARK, False)
            and (f := inspect.unwrap(cast(Callable[..., object], m))) is not None
        }

    @classmethod
    def semantic_reviewer(cls) -> "type[Agent[Any, VerifierOutput]] | None":
        """The Agent role this role composes to judge its own candidates.

        A role that has one names it here and nowhere else: the reviewer
        reaches the run through this answer, so no Search list, config
        allowlist, or registry has to repeat that the role has a reviewer at
        all -- and that includes the producer's own chain, which constructs
        THIS answer rather than naming the class a second time, so a static
        composition and a runtime one cannot disagree. Whether it takes part in
        a given run is ``composes_reviewer``. ``None`` is a role that judges
        its candidates itself."""
        return None

    @classmethod
    def enabled_in(cls, configs: "AgentConfig") -> bool:
        """Whether this role takes part in a run configured this way.

        One activation authority for every Agent: the active set asks each
        declared role this question, so a role the operator can switch off
        answers it here, beside the role, instead of being deleted by name
        somewhere the role cannot see. Most roles are part of their Search's
        composition unconditionally and answer True."""
        del configs
        return True

    @property
    @final
    def llm_cost(self) -> float:
        return self.cost.total_cost_usd

    def action_llm_cost(self, ordinal: int) -> float:
        return self.cost.action_cost_usd(ordinal)

    @property
    @final
    def llm_budget_s(self) -> float | None:
        return self.capability.wall_clock_seconds

    @property
    @final
    def rate_limited(self) -> bool:
        return self._action.rate_limited_at_s is not None

    @final
    def _active_action(self) -> ActionRecord:
        """The one Action this Agent is answering.

        An Agent's conversation is one lineage, so it answers one call at a
        time; a conversation fact therefore names its Action without carrying
        an ordinal of its own."""
        active = self.record.active_actions
        if len(active) != 1:
            raise AssertionError(f"{self.path!r} has no active Action")
        return active[0]

    # --- Preparing one run -------------------------------------------------
    @final
    async def _time_budget_reminder(
        self,
        *,
        interval_s: float,
        finalize_threshold_pct: float,
    ) -> None:
        clock = run_clock()
        budget_s = self.capability.wall_clock_seconds
        if budget_s is None:
            raise AssertionError("time reminder requires a bounded clock")
        budget_min = int(budget_s / 60)
        threshold_frac = finalize_threshold_pct / 100.0
        while True:
            await asyncio.sleep(interval_s)
            remaining_s = clock.effective_remaining_s(self)
            if remaining_s <= 0:
                return
            remaining_min = int(remaining_s / 60)
            remaining_pct = int(remaining_s / budget_s * 100)
            if remaining_s / budget_s <= threshold_frac:
                message = (
                    f"[Budget Reminder — FINALIZE] ~{remaining_min} min remaining "
                    f"out of {budget_min} min ({remaining_pct}%). "
                    f"Stop optional work. Use only the minimum tools needed to "
                    f"satisfy this role's explicit completion contract, then call "
                    f"StructuredOutput immediately."
                )
            else:
                message = (
                    f"[Budget Reminder] ~{remaining_min} min remaining out of "
                    f"{budget_min} min ({remaining_pct}%). [authoritative wall-clock]"
                )
            if not await self.add_runtime_message(message):
                raise AssertionError("budget reminder has no open Agent session")
            running_product_search().record_event(
                BackendLog,
                self,
                level="info",
                source="budget",
                text=message,
            )

    @final
    async def _cost_budget_reminder(
        self,
        *,
        interval_s: float,
        finalize_threshold_pct: float,
    ) -> None:
        soft_frac = 1.0 - finalize_threshold_pct / 100.0
        while True:
            await asyncio.sleep(interval_s)
            cap = self.capability.cost_cap_usd
            if cap is None:
                continue
            spent = self.cost.action_cost_usd(self.ctx.ordinal)
            if spent < cap * soft_frac:
                continue
            message = (
                f"[Budget Reminder — FINALIZE] live spend ~${spent:.2f} of "
                f"${cap:.2f} USD cap. Stop optional work. Use only the minimum "
                f"tools needed to satisfy this role's explicit completion "
                f"contract, then call StructuredOutput immediately."
            )
            if not await self.add_runtime_message(message):
                raise AssertionError("cost reminder has no open Agent session")
            running_product_search().record_event(
                BackendLog,
                self,
                level="info",
                source="budget",
                text=message,
            )
            return

    @final
    def _scheduled_reminders(self) -> list[Coroutine[Any, Any, None]]:
        """Build the wall-clock and cost reminders for this Agent run."""
        configs = run_configs()
        budget_s = self.capability.wall_clock_seconds
        divisor = int(configs.run.budget.reminder_interval_divisor)
        if budget_s is None or budget_s <= 0 or divisor <= 0:
            return []
        interval_s = max(10.0, budget_s / divisor)
        reminders: list[Coroutine[Any, Any, None]] = [
            self._time_budget_reminder(
                interval_s=interval_s,
                finalize_threshold_pct=configs.run.budget.finalize_threshold_pct,
            )
        ]
        if self.capability.cost_cap_usd is not None:
            reminders.append(
                self._cost_budget_reminder(
                    interval_s=interval_s,
                    finalize_threshold_pct=configs.run.budget.finalize_threshold_pct,
                )
            )
        return reminders

    @action
    async def run(self, message: str) -> AgentSuccessT | Failure:
        """Answer one caller message in this Agent's one conversation.

        An Agent has this one public Action, so it carries the name every
        single-Action durable execution carries. `_run` is the Attempt loop
        every WorkUnit family shares, so it is typed by the shared base; every
        Attempt re-enters `execute` with this same call's message. This
        annotation is this Action's result authority, and `execute` produced
        exactly it."""
        return cast("AgentSuccessT | Failure", await self._run(message))

    # --- Running one Agent and its backend conversation --------------------
    async def execute(self, *request: object) -> AgentSuccessT | Failure:
        """Start or resume one Agent and drive its conversation to an Output."""
        message = cast(str, request[0])
        await self._prepare_conversation()
        agent_spec = await running_product_search().prepare_agent(self)
        # Bind the live-run state at the door; a re-entered Agent runs this
        # again, so every private starts fresh here rather than at declaration.
        # No folded conversation fact is read here: the workflow body replays
        # from the state its last durable point left, and what a crashed step
        # recorded reaches this Agent only inside the re-entered step, so the
        # conversation's facts are read at that step's entry.
        self._agent_spec = agent_spec
        self._live_conversation = None
        self._submitted_output = None
        self._verified_output = None
        model = self.resolved_model or agent_spec.model
        if model != agent_spec.model:
            raise AssertionError(
                f"{type(self).__name__} resolved model changed from "
                f"{model!r} to {agent_spec.model!r}"
            )
        if self.resolved_model is None:
            running_product_search().record_event(AgentSpecBuilt, self, model=model)
        hook_tasks = [
            asyncio.create_task(reminder) for reminder in self._scheduled_reminders()
        ]

        async def drive() -> AgentSuccessT | Failure:
            resumed = self._resume_result()
            if resumed is not None:
                return resumed
            return await self._conversation(message)

        result: AgentSuccessT | Failure | None = None
        try:
            try:
                try:
                    result = await drive()
                finally:
                    await self._close_conversation()
            except BackendError as exc:
                result = self._backend_failure(exc)
            except CostLimitError as exc:
                result = Failure(kind=FailureKind.COST_LIMIT, reason=str(exc))
            if isinstance(result, Failure) and self.rate_limited:
                failure = result
                result = Failure(
                    kind=FailureKind.RATE_LIMITED,
                    reason=failure.reason,
                    code=failure.code,
                )
        finally:
            for task in hook_tasks:
                task.cancel()
            if hook_tasks:
                await asyncio.gather(*hook_tasks, return_exceptions=True)
            self._session_ready.set()

        if result is None:
            raise AssertionError("Agent execution ended without an Output")

        result = self._settle_terminal_output(result, self._verified_output)
        result = self._finalize_output(result)
        return result

    @final
    def _backend_failure(self, exc: BackendError) -> Failure:
        """Keep provider failure facts inside the Agent before a durable step returns."""
        if isinstance(exc, PermanentError):
            return Failure(
                kind=backend_failure_kind(exc),
                reason=f"PermanentError: {exc}",
                code=exc.provider_code or exc.reason.value,
            )
        if isinstance(exc, AgentError):
            # A fatal backend subprocess is an expected infrastructure
            # failure, not an internal Python bug. Preserve its diagnostic
            # in the typed Failure so mandatory phases reach the ordinary
            # normal failed-run headline instead of the crash banner.
            #
            # Lead with what the product OWNS -- which role stopped,
            # what it had spent, and the cap it was spending against -- and
            # keep the backend's own text after it.
            #
            # When the kernel's own per-cgroup counter says this
            # unit was OOM-killed, say the memory cap and classify it as
            # one, the way a training subprocess in the same report
            # already does.
            # Both halves are this ACTION's: the cap it was spending
            # against, and what IT spent. Reading the identity's lifetime
            # bill here would tell a second call of the same Agent that it
            # spent more than its cap on work an earlier call did.
            cap = self.capability.cost_cap_usd
            spent = self.cost.action_cost_usd(self.ctx.ordinal)
            against = "" if cap is None else f" of its ${cap:.4f} cap"
            stopped = (
                "was killed at its memory cap (kernel OOM kill, attributed "
                "by the unit's cgroup memory.events)"
                if exc.oom_killed
                else "stopped before it finished"
            )
            return Failure(
                kind=backend_failure_kind(exc),
                reason=(
                    f"the model backend process running {type(self).name} "
                    f"{stopped}, after spending "
                    f"${spent:.4f}{against}. "
                    f"The backend reported: {exc}"
                ),
            )
        return Failure(
            kind=backend_failure_kind(exc),
            reason=f"{type(exc).__name__}: {exc}",
        )

    @final
    async def _conversation(self, message: str) -> AgentSuccessT | Failure:
        """Run conversation segments until one candidate passes this Action's verifier chain.

        ``message`` is this Action's own argument and opens the first segment.
        A rejected candidate opens the next segment with the rejection's own
        sentence, so the message a segment owes is that segment's own durable
        step argument and a re-entered process owes exactly what it owed
        before. Every candidate starts the chain again at its first
        verifier: nothing an earlier candidate or an earlier Action passed
        carries over."""
        prompt = message
        rejected = ""
        while True:
            candidate = await self._run_conversation_segment(prompt)
            if isinstance(candidate, Failure):
                return self._no_candidate(candidate, rejected)
            outcome = self._validated_candidate(candidate)
            if isinstance(outcome, str):
                reason, verifier = outcome, ""
            else:
                verdict = await self._verify(self.ctx, outcome)
                if verdict is None:
                    self._verified_output = outcome
                    return outcome
                reason, verifier = verdict
            record_durable_event(
                StructuredOutputRejected, self, reason=reason, verifier=verifier
            )
            self._verified_output = None
            rejected = reason
            if run_clock().effective_remaining_s(self) <= 0:
                return Failure(
                    kind=FailureKind.NO_OUTPUT,
                    reason=f"{type(self).name} ran out of time after its "
                    f"candidate failed verification: {reason}",
                )
            prompt = rejected_resubmit(
                gate="verification",
                instruction=(
                    "Address the reason below and call StructuredOutput again."
                ),
                label="Reason",
                reason=reason,
            )

    @final
    def _no_candidate(
        self, failure: Failure, rejected: str
    ) -> Failure:
        """Say why this Action ends with no accepted candidate.

        Naming the last rejection is honest whichever verifier gave it:
        any of them can reject a candidate the model then declines to replace.
        Only a call that never had a candidate rejected has none to name."""
        if failure.kind is not FailureKind.NO_OUTPUT or not rejected:
            return failure
        return Failure(
            kind=FailureKind.NO_OUTPUT,
            reason=f"{type(self).name} produced no returnable output: "
            f"its last candidate failed verification: {rejected}",
            code=failure.code,
        )

    @final
    def _validated_candidate(self, candidate: dict[str, object]) -> "AgentSuccessT | str":
        """Validate one raw provider candidate against this Action's Output schema.

        The concrete schema with the Agent's own validation context is the
        chain's first gate and the framework owns it: a candidate that is not
        even this Action's Output cannot be handed to a verifier that is typed
        by it. A string is the rejection reason owed back to the model."""
        try:
            return cast(
                "AgentSuccessT",
                _action_spec(type(self), "run").success.model_validate(
                    candidate, context={"input": self.input}
                ),
            )
        except ValidationError as exc:
            return (
                f"{type(self).name} Output does not validate: {exc}. "
                "Fix the problem and call StructuredOutput again."
            )

    @final
    async def _verify(
        self, ctx: "ExecutionContext", candidate: "AgentSuccessT"
    ) -> "tuple[str, str] | None":
        """Run one candidate through the verifier invocations its producer built.

        The producer's list order is the whole order. Each entry is already a
        fully determined Action invocation, so this starts it as an ordinary
        child WorkUnit Action through the one ``ExecutionContext`` and stops at
        the first negative verdict. It never asks what family a verifier
        belongs to.

        The caller supplies the running Action's context, so the producer owns
        construction wherever the chain runs: a delivery recheck outside the
        producer's own Action asks the producer for the same invocations
        instead of building a second copy of them.

        ``None`` accepts the candidate and a ``(reason, verifier)`` pair is
        the first rejecting verdict with the name that gave it, and those are
        the only two answers this can give. A verifier that FAILED gave no
        verdict at all: it is reported and skipped, it cannot reject the
        candidate, and it never becomes the failure of the producer that ran
        it.

        The producer's list is the WHOLE order. Nothing is spliced into it
        here, not even the gate on the document this base asked the role for:
        a role that promises a document places that gate itself, where it
        belongs among its own checks."""
        for invocation in await self.verifier_invocations(candidate):
            target = invocation[0]
            unit, _ = _action_target(target)
            if not isinstance(unit, WorkUnit):
                raise AssertionError(
                    f"{type(unit).__name__} is not a WorkUnit, so it cannot "
                    "verify a candidate"
                )
            # No upstream: a verifier reads an intermediate candidate of the
            # Action that is running right now. Containment already records
            # which Action owns it, and an active Action is never a legal
            # upstream source.
            # A reviewer is granted READ over everything its producer could
            # touch -- what the producer read, what it wrote, and its own tree
            # -- plus whatever the producer says its reviewers additionally
            # need. It judges one submission against the material the producer
            # worked from, and against the files that submission points at,
            # which for SETUP is the run resources it just filled; a role whose
            # reviewer audits it against material IT must not see adds that
            # itself. It never receives a write: the reviewed work stays
            # immutable to the role judging it.
            # A self-reference in the producer's own grants means the
            # PRODUCER's tree, so it is resolved here rather than forwarded:
            # left alone it would resolve again at the reviewer's launch, and
            # mean the reviewer's own tree instead. Naming the owner keeps the
            # rest of the reference, so a narrowed one stays narrowed.
            own = self.files()
            inherited = tuple(
                ref.model_copy(update={"owner": self.path})
                if ref.root is FileRoot.EXECUTION and not ref.owner
                else ref
                for ref in (*self._action.read, *self._action.write)
            )
            grants = (*inherited, own, *self.reviewer_reads())
            handle = (
                await ctx.spawn(
                    target,
                    read=grants,
                    capability=invocation[1],
                    capture_failure=True,
                )
                if len(invocation) == 2
                else await ctx.spawn(
                    target,
                    invocation[2],
                    read=grants,
                    capability=invocation[1],
                    capture_failure=True,
                )
            )
            verdict = await handle.result()
            if verdict.failed is True:
                # A verifier that FAILED never judged this candidate, so it
                # has said nothing about it. Rejecting work is a verdict and
                # only a verifier that ran can give one, so a failed verifier
                # neither rejects the candidate nor becomes the producer's own
                # failure: it is reported here and the chain goes on. Letting
                # it answer for the candidate is what settled a dead worker as
                # the review's outcome and killed the run at the first
                # ancestor that had not captured it.
                logger.warning(
                    "%s: verifier %s gave no verdict: %s",
                    self.path,
                    type(unit).name_segment(),
                    cast(Failure, verdict).reason,
                )
                continue
            if not isinstance(verdict, VerifierOutput):
                raise AssertionError(
                    f"{type(unit).__name__} returned "
                    f"{type(verdict).__name__}, not a VerifierOutput"
                )
            if not verdict.passed:
                return verdict.reason, type(unit).name_segment()
        return None

    @property
    @final
    def review_capability(self) -> ExecutionCapability:
        """What this role's own semantic review may spend.

        A role that composes a reviewer carries the reviewer's resolved
        capability on its own Input, exactly like its business facts. It is not
        looked up: the package that decided to run this role decided what
        reviewing it costs, at the spawn site, once."""
        if self.reviewer_capability is None:
            raise AssertionError(
                f"{type(self).name} composes a reviewer but was constructed "
                "without reviewer_capability"
            )
        return self.reviewer_capability

    async def verifier_invocations(
        self, candidate: "AgentSuccessT"
    ) -> "tuple[ActionInvocation[Any, VerifierOutput], ...]":
        """This candidate's ordered verifier Action invocations, freshly built.

        The producer is the construction owner because it alone knows both its
        own stable facts and the candidate in front of it, and it is the ONLY
        owner: no registry elsewhere holds a second list of what verifies this
        role. Each entry is one fresh WorkUnit's ``run`` bound method, with the
        request that Action takes when it declares one.

        A fresh object per candidate and per slot: a verifier never sees a
        candidate it has already judged, and no verifier conversation state
        crosses candidates or Actions. A role with nothing to verify returns
        the empty chain."""
        del candidate
        return ()

    @classmethod
    def composes_reviewer(cls, configs: "AgentConfig") -> bool:
        """Whether a run configured this way composes this role's reviewer.

        One predicate, asked by everything that needs the answer: the producer
        building its chain, the active Agent set, and the config check. The
        switch is keyed by THIS role's name because that is what
        ``verifier.enable`` names, so no reviewer has to carry a copy of its
        producer's name to find its own switch. A role whose review is not
        optional overrides this and says so."""
        return cls.semantic_reviewer() is not None and configs.verifier.enable.get(
            cls.name, False
        )

    @final
    def document_verifier(self) -> "tuple[ActionInvocation[Any, VerifierOutput], ...]":
        """The gate on the document this role promised through ``required_pdf``.

        It is built here because ``required_pdf`` is declared here and the
        answer is the same for every role that has one, and it is RETURNED,
        never spliced: the role splices it into its own chain, at the position
        it chooses, so the producer's order stays the whole order. Empty when
        this invocation asked the role for no document at all, so a chain can
        splice the answer in either way."""
        from engine.work_unit.program.document_verifier import (
            DocumentVerifierInput,
            DocumentVerifierProgram,
        )

        required = self.required_pdf_for_run()
        if required is None:
            return ()
        return (
            (
                DocumentVerifierProgram(
                    input=DocumentVerifierInput(
                        document_path=str(Path(self.artifacts_dir) / required),
                        artifacts_dir=self.artifacts_dir,
                    ),
                ).run,
                ExecutionCapability(
                    wall_clock_seconds=self._local_wall_clock,
                    gpus=0
                ),
            ),
        )

    async def _run_conversation_segment(
        self, prompt: str
    ) -> "dict[str, object] | Failure":
        """Checkpoint one leaf backend conversation segment."""
        # With tools declared, the same conversation runs as a served Step: its
        # model calls each declared method back here, on this Agent's own
        # Workflow, where a method may start real child Actions.
        if type(self).tools:
            return cast(
                "dict[str, object] | Failure",
                await self.ctx._serve(
                    self, type(self).tools, run_agent_conversation, self.path, prompt, preemptible=True
                ),
            )
        return cast(
            "dict[str, object] | Failure",
            await self.ctx.step(
                run_agent_conversation, self.path, prompt, preemptible=True
            ),
        )

    @final
    async def _conversation_step(self, prompt: str) -> "dict[str, object] | Failure":
        """Run the live Conversation until it yields one terminal candidate.

        The provider seam and nothing else: this returns the raw structured
        candidate the model submitted, and every gate on it runs above, in the
        Action workflow, where a durable child WorkUnit is legal to start.
        Provider interruptions are the Conversation's business and never reach
        this loop. What this loop sends is read from the folded journal each
        pass, so a live pass and a re-entered one owe the model the same
        message.
        """
        # Read at the step's entry, once the facts a crashed predecessor left
        # have reached this Agent. The last completed turn's candidate is
        # offered again at the top of the first pass, unless a later request
        # of the same send is still open: it may have acted on a notification,
        # so nothing is examinable until it is recovered.
        self._recovering = self.cost.invocation_is_open()
        self._submitted_output = None if self._recovering else self.structured_output_payload
        while True:
            if self._submitted_output is not None:
                unfinished = self._unfinished_work_notice()
                if unfinished is None:
                    return self._submitted_output
                # Role lifecycle, never a verdict: the candidate is neither
                # judged nor kept, and the same conversation is told what this
                # role must still do before it may finish.
                self._submitted_output = None
                message = unfinished
            elif self.segment_turns == 0 or self._recovering:
                # The segment's own durable step argument, until a request
                # answers it; a request still open from before a re-entry is
                # recovered with the same message it was carrying.
                message = prompt
            else:
                unfinished = self._unfinished_work_notice()
                if unfinished is None:
                    # The model answered without a candidate and owes nothing
                    # more; this Action has none to verify.
                    return Failure(
                        kind=FailureKind.NO_OUTPUT,
                        reason=f"{type(self).name} produced no returnable "
                        "output: the model produced no structured output",
                    )
                message = unfinished
            failure = await self._send(message)
            if failure is not None:
                return failure

    @final
    def _pause_open_for_s(self) -> float | None:
        """How long this Action's recorded provider outage pause has been open, or None: the one folded fact the Conversation needs to own that pause."""
        started = self._action.rate_limited_at_s
        if started is None:
            return None
        return max(0.0, run_clock().run_elapsed_s() - started)

    @final
    async def _send(self, message: str) -> Failure | None:
        """One caller interaction with this Agent's Conversation. The candidate the completed turn produced is read back from the folded journal, so a live pass and a replayed one examine the same fact."""
        conversation = self._live_conversation
        if conversation is None:
            # Opened inside the step at this process's first send, told the
            # caught-up journal facts and interpreting them alone: an open
            # invocation means a request with no recorded completion.
            # The identity's one recorded session is always the one resumed;
            # one Action never opens a second conversation beside it.
            agent_spec = self._agent_spec
            if agent_spec is None:
                raise AssertionError(f"{type(self).__name__} is not in a conversation")
            conversation = Conversation(
                agent_spec,
                resume_session_id=self.session_id,
                unfinished_request=self._recovering,
                pause_open_for_s=self._pause_open_for_s(),
                retry_ceiling_s=run_configs().llm.retry_ceiling_s,
            )
            self._live_conversation = conversation
            self._session_ready.set()
        try:
            self._enforce_cost_ceiling()
            await conversation.send(message, observer=_AgentObserver(self))
        except BackendError as exc:
            state = latest_run_state().aibuildai_search
            if state.run_suspend_requested is not None:
                # A suspending epoch killed this stream: record no ending,
                # so the unit stays RUNNING and resume replays this step.
                # The unwind is RunSuspending, never CancelledError: the
                # durable step's preemption machinery claims that one as
                # its own and swallows a hand-raised one, which parks the
                # conversation forever in a RESUMED epoch.
                raise RunSuspending from None
            return self._backend_failure(exc)
        except CostLimitError as exc:
            return Failure(kind=FailureKind.COST_LIMIT, reason=str(exc))
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001 - this Agent's backend boundary
            logger.exception("[%s] uncaught", type(self).name)
            return Failure(
                kind=FailureKind.UNEXPECTED,
                reason=f"{type(exc).__name__}: {exc}",
            )
        self._recovering = False
        self._submitted_output = self.structured_output_payload
        return None

    @final
    async def _close_conversation(self) -> None:
        """Close what the step opened. Unbound before close() awaits: a reminder that fires during the disconnect must see the Agent as ended."""
        conversation = self._live_conversation
        self._live_conversation = None
        if conversation is not None:
            await conversation.close()

    @final
    async def add_runtime_message(self, text: str) -> bool:
        """Hand text to this Agent's live Conversation as a notification.

        A started Agent may still be opening its Conversation, so this waits for it. The text takes effect at the next provider request boundary, never inside the request in flight, and it is not durable. ``False`` means the Agent has not started or already ended.
        """
        if self._ctx is not None and not self._session_ready.is_set():
            await self._session_ready.wait()
        if self._live_conversation is None:
            return False
        await self._live_conversation.notify(text)
        return True

    async def _prepare_conversation(self) -> None:
        """Build whatever only this role gives its own conversation."""

    def identity_cost_cap_usd(self) -> float | None:
        """The configured per-Agent cap, which is this identity's whole bill.

        One Action's own ``cost_cap_usd`` bounds that call; this bounds every
        call the same Agent ever answers, so a second Action continues to
        spend what the first one left."""
        return run_configs().run.budget.per_agent_cost_cap_usd

    @final
    def _enforce_cost_ceiling(self) -> None:
        cap = self.capability.cost_cap_usd
        if cap is not None:
            spent = self.cost.action_cost_usd(self.ctx.ordinal)
            if spent >= cap:
                raise CostLimitError(
                    f"[{type(self).name}] this call spent ${spent:.4f} "
                    f">= its cost cap ${cap:.4f}"
                )
        lifetime = self.identity_cost_cap_usd()
        if lifetime is not None and self.cost.total_cost_usd >= lifetime:
            raise CostLimitError(
                f"[{type(self).name}] lifetime spend "
                f"${self.cost.total_cost_usd:.4f} >= per-Agent cap "
                f"${lifetime:.4f}"
            )

    # --- Accepting, settling, and finishing output -------------------------
    def _resume_result(self) -> "AgentSuccessT | Failure | None":
        """Return a saved role result that needs no new backend turn."""
        return None

    @final
    def _settle_terminal_output(
        self,
        result: "AgentSuccessT | Failure",
        verified_output: BaseModel | None,
    ) -> "AgentSuccessT | Failure":
        """Decide the one Output this Action ends with.

        Every path that ends an Agent arrives here, so this is the one place that answers "what does this Action record". A path that ended in failure may still be holding work the role's own chain already cleared -- the clock ran out while a verifier was reading it, the provider throttled, the run's money ran out -- and that work is what the Action records. The Failure keeps saying WHY the run ended; it no longer also decides WHAT is recorded.

        It reads only the CLEARED work, never everything the agent submitted. With nothing cleared the Failure stands: no role stands anything in for its own silence, because an Output nobody produced would be recorded as one somebody did."""
        if not isinstance(result, Failure):
            return result
        if verified_output is not None:
            return cast("AgentSuccessT", verified_output)
        return result

    def _finalize_output(
        self,
        output: "AgentSuccessT | Failure",
    ) -> "AgentSuccessT | Failure":
        """Finish role-owned work before recording the terminal Output."""
        return output

    async def notify_stop(self, reason: str) -> None:
        """Tell a live role to wrap up, so its grace is worth giving.

        A message rather than ``Conversation.interrupt()``: the point of the grace is that the role can still submit what it holds, and interrupting its turn is exactly what would stop it doing that. ``add_runtime_message`` answers False when the Conversation already closed, which is not a failure -- it means the role ended on its own and there is nobody left to tell.
        """
        await self.add_runtime_message(
            f"Stopping now: {reason}. Do not start new work. Submit what you "
            "already have by calling StructuredOutput immediately.",
        )

    # --- Verifying a submitted candidate -----------------------------------
    def required_pdf_for_run(self) -> str | None:
        """The document this invocation must deliver, or None for no document gate.

        The class attribute ``required_pdf`` is the role's declaration and is what puts ``tectonic`` on its PATH; a role whose Input can switch the document off overrides this to answer None for that invocation, and the document verifier and the launch prompt both read the answer here, so they can never disagree."""
        return type(self).required_pdf

    def _unfinished_work_notice(self) -> str | None:
        """What this role must still do before its conversation may end, or None.

        Role lifecycle, never verification: it is asked before a candidate is
        verified, it judges the run's state rather than the candidate, and its
        answer is a "not yet" that returns the model to work. Nothing about it
        is recorded and no candidate is judged by it, so a re-entered process
        simply asks it again. Most roles may finish whenever the model says so
        and answer None."""
        return None

    # --- Applying Events to a detached Agent state -------------------------
    def apply_event(
        self,
        event: object,
        ts: float,
        *,
        is_anthropic: bool | None = None,
    ) -> bool:
        """Apply one Agent-owned Event to this addressed Agent."""
        if isinstance(event, SessionStarted):
            self._apply_session_started(event)
        elif isinstance(event, InvocationStarted):
            self.cost.open_invocation(event.action)
        elif isinstance(event, UsageDelta):
            if is_anthropic is None:
                raise AssertionError("UsageDelta needs the run endpoint kind")
            self._apply_usage_delta(event, is_anthropic=is_anthropic)
        elif isinstance(event, TurnComplete):
            if is_anthropic is None:
                raise AssertionError("TurnComplete needs the run endpoint kind")
            self._apply_turn_complete(event, is_anthropic=is_anthropic)
        elif isinstance(event, AgentSpecBuilt):
            self._apply_agent_spec_built(event)
        elif isinstance(event, ToolUseStart):
            self._validate_tool_use_start(event)
        elif isinstance(event, SessionReplaced):
            self.session_id = event.session_id
        elif isinstance(event, StructuredOutputRejected):
            # The rejected candidate is no longer examinable: a re-entry after
            # this fact must not verify it a second time. A new segment
            # begins with the rejection's own sentence, which is that segment's
            # durable step argument, so nothing of the last one stays owed.
            self.structured_output_payload = None
            self.segment_turns = 0
        elif isinstance(event, RateLimitPaused):
            self._apply_rate_limit_paused(ts)
        elif isinstance(event, RateLimitResumed):
            self._apply_rate_limit_resumed(ts)
        elif isinstance(
            event,
            (
                TextDelta,
                ReasoningDelta,
                ToolUseEnd,
            ),
        ):
            # Stream facts owned by the account: they change no Agent state;
            # the Transcript folds the ones it renders after the event is
            # saved and lets the rest pass.
            pass
        else:
            # BackendLog changes run state, not Agent state.
            return super().apply_event(event, ts)
        return True

    @final
    def accept_conversation(
        self,
        event: object,
        ts: float,
        *,
        is_anthropic: bool | None = None,
    ) -> None:
        """Fold one conversation fact into this Agent's runtime record."""
        if not self.apply_event(
            event,
            ts,
            is_anthropic=is_anthropic,
        ):
            raise AssertionError(
                f"{type(self).__name__} does not accept {type(event).__name__}"
            )

    @final
    def _apply_session_started(self, event: SessionStarted) -> None:
        """Apply one SessionStarted to this Agent's session identity."""
        # One recorded conversation per identity: a new session may appear
        # only while none is recorded, so a turn can never claim a different
        # conversation than the one the journal already names. A provider
        # replacement is its own fact, SessionReplaced.
        if self.session_id is not None and self.session_id != event.session_id:
            raise AssertionError(
                f"Agent session changed from {self.session_id!r} to "
                f"{event.session_id!r} without a recorded replacement"
            )
        self.session_id = event.session_id

    @final
    def _apply_usage_delta(self, event: UsageDelta, *, is_anthropic: bool) -> None:
        """Apply one UsageDelta to this Agent's Cost. ``is_anthropic`` decides whether the SDK's costUSD is authoritative; RunState owns and supplies that fact."""
        warned_count = len(self.cost.unpriced_models_warned)
        self.cost.record_usage(
            Usage(
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                cache_read_input_tokens=event.cached_input_tokens,
                cache_creation_input_tokens=(event.cache_creation_input_tokens or 0),
                web_search_requests=event.web_search_requests,
                speed=event.speed,
            ),
            cumulative=event.cumulative,
            message_id=event.message_id,
            event_model=event.model,
            parent_tool_use_id=event.parent_tool_use_id,
            resolved_model=self.resolved_model,
            local_rates_by_model=event.local_rates_by_model,
            cache_creation_input_tokens=event.cache_creation_input_tokens,
            is_anthropic=is_anthropic,
            owner_role=type(self).name,
        )
        self._log_new_unpriced(warned_count, settled=False)

    @final
    def _apply_turn_complete(self, event: TurnComplete, *, is_anthropic: bool) -> None:
        """Settle one completed turn into this Agent's Cost. ``is_anthropic`` is supplied by RunState."""
        warned_count = len(self.cost.unpriced_models_warned)
        self.cost.settle_turn(
            InvocationResult(
                cost_usd=event.cost_usd,
                usage_by_model=event.usage_by_model,
                duration_s=event.duration_s,
                duration_api_s=event.duration_api_s,
                num_turns=event.num_turns,
                local_rates_by_model=event.local_rates_by_model,
            ),
            is_anthropic=is_anthropic,
            resolved_model=self.resolved_model,
            owner_role=type(self).name,
        )
        self._log_new_unpriced(warned_count, settled=True)
        # The turn's raw candidate until the gates decide. A completed request
        # that produced none (a notification the model only acknowledged)
        # leaves the model's last unjudged submission standing; a rejection or
        # a new segment is what clears it.
        if event.structured_output is not None:
            self.structured_output_payload = event.structured_output
        self.segment_turns += 1

    def apply_action_started(self, state: Any, event: object) -> None:
        """Open a new Action: a new conversation segment with nothing owed yet.

        An Agent's conversation is one lineage, so one call at a time is this
        family's own rule: a second one would send into the same session
        while the first is still being answered."""
        active = self.record.active_actions
        if active:
            raise ValueError(
                f"{self.path} is still answering Action #{active[0].ordinal} in "
                "its one conversation; an Agent answers one call at a time"
            )
        super().apply_action_started(state, event)
        self.structured_output_payload = None
        self.segment_turns = 0
        self._session_ready.clear()

    @final
    def apply_output_recorded(self, ordinal: int, value: object, ts: float) -> None:
        output = cast(
            "AgentSuccessT | Failure",
            _action_spec(type(self), "run").output_adapter.validate_python(value),
        )
        if self.record.action(ordinal).attempts:
            self._require_active_execution()
        elif not output.failed:
            raise AssertionError("pre-start Agent Output must be a Failure")
        # Terminalizing never leaves an open invocation behind.
        self.cost.abandon_invocation()
        self.structured_output_payload = None
        super().apply_output_recorded(ordinal, value, ts)

    def _reset_for_retry(self, ordinal: int) -> None:
        """Open the same Action's next Attempt in the same conversation.

        The lineage is the identity's: the session, a standing candidate, a
        request the failed Attempt left open, and the accumulated ``cost``
        all carry into the next Attempt, which recovers an open request the
        way a re-entered process does. The segment starts over, so the next
        Attempt asks the same request again unless it is recovering one."""
        del ordinal
        self.segment_turns = 0

    @final
    def _apply_agent_spec_built(self, event: AgentSpecBuilt) -> None:
        if self.resolved_model is not None and self.resolved_model != event.model:
            raise AssertionError(
                f"{type(self).__name__} resolved model changed from "
                f"{self.resolved_model!r} to {event.model!r}"
            )
        self.resolved_model = event.model

    @final
    def _validate_tool_use_start(self, event: ToolUseStart) -> None:
        if event.parent_tool_use_id is not None and event.name in {"Agent", "Task"}:
            raise AssertionError(
                f"recursive sub-agent dispatch detected: agent "
                f"name={type(self).name!r} uid={self.path!r} "
                f"received a ToolUseStart with "
                f"parent_tool_use_id={event.parent_tool_use_id!r} "
                f"tool={event.name!r} "
                f"(tool_use id={event.tool_use_id!r}). Sub-agents "
                f"must not dispatch other sub-agents."
            )

    @final
    def _apply_rate_limit_paused(self, at_s: float) -> None:
        action_record = self._require_active_execution()
        if action_record.rate_limited_at_s is not None:
            raise AssertionError(f"{self.path!r} is already rate limited")
        if action_record.attempt_started_at_s is not None:
            action_record.active_s += max(
                0.0, at_s - action_record.attempt_started_at_s
            )
            action_record.attempt_started_at_s = None
        action_record.rate_limited_at_s = at_s

    @final
    def _apply_rate_limit_resumed(self, at_s: float) -> None:
        action_record = self._require_active_execution()
        pause_started_at_s = action_record.rate_limited_at_s
        if pause_started_at_s is None:
            raise AssertionError(f"{self.path!r} is not rate limited")
        if at_s < pause_started_at_s:
            raise AssertionError("rate-limit resume precedes its pause")
        action_record.rate_limited_at_s = None
        action_record.attempt_started_at_s = at_s

    @final
    def _log_new_unpriced(self, warned_count: int, *, settled: bool) -> None:
        where = "settled cost" if settled else "cost"
        for model in self.cost.unpriced_models_warned[warned_count:]:
            logger.warning(
                f"[{type(self).name}] no pricing tier for model {model!r}; its {where} "
                "is shown as $0 — add a tier in infra/cost_pricing.py "
                ""
            )

    @final
    def _require_active_execution(self) -> ActionRecord:
        action_record = self._active_action()
        if not action_record.attempts:
            raise AssertionError(f"{self.path!r} has not started its Action")
        return action_record


def review_request(candidate: SuccessfulOutput) -> str:
    """One candidate as the reviewer's own first user turn.

    The submission is the thing that changes from one review to the next, so
    it is the review Action's REQUEST. A reviewer identity that carried it as
    an Input field would be describing a per-candidate fact as a stable one,
    and would still have to be told which turn to start on."""
    return (
        "Review this submission:\n\n"
        f"{json.dumps(candidate.model_dump(mode='json'), indent=2)}"
    )


async def run_agent_conversation(path: str, prompt: str) -> object:
    """Run one Agent conversation until it submits its next raw candidate."""

    agent = latest_run_state().execution_for(path)
    if not isinstance(agent, Agent):
        raise TypeError(f"{path} is not an Agent")
    return await agent._conversation_step(prompt)
