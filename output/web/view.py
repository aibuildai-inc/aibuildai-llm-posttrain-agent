"""Adapt existing run facts to Web responses.

This module derives, it never owns: status words come from
``output.status_presentation``, the run cost from ``output.run_cost``, the
selected result from the root Search Output, and containment from the durable
journal ``path`` / ``parent_path`` on
``RunState.records``. Topology is the
recorded Action references, published as their own
channel on the summary and never turned into an execution-tree parent
link — containment and topology stay two separate relationships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel

from engine.durable_execution import action_output
from engine.failure import Failure
from engine.composite import Composite
from engine.search.base import Search
from engine.builtin.aibuildai.io import SearchOutput, SearchResult
from engine.builtin.aibuildai.programs.score import ScoreOutput
from engine.status import StatusState
from engine.work_unit.agent.base import Agent
from engine.work_unit.base import WorkUnit
from engine.work_unit.program.base import Program
from infra import model_catalog
from infra.cost_pricing import can_price
from output.run_helpers import (
    _resolve_metric_direction,
    _resolve_metric_name,
)
from output.run_cost import compute_run_cost
from output.status_presentation import classify_run, presentation_for
from output.transcript._context_line import CONTEXT_RE
from output.transcript.format import FOLD_HEAD_RE, TEXT_RE, TOOL_CALL_RE, TURN_HEAD_RE
from output.web.metric_series import MetricSeriesTail
from output.web.models import (
    ExecutionDetailResponse,
    ExecutionFamily,
    ExecutionSummary,
    FactRow,
    MetricComponent,
    RunResponse,
    ScoredExecution,
    RunWarningRow,
    MetricSeries,
    MetricSeriesResponse,
    TranscriptResponse,
    FoldBlock,
    OtherBlock,
    TextBlock,
    ToolCallBlock,
    TranscriptBlock,
    TranscriptTurn,
)

if TYPE_CHECKING:
    from engine.event.base import Event
    from engine.event.events import RunOpened
    from engine.durable_execution import ActionRecord, ExecutionRecord
    from engine.run_state import RunState
    from infra.util.clock import Clock
    from output.transcript import Transcript

TranscriptFor = Callable[[Agent], "Transcript"]


def _occurrence(path: str, ordinal: int) -> str:
    return f"{path}#{ordinal}"


def _parse_occurrence(value: str) -> tuple[str, int]:
    path, separator, ordinal = value.rpartition("#")
    if not separator or not ordinal.isdigit():
        raise ValueError(f"invalid Action occurrence {value!r}")
    return path, int(ordinal)


def display_clock(run_state: "RunState", now_relative: float) -> "Clock":
    """Build the Clock view for one Web frame."""
    from infra.util.clock import Clock

    cfg = run_state.require_run_config()
    return Clock.at_elapsed(run_state, cfg.run_budget_s, now_relative)


def _model_display(configured: str, *, routed: bool) -> str:
    """Return the model label shown by the Web view."""
    if routed:
        return "routed"
    matched = model_catalog.canonical_key_for(configured)
    if matched is not None and matched != configured:
        return f"{configured} -> {matched}"
    return configured


@dataclass(frozen=True)
class WebFrame:
    state: "RunState"
    transcript_for: TranscriptFor
    now_relative: float
    now_absolute: float


class WebViewStore:
    """The disposable current and historical frames of one Run journal."""

    def __init__(
        self,
        run_home: str | Path,
        state_at: "Callable[[int], RunState]",
    ) -> None:
        from output.transcript import TranscriptProjection

        self._run_home = Path(run_home)
        self._state_at = state_at
        self._events: list[Event | RunOpened] = []
        self._head_transcripts = TranscriptProjection(run_home)
        self.head: WebFrame | None = None
        # Each WorkUnit has one incremental reader for its append-only metrics.
        self.metric_tails: dict[str, MetricSeriesTail] = {}

    def publish(
        self,
        state: "RunState",
        events: "list[Event | RunOpened] | tuple[Event, ...]",
    ) -> None:
        """Publish one complete committed event tail as the new head."""
        if not events:
            return
        if self.head is None:
            self._head_transcripts.resume_all(events)
        else:
            for event in events:
                self._head_transcripts.apply(event)
        self._events.extend(events)
        latest = self._events[-1]
        self.head = WebFrame(
            state=state,
            transcript_for=self._head_transcripts.for_agent,
            now_relative=latest.ts,
            now_absolute=latest.timestamp.timestamp(),
        )

    def frame(
        self, *, revision: int | None = None, at_s: float | None = None
    ) -> WebFrame:
        """Return the head or one journal-time historical frame."""
        head = self.head
        if head is None:
            raise LookupError("the Run has no projected frame")
        if revision is not None and at_s is not None:
            raise ValueError("request a frame by revision or time, not both")
        if at_s is not None:
            revision = next(
                (
                    event.originator_version
                    for event in reversed(self._events)
                    if event.ts <= at_s
                ),
                self._events[0].originator_version,
            )
        if revision is None or revision == head.state.version:
            return head
        if revision < 1 or revision > head.state.version:
            raise LookupError(f"Run revision {revision} is outside the journal")
        event = next(
            (item for item in self._events if item.originator_version == revision),
            None,
        )
        if event is None:
            raise LookupError(f"Run revision {revision} is not recorded")
        from output.transcript import TranscriptProjection

        transcripts = TranscriptProjection(self._run_home)
        transcripts.resume_all(
            item for item in self._events if item.originator_version <= revision
        )
        return WebFrame(
            state=self._state_at(revision),
            transcript_for=transcripts.for_agent,
            now_relative=event.ts,
            now_absolute=event.timestamp.timestamp(),
        )


def _style_token(state: StatusState) -> str:
    """The CSS-variable suffix for one status, read off the presentation SSOT's theme token (``bold success`` and ``success`` both style with the success ink)."""
    return presentation_for(state).token.removeprefix("bold ").strip()


