"""Native Claude SDK messages -> semantic observations of one provider request.

``RequestStream`` reads every native SDK message of one provider request and calls the ``ConversationObserver`` method that names what the provider did: a session that started, a text block, a tool use, a usage report, a completed turn. There is no intermediate message object between the two: the observer's arguments are the facts themselves, so ``engine`` never sees a Claude SDK type and this package never sees an engine Event.

Reading is one-to-N: an AssistantMessage with mixed blocks reports reasoning, text, tool-start, and usage observations; a ResultMessage reports the cumulative usage and the turn completion; the init system message reports the session start; a UserMessage's tool results report tool ends. ``parent_tool_use_id`` travels from the SDK message onto every observation: None at top level, the spawning Agent tool's id for a sub-agent's messages (with ``forward_subagent_text`` its text and thinking arrive the same way), which routes them to the Task tool's transcript sub-block.

Dispatch is by product meaning, not by SDK class inventory. A system message is classified by its ``subtype`` against an explicit list; a subtype, a content block, or a top-level message class this module has never classified fails loud, because it may carry state the product cannot see.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Literal, cast

from claude_agent_sdk import (
    AssistantMessage,
    Message,
    ModelUsage,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from infra.util.formatting import canonical_model_name


_API_ERROR_PREFIX = "API Error:"

# Bookkeeping the bundled CLI 2.1.251 writes as system messages: turn and
# session state notes, and the sub-agent task lifecycle that the spawning
# Agent tool's own tool_use / tool_result blocks already attribute. None of
# them carries content, usage, or a lifecycle change the product acts on.
_BOOKKEEPING_SUBTYPES = frozenset(
    {
        "api_retry",
        "compact_boundary",
        "informational",
        "session_state_changed",
        "status",
        "thinking_tokens",
        "turn_duration",
        "turn_starting",
        "task_started",
        "task_progress",
        "task_notification",
        "task_updated",
    }
)


class ConversationObserver(ABC):
    """What a Conversation tells its caller about its provider requests, as each fact happens.

    Each method names one semantic fact of the conversation and carries that fact's own values, nothing else: no SDK type comes up through it, and no Event, journal, or run state goes down through it. The observer belongs to one ``send``: every fact of a conversation is reported inside the interaction that caused it, so a caller that records each interaction separately hands ``send`` that interaction's own recorder, and a caller with nothing to record passes none.
    """

    @abstractmethod
    def invocation_started(self) -> None:
        """The Conversation is about to send one provider request. Reported before the send, so a request with no reported completion leaves its trace."""

    @abstractmethod
    def session_started(self, *, session_id: str, model: str, cwd: str, tools: tuple[str, ...], mcp_server_names: tuple[str, ...], launcher_path: str | None) -> None:
        """The provider opened a session under ``model``, the accepted semantic model (its family checked against the requested one). ``cwd`` is the SDK subprocess working directory, not the run's output directory; ``mcp_server_names`` are the servers the provider mounted; ``launcher_path`` is the exact SDK executable this Conversation launched."""

    @abstractmethod
    def session_replaced(self, *, session_id: str) -> None:
        """The provider replaced this conversation under the same caller (Claude ``ConversationResetMessage``) and continues it as ``session_id``; the request in flight ends without completing."""

    @abstractmethod
    def text_delta(self, text: str, *, parent_tool_use_id: str | None) -> None:
        """One text block of an assistant message."""

    @abstractmethod
    def reasoning_delta(self, text: str, *, parent_tool_use_id: str | None) -> None:
        """One thinking block of an assistant message: its text."""

    @abstractmethod
    def tool_use_start(self, *, tool_use_id: str, name: str, input: dict, parent_tool_use_id: str | None) -> None:
        """The model called one tool, a server-side tool included."""

    @abstractmethod
    def tool_use_end(self, *, tool_use_id: str, output: str | list | None, is_error: bool, parent_tool_use_id: str | None) -> None:
        """One tool call returned."""

    @abstractmethod
    def usage_delta(self, *, input_tokens: int, output_tokens: int, cached_input_tokens: int, cache_creation_input_tokens: int | None, web_search_requests: int, speed: str, model: str, cumulative: bool, message_id: str | None, parent_tool_use_id: str | None) -> None:
        """Token usage. ``cumulative=False`` is one assistant model call's own usage, keyed by ``message_id``: one real turn arrives as several per-block AssistantMessages sharing the id, and the settled stream figure overwrites the snapshot. ``cumulative=True`` is the whole-invocation total of Claude's ResultMessage: the billing total, which must neither be added on top of the per-call figures nor mistaken for one call's context occupancy. Only the cumulative usage carries ``web_search_requests`` and ``speed``; a per-call usage reports 0 / "" (probed 2026-06-02)."""

    @abstractmethod
    def turn_complete(self, *, duration_s: float | None, duration_api_s: float | None, cost_usd: float | None, stop_reason: str, num_turns: int, usage_by_model: dict[str, dict[str, float]] | None, structured_output: dict[str, Any] | None) -> None:
        """One assistant turn ended. ``usage_by_model`` is the turn's settled usage per canonical model, in the product's own usage vocabulary (``input_tokens``, ``output_tokens``, ``cache_read_input_tokens``, ``cache_creation_input_tokens``, ``web_search_requests``, ``cost_usd``), covering every model call of the turn, the provider's hidden side queries included; the caller settles it by endpoint. ``structured_output`` is the provider's one terminal structured candidate for a spec with an output schema, judged by the caller's own gates."""

    @abstractmethod
    def backend_log(self, *, level: Literal["debug", "info", "warn", "error"], source: str, text: str) -> None:
        """A provider diagnostic with no product content, such as a CLI status notification."""

    @abstractmethod
    def rate_limit_paused(self) -> None:
        """The provider stopped answering; the caller's Local Time pauses."""

    @abstractmethod
    def rate_limit_resumed(self) -> None:
        """The provider answers again; the paused Local Time resumes."""


