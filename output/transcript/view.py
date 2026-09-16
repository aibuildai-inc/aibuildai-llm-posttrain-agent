"""Transcript view models — plain dataclasses, no I/O, no side effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.cost import Usage

TAG_INFO = "[INFO]"
TAG_STOP = "[STOP]"
TAG_WARN = "[WARN]"
TAG_ERROR = "[ERROR]"
TAG_HOOK = "[HOOK]"
TAG_BUDGET = "[BUDGET]"
TAG_USER = "[USER]"
TAG_SUB = "[SUB]"

# The complete set of BlockData.kind values any producer emits (the
# transcript compute and writer helpers, the record renderer, and the
# trajectory panel all construct blocks). It is the SHARED kind model both block
# renderers iterate, so they cannot drift apart: the saved transcript's
# output/transcript/format.py:format_block and the Web trajectory view must EACH
# be exhaustive over this set -- neither may silently drop a kind the other shows.
# A new producer kind is added here, and each renderer's handling of it is a
# review obligation (no machine check enforces the exhaustiveness).
BLOCK_KINDS: frozenset[str] = frozenset(
    {
        "text",
        "thinking",
        "tool_use",
        "tool_result",
        "warn",
        "sub_agent_dispatch",
        "sub_agent_return",
        "task_progress",
        "user_text",
        "hook_injection",
        "slash_stdout",
        "system",
        "logline",
        "output_rejected",
    }
)


# ---- block-level dataclasses ------------------------------------------------


@dataclass
class BlockData:
    """Generic block for thinking, text, tool_use, tool_result, etc.

    Audited (loud-fail): every ``T | None = None`` below is legitimately optional because the kind-tag selects which field set is populated. The mapping is:

    - ``thinking``           : ``thinking``, optional ``collapsible``
    - ``text``               : ``content``, optional ``source_tag``
    - ``tool_use``           : ``tool_name``, ``tool_input``
    - ``tool_result``        : ``tool_use_id``, optional ``is_error``, ``result_content``, ``result_lang``, ``result_lines``
    - ``warn``               : ``ts``, ``warn_reason``, optional ``tool_use_id``
    - ``sub_agent_dispatch`` : ``sub_name``, ``tool_use_id``
    - ``sub_agent_return``   : ``ts``, ``sub_return_name``, ``tool_use_id``
    - ``hook_injection`` / ``slash_stdout`` / ``user_text`` : ``text``, optional ``ts``
    - ``task_progress``      : ``extra`` (TaskProgressMessage dict)
    - ``logline`` / ``system`` / ``output_rejected`` : ``extra``

    The clean fix is a tagged-union split (one dataclass per kind); that is explicitly Out-of-Scope per the loud-fail plan and is queued for the successor plan ``2026-MM-DD-loud-fail-w2-tagged- union.md``. Until then, callers MUST set the fields listed for their kind; ``format_block`` raises when a required field is None.
    """

    kind: str
    # thinking
    thinking: str | None = None
    collapsible: bool = False
    # text
    content: str | None = None
    source_tag: str | None = None
    # tool_use
    tool_name: str | None = None
    tool_input: dict | None = None
    # tool_result
    tool_use_id: str | None = None
    is_error: bool = False
    result_content: str | None = None
    result_lang: str | None = None
    result_lines: int | None = None
    # warn
    ts: str | None = None
    warn_reason: str | None = None
    # sub_agent_dispatch
    sub_name: str | None = None
    # sub_agent_return
    sub_return_name: str | None = None
    # hook / slash / user_text
    text: str | None = None
    # logline, system, etc.
    extra: Any = None


@dataclass
class StepResultData:
    subtype: str
    stop_reason: str
    turns: int | str
    duration_ms: int | str | None
    duration_api_ms: int | str | None
    total_cost_usd: float | None
    # Formatted "H:MM:SS" timestamp at which the result arrived. Required
    # (loud-fail): previously the section header used a literal
    # "?" because StepResultData had no ts field.
    ts: str
    is_error: bool = False


@dataclass
class CostBreakdownTableData:
    """Cost-breakdown payload emitted after each ResultMessage in execution.md.

    ``rows`` are 4-tuples (source, calls, summed_usage, dollars) — one row per observed parent/sub-agent source. No token-derived residual row is computed or priced (removed): ``ResultMessage.usage`` doesn't reconcile against summed per-message usage by subtraction, so that gap is never guessed at — it shows up only via ``derived_total`` vs. ``sdk_total`` in the renderer's "Unattributable" line.

    ``result_usage_available``: True when the SDK result record carried a usage dict (normal case); False when the result had no usage (e.g. early-abort turn). Feeds ``attribution_available`` below.

    ``attribution_available``: False when the breakdown cannot be meaningfully attributed for this model / backend — the parent model is unpriceable (a genuinely-unknown id or the flat subscription-billed route) OR no token traffic was recorded (all per-message usage zero, as the subscription proxy reports). When False the renderer degrades honestly to "cost unavailable for this backend/model" instead of the misleading "$X SDK billed | $0 attributable | +100% unattributable | investigate" banner.
    """

    label: str
    parent_model: str
    rows: list[tuple[str, int, Usage, float]]
    derived_total: float
    sdk_total: float | None
    result_usage_available: bool
    attribution_available: bool = True


@dataclass
class TurnData:
    turn_number: int
    blocks: list[BlockData] = field(default_factory=list)
    context_line: str | None = None
    step_result: StepResultData | None = None
    message_id: str | None = None
    # Structured siblings of context_line (the formatted string): the turn's last
    # assistant record's total input tokens, output tokens, and model id -- set by
    # compute._set_turn_context alongside context_line. The Web trajectory
    # trajectory master-detail reads these for the per-turn header (tokens in->out,
    # ctx% via context_window_for_model); None on a turn with no recorded usage.
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # True when this turn belongs to a dispatched sub-agent (the turn's assistant
    # record carried parent_tool_use_id). The trajectory master-detail's MAIN list
    # shows only parent-owned turns; a sub-agent's turns live in its own tab.
    sub_owned: bool = False


# ---- sub-agent document -------------------------------------------------------


@dataclass
class SubAgentDocument:
    sub_name: str
    subagent_type: str | None
    dispatch_description: str | None
    turns: list[TurnData] = field(default_factory=list)
    final_reply: str | None = None


# ---- top-level execution document -------------------------------------------


@dataclass
class ExecutionDocument:
    turns: list[TurnData]
    sub_agent_documents: list[SubAgentDocument]
