"""One output-owned Transcript for one agent session."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import Field

from engine.base import WorkflowBaseModel
from engine.cost import Usage
from engine.event.events import ActionAttemptStarted, ActionStarted
from engine.work_unit.events import (
    BackendLog,
    ReasoningDelta,
    SessionStarted,
    StructuredOutputRejected,
    TextDelta,
    ToolUseEnd,
    ToolUseStart,
    TurnComplete,
    UsageDelta,
)
from infra.backends.claude.event_adapter import ConversationObserver
from infra.util.atomic_io import atomic_write_text
from infra.cost_pricing import cost_for_call
from output.transcript._compute_helpers import (
    get_block_kind,
    get_content,
    get_msg_id,
)
from output.transcript._record_render import render_record
from output.transcript._compute_helpers import resolve_sub_name
from output.transcript._writer_helpers import (
    prepopulate_sub_name_by_tu,
    render_appended_assistant_blocks,
)
from output.transcript.files import append_transcript, write_sidecar
from output.transcript.format import format_execution_header, output_json_footer

if TYPE_CHECKING:
    from collections.abc import Iterable

    from engine.event.base import Event
    from engine.work_unit.agent.base import Agent


class _Step(WorkflowBaseModel):
    """One ResultMessage cycle inside its owning Transcript."""

    start_ts: float
    end_ts: float | None = None
    sdk_records: list[dict] = Field(default_factory=list)
    pending_content_blocks: list[tuple[str | None, dict]] = Field(default_factory=list)
    pending_tool_results: dict[str, dict] = Field(default_factory=dict)

    def append_record(self, record: dict) -> None:
        self.sdk_records.append(record)

    def buffer_content(self, parent_tool_use_id: str | None, block: dict) -> None:
        self.pending_content_blocks.append((parent_tool_use_id, block))

    def pending_content(self, parent_tool_use_id: str | None) -> list[dict]:
        return [
            block
            for identity, block in self.pending_content_blocks
            if identity == parent_tool_use_id
        ]

    def take_pending_content(self, parent_tool_use_id: str | None) -> list[dict]:
        blocks = self.pending_content(parent_tool_use_id)
        self.pending_content_blocks = [
            item
            for item in self.pending_content_blocks
            if item[0] != parent_tool_use_id
        ]
        return blocks

    def has_pending_tool_use_block(self, tool_use_id: str) -> bool:
        return any(
            block.get("kind") == "tool_use" and block.get("id") == tool_use_id
            for _identity, block in self.pending_content_blocks
        )

    def queue_tool_result(self, tool_use_id: str, record: dict) -> None:
        self.pending_tool_results[tool_use_id] = record

    def take_results_for(self, blocks: list[dict]) -> list[dict]:
        results = []
        for block in blocks:
            tool_use_id = block.get("id") if block.get("kind") == "tool_use" else None
            if tool_use_id is None:
                continue
            result = self.pending_tool_results.pop(tool_use_id, None)
            if result is not None:
                results.append(result)
        return results


class _UpdateAction(WorkflowBaseModel):
    """Blocks appended to an already-rendered assistant record."""

    record: dict
    end: int


class _OutputAction(WorkflowBaseModel):
    """Where one accepted Action Output link anchors in the record list."""

    record_index: int
    end: int | None = None


class Transcript(ConversationObserver):
    """State and files for one agent session.

    Two paths feed it, each with one representation: the durable Agent path replays the saved Events through ``apply_event`` and ``resume``, and a graph-free caller hands the Transcript to its Conversation as the ``ConversationObserver``, so the facts are recorded as they happen with no Event and no journal.
    """

    def __init__(
        self,
        step_dir: str | Path,
        unit_name: str,
        *,
        write_files: bool,
        cost_fn: Callable[[str, Usage], dict] | None = cost_for_call,
    ) -> None:
        self.step_dir = Path(step_dir)
        self.unit_name = unit_name
        self._write_files = write_files
        self._cost_fn = cost_fn
        self._steps: list[_Step] = []
        self._render_actions: list[_UpdateAction | _OutputAction] = []
        self._cumulative_usage: Usage | None = None
        self._structured_output_payload: dict | None = None
        self._has_input = False
        self._has_user_message = False
        self._started = False
        self._live_started_at: float | None = None
        self._cursor = 0
        self._render_action_cursor = 0
        self._turn_number = 0
        # Where each query Action begins, as a turn index into the rendered
        # markdown: the count of turns already written when it started.
        self._query_boundaries: dict[int, int] = {}
        self._current_mid: str | None = None
        self._last_assistant_index: int | None = None
        self._seen_ids: set[str] = set()
        self._sub_name_by_tu: dict[str, str] = {}
        # The model this session runs. A turn-boundary flush has no usage event
        # of its own to read a model from, and an assistant record without one
        # is refused downstream (compute_context_line), so the session's model
        # is kept here to fill those synthetic records truthfully.
        self._session_model: str = ""
        self._sub_name_seq: dict[str, int] = {}
        self._output_link_written = False
        self._output_json_sha256: str | None = None
        self._action_ordinal: int | None = None
        self._started_iso: str | None = None
        self._execution_chunks: list[str] = []

    @property
    def started(self) -> bool:
        """Whether this agent session has started."""
        return self._started

    @property
    def described(self) -> bool:
        """Whether the prompt and typed Input sidecars were attached."""
        return self._has_input or self._has_user_message

    def iter_records(self) -> Iterator[dict]:
        for step in self._steps:
            yield from step.sdk_records

    @property
    def execution_markdown(self) -> str:
        """The same execution transcript written to ``execution.md``."""
        if self._started_iso is None:
            raise AssertionError("Transcript session has not started")
        return self._build_header(self._started_iso) + "".join(self._execution_chunks)

    def start(
        self,
        *,
        system_prompt: str,
        user_message: str | None = None,
        input_payload: dict[str, object] | None = None,
    ) -> None:
        """Start a new agent session and write its initial files.

        ``input_payload`` is the machine-faithful input record; ``user_message``
        is the plain text of the first user turn, rendered as its own readable
        sidecar for callers whose entire variable input is that one string.
        """
        if self.described:
            raise AssertionError("Transcript session metadata was already attached")
        self._started = True
        self._live_started_at = time.monotonic()
        if self._started_iso is None:
            self._started_iso = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
        self._has_input = input_payload is not None
        self._has_user_message = user_message is not None
        if not self._write_files:
            return
        self._reset_owned_files()
        self.step_dir.mkdir(parents=True, exist_ok=True)
        write_sidecar(str(self.step_dir), "system_prompt.md", system_prompt)
        if user_message is not None:
            write_sidecar(str(self.step_dir), "user_message.md", user_message)
        if input_payload is not None:
            write_sidecar(
                str(self.step_dir),
                "input.json",
                json.dumps(input_payload, indent=2),
            )
        atomic_write_text(
            self.step_dir / "execution.md",
            self.execution_markdown,
        )

    def _reset_owned_files(self) -> None:
        for name in (
            "execution.md",
            "system_prompt.md",
            "user_message.md",
            "input.json",
        ):
            (self.step_dir / name).unlink(missing_ok=True)
        sub_agents = self.step_dir / "sub-agents"
        if sub_agents.exists():
            shutil.rmtree(sub_agents)

    def apply_event(self, event: object, ts: float) -> bool:
        """Apply one saved Event and write its transcript changes."""
        handled = self._apply_event(event, ts)
        if handled and self._write_files and not self._started:
            raise AssertionError("Transcript session has not started")
        execution_path = self.step_dir / "execution.md"
        if handled and self._write_files and not execution_path.is_file():
            if self._started_iso is None:
                raise AssertionError("Agent start time was not recorded")
            self.step_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                execution_path,
                self._build_header(self._started_iso),
            )
        if handled:
            self._render_incremental()
        return handled

    def begin_action(self, ordinal: int, message: str, ts: float, *, started_iso: str | None = None) -> None:
        """Open query ``ordinal`` with the caller's ``message``: the Output link and sidecar of everything that follows belong to it.

        The durable Agent path calls this from the recorded ``ActionStarted``; a caller that observes a Conversation live and keeps one transcript per call (the sibling's sourcing recorder) calls it itself before the first fact, since its one prompt is its one query."""
        self._action_ordinal = ordinal
        self._query_boundaries[ordinal] = self._turn_number
        self._structured_output_payload = None
        self._output_json_sha256 = None
        self._output_link_written = False
        self._render_actions = [
            item for item in self._render_actions if not isinstance(item, _OutputAction)
        ]
        self._render_action_cursor = len(self._render_actions)
        if self._started_iso is None and started_iso is not None:
            self._started = True
            self._started_iso = started_iso
        self._append_record(
            {
                "type": "system",
                "subtype": "action",
                "data": {"ordinal": ordinal, "message": message},
                "ts": ts,
            },
            ts,
        )

    def _apply_event(self, event: object, ts: float) -> bool:
        """Fold one saved durable Event: the Agent path's one representation."""
        if isinstance(event, ActionStarted):
            if event.method != "run":
                raise AssertionError(f"Agent Action must be run, got {event.method!r}")
            message = event.request or ""
            if not isinstance(message, str):
                raise AssertionError("Agent run Action has no string message")
            self.begin_action(
                event.ordinal,
                message,
                ts,
                started_iso=event.timestamp.astimezone().isoformat(timespec="seconds"),
            )
        elif isinstance(event, ActionAttemptStarted) and not self._started:
            self._started = True
            self._started_iso = event.timestamp.astimezone().isoformat(
                timespec="seconds"
            )
        elif isinstance(event, SessionStarted):
            self._record_session_init(ts, session_id=event.session_id, model=event.model, cwd=event.cwd, tools=event.tools, mcp_server_names=event.mcp_server_names, launcher_path=event.launcher_path)
        elif isinstance(event, TextDelta):
            self._record_text(ts, event.text, event.parent_tool_use_id)
        elif isinstance(event, ReasoningDelta):
            self._record_reasoning(ts, event.text, event.parent_tool_use_id)
        elif isinstance(event, ToolUseStart):
            self._record_tool_use_start(ts, tool_use_id=event.tool_use_id, name=event.name, input=event.input, parent_tool_use_id=event.parent_tool_use_id)
        elif isinstance(event, StructuredOutputRejected):
            self._structured_output_payload = None
            self._record_structured_output_rejected(event, ts)
        elif isinstance(event, UsageDelta):
            self._record_usage(ts, input_tokens=event.input_tokens, output_tokens=event.output_tokens, cached_input_tokens=event.cached_input_tokens, cache_creation_input_tokens=event.cache_creation_input_tokens, web_search_requests=event.web_search_requests, speed=event.speed, model=event.model, cumulative=event.cumulative, message_id=event.message_id, parent_tool_use_id=event.parent_tool_use_id)
        elif isinstance(event, TurnComplete):
            self._record_turn_complete(ts, duration_s=event.duration_s, duration_api_s=event.duration_api_s, cost_usd=event.cost_usd, stop_reason=event.stop_reason, num_turns=event.num_turns, usage_by_model=event.usage_by_model, structured_output=event.structured_output)
        elif isinstance(event, ToolUseEnd):
            self._record_tool_use_end(ts, tool_use_id=event.tool_use_id, output=event.output, is_error=event.is_error, parent_tool_use_id=event.parent_tool_use_id)
        elif isinstance(event, BackendLog):
            self._record_backend_log(ts, level=event.level, source=event.source, text=event.text)
        else:
            return False
        return True

    # --- The graph-free path: this Transcript observes a Conversation directly.
    # A caller with no journal hands the Transcript to the Conversation as its
    # observer, and each fact is recorded as it happens at the session's own
    # elapsed time. The lifecycle facts with no rendered form (a request
    # boundary, a replacement, a pause) leave no record, as they leave none on
    # the durable path either.

    def _live_ts(self) -> float:
        if not self._started or self._live_started_at is None:
            raise AssertionError("Transcript session has not started")
        return time.monotonic() - self._live_started_at

    def invocation_started(self) -> None: ...

    def session_started(self, *, session_id: str, model: str, cwd: str, tools: tuple[str, ...], mcp_server_names: tuple[str, ...], launcher_path: str | None) -> None:
        self._record_session_init(self._live_ts(), session_id=session_id, model=model, cwd=cwd, tools=tools, mcp_server_names=mcp_server_names, launcher_path=launcher_path)
        self._render_incremental()

    def session_replaced(self, *, session_id: str) -> None: ...

    def text_delta(self, text: str, *, parent_tool_use_id: str | None) -> None:
        self._record_text(self._live_ts(), text, parent_tool_use_id)
        self._render_incremental()

    def reasoning_delta(self, text: str, *, parent_tool_use_id: str | None) -> None:
        self._record_reasoning(self._live_ts(), text, parent_tool_use_id)
        self._render_incremental()

    def tool_use_start(self, *, tool_use_id: str, name: str, input: dict, parent_tool_use_id: str | None) -> None:
        self._record_tool_use_start(self._live_ts(), tool_use_id=tool_use_id, name=name, input=input, parent_tool_use_id=parent_tool_use_id)
        self._render_incremental()

    def tool_use_end(self, *, tool_use_id: str, output: str | list | None, is_error: bool, parent_tool_use_id: str | None) -> None:
        self._record_tool_use_end(self._live_ts(), tool_use_id=tool_use_id, output=output, is_error=is_error, parent_tool_use_id=parent_tool_use_id)
        self._render_incremental()

    def usage_delta(self, *, input_tokens: int, output_tokens: int, cached_input_tokens: int, cache_creation_input_tokens: int | None, web_search_requests: int, speed: str, model: str, cumulative: bool, message_id: str | None, parent_tool_use_id: str | None) -> None:
        self._record_usage(self._live_ts(), input_tokens=input_tokens, output_tokens=output_tokens, cached_input_tokens=cached_input_tokens, cache_creation_input_tokens=cache_creation_input_tokens, web_search_requests=web_search_requests, speed=speed, model=model, cumulative=cumulative, message_id=message_id, parent_tool_use_id=parent_tool_use_id)
        self._render_incremental()

    def turn_complete(self, *, duration_s: float | None, duration_api_s: float | None, cost_usd: float | None, stop_reason: str, num_turns: int, usage_by_model: dict[str, dict[str, float]] | None, structured_output: dict[str, Any] | None) -> None:
        self._record_turn_complete(self._live_ts(), duration_s=duration_s, duration_api_s=duration_api_s, cost_usd=cost_usd, stop_reason=stop_reason, num_turns=num_turns, usage_by_model=usage_by_model, structured_output=structured_output)
        self._render_incremental()

    def backend_log(self, *, level: Literal["debug", "info", "warn", "error"], source: str, text: str) -> None:
        self._record_backend_log(self._live_ts(), level=level, source=source, text=text)
        self._render_incremental()

    def rate_limit_paused(self) -> None: ...

    def rate_limit_resumed(self) -> None: ...

    # --- Record helpers: one representation of each fact, whichever path fed it.

    def _record_session_init(self, ts: float, *, session_id: str, model: str, cwd: str, tools: tuple[str, ...], mcp_server_names: tuple[str, ...], launcher_path: str | None) -> None:
        self._cumulative_usage = None
        if model:
            self._session_model = model
        self._append_record(
            {
                "type": "system",
                "subtype": "init",
                "data": {
                    "session_id": session_id,
                    "model": model,
                    "cwd": cwd,
                    "tools": list(tools),
                    "mcp_server_names": list(mcp_server_names),
                    "launcher_path": launcher_path,
                },
                "ts": ts,
            },
            ts,
        )

    def _record_text(self, ts: float, text: str, parent_tool_use_id: str | None) -> None:
        self._buffer_block(parent_tool_use_id, {"kind": "text", "text": text}, ts)

    def _record_reasoning(self, ts: float, text: str, parent_tool_use_id: str | None) -> None:
        self._buffer_block(parent_tool_use_id, {"kind": "thinking", "thinking": text}, ts)

    def _record_tool_use_start(self, ts: float, *, tool_use_id: str, name: str, input: dict, parent_tool_use_id: str | None) -> None:
        step = self._ensure_step(ts)
        block = {"kind": "tool_use", "id": tool_use_id, "name": name, "input": dict(input)}
        step.buffer_content(parent_tool_use_id, block)
        self._register_dispatch(parent_tool_use_id, block)

    def _register_dispatch(self, parent_tool_use_id: str | None, block: dict) -> None:
        """Name a sub-agent dispatch AS IT IS BUFFERED, not when it is rendered.

        ``prepopulate_sub_name_by_tu`` derives the same map by scanning the
        RECORDS, which works only once the dispatch has been flushed into one.
        A dispatch is buffered under the DISPATCHER's identity and flushes on
        the dispatcher's own usage event, but the sub-agent's usage event
        arrives first — so the sub-agent's records are appended and rendered
        while the dispatch is still pending and invisible to that scan. The
        renderer then cannot name the owning sub-agent and aborts the whole run
        (find_owning_sub_name).

        Registering here removes the ordering dependency entirely: the id is
        known the moment the dispatch is seen. prepopulate still runs and is
        idempotent, so the resume and re-render paths are unchanged, and the
        allocation order is the same one it would have produced (dispatches are
        buffered in the order they occur).

        Mirrors prepopulate's own condition exactly, including that only a
        TOP-LEVEL dispatch is named; a sub-agent dispatching a further
        sub-agent is left unnamed there too, and changing that belongs in its
        own fix rather than riding along with this one.
        """
        if parent_tool_use_id is not None:
            return
        if block["name"] not in {"Agent", "Task"}:
            return
        tu_id = block["id"]
        if tu_id is None or tu_id in self._sub_name_by_tu:
            return
        sub_type = block["input"].get("subagent_type", "general-purpose")
        self._sub_name_by_tu[tu_id] = resolve_sub_name(self._sub_name_seq, sub_type)

    def _record_structured_output(self) -> None:
        # A unit can submit more than once: a verify/review rejection voids
        # the candidate and the repaired resubmission arrives on a later
        # turn. The link is a pointer to output.json, and that is
        # ONE file holding the unit's final payload, so it stays ONE link
        # anchored where the output first appeared; a later candidate adds
        # nothing to place.
        if any(isinstance(action, _OutputAction) for action in self._render_actions):
            return
        records = list(self.iter_records())
        if records and records[-1].get("type") == "assistant":
            self._render_actions.append(
                _OutputAction(
                    record_index=len(records) - 1,
                    end=len(get_content(records[-1])),
                )
            )
        else:
            self._render_actions.append(_OutputAction(record_index=len(records)))

    def _record_structured_output_rejected(
        self, event: StructuredOutputRejected, ts: float
    ) -> None:
        if self._steps:
            self._append_current_record(
                {"type": "output_rejected", "diagnostic": event.reason, "ts": ts}
            )

    def _record_usage(self, ts: float, *, input_tokens: int, output_tokens: int, cached_input_tokens: int, cache_creation_input_tokens: int | None, web_search_requests: int, speed: str, model: str, cumulative: bool, message_id: str | None, parent_tool_use_id: str | None) -> None:
        usage: dict[str, object] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cached_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens or 0,
            "web_search_requests": web_search_requests,
            "speed": speed,
        }
        if cumulative:
            # The whole-invocation total: kept for the result record, never an
            # assistant record's own occupancy.
            self._cumulative_usage = Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cached_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens or 0,
                web_search_requests=web_search_requests,
                speed=speed,
            )
            return
        if model:
            self._session_model = model
        target = self._find_assistant(message_id)
        if target is not None:
            target["usage"] = usage
            self._extend_assistant(target)
            return
        self._flush_assistant(parent_tool_use_id, usage, ts, model=model, message_id=message_id)

    def _record_turn_complete(self, ts: float, *, duration_s: float | None, duration_api_s: float | None, cost_usd: float | None, stop_reason: str, num_turns: int, usage_by_model: dict[str, dict[str, float]] | None, structured_output: dict[str, Any] | None) -> None:
        result_usage, self._cumulative_usage = self._cumulative_usage, None
        if self._steps:
            identities: list[str | None] = []
            for identity, _block in self._steps[-1].pending_content_blocks:
                if identity not in identities:
                    identities.append(identity)
            for identity in identities:
                # NOT model="": a turn can end with content still pending
                # (a sub-agent dispatch whose own usage event never arrived
                # before the turn boundary), and a record synthesised without a
                # model is refused by compute_context_line, which aborts the
                # run. The session's model is the true author of these blocks.
                self._flush_assistant(
                    identity, None, ts, model=self._session_model, message_id=None
                )
        if structured_output is not None:
            # The terminal candidate of this turn: the link anchors
            # after the assistant record that produced it, before the result.
            self._structured_output_payload = dict(structured_output)
            self._record_structured_output()
        record_usage = None
        if result_usage is not None:
            record_usage = {
                "input_tokens": result_usage.input_tokens,
                "output_tokens": result_usage.output_tokens,
                "cache_read_input_tokens": result_usage.cache_read_input_tokens,
                "cache_creation_input_tokens": result_usage.cache_creation_input_tokens,
                "web_search_requests": result_usage.web_search_requests,
                "speed": result_usage.speed,
            }
        self._append_record(
            {
                "type": "result",
                "subtype": "success" if stop_reason != "error" else "error",
                "duration_ms": int(duration_s * 1000) if duration_s is not None else None,
                "duration_api_ms": int(duration_api_s * 1000) if duration_api_s is not None else None,
                "num_turns": num_turns,
                "total_cost_usd": cost_usd,
                "is_error": stop_reason == "error",
                "stop_reason": stop_reason,
                "usage": record_usage,
                "usage_by_model": usage_by_model,
                "session_id": None,
                "ts": ts,
            },
            ts,
        )
        self._steps[-1].end_ts = ts

    def _record_tool_use_end(self, ts: float, *, tool_use_id: str, output: str | list | None, is_error: bool, parent_tool_use_id: str | None) -> None:
        record = {
            "type": "user",
            "content": [
                {
                    "kind": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": output,
                    "is_error": is_error,
                }
            ],
            "parent_tool_use_id": parent_tool_use_id,
            "ts": ts,
        }
        if self._steps and self._steps[-1].has_pending_tool_use_block(tool_use_id):
            self._steps[-1].queue_tool_result(tool_use_id, record)
        else:
            self._append_record(record, ts)

    def _record_backend_log(self, ts: float, *, level: str, source: str, text: str) -> None:
        if self._steps:
            self._append_current_record(
                {"type": "logline", "text": text, "level": level, "source": source, "ts": ts}
            )

    def _ensure_step(self, ts: float) -> _Step:
        if not self._steps or self._steps[-1].end_ts is not None:
            self._steps.append(_Step(start_ts=ts))
        return self._steps[-1]

    def _buffer_block(self, identity: str | None, block: dict, ts: float) -> None:
        self._ensure_step(ts).buffer_content(identity, block)

    def _append_record(self, record: dict, ts: float) -> None:
        self._ensure_step(ts)
        self._append_current_record(record)

    def _append_current_record(self, record: dict) -> None:
        if not self._steps:
            raise AssertionError("Transcript has no current step")
        self._steps[-1].append_record(record)

    def _find_assistant(self, message_id: str | None) -> dict | None:
        if message_id is None or not self._steps:
            return None
        return next(
            (
                record
                for record in reversed(self._steps[-1].sdk_records)
                if record["type"] == "assistant"
                and record.get("message_id") == message_id
            ),
            None,
        )

    def _extend_assistant(self, target: dict) -> None:
        if not self._steps:
            return
        rendered_blocks = len(get_content(target))
        blocks = self._steps[-1].take_pending_content(target.get("parent_tool_use_id"))
        if blocks:
            target["content"].extend(blocks)
        self._render_actions.append(
            _UpdateAction(record=copy.deepcopy(target), end=rendered_blocks)
        )
        if blocks:
            self._append_pending_results(blocks)

    def _append_pending_results(self, blocks: list[dict]) -> None:
        if not self._steps:
            return
        for result in self._steps[-1].take_results_for(blocks):
            self._append_current_record(result)

    def _flush_assistant(
        self,
        identity: str | None,
        usage: dict | None,
        ts: float,
        *,
        model: str,
        message_id: str | None,
    ) -> None:
        if not self._steps:
            return
        blocks = self._steps[-1].pending_content(identity)
        if not blocks:
            return
        if message_id is None:
            prefix = f"msg_synth_{self.unit_name}_"
            sequences = [
                int(record["message_id"].removeprefix(prefix))
                for step in self._steps
                for record in step.sdk_records
                if record.get("type") == "assistant"
                and isinstance(record.get("message_id"), str)
                and record["message_id"].startswith(prefix)
                and record["message_id"].removeprefix(prefix).isdigit()
            ]
            message_id = f"{prefix}{max(sequences, default=0) + 1:04d}"
        self._append_record(
            {
                "type": "assistant",
                "model": model,
                "content": blocks,
                "usage": usage,
                "parent_tool_use_id": identity,
                "message_id": message_id,
                "stop_reason": None,
                "session_id": None,
                "uuid": None,
                "ts": ts,
            },
            ts,
        )
        self._steps[-1].take_pending_content(identity)
        self._append_pending_results(blocks)

    def resume(self, records: "Sequence[Event]") -> None:
        """Continue this session from its saved events.

        Folds the events into memory, then advances the same per-record render state the live writer advances, so the next append lands after everything already recorded."""
        self._started = True
        write_files = self._write_files
        self._write_files = False
        try:
            for stored_event in records:
                if self._apply_event(stored_event, stored_event.ts):
                    self._render_incremental()
        finally:
            self._write_files = write_files
        replayed = list(self.iter_records())
        # The update actions were consumed by the process that made them;
        # only the output anchor still has a job. Two unflushed windows
        # surround an accept. Saved before any record existed: the link never
        # reached the file (the gate needs a rendered record), so the latch
        # stays open and the anchor places it on the next render. Saved with
        # records present but killed before the footer append: the footer is
        # gone with every other unflushed write, and the latch below closes
        # over it on purpose -- the run does not look back.
        self._render_actions = [
            a for a in self._render_actions if isinstance(a, _OutputAction)
        ]
        self._render_action_cursor = len(self._render_actions)
        self._output_link_written = bool(replayed) and bool(self._render_actions)

    def _render_incremental(self) -> None:
        records = list(self.iter_records())
        has_sdk = bool(records)
        if not has_sdk and self._structured_output_payload is None:
            return
        step_dir = str(self.step_dir)
        if self._write_files:
            self._write_output_json(step_dir)
        if not has_sdk:
            return
        prepopulate_sub_name_by_tu(records, self._sub_name_by_tu, self._sub_name_seq)
        self._render_records(records, step_dir)

    def _render_records(self, records: list[dict], step_dir: str) -> None:
        for action in self._render_actions[self._render_action_cursor :]:
            if not isinstance(action, _UpdateAction):
                continue
            chunk, sub_appends = render_appended_assistant_blocks(
                self.unit_name,
                action.record,
                action.end,
                self._sub_name_by_tu,
                self._sub_name_seq,
                self._seen_ids,
            )
            if chunk:
                self._append_execution(step_dir, chunk)
            for sub_name, text in sub_appends:
                self._append_sub_agent(step_dir, sub_name, text)
        while self._cursor < len(records):
            self._render_output_link(step_dir)
            rec = records[self._cursor]
            chunk, sub_appends = render_record(
                rec,
                records,
                self.unit_name,
                self._current_mid,
                self._resolve_last_assistant(records),
                self._seen_ids,
                self._next_turn_number(rec),
                self._sub_name_by_tu,
                self._sub_name_seq,
                self._cost_fn,
            )
            if chunk:
                self._append_execution(step_dir, chunk)
            for sub_name, text in sub_appends:
                self._append_sub_agent(step_dir, sub_name, text)
            self._advance_render_state(rec, self._cursor)
            self._cursor += 1
        self._render_output_link(step_dir)
        self._render_action_cursor = len(self._render_actions)

    def _output_action(self) -> _OutputAction | None:
        return next(
            (a for a in self._render_actions if isinstance(a, _OutputAction)),
            None,
        )

    def _render_output_link(self, step_dir: str) -> None:
        action = self._output_action()
        if (
            self._structured_output_payload is not None
            and not self._output_link_written
            and action is not None
            and (
                (
                    action.end is None
                    and self._cursor > 0
                    and action.record_index in {0, self._cursor}
                )
                or (action.end is not None and action.record_index < self._cursor)
            )
        ):
            ordinal = self._action_ordinal
            if ordinal is None:
                raise AssertionError("structured Output has no Action ordinal")
            self._append_execution(step_dir, output_json_footer(ordinal))
            self._output_link_written = True

    def _append_execution(self, step_dir: str, chunk: str) -> None:
        self._execution_chunks.append(chunk)
        if self._write_files:
            append_transcript(step_dir, "execution.md", chunk)

    def _append_sub_agent(self, step_dir: str, name: str, chunk: str) -> None:
        if self._write_files:
            append_transcript(step_dir + "/sub-agents", f"{name}.md", chunk)

    def _resolve_last_assistant(self, records: Sequence[dict]) -> dict | None:
        if self._last_assistant_index is None:
            return None
        if self._current_mid is None:
            raise AssertionError("Transcript assistant render identity is incomplete")
        if self._last_assistant_index >= len(records):
            raise AssertionError("Transcript assistant render index is out of range")
        record = records[self._last_assistant_index]
        if record.get("type") != "assistant":
            raise AssertionError(
                "Transcript assistant render index resolved non-assistant"
            )
        if get_msg_id(record) != self._current_mid:
            raise AssertionError("Transcript assistant render message changed")
        return record

    def _advance_render_state(self, record: dict, record_index: int) -> None:
        if record.get("type") == "assistant":
            self._current_mid = get_msg_id(record)
            self._last_assistant_index = record_index
            for block in get_content(record):
                if get_block_kind(block) == "tool_use":
                    self._seen_ids.add(block.get("id", ""))
        elif record.get("type") == "result":
            self._current_mid = None
            self._last_assistant_index = None

    def _next_turn_number(self, record: dict) -> int | None:
        if record.get("type") != "assistant":
            return None
        parent_id = record.get("parent_tool_use_id")
        if parent_id is not None and parent_id in self._sub_name_by_tu:
            return None
        if self._current_mid == get_msg_id(record):
            return None
        self._turn_number += 1
        return self._turn_number

    def _build_header(self, started_iso: str) -> str:
        links = ["system_prompt.md"] if self._write_files else []
        if self._has_user_message and self._write_files:
            links.append("user_message.md")
        if self._has_input and self._write_files:
            links.append("input.json")
        return format_execution_header(self.unit_name, started_iso, links)

    def _write_output_json(self, step_dir: str) -> None:
        if self._structured_output_payload is None:
            return
        ordinal = self._action_ordinal
        if ordinal is None:
            raise AssertionError("structured Output has no Action ordinal")
        payload = json.dumps(self._structured_output_payload, indent=2)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if digest == self._output_json_sha256:
            return
        self._output_json_sha256 = digest
        write_sidecar(step_dir, f"output_{ordinal}.json", payload)

    def query_boundary(self, ordinal: int) -> int | None:
        """The turn index where query ``ordinal`` begins, if it started."""
        return self._query_boundaries.get(ordinal)

    def latest_assistant_usage(self) -> tuple[int, int]:
        if not self._steps:
            return 0, 0
        turns = 0
        latest_input = 0
        for record in self._steps[-1].sdk_records:
            if record.get("type") != "assistant":
                continue
            turns += 1
            usage = Usage.from_raw(record.get("usage"))
            latest_input = (
                usage.input_tokens
                + usage.cache_read_input_tokens
                + usage.cache_creation_input_tokens
            )
        return turns, latest_input

    def last_activity_s(self, now: float) -> float | None:
        if not self._steps:
            return None
        last = self._steps[-1]
        stamp = last.end_ts if last.end_ts is not None else last.start_ts
        return max(0.0, now - stamp)


