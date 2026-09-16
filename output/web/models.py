"""Web API response models — the thin HTTP boundary, not a domain mirror.

Every value here is derived by ``output.web.view`` from existing run facts.
FastAPI publishes these models as the OpenAPI schema, and the frontend's
TypeScript types are generated from that schema, so this file is the one
contract between the Python side and the browser.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

ExecutionFamily = Literal[
    "search", "composite", "agent", "program", "work_unit", "execution"
]


class MetricComponent(BaseModel):
    """One named component of a recorded metric."""

    name: str
    value: float


class ScoredExecution(BaseModel):
    """The score one execution's own Output carries."""

    score: float
    metric_components: list[MetricComponent]


class ExecutionSummary(BaseModel):
    """One execution summary for the Execution Atlas and run overview."""

    path: str
    identity_path: str
    action_ordinal: int
    parent_path: str | None
    family: ExecutionFamily
    label: str
    # The Action method this occurrence ran, when the number alone cannot say
    # which it was: None for the ``run`` method that every Search, Agent, and
    # single-method Program shares.
    action_method: str | None
    status: Literal["pending", "running", "done", "failed"]
    status_word: str
    status_symbol: str
    status_style_token: str
    elapsed_s: float | None
    cost: float | None
    metric: float | None
    model: str | None = None
    budget_s: float | None = None
    child_count: int
    transcript_available: bool
    # The generic direct topology: the recorded paths of the executions this
    # one consumes, in declared order. A separate relationship channel from
    # parent_path containment. The edges between two Composites are the subset
    # whose both ends are Composites; the Atlas derives that, never a second
    # field.
    upstream_actions: list[str]
    # This execution's own score, when its terminal Output carries one.
    score: ScoredExecution | None


class RunWarningRow(BaseModel):
    """One run-level downgrade as the display reads it.

    Typed rather than a loose mapping, so the generated frontend types name
    these four fields and a renderer that reads a fifth fails to compile."""

    source: str
    category: str
    message: str
    at_s: float


class RunResponse(BaseModel):
    """The polled run surface: run-level facts plus the flat execution list."""

    run_id: str
    head_revision: int
    frame_revision: int
    head_elapsed_s: float
    frame_elapsed_s: float
    task_name: str
    status: Literal["pending", "running", "done", "failed"]
    status_word: str
    status_symbol: str
    status_style_token: str
    elapsed_s: float
    remaining_s: float | None
    total_cost: float
    cost_is_estimate: bool
    cost_budget_usd: float | None
    selected_result_path: str | None
    selected_metric: float | None
    metric_name: str
    metric_direction: Literal["max", "min"]
    model: str
    active_execution_count: int
    warnings_count: int
    warnings: list["RunWarningRow"] = []
    result_recorded: bool
    generated_at_unix: float
    # Seconds since this view was generated, measured ON THE SERVER CLOCK at
    # response time (the /api/v1/run route recomputes it per request). The
    # browser judges staleness from this alone: with SSH port forwarding the
    # browser and the run sit on different machines, so comparing a browser
    # timestamp against generated_at_unix would measure clock skew, not age.
    view_age_s: float
    executions: list[ExecutionSummary]


class MemberSummary(BaseModel):
    """One member's compact row, read over its socket: a live run's current facts, or a recorded run's latest facts. Never the full execution list."""

    run_id: str
    task_name: str
    status: Literal["pending", "running", "done", "failed"]
    status_word: str
    status_style_token: str
    elapsed_s: float
    model: str
    metric_name: str
    selected_metric: float | None
    result_recorded: bool
    generated_at_unix: float
    view_age_s: float


class MemberPauseResponse(BaseModel):
    """A live member's answer to its private pause route: the request was accepted now, was already recorded, or the run already has a result. Idempotent: asking again never records a second suspend."""

    result: Literal["accepted", "already-requested", "terminal"]


class MemberIdentity(BaseModel):
    """What a member is, fixed at startup and answered before any view exists. The service compares ``member_protocol_version`` before it forwards anything."""

    run_id: str
    member_protocol_version: int


RunResult = Literal["completed", "failed"]
RunAvailability = Literal["ready", "restoring", "unavailable"]


class RunSummary(BaseModel):
    """One indexed run's row for the global run selector and the Workspace home page.

    Orthogonal authoritative facts, never a lifecycle state machine:
    ``active`` says the run has a process owner right now (its systemd
    unit or live member), ``result`` is the journal's explicit final
    Search result, ``incompatible`` says this build cannot restore the
    records, and ``availability`` says whether its projection is ready.
    ``status_word`` and ``status_style_token`` are derived presentation
    only; no policy reads them back. The optional facts are filled only
    when they are cheap: the final config for the model, and a member's
    summary when one is connected.
    """

    run_id: str
    task_name: str
    active: bool
    result: RunResult | None
    incompatible: bool
    availability: RunAvailability
    started_at_unix: float | None
    updated_at_unix: float | None
    ended_at_unix: float | None
    model: str | None
    metric_name: str | None
    selected_metric: float | None
    elapsed_s: float | None
    status_word: str
    status_style_token: str
    # A short reason when availability is ``unavailable``; never a host
    # path or a raw traceback.
    diagnostic: str | None


RunActionKind = Literal["pause", "resume"]


class RunControl(BaseModel):
    """The one Run control the server tells Web to render, if any."""

    kind: RunActionKind
    enabled: bool
    reason: str | None
    pending: bool


class RunActionFailure(BaseModel):
    """The user operation that failed and its safe display message."""

    kind: RunActionKind
    error: str


class RunCapabilitiesResponse(BaseModel):
    """The server-decided action surface and temporary per-Run operation."""

    control: RunControl | None
    failure: RunActionFailure | None