def _agent_cost(agent: Agent, ordinal: int) -> float | None:
    """Return what ONE Action of this Agent spent, or no value for an unpriced zero.

    A row is one Action occurrence, so it shows that Action's own bill. The
    identity's lifetime total is a different number with its own place; showing
    it on every row would tell a reader that each of two calls spent what the
    two spent together."""
    cost = agent.cost.action_cost_usd(ordinal)
    model = getattr(agent, "resolved_model", None)
    if model and cost == 0.0 and not can_price(model):
        return None
    return cost


def _selected_result(run_state: "RunState") -> SearchOutput | None:
    output = run_state.search._run_output()
    return output.output if isinstance(output, SearchResult) else None


def _selected_execution(
    run_state: "RunState", selected: "SearchOutput | None"
) -> str | None:
    """Which execution produced the run's selected Output.

    Derived, not stored: the selected Output names the attempt directory it was
    measured on, and the execution that measured that directory is the one the
    Atlas highlights. Nothing persists a producer path for the display to read
    back."""
    if selected is None:
        return None
    for record in run_state.records.values():
        for action in record.actions:
            output = action.output
            if (
                isinstance(output, ScoreOutput)
                and output.output_dir == selected.output_dir
            ):
                return _occurrence(record.path, len(record.actions))
    return None


def _scored_by_path(run_state: "RunState") -> dict[str, ScoredExecution]:
    """Every execution whose own terminal Output carries the run's score.

    Derived from the journal, not asked of any algorithm: the run's own score
    Program declares :class:`ScoreOutput`, so an execution that settled with
    one IS a scored result and its own recorded path is the key. A generated
    Search package's own scored executions are found by the same rule as a
    built-in package's, because both settle with the same Output type."""
    scored: dict[str, ScoredExecution] = {}
    for record in run_state.records.values():
        for action in record.actions:
            output = action.output
            if isinstance(output, ScoreOutput):
                scored[record.path] = ScoredExecution(
                    score=output.score,
                    metric_components=[
                        MetricComponent(name=name, value=value)
                        for name, value in output.components
                    ],
                )
    return scored