# The per-model usage facts of a Claude ResultMessage, named in the product's
# own usage vocabulary. The SDK's per-model capability metadata (its
# contextWindow and maxOutputTokens, recomputed from the model id on every
# response and wrong for a model outside its registry) is not a fact
# of this run and is not read; infra.model_info.context_window_for_model is the
# one source for a model's context window.
_MODEL_USAGE_FIELDS: dict[str, str] = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
    "webSearchRequests": "web_search_requests",
    "costUSD": "cost_usd",
}


def _model_family(model_id: str) -> str:
    """The family a Claude model id names, across its aliases, dated snapshots, and the ``[1m]`` context suffix."""
    base = model_id.lower().removesuffix("[1m]")
    for family in ("opus", "sonnet", "haiku"):
        if family in base:
            return family
    return base


def _usage_by_model(raw: Mapping[str, ModelUsage]) -> dict[str, dict[str, float]]:
    """The turn's usage per canonical model, from the SDK's per-model records.

    ``canonicalModel`` is the SDK's pricing identity for an entry; the raw key (an alias, a dated snapshot, a provider id) names it only when the SDK gives none. An alias and its dated snapshot are one model, so their usage adds."""
    out: dict[str, dict[str, float]] = {}
    for raw_key, record in raw.items():
        entry = cast(dict[str, Any], record)
        model = canonical_model_name(entry.get("canonicalModel") or raw_key)
        missing = sorted(set(_MODEL_USAGE_FIELDS) - set(entry))
        if missing:
            raise AssertionError(
                f"Claude ResultMessage.model_usage[{raw_key!r}] lacks {missing}; "
                f"keys={sorted(entry)}. Wire shape changed."
            )
        totals = out.setdefault(model, {})
        for sdk_key, field in _MODEL_USAGE_FIELDS.items():
            totals[field] = totals.get(field, 0) + entry[sdk_key]
    return out


def _tool_result_is_error(block: ToolResultBlock) -> bool:
    if block.is_error is True:
        return True
    content = block.content
    if isinstance(content, str):
        return content.lstrip().startswith(_API_ERROR_PREFIX)
    if isinstance(content, list):
        return any(
            isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and item["text"].lstrip().startswith(_API_ERROR_PREFIX)
            for item in content
        )
    return False


