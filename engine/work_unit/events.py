"""Facts recorded for concrete WorkUnits."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import TYPE_CHECKING, Literal

from engine.event.base import Event, UnitFact
from infra.cost_observation import PricingRates

if TYPE_CHECKING:
    from engine.run_state import RunState


def _assert_json_native(value: object, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AssertionError(f"{path} contains a non-finite float")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_native(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AssertionError(f"{path} contains a non-string object key")
            _assert_json_native(item, f"{path}.{key}")
        return
    raise AssertionError(
        f"{path} must be recursively JSON-native, got {type(value).__name__}"
    )


@dataclass(frozen=True)
class SessionStarted(Event):
    session_id: str
    model: str
    cwd: str
    tools: tuple[str, ...]
    mcp_server_names: tuple[str, ...] = ()
    launcher_path: str | None = field(kw_only=True)

    def apply(self, state: "RunState") -> None:
        anthropic = state.require_run_config().is_anthropic
        self.agent(state).accept_conversation(self, self.ts, is_anthropic=anthropic)


@dataclass(frozen=True)
class InvocationStarted(UnitFact):
    """The Conversation is about to send one provider request; recorded before the send so an unfinished request leaves its open Cost invocation.

    ``action`` is the Action the send is made for, decided by the caller that records this fact while that Action is running. A fold reads it back from the fact: which Action a historical send belonged to is a recorded fact, and asking which Action happens to be running now would answer a different question, or none at all in a restore."""

    action: int


@dataclass(frozen=True)
class SessionReplaced(Event):
    """The provider replaced this Agent's conversation; the Agent keeps its identity and continues under ``session_id``. The replaced id is the state this fact applies to, and the Conversation holds both for the model's own lifecycle facts."""

    session_id: str

    def apply(self, state: "RunState") -> None:
        self.agent(state).accept_conversation(self, self.ts)


@dataclass(frozen=True)
class TextDelta(UnitFact):
    text: str
    parent_tool_use_id: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class ReasoningDelta(UnitFact):
    text: str
    parent_tool_use_id: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class ToolUseStart(UnitFact):
    tool_use_id: str
    name: str
    input: dict
    parent_tool_use_id: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class ToolUseEnd(UnitFact):
    tool_use_id: str
    output: str | list | None
    is_error: bool
    parent_tool_use_id: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class UsageDelta(Event):
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int | None = None
    model: str = ""
    cumulative: bool = False
    message_id: str | None = None
    web_search_requests: int = 0
    speed: str = ""
    local_rates_by_model: dict[str, PricingRates | None] | None = None
    parent_tool_use_id: str | None = field(default=None, kw_only=True)

    def apply(self, state: "RunState") -> None:
        anthropic = state.require_run_config().is_anthropic
        self.agent(state).accept_conversation(self, self.ts, is_anthropic=anthropic)


@dataclass(frozen=True)
class TurnComplete(Event):
    duration_s: float | None
    duration_api_s: float | None
    cost_usd: float | None
    stop_reason: str
    num_turns: int
    # The turn's usage per canonical model in the product's usage vocabulary
    # (input_tokens, output_tokens, cache_read_input_tokens,
    # cache_creation_input_tokens, web_search_requests, cost_usd).
    usage_by_model: dict[str, dict[str, float]] | None = None
    local_rates_by_model: dict[str, PricingRates | None] | None = None
    # The provider's raw structured candidate, persisted so a crash or replay
    # recovers it without another provider send.
    structured_output: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.structured_output is not None:
            _assert_json_native(self.structured_output, "TurnComplete.structured_output")

    def apply(self, state: "RunState") -> None:
        anthropic = state.require_run_config().is_anthropic
        self.agent(state).accept_conversation(self, self.ts, is_anthropic=anthropic)


@dataclass(frozen=True)
class BackendLog(Event):
    level: Literal["debug", "info", "warn", "error"]
    source: str
    text: str

    def apply(self, state: "RunState") -> None:
        """A typed journal fact with no folded state of its own."""


@dataclass(frozen=True)
class RateLimitPaused(Event):
    def apply(self, state: "RunState") -> None:
        self.agent(state).accept_conversation(self, self.ts)


@dataclass(frozen=True)
class RateLimitResumed(Event):
    def apply(self, state: "RunState") -> None:
        self.agent(state).accept_conversation(self, self.ts)


@dataclass(frozen=True)
class StructuredOutputRejected(Event):
    """One structured-output candidate of this Agent Action was rejected before it could become the Action result.

    The one rejection fact, whichever verifier gave it: ``reason`` is the actionable verdict text the producer is owed back and ``verifier`` names the entry of the chain that gave it, empty for the framework's own schema gate. Recording it keeps every rejection in the run's account even when a later candidate is accepted, and it is what tells a re-entered process that the rejected candidate is no longer examinable."""

    reason: str
    verifier: str = ""

    def apply(self, state: "RunState") -> None:
        agent = self.agent(state)
        agent.accept_conversation(self, self.ts)


@dataclass(frozen=True)
class ActionAttemptFailed(Event):
    """Record one failed Attempt before the same exact Action tries again.

    The Attempt's own reason is already recorded where it happened, so this
    event carries only what settles the Attempt: which Action it belongs to."""

    ordinal: int

    def apply(self, state: "RunState") -> None:
        self.unit(state).apply_retry(self, self.ts)


@dataclass(frozen=True)
class ProgramAttemptRecorded(UnitFact):
    """Record the process facts that accompany one exact Action's settled Attempt."""

    ordinal: int
    return_code: int | None
    state: dict[str, object]
    commit_state: bool

    def __post_init__(self) -> None:
        _assert_json_native(self.state, "ProgramAttemptRecorded.state")


@dataclass(frozen=True)
class AgentSpecBuilt(Event):
    """An Agent's current-process backend specification was resolved."""

    model: str

    def apply(self, state: "RunState") -> None:
        self.agent(state).accept_conversation(self, self.ts)