def _summarize(
    record: "ExecutionRecord",
    action: "ActionRecord",
    run_state: "RunState",
    clock: "Clock",
    *,
    scored: dict[str, ScoredExecution],
) -> ExecutionSummary:
    execution = record.execution
    status = StatusState.of_action(action)
    pres = presentation_for(status)
    family: ExecutionFamily = "execution"
    elapsed = action.active_s
    if action.attempt_started_at_s is not None:
        elapsed += max(0.0, clock.run_elapsed_s() - action.attempt_started_at_s)
    cost: float | None = None
    own_score = scored.get(record.path)
    metric: float | None = None if own_score is None else own_score.score
    model: str | None = None
    if isinstance(execution, Search):
        family = "search"
    elif isinstance(execution, Composite):
        family = "composite"
        cost = run_state.subtree_cost_usd(record.path)
    elif isinstance(execution, Agent):
        family = "agent"
        cost = _agent_cost(execution, action.ordinal)
        model = getattr(execution, "resolved_model", None)
    elif isinstance(execution, Program):
        family = "program"
    elif isinstance(execution, WorkUnit):
        family = "work_unit"
    budget_s: float | None = None
    if isinstance(execution, WorkUnit):
        # This row's own Action, read from the record this summary already
        # holds. The live property answers for the Action a process is
        # running, and a rendered row is not running one.
        budget_s = action.capability.wall_clock_seconds
    return ExecutionSummary(
        path=_occurrence(record.path, action.ordinal),
        identity_path=record.path,
        action_ordinal=action.ordinal,
        parent_path=(
            None
            if record.parent_path is None or action.caller_ordinal is None
            else _occurrence(record.parent_path, action.caller_ordinal)
        ),
        family=family,
        label=(
            record.uid
            if len(record.actions) == 1
            else f"{record.uid} #{action.ordinal}"
        ),
        action_method=None if action.method == "run" else action.method,
        status=status.value,
        status_word=pres.label,
        status_symbol=pres.symbol,
        status_style_token=_style_token(status),
        elapsed_s=elapsed,
        cost=cost,
        metric=metric,
        model=model,
        budget_s=budget_s,
        child_count=sum(
            1
            for other in run_state.records.values()
            if other.parent_path == record.path
            for child_action in other.actions
            if child_action.caller_ordinal == action.ordinal
        ),
        transcript_available=isinstance(execution, Agent) and action.attempts > 0,
        upstream_actions=[
            _occurrence(path, ordinal) for path, ordinal in action.upstream
        ],
        score=(
            own_score if action.ordinal == len(record.actions) else None
        ),
    )


def build_run_response(
    run_state: "RunState",
    *,
    head_revision: int,
    head_elapsed_s: float,
    now_relative: float,
    now_absolute: float,
    view_age_s: float,
) -> RunResponse:
    """The polled run surface, derived once per render from existing owners."""
    cfg = run_state.require_run_config()
    clock = display_clock(run_state, now_relative)
    search = run_state.aibuildai_search
    terminal = (
        search.terminal_status() if run_state.search._run_output() is not None else None
    )
    status, status_word = classify_run(terminal)
    suspended = terminal is None and search.run_suspend_requested is not None
    if suspended:
        status_word = "PAUSED"
    run_cost = compute_run_cost(run_state)
    direction = _resolve_metric_direction(run_state)
    scored = _scored_by_path(run_state)
    executions = [
        _summarize(
            record,
            action,
            run_state,
            clock,
            scored=scored,
        )
        for record in run_state.records.values()
        for action in record.actions
    ]
    selected = _selected_result(run_state)
    return RunResponse(
        run_id=cfg.run_id,
        head_revision=head_revision,
        frame_revision=run_state.version,
        head_elapsed_s=head_elapsed_s,
        frame_elapsed_s=now_relative,
        task_name=cfg.task_name,
        status=status.value,
        status_word=status_word,
        status_symbol=presentation_for(status).symbol,
        status_style_token="warning" if suspended else _style_token(status),
        elapsed_s=now_relative,
        remaining_s=(
            None
            if clock.exploration_remaining_s() == float("inf")
            else clock.exploration_remaining_s()
        ),
        total_cost=run_cost.headline_total,
        cost_is_estimate=run_cost.is_estimate,
        cost_budget_usd=cfg.cost_budget_usd,
        selected_result_path=_selected_execution(run_state, selected),
        selected_metric=None if selected is None else selected.score,
        metric_name=_resolve_metric_name(run_state),
        metric_direction=direction,
        model=_model_display(cfg.model, routed=cfg.models_routed),
        active_execution_count=sum(
            len(record.active_actions) for record in run_state.records.values()
        ),
        warnings_count=len(run_state.run_warnings),
        warnings=[
            RunWarningRow(
                source=w.source, category=w.category, message=w.message, at_s=w.at_s
            )
            for w in run_state.run_warnings
        ],
        result_recorded=terminal is not None,
        generated_at_unix=now_absolute,
        view_age_s=view_age_s,
        executions=executions,
    )


def _dump(value: object) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    return str(value)


def _facts(
    record: "ExecutionRecord", run_state: "RunState", summary: ExecutionSummary
) -> list[FactRow]:
    execution = record.execution
    facts: list[FactRow] = []
    if isinstance(execution, Search):
        # The Search class itself. There is no second root-selection name to
        # show: one map selects the package, and this is what it selected.
        facts.append(FactRow(label="search", value=type(execution).__name__))
    if summary.action_method is not None:
        facts.append(FactRow(label="action", value=summary.action_method))
    if isinstance(execution, Agent):
        facts.append(FactRow(label="role", value=type(execution).name))
        facts.append(FactRow(label="turns", value=str(execution.cost.total_turns)))
    if summary.metric is not None:
        facts.append(
            FactRow(label=_resolve_metric_name(run_state), value=f"{summary.metric:g}")
        )
    return facts