class TranscriptProjection:
    """Transcript-only fold over one run notification prefix."""

    def __init__(
        self, run_home: str | Path, *, write_files: bool = False
    ) -> None:
        self._write_files = write_files
        self._workspace = Path(run_home) / "workspace"
        self._paths: set[str] = set()
        self._transcripts: dict[str, Transcript] = {}

    def reset(self) -> None:
        self._paths.clear()
        self._transcripts.clear()

    def resume_all(self, events: "Iterable[object]") -> None:
        for event in events:
            path = self._route(event)
            if path is not None:
                self._transcript(path).resume([cast("Event", event)])

    def apply(self, event: object) -> None:
        path = self._route(event)
        if path is not None:
            event = cast("Event", event)
            self._transcript(path).apply_event(event, event.ts)

    def _route(self, event: object) -> str | None:
        from engine.durable_execution import resolve_execution_type
        from engine.event.events import ExecutionCreated
        from engine.work_unit.agent.base import Agent

        if isinstance(event, ExecutionCreated):
            execution_type = resolve_execution_type(event.type_name)
            if issubclass(execution_type, Agent):
                path = (
                    event.uid if event.target is None else f"{event.target}/{event.uid}"
                )
                self._paths.add(path)
            return None
        target = getattr(event, "target", None)
        if target not in self._paths:
            return None
        return cast(str, target)

    def for_agent(self, agent: "Agent") -> Transcript:
        return self._transcript(agent.path)

    def _transcript(self, path: str) -> Transcript:
        transcript = self._transcripts.get(path)
        if transcript is None:
            # ``run_1`` is the fixed on-disk home of an identity's transcript;
            # a retry is an Attempt of the same Action, never a new run.
            run_dir = self._workspace / path / "runs" / "run_1"
            transcript = Transcript(
                run_dir, path.replace("/", "_"), write_files=self._write_files
            )
            self._transcripts[path] = transcript
        return transcript