class RequestStream:
    """The native SDK messages of one provider request, read into observations for the Conversation's observer and into the request's own outcome: whether a turn completed, its top-level text, and its terminal structured candidate."""

    def __init__(self, observer: ConversationObserver, *, launcher_path: str | None, requested_model: str) -> None:
        self._observer = observer
        self._launcher_path = launcher_path
        self._requested_model = requested_model
        # The in-flight message per (session_id, parent_tool_use_id) lane of
        # the partial-message stream, from its message_start.
        self._open_lanes: dict[tuple[str, str | None], tuple[str, str]] = {}
        self._text: list[str] = []
        self.completed = False
        self.output: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        """The completed turn's top-level assistant text so far."""
        return "".join(self._text)

    def read(self, ev: Message) -> None:
        """Read one native SDK message of this request."""
        if isinstance(ev, StreamEvent):
            self._read_stream(ev)
        elif isinstance(ev, SystemMessage):
            self._read_system(ev)
        elif isinstance(ev, AssistantMessage):
            self._read_assistant(ev)
        elif isinstance(ev, UserMessage):
            self._read_user(ev)
        elif isinstance(ev, ResultMessage):
            self._read_result(ev)
        else:
            # The conversation consumes the rate-limit and conversation-reset
            # messages before they reach this reader.
            raise AssertionError(
                f"unclassified Claude SDK message {type(ev).__name__} reached the request reader"
            )

    def _read_system(self, ev: SystemMessage) -> None:
        data = ev.data or {}
        if ev.subtype == "init":
            sid = data.get("session_id")
            if sid is None:
                raise AssertionError(
                    f"Claude SystemMessage(init).data lacks session_id; "
                    f"data keys={sorted(data.keys())}. Wire shape changed."
                )
            model = canonical_model_name(data["model"])
            # The provider's naming is checked here, where the names are the
            # provider's: the session must serve the requested model's family.
            if _model_family(self._requested_model) != _model_family(model):
                raise AssertionError(
                    f"model mismatch: requested {self._requested_model!r}, "
                    f"backend reported {model!r}"
                )
            self._observer.session_started(
                session_id=sid,
                model=model,
                cwd=data["cwd"],
                tools=tuple(data.get("tools") or ()),
                mcp_server_names=tuple(
                    str(server["name"]) for server in (data.get("mcp_servers") or ())
                ),
                launcher_path=self._launcher_path,
            )
        elif ev.subtype == "notification":
            # A CLI status-line notice: its own text, with a color that marks a
            # warning (a blocked agent MCP server, a restricted sub-agent model).
            self._observer.backend_log(
                level="warn" if data.get("color") == "warning" else "info",
                source="claude-sdk",
                text=f"Claude notification: {data.get('text', '')}",
            )
        elif ev.subtype == "permission_denied":
            # The CLI refused a tool call on its own authority: a safetyCheck
            # (an `rm` whose target is a possibly-empty shell variable, say) that
            # no permission rule, bypassPermissions included, may auto-allow.
            # The call is already settled for the model, which sees the refusal
            # as that tool_use's error result and re-plans on its next turn; the
            # product only records why. Failing loud here instead tore down the
            # whole run (every unit of the search) on the first such refusal.
            # The CLI's schema requires tool_name, tool_use_id and message;
            # decision_reason_type and decision_reason are optional.
            self._observer.backend_log(
                level="warn",
                source="claude-sdk",
                text=(
                    f"Claude denied {data['tool_name']} call {data['tool_use_id']} "
                    f"({data.get('decision_reason_type', 'unspecified')}): "
                    f"{data.get('decision_reason') or data['message']}"
                ),
            )
        elif ev.subtype not in _BOOKKEEPING_SUBTYPES:
            # Hook events and session-store mirror errors need options the
            # product never sets; anything else is a subtype never classified.
            raise AssertionError(
                f"unclassified Claude system message {ev.subtype!r}: {data}"
            )

    def _read_assistant(self, ev: AssistantMessage) -> None:
        parent = ev.parent_tool_use_id
        for block in (ev.content or []):
            if isinstance(block, TextBlock):
                if parent is None:
                    self._text.append(block.text)
                self._observer.text_delta(block.text, parent_tool_use_id=parent)
            elif isinstance(block, ThinkingBlock):
                self._observer.reasoning_delta(block.thinking, parent_tool_use_id=parent)
            elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                # A server-side tool (web search / fetch, advisor) ran inside the
                # API call; it is a tool use of this turn all the same.
                self._observer.tool_use_start(
                    tool_use_id=block.id,
                    name=block.name,
                    input=dict(block.input),
                    parent_tool_use_id=parent,
                )
            elif isinstance(block, ServerToolResultBlock):
                self._observer.tool_use_end(
                    tool_use_id=block.tool_use_id,
                    output=[block.content],
                    is_error=str(block.content.get("type", "")).endswith("_error"),
                    parent_tool_use_id=parent,
                )
            else:
                # A ToolResultBlock arrives on the UserMessage, never here.
                raise AssertionError(
                    f"unclassified Claude content block {type(block).__name__} "
                    f"in an AssistantMessage"
                )
        if ev.usage:
            self._usage(
                ev.usage,
                model=canonical_model_name(ev.model or ""),
                parent_tool_use_id=parent,
                message_id=ev.message_id,
            )

    def _read_user(self, ev: UserMessage) -> None:
        content = ev.content
        if not isinstance(content, list):
            # str content (prompt echo, not a tool result) carries no facts.
            return
        parent = ev.parent_tool_use_id
        for block in content:
            if isinstance(block, ToolResultBlock):
                self._observer.tool_use_end(
                    tool_use_id=block.tool_use_id,
                    output=block.content,
                    is_error=_tool_result_is_error(block),
                    parent_tool_use_id=parent,
                )
            # A user turn's own text blocks are the prompt the product sent (or
            # the parent's brief to a sub-agent), which the product already has.

    def _usage(
        self,
        usage: dict,
        *,
        model: str = "",
        cumulative: bool = False,
        message_id: str | None = None,
        parent_tool_use_id: str | None = None,
    ) -> None:
        """Report one SDK usage dict by its actual shape.

        Only the cumulative ResultMessage.usage carries ``server_tool_use`` and ``speed``; a per-call or stream usage reports their honest absence (0 / "") rather than an invented value.
        """
        server_tools = usage.get("server_tool_use") or {}
        self._observer.usage_delta(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
            web_search_requests=int(server_tools.get("web_search_requests", 0) or 0),
            speed=str(usage.get("speed", "") or ""),
            model=model,
            cumulative=cumulative,
            message_id=message_id,
            parent_tool_use_id=parent_tool_use_id,
        )

    def _read_result(self, ev: ResultMessage) -> None:
        # ResultMessage.usage is the whole-invocation total (summed across
        # every internal turn), not a single turn's occupancy. Mark it so the
        # consumer feeds totals from it without clobbering last_turn_*.
        self._usage(ev.usage or {}, cumulative=True)
        # The provider's terminal structured candidate. The caller's
        # own gates judge it; reading requires only the declared object shape.
        candidate = ev.structured_output
        if candidate is not None and not isinstance(candidate, dict):
            raise AssertionError(
                "ResultMessage.structured_output must be an object, "
                f"got {type(candidate).__name__}"
            )
        self._observer.turn_complete(
            duration_s=ev.duration_ms / 1000.0,
            duration_api_s=(
                ev.duration_api_ms / 1000.0
                if ev.duration_api_ms is not None else None
            ),
            cost_usd=ev.total_cost_usd,
            # The only error result that reaches this reader is a cancelled turn;
            # its terminal_reason names the abort, so the turn ends as what it was.
            stop_reason=(ev.terminal_reason if ev.is_error else ev.stop_reason) or "end_turn",
            num_turns=ev.num_turns,
            usage_by_model=_usage_by_model(ev.model_usage) if ev.model_usage else None,
            structured_output=candidate,
        )
        self.output = candidate
        self.completed = True

    def _read_stream(self, ev: StreamEvent) -> None:
        """Bind a raw partial-message stream event to its in-flight message and report the SETTLED per-call usage when that message's message_delta arrives.

        The CLI's AssistantMessage snapshots usage at message_start, where an OpenAI-protocol adapter endpoint can only report zeros (the OpenAI protocol reveals usage at stream end) and the Anthropic route holds a placeholder output count. The raw message_delta carries the settled numbers on BOTH routes; the delta event itself has no message id, so the lane tracks the current message from the preceding message_start. Downstream folds key on message_id and let the later figure overwrite the earlier snapshot.
        """
        raw = ev.event
        kind = raw.get("type")
        lane = (ev.session_id, ev.parent_tool_use_id)
        if kind == "message_start":
            message = raw.get("message") or {}
            message_id = message.get("id")
            if message_id is None:
                raise AssertionError(
                    f"stream message_start lacks message.id; "
                    f"keys={sorted(message.keys())}. Wire shape changed."
                )
            self._open_lanes[lane] = (
                message_id,
                canonical_model_name(message.get("model") or ""),
            )
            return
        if kind != "message_delta":
            return  # other partial-stream kinds carry no settlement data
        usage = raw.get("usage")
        if not usage:
            return
        bound = self._open_lanes.get(lane)
        if bound is None:
            raise AssertionError(
                f"stream message_delta with usage arrived on lane {lane} "
                f"with no preceding message_start. Wire shape changed."
            )
        message_id, model = bound
        self._usage(
            usage,
            model=model,
            parent_tool_use_id=ev.parent_tool_use_id,
            message_id=message_id,
        )