def build_execution_detail(
    run_state: "RunState",
    path: str,
    *,
    now_relative: float,
) -> ExecutionDetailResponse | None:
    """One execution's detail, or None for an unknown path (the app's 404)."""
    try:
        identity_path, ordinal = _parse_occurrence(path)
    except ValueError:
        return None
    record = run_state.records.get(identity_path)
    if record is None:
        return None
    if ordinal < 1 or ordinal > len(record.actions):
        return None
    action = record.actions[ordinal - 1]
    clock = display_clock(run_state, now_relative)
    summary = _summarize(
        record,
        action,
        run_state,
        clock,
        scored=(scored := _scored_by_path(run_state)),
    )
    output = action_output(record, ordinal)
    failure = output if isinstance(output, Failure) else None
    own_score = scored.get(identity_path)
    components = [] if own_score is None else own_score.metric_components
    return ExecutionDetailResponse(
        summary=summary,
        facts=_facts(record, run_state, summary),
        metric_components=components,
        input_summary=_dump(record.execution.input),
        output_summary=(
            _dump(output) if output is not None and failure is None else None
        ),
        failure_summary=_dump(failure) if failure is not None else None,
        child_paths=[
            _occurrence(other.path, child_action.ordinal)
            for other in run_state.records.values()
            if other.parent_path == identity_path
            for child_action in other.actions
            if child_action.caller_ordinal == ordinal
        ],
        workspace_path=str(
            Path(run_state.execution_directory(identity_path)).relative_to(
                run_state.workspace_directory()
            )
        ),
    )


def metric_series_source(
    run_state: "RunState", path: str
) -> tuple[str, StatusState] | None:
    """Copy one WorkUnit's file address and status on the run event loop. RunState owns the directory formula, so a restored run (no running Search bound) answers the same address a live one does."""
    try:
        identity_path, ordinal = _parse_occurrence(path)
    except ValueError:
        return None
    record = run_state.records.get(identity_path)
    if record is None or not isinstance(record.execution, WorkUnit):
        return None
    if ordinal < 1 or ordinal > len(record.actions):
        return None
    unit = record.execution
    action = record.actions[ordinal - 1]
    return (
        f"{unit.artifacts_dir_at(run_state, ordinal, max(1, action.attempts))}/full",
        StatusState.of_action(action),
    )


def build_metric_series(
    path: str,
    attempt_dir: str,
    status: StatusState,
    tail: MetricSeriesTail,
) -> MetricSeriesResponse:
    """Read one WorkUnit metric file into recorded curves.

    The caller runs this file work outside the run event loop. ``tail`` is
    an incremental reader, so a poll parses only appended bytes. An empty
    ``series`` is the honest no-metrics state.
    """
    segments = tail.resolve(attempt_dir, status)
    series: list[MetricSeries] = []
    for segment in segments:
        for name in segment.fields:
            trace = tail.series(segment.index, name)
            if not trace.ys:
                continue
            series.append(
                MetricSeries(
                    id=f"t{segment.index}:{name}",
                    name=name,
                    segment=segment.index,
                    x_kind="epoch" if trace.x_label == "epoch" else "step",
                    xs=trace.xs,
                    ys=trace.ys,
                )
            )
    return MetricSeriesResponse(path=path, series=series)


def _chunks(body: str) -> list[str]:
    """Split one turn's Markdown into block chunks. A chunk starts at a stamped line (``[+t]``), a stamped fold, or the context line; a fold or a code fence is never split inside. A fold WITHOUT a stamp is a tool input and stays in its tool call's chunk."""
    chunks: list[list[str]] = []
    in_fold = in_fence = False
    for line in body.split("\n"):
        starts = (
            line.startswith("[+")
            or line.startswith("<details><summary>[+")
            or line.startswith("**Context**")
        )
        if starts and not in_fold and not in_fence and chunks:
            chunks.append([])
        if not chunks:
            chunks.append([])
        chunks[-1].append(line)
        if line.startswith("```"):
            in_fence = not in_fence
        if line.startswith("<details>"):
            in_fold = True
        if line.startswith("</details>"):
            in_fold = False
    return [c for c in ("\n".join(lines).strip() for lines in chunks) if c]


def _fold(head: "re.Match[str]", lines: list[str]) -> FoldBlock:
    # shape_collapsible always writes one fenced block; anything else is a
    # malformed transcript and must fail here, never render as "empty".
    fences = [i for i, l in enumerate(lines) if i > 0 and l.startswith("```")]
    if len(fences) < 2:
        raise AssertionError(f"fold without a closed code fence: {lines[0]!r}")
    fence, end = fences[0], fences[-1]
    code = "\n".join(lines[fence + 1:end])
    return FoldBlock(ts=head[1], tag=head[2], title=head[3], meta=head[4], lang=lines[fence][3:].strip(), code=code)


