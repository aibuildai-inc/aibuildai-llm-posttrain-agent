"""Turn one Transcript into document values without writing files."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

from output.transcript._compute_helpers import (
    append_assistant_blocks,
    compute_user_blocks,
    get_block_kind,
    get_content,
    get_model,
    get_msg_id,
)
from output.transcript._context_line import compute_context_line, compute_turn_usage
from output.transcript.view import (
    BlockData,
    ExecutionDocument,
    StepResultData,
    SubAgentDocument,
    TurnData,
)

if TYPE_CHECKING:
    from output.transcript.transcript import Transcript


def _require_result_field(rec: dict, key: str):
    """Return rec[key] or raise — used for ResultMessage fields the SDK contract guarantees (subtype, stop_reason, num_turns).

    Loud-fail: was previously rec.get(k, "?"), which let displayable "?" sentinels leak into Step result blocks in execution.md.
    """
    v = rec.get(key)
    if v is None:
        raise AssertionError(
            f"_walk_records: result record missing {key!r}; rec keys: "
            f"{sorted(rec.keys())}, rec: {rec!r}. Upstream: the backend event adapter "
            f"(SDK ResultMessage always populates {key!r})."
        )
    return v


@overload
def _require_optional_number(
    rec: dict, key: Literal["duration_ms", "duration_api_ms"]
) -> "int | None": ...


@overload
def _require_optional_number(
    rec: dict, key: Literal["total_cost_usd"]
) -> "float | None": ...


def _require_optional_number(
    rec: dict,
    key: "Literal['duration_ms', 'duration_api_ms', 'total_cost_usd']",
) -> "int | float | None":
    """Return a result number that may truthfully carry None."""
    if key not in rec:
        raise AssertionError(
            f"_walk_records: result record missing {key!r}; rec keys: "
            f"{sorted(rec.keys())}, rec: {rec!r}."
        )
    value = rec[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(
            f"_walk_records: result field {key!r} must be a number or None, "
            f"got {value!r}."
        )
    if key in ("duration_ms", "duration_api_ms"):
        if not isinstance(value, int):
            raise AssertionError(
                f"_walk_records: result field {key!r} must be an integer or "
                f"None, got {value!r}."
            )
        return value
    return float(value)


def _set_turn_context(turn: TurnData, rec: dict) -> None:
    """Set the turn's ``context_line`` AND its structured siblings (model / input / output tokens) from its last assistant ``rec`` (the canonical 'this turn's context' record). Folds the three formerly-duplicated context_line sites into one, so the formatted line and the structured fields the Web trajectory header reads never drift apart."""
    src = "parent" if not rec.get("parent_tool_use_id") else "sub"
    turn.context_line = compute_context_line(rec, src)
    turn.input_tokens, turn.output_tokens, turn.model = compute_turn_usage(rec)


def compute_execution_document(src: "Transcript") -> ExecutionDocument:
    """Build an ExecutionDocument from one Transcript. Pure; no I/O."""
    turns, sub_docs = _walk_records(src)
    return ExecutionDocument(turns=turns, sub_agent_documents=sub_docs)


def _walk_records(
    src: "Transcript",
) -> tuple[list[TurnData], list[SubAgentDocument]]:
    turns: list[TurnData] = []
    sub_docs: list[SubAgentDocument] = []
    current_turn: TurnData | None = None
    current_mid: str | None = None
    last_assistant: dict | None = None
    current_owner: "SubAgentDocument | None" = None
    # None until the first assistant record sets it; reads at the
    # "result" branch raise if it is still None (broken record order:
    # SDK contract is that every result follows >=1 assistant in the
    # same step). Loud-fail clean-up: was previously "?".
    last_model: str | None = None
    seen_ids: set[str] = set()
    sub_by_tu: dict[str, SubAgentDocument] = {}
    sub_name_by_tu: dict[str, str] = {}
    # A LOCAL sub-name counter: each document build owns its own sequence so a
    # re-render is idempotent and never carries a bumped count into the next
    # call (every caller of resolve_sub_name, including the
    # incremental writer, now scopes its counter to its own lifetime).
    # Without this a Web re-render bumped "general-purpose" ->
    # "general-purpose_2" -> ...
    name_seq: dict[str, int] = {}
    # Per-owner turn numbering: the parent agent and EACH sub-agent number their
    # own turns 1..n independently. A sub-owned turn counts off its
    # SubAgentDocument's own turns list (the current turn is appended only once
    # the NEXT turn opens, so +1 is the in-flight turn); a parent turn counts off
    # this dedicated parent counter. Numbering off the single global len(turns)
    # mixed parent + interleaved sub-agent turns, so the parent tab skipped
    # numbers (1,2,3,41..) and concurrent sub-agents got sparse, non-1-based tabs.
    parent_turn_seq = 0

    def _next_turn_number(owner: "SubAgentDocument | None") -> int:
        nonlocal parent_turn_seq
        if owner is not None:
            return len(owner.turns) + 1
        parent_turn_seq += 1
        return parent_turn_seq

    for rec in src.iter_records():
        kind = rec.get("type")

        if kind == "assistant":
            if get_model(rec):
                last_model = get_model(rec)
            mid = get_msg_id(rec)
            ptid = rec.get("parent_tool_use_id")
            source = "parent" if not ptid else f"sub/{sub_name_by_tu.get(ptid, ptid)}"
            for b in get_content(rec):
                if get_block_kind(b) == "tool_use":
                    seen_ids.add(b.get("id", ""))
            if current_mid is None or current_mid != mid:
                if current_turn is not None:
                    if last_assistant is not None:
                        _set_turn_context(current_turn, last_assistant)
                    turns.append(current_turn)
                    if current_owner is not None:
                        current_owner.turns.append(current_turn)
                current_owner = sub_by_tu.get(ptid) if ptid else None
                current_turn = TurnData(
                    turn_number=_next_turn_number(current_owner), message_id=mid
                )
                current_turn.sub_owned = current_owner is not None
            current_mid, last_assistant = mid, rec
            if (
                current_turn is None
            ):  # current_mid and current_turn are stamped together above
                raise AssertionError("assistant record reached without an open turn")
            append_assistant_blocks(
                rec,
                source,
                current_turn,
                sub_docs,
                sub_by_tu,
                sub_name_by_tu,
                name_seq,
            )

        elif kind == "user":
            sub_ids = set(sub_by_tu.keys())
            user_blocks, _ = compute_user_blocks(rec, "0:00:00", seen_ids, sub_ids)
            if current_turn is None:
                current_owner = None
                current_turn = TurnData(turn_number=_next_turn_number(current_owner))
            for ub in user_blocks:
                if ub.kind == "sub_agent_return":
                    tid = ub.tool_use_id
                    if not tid:
                        raise AssertionError(
                            f"_walk_records: sub_agent_return block missing "
                            f"tool_use_id; block: {ub!r}. Upstream: "
                            f"_compute_helpers.compute_user_blocks (the "
                            f"sub_agent_return branch is gated on tid in sub_ids)."
                        )
                    ub.sub_return_name = sub_name_by_tu.get(tid, tid)
                    if tid in sub_by_tu:
                        sub_by_tu[tid].final_reply = ub.content
                current_turn.blocks.append(ub)

        elif kind == "result":
            total_cost = _require_optional_number(rec, "total_cost_usd")
            if last_assistant is not None and current_turn is not None:
                _set_turn_context(current_turn, last_assistant)
            # compute.py uses "0:00:00" as the canonical "no per-record time"
            # marker (compute_user_blocks above); follow the same convention
            # for the result-section ts so the document is internally consistent.
            sr = StepResultData(
                subtype=_require_result_field(rec, "subtype"),
                stop_reason=_require_result_field(rec, "stop_reason"),
                turns=_require_result_field(rec, "num_turns"),
                duration_ms=_require_optional_number(rec, "duration_ms"),
                duration_api_ms=_require_optional_number(rec, "duration_api_ms"),
                total_cost_usd=total_cost,
                ts="0:00:00",
                is_error=rec.get("is_error", False),
            )
            if last_model is None:
                raise AssertionError(
                    f"_walk_records: result record arrived before any "
                    f"assistant record set last_model; rec: {rec!r}. SDK "
                    f"contract: every ResultMessage follows >=1 AssistantMessage "
                    f"in the same step."
                )
            if current_turn is not None:
                current_turn.step_result = sr
                turns.append(current_turn)
                if current_owner is not None:
                    current_owner.turns.append(current_turn)
                current_turn = None
            current_mid = None
            last_assistant = None
            current_owner = None

        elif kind in (
            "system",
            "logline",
            "task_progress",
            "output_rejected",
        ):
            if current_turn is not None:
                current_turn.blocks.append(BlockData(kind=kind, extra=rec))

    if current_turn is not None:
        if last_assistant is not None:
            _set_turn_context(current_turn, last_assistant)
        turns.append(current_turn)
        if current_owner is not None:
            current_owner.turns.append(current_turn)
    return turns, sub_docs