class CsrfResponse(BaseModel):
    """The service's per-start CSRF token. Mutation routes require it back in the ``X-AIBuildAI-Csrf`` header; a cross-site page cannot read this response, so it cannot mutate."""

    token: str


class RunsResponse(BaseModel):
    """Every indexed run, sorted for the selector: running first by latest update, then resumable, then ended runs by end time; ``run_id`` breaks ties."""

    runs: list[RunSummary]


class WorkspaceHealth(BaseModel):
    """The named backend identity at ``/api/v1/health``. The deployment controller reads it before Caddy promotes a generation; the frontend reads ``ready`` only."""

    workspace_name: str
    ready: Literal[True]
    app_version: str
    web_protocol_version: int
    web_build_id: str
    member_protocol_version: int


class FactRow(BaseModel):
    """One family-specific detail fact, already rendered as label and text."""

    label: str
    value: str


class ExecutionDetailResponse(BaseModel):
    """One execution's readable detail for the Inspector."""

    summary: ExecutionSummary
    facts: list[FactRow]
    # Typed metric components (Composite only; empty elsewhere) — the
    # structured-table source, kept out of the prose facts.
    metric_components: list[MetricComponent]
    input_summary: str | None
    output_summary: str | None
    failure_summary: str | None
    child_paths: list[str]
    # The execution's own directory, workspace-relative, for the Workspace
    # tree's "Reveal in workspace": RunState owns the formula, the browser
    # reconstructs nothing.
    workspace_path: str


class MetricSeries(BaseModel):
    """One metric's recorded curve from metrics.jsonl.

    A typed projection of the recorded metric data. It contains semantic
    series, never chart configuration: the frontend owns how a series
    becomes a chart. ``segment`` is the 1-based series segment (one per
    ``aibuildai.init()``), ``x_kind`` names the x axis the recorded data
    supports (epoch when strictly increasing, else step).
    """

    id: str
    name: str
    segment: int
    x_kind: Literal["epoch", "step"]
    xs: list[float]
    ys: list[float]


class MetricSeriesResponse(BaseModel):
    """The recorded metric curves for one WorkUnit execution.

    ``series`` is empty when the unit recorded no metrics (the honest
    empty state) — nothing is fabricated for display.
    """

    path: str
    series: list[MetricSeries]


class FoldBlock(BaseModel):
    """A time-stamped folded block: thinking, a tool result, a warning, a budget note. ``tag`` is the bracket tag the transcript writes (``thinking``, ``INFO``, ``WARN``, ``BUDGET``), ``title`` its summary words, ``meta`` the size note (``text, 25 lines``)."""

    kind: Literal["fold"] = "fold"
    ts: str
    tag: str
    title: str
    meta: str
    lang: str
    code: str


class TextBlock(BaseModel):
    """Agent prose. ``source`` is ``parent`` or a sub-agent name; ``markdown`` is the prose as written."""

    kind: Literal["text"] = "text"
    ts: str
    source: str
    markdown: str


class ToolCallBlock(BaseModel):
    """One tool call with its input (Markdown, as the transcript renders that tool's input), the tool's note line when the input carried one, and the result that answered it, when it has arrived."""

    kind: Literal["tool_call"] = "tool_call"
    ts: str
    name: str
    input_markdown: str
    note: str | None
    result: FoldBlock | None


class OtherBlock(BaseModel):
    """Any transcript chunk the typed blocks do not name (sub-agent dispatch, hook text, the step result table): rendered as Markdown."""

    kind: Literal["other"] = "other"
    markdown: str


TranscriptBlock = Annotated[
    FoldBlock | TextBlock | ToolCallBlock | OtherBlock, Field(discriminator="kind")
]


class TranscriptTurn(BaseModel):
    """One assistant turn. ``start_ts`` is the first stamped block's elapsed time; ``duration_s`` the span to the last one; ``tokens_in``/``tokens_out`` the context line's counts, when the turn has closed."""

    index: int
    start_ts: str | None
    duration_s: int | None
    tokens_in: str | None
    tokens_out: str | None
    blocks: list[TranscriptBlock]
    # The first turn of the selected occurrence: selecting `Coder #2` opens
    # the same identity positioned at query #2 instead of at the beginning.
    selected: bool = False


class TranscriptResponse(BaseModel):
    """The Agent transcript read back from its written Markdown into turns of typed blocks. ``header_markdown`` is everything before the first turn (the session header)."""

    path: str
    header_markdown: str
    turns: list[TranscriptTurn]
    terminal: bool


PreviewKind = Literal["code", "text", "markdown", "pdf", "image", "download_only", "unavailable"]


class WorkspaceEntry(BaseModel):
    """One entry of the run's workspace tree. ``path`` is workspace-relative POSIX; the host path never leaves the member. ``preview_kind`` is the one classifier: the browser picks its renderer from it and keeps no extension table of its own."""

    name: str
    path: str
    kind: Literal["directory", "file", "symlink", "other"]
    size: int | None
    modified_at_unix: float | None
    preview_kind: PreviewKind
    language: str | None
    mime_type: str | None


class WorkspaceEntriesResponse(BaseModel):
    """The direct children of one directory, directories first; ``truncated`` says the listing was cut at the entry limit."""

    path: str
    entries: list[WorkspaceEntry]
    truncated: bool


class WorkspaceStatResponse(BaseModel):
    """One entry's metadata and its revision identity (the ETag the byte routes also send)."""

    entry: WorkspaceEntry
    etag: str


class WorkspaceTextResponse(BaseModel):
    """The first bytes of one regular file as text. ``truncated`` means only the initial portion up to the preview limit is here; ``size`` is the whole file."""

    path: str
    text: str
    size: int
    returned_bytes: int
    truncated: bool
    language: str | None
    encoding: str
    etag: str