def _block(chunk: str) -> "FoldBlock | TextBlock | ToolCallBlock | OtherBlock | tuple[str, str]":
    lines = chunk.split("\n")
    head = lines[0]
    if (m := FOLD_HEAD_RE.match(head)) is not None:
        return _fold(m, lines)
    if (m := TOOL_CALL_RE.match(head)) is not None:
        rest = lines[1:]
        note = None
        if rest and re.fullmatch(r"_.+_", rest[-1].strip()):
            note = rest.pop().strip()[1:-1]
        return ToolCallBlock(ts=m[1], name=m[2], input_markdown="\n".join(rest).strip(), note=note, result=None)
    if (m := CONTEXT_RE.match(head)) is not None:
        return (m[2], m[3])
    if (m := TEXT_RE.match(chunk)) is not None:
        return TextBlock(ts=m[1], source=m[2][1:-1], markdown=m[3])
    return OtherBlock(markdown=chunk)


def _seconds(ts: str) -> int:
    total = 0
    for part in ts.split(":"):
        total = total * 60 + int(part)
    return total


def _turn(index: int, body: str) -> TranscriptTurn:
    blocks: list[TranscriptBlock] = []
    tokens: tuple[str, str] | None = None
    for chunk in _chunks(body):
        parsed = _block(chunk)
        if isinstance(parsed, tuple):
            tokens = parsed
        else:
            blocks.append(parsed)
    # A turn may issue several tool calls at once; their results arrive in
    # the same order, so the k-th result answers the k-th call of that run.
    paired: list[TranscriptBlock] = []
    i = 0
    while i < len(blocks):
        if not isinstance(blocks[i], ToolCallBlock):
            paired.append(blocks[i])
            i += 1
            continue
        calls: list[ToolCallBlock] = []
        while i < len(blocks) and isinstance(blocks[i], ToolCallBlock):
            calls.append(blocks[i])  # type: ignore[arg-type]
            i += 1
        results: list[FoldBlock] = []
        while i < len(blocks) and len(results) < len(calls):
            nxt = blocks[i]
            if not isinstance(nxt, FoldBlock) or nxt.tag == "thinking":
                break
            results.append(nxt)
            i += 1
        for k, call in enumerate(calls):
            paired.append(call.model_copy(update={"result": results[k] if k < len(results) else None}))
    stamps = [b.ts for b in paired if not isinstance(b, OtherBlock)]
    stamps += [b.result.ts for b in paired if isinstance(b, ToolCallBlock) and b.result is not None]
    stamps.sort(key=_seconds)
    duration = _seconds(stamps[-1]) - _seconds(stamps[0]) if len(stamps) > 1 else None
    return TranscriptTurn(
        index=index,
        start_ts=stamps[0] if stamps else None,
        duration_s=duration if duration else None,
        tokens_in=tokens[0] if tokens else None,
        tokens_out=tokens[1] if tokens else None,
        blocks=paired,
    )


def parse_transcript(markdown: str) -> tuple[str, list[TranscriptTurn]]:
    """Read the written transcript back into its session header and turns."""
    parts = TURN_HEAD_RE.split(markdown)
    header = parts[0].strip()
    turns = [_turn(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return header, turns


def build_transcript(
    run_state: "RunState", path: str, *, transcript_for: TranscriptFor
) -> TranscriptResponse | None:
    """The Agent's existing rendered transcript, or None when ``path`` is unknown or names an execution that owns no transcript."""
    try:
        identity_path, ordinal = _parse_occurrence(path)
    except ValueError:
        return None
    record = run_state.records.get(identity_path)
    if record is None or not isinstance(record.execution, Agent):
        return None
    if ordinal < 1 or ordinal > len(record.actions):
        return None
    agent = record.execution
    transcript = transcript_for(agent)
    header, turns = (
        parse_transcript(transcript.execution_markdown)
        if transcript.started
        else ("", [])
    )
    # The one identity transcript records where each query began; an older
    # occurrence's page still ends where the next query starts.
    following = transcript.query_boundary(ordinal + 1)
    if following is not None:
        turns = turns[:following]
    boundary = transcript.query_boundary(ordinal) or 0
    if boundary < len(turns):
        turns[boundary].selected = True
    return TranscriptResponse(
        path=path,
        header_markdown=header,
        turns=turns,
        terminal=record.actions[ordinal - 1].ended_at_s is not None,
    )
