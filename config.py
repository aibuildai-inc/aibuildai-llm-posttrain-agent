import decimal
import enum
import os
import textwrap
from pathlib import Path
from typing import Annotated, Any, Literal, Union, cast
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic.config import JsonDict

from engine.base import IGNORED_MODEL_ATTR_TYPES
from infra.model_catalog import endpoint_is_anthropic
from infra.fs import pathing
from infra.model_catalog import (
    canonical_key_for,
    endpoint_provider_for_model,
    is_routable_model_id,
)

_MAX_PATH_COMPONENT_BYTES = 255
# pydantic's own str coercion, borrowed by the hidden-value check below so the
# refusal covers every spelling of a value rather than a table written by hand.
_AS_STR = TypeAdapter(str)


def _non_empty_normalized(value: str) -> str:
    """Boundary form of the shared no-resolve path rule: reject the empty string, then expanduser + absolutize + normpath without following symlinks -- the user's configured path string is echoed back verbatim by every surface, never its symlink target."""
    if not value:
        raise ValueError("path must be non-empty")
    return pathing.normalize_path(value)


_NormalizedPath = Annotated[str, AfterValidator(_non_empty_normalized)]

# config.py is the SINGLE SOURCE OF TRUTH for every default VALUE and every
# per-option doc: each field below carries its literal default (required fields
# carry a ``json_schema_extra`` placeholder), and its ``Field(description=...)``
# is the per-option doc. example.yaml is GENERATED from these models by
# ``startup/example_yaml.py`` (run ``python -m startup.example_yaml``) and
# committed as a generated reference. ``aibuildai config`` GENERATES the text
# at runtime through that module's example_yaml_text().


class _ConfigBaseModel(BaseModel):
    """Common base for every sub-model under :class:`AgentConfig`.

    Setting ``extra="forbid"`` here turns a typo in any leaf key under any YAML sub-block (``search.kidn``, ``run.budget.wall_clock_minutezz``, ``resources.gpu.min_free_mibzz``, ...) into a pydantic ``ValidationError`` at config load — naming the misspelled key — instead of silently dropping the key, falling back to the field default, and burning ~4.5 minutes of API credits before crashing with a misleading downstream error.

    ``AgentConfig`` itself inherits this base, so the same ``extra="forbid"`` policy covers the top-level mapping and every nested block uniformly.

    ``ignored_types`` carries the build-agnostic Cython method type so sub-models that define methods (e.g. ``LlmConfig.profile_for``, the budget helpers) still build when this module ships as a native ``.so`` (see ``engine.base``).
    """

    model_config = ConfigDict(extra="forbid", ignored_types=IGNORED_MODEL_ATTR_TYPES)


class _CpuResourceConfig(_ConfigBaseModel):
    @field_validator("cpu_max_cores", mode="before", check_fields=False)
    @classmethod
    def _validate_cpu_precision(cls, value: Any) -> Any:
        """Reject values the cgroup cpu.max quota cannot keep without rounding.

        ``Any`` is needed because this validator reads raw YAML before Pydantic converts the field to ``float``.
        """
        if value is None:
            return value
        if isinstance(value, bool):
            raise ValueError("cpu_max_cores must be a number, not a boolean")
        try:
            quantity = decimal.Decimal(str(value))
        except decimal.InvalidOperation as exc:
            raise ValueError("cpu_max_cores must be a number") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError(f"cpu_max_cores must be > 0, got {value!r}")
        exponent = quantity.as_tuple().exponent
        if not isinstance(exponent, int):
            raise ValueError(f"cpu_max_cores must be finite, got {value!r}")
        if exponent < -3:
            raise ValueError(
                f"cpu_max_cores supports at most three decimal places, got {value!r}"
            )
        return value


class RunResourceConfig(_CpuResourceConfig):
    """Optional hard limits for the whole run.

    These limits are independent of WorkUnit limits and are never used to derive them. A run limit may sit below the sum of all WorkUnits that may overlap. In that case, the run limit can kill a process before its WorkUnit reaches its own limit. Include the framework and all WorkUnits that may overlap when every WorkUnit limit must be reachable.
    """

    cpu_max_cores: float | None = Field(
        default=None,
        gt=0.0,
        description="optional hard CPU ceiling for the whole run",
    )
    memory_max_gb: int | None = Field(
        default=None,
        gt=0,
        description="optional hard memory ceiling for the whole run, in GiB "
        "(one GB here is 1024^3 bytes)",
    )
    disk_free_floor_gb: int = Field(
        default=50,
        ge=1,
        description="stop admitting new work when the run's filesystem has less "
        "free space than this. It protects the machine -- and, on a "
        "shared host, other people's data. It is admission control, not "
        "a kill: a training run three hours in is not the right thing to "
        "shoot because the disk got tight.",
        json_schema_extra={"advanced": True},
    )


class WorkUnitResourceLimit(_CpuResourceConfig):
    """The complete CPU and memory limits for one WorkUnit."""

    cpu_max_cores: float = Field(
        gt=0.0,
        description="hard CPU ceiling for one WorkUnit, in cores, with at most "
        "three decimal places",
        json_schema_extra={"placeholder": 4},
    )
    memory_max_gb: int = Field(
        gt=0,
        description="hard memory ceiling for one WorkUnit, in GiB "
        "(one GB here is 1024^3 bytes)",
        json_schema_extra={"placeholder": 16},
    )


class WorkUnitResourceOverride(_CpuResourceConfig):
    """A partial resource limit for one ``resources.work_unit.by_kind`` entry."""

    cpu_max_cores: float | None = Field(
        default=None,
        gt=0.0,
        description="CPU override for this WorkUnit kind; unset keeps the base value",
    )
    memory_max_gb: int | None = Field(
        default=None,
        gt=0,
        description="memory override for this WorkUnit kind; unset keeps the base value",
    )


class WorkUnitResourceConfig(WorkUnitResourceLimit):
    """Base limits plus replacements keyed by WorkUnit kind and name.

    The ``llm_agent`` limit covers the model process and all Bash commands it starts together.

    A role's limit is also the ceiling every WorkUnit it starts may declare. Most roles start none, so their limit is about one agent, and this row is that limit. A role that runs a whole search by starting durable work -- ``manager`` -- has the search's resource authority instead of one of its own, so it declares no ceiling here and this row does not reach it.
    """

    by_kind: dict[str, dict[str, WorkUnitResourceOverride]] = Field(
        default_factory=dict,
        description="partial limits keyed first by WorkUnit kind, then by name. "
        "Each set field replaces the base value for that identity. "
        "The llm_agent limit covers its model process and all Bash commands",
        json_schema_extra={
            "example": textwrap.dedent("""\
            by_kind:
              program:
                training: {cpu_max_cores: 8, memory_max_gb: 64}
                score_program: {memory_max_gb: 8}""")
        },
    )

    @field_validator("by_kind")
    @classmethod
    def _validate_kind_names(
        cls, value: dict[str, dict[str, WorkUnitResourceOverride]]
    ) -> dict[str, dict[str, WorkUnitResourceOverride]]:
        invalid = sorted(
            key
            for kind, entries in value.items()
            for key in (kind, *entries)
            if not key or key.strip() != key
        )
        if invalid:
            raise ValueError(
                f"resources.work_unit.by_kind has invalid keys: {invalid!r}"
            )
        return value

    def limit_for(self, kind: str, name: str) -> WorkUnitResourceLimit:
        override = self.by_kind.get(kind, {}).get(name)
        values = {
            "cpu_max_cores": self.cpu_max_cores,
            "memory_max_gb": self.memory_max_gb,
        }
        if override is not None:
            values.update(
                {
                    name: value
                    for name in ("cpu_max_cores", "memory_max_gb")
                    if (value := getattr(override, name)) is not None
                }
            )
        return WorkUnitResourceLimit.model_validate(values)


class GpuConfig(_ConfigBaseModel):
    min_free_mib: int = Field(
        default=8192,
        ge=1,
        description="a GPU must have at least this much free VRAM (MiB) to be used",
    )
    busy_util_threshold: int = Field(
        default=90,
        ge=1,
        le=100,
        description="a GPU above this utilization percent is considered busy and skipped",
    )
    util_samples: int = Field(
        default=1,
        ge=1,
        description="number of fast NVML probes whose MEDIAN utilization "
        "is used (1 = single probe, no debounce; >1 smooths the "
        "soft-sort util signal against second-scale spikes)",
    )


class SandboxConfig(_ConfigBaseModel):
    """The bwrap FILE-ISOLATION layer (opt-in, default off). Much of the fleet blocks unprivileged user namespaces, so sandbox-off is the standard operating mode.

    It governs ONE axis: what the filesystem looks like to a unit. It does NOT govern resources. The kernel bounds every WorkUnit Scope either way -- joining a cgroup needs no namespace and no privilege -- and child WorkUnits and Bash commands stay below their owning WorkUnit's ceiling with this off as with it on. The only thing that changes is the PATH a command sees for its own cgroup (``Confinement.commands_root()``).

    Coupling the two axes is a real defect, and it shipped once: because the commands cgroup reached the model only THROUGH the bwrap bind, turning the sandbox off left the commands tier out of existence entirely and quietly cut every Bash command to the agent session's share, a tenth of what it is owed.

    What sandbox-off genuinely costs: read/write confinement, and with it any defence against a DELIBERATE model -- one that walks /proc/self/cgroup and rewrites a limit, or reads a file it should not. The cgroup bounds accidents; the sandbox is what bounds an adversary."""

    enable: bool = Field(
        default=False,
        description="set true on hosts with working userns to add kernel-level "
        "filesystem read/write confinement. It does not change any "
        "resource limit: the kernel bounds this run either way",
    )
    system_read_paths: tuple[str, ...] = Field(
        default=(),
        description="extra host paths to bind read-only when the sandbox is on, "
        "ADDED to the exact view each launch declares (the operating system's "
        "/usr, /etc, and /run, the interpreter it runs, and the directories "
        "its role names) — a listed path can never drop a declared one. List "
        "only what this host additionally needs, e.g. a toolchain the task uses",
    )
    system_write_paths: tuple[str, ...] = Field(
        default=(),
        description="extra host paths to bind read-write when the sandbox is on, "
        "ADDED to the exact view each launch declares (only the role that "
        "builds environments writes the Conda root; the model CLI writes its "
        "own config dir) — a listed path can never drop a declared one. List "
        "only what this host additionally needs, e.g. a credential dir a task "
        "submits with",
    )

    @field_validator("system_read_paths", "system_write_paths")
    @classmethod
    def _validate_system_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                raise ValueError(f"system sandbox path must be absolute: {raw!r}")
            value = os.path.normpath(str(path))
            if value == "/tmp":
                raise ValueError(
                    "host /tmp cannot be a system sandbox path; the sandbox "
                    "uses a private /tmp"
                )
            if value not in normalized:
                normalized.append(value)
        return tuple(normalized)


class ResourcesConfig(_ConfigBaseModel):
    """Absolute WorkUnit limits and optional independent run totals."""

    run: RunResourceConfig = Field(default_factory=RunResourceConfig)
    work_unit: WorkUnitResourceConfig
    gpu: GpuConfig = Field(default_factory=GpuConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    cuda_visible_devices: tuple[int, ...] | None = Field(
        default=None,
        description="the GPUs this run may see, as physical NVIDIA card indices. "
        "Unset = every host GPU. An empty list = a CPU-only run: no card "
        "is claimed and no CUDA-visibility check runs. Set = the whole "
        "run's visible set: the allocator claims only inside it, every "
        "sandboxed unit dev-binds only its cards, and startup "
        "refuses indices NVML does not report. The external "
        "CUDA_VISIBLE_DEVICES env var is refused at startup; this "
        "field is the single control point.",
    )

    @field_validator("cuda_visible_devices")
    @classmethod
    def _validate_cuda_visible_devices(
        cls, value: "tuple[int, ...] | None"
    ) -> "tuple[int, ...] | None":
        if value is None:
            return value
        if any(index < 0 for index in value):
            raise ValueError(
                f"cuda_visible_devices indices must be >= 0 (got {list(value)})"
            )
        if len(set(value)) != len(value):
            raise ValueError(
                f"cuda_visible_devices must not repeat an index (got {list(value)})"
            )
        return value


class Effort(str, enum.Enum):
    """Reasoning-effort knob forwarded to the Claude backend as the SDK's ``effort=`` option (-> the bundled binary's ``--effort`` flag).

    Promoted from a free-form string to a closed enum so a typo in YAML (e.g. ``effort: med``) raises a pydantic validation error at config load instead of silently flowing through as an unrecognised value. Subclassing ``str`` keeps the value JSON-safe.

    ``llm.default.effort`` defaults to None (UNSET): when unset, no ``--effort`` flag is sent and the binary's own default applies — so existing runs (Claude) are unchanged. Set it explicitly to steer reasoning depth; for DeepSeek's /anthropic endpoint ``max`` maps to its thinking mode (its docs recommend ``CLAUDE_CODE_EFFORT_LEVEL=max`` on cheaper models). The SDK EffortLevel accepts low/medium/high/xhigh/max; xhigh is Opus-4.7-only and falls back to high elsewhere.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class RunBudgetConfig(_ConfigBaseModel):
    """The run's budget: the whole-pipeline global budget (wall-clock + USD) plus the per-agent governance knobs (cost cap / reminder cadence / finalize threshold). Owned by `run` because a run IS a concern; the budget never forms its own top-level concept."""

    wall_clock_minutes: int = Field(
        default=1440,
        gt=0,
        description=(
            "minutes before the run stops starting new exploration work "
            "(1440 = 24h)\n"
            "work already running and final delivery can continue, so the result "
            "can arrive later"
        ),
    )
    # null is the one spelling of "no limit" across this config. A number here
    # means only what it says, so 0 is not a legal cap: it would read as "spend
    # nothing" and stop the run before its first agent.
    cost_usd: float | None = Field(
        default=None,
        gt=0,
        description=(
            "soft run-level USD admission gate over every exploration agent's "
            "own spend; null = no limit"
        ),
    )
    reminder_interval_divisor: int = Field(
        default=10,
        ge=1,
        description="budget-reminder cadence = budget / this",
        json_schema_extra={"advanced": True},
    )
    finalize_threshold_pct: float = Field(
        default=20.0,
        gt=0.0,
        lt=100.0,
        description=(
            "remaining-budget percent reserved for finishing: an agent below it "
            "switches to FINALIZE tone, and the run below it launches no new work"
        ),
        json_schema_extra={"advanced": True},
    )
    # One Agent identity's whole provider bill across every call it answers,
    # never the work it starts: a child Agent has its own lifetime and its own
    # spend against this same number. One CALL is capped by the capability the
    # caller declared for it, which is a different limit with a different owner.
    per_agent_cost_cap_usd: float | None = Field(
        default=None,
        gt=0,
        description="USD cap on one agent's own spend; null = no limit",
    )

    @property
    def pipeline_budget_label(self) -> str:
        minutes = self.wall_clock_minutes
        if minutes % 60 == 0:
            hours = minutes // 60
            unit = "hour" if hours == 1 else "hours"
            return f"{hours} {unit}"
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit}"


class FixedWorkUnitBudgetConfig(_ConfigBaseModel):
    kind: Literal["fixed"]
    minutes: float = Field(gt=0, description="wall-clock budget in minutes")

    def minutes_for(self, *, expected_minutes: float | None) -> float:
        return self.minutes


class ExpectedWorkUnitBudgetConfig(_ConfigBaseModel):
    kind: Literal["expected"]
    min_minutes: float = Field(gt=0, description="minimum wall-clock budget")
    max_minutes: float = Field(gt=0, description="maximum wall-clock budget")
    slack_fraction: float = Field(
        ge=0, description="extra fraction above expected duration"
    )

    @model_validator(mode="after")
    def _validate_range(self) -> "ExpectedWorkUnitBudgetConfig":
        if self.max_minutes < self.min_minutes:
            raise ValueError("max_minutes must be >= min_minutes")
        return self

    def minutes_for(self, *, expected_minutes: float | None) -> float:
        if expected_minutes is None or expected_minutes <= 0:
            raise ValueError("expected budget requires a positive expected duration")
        proposed = expected_minutes * (1 + self.slack_fraction)
        return max(self.min_minutes, min(proposed, self.max_minutes))


class PipelineWorkUnitBudgetConfig(_ConfigBaseModel):
    kind: Literal["pipeline"]


WorkUnitBudgetConfig = Union[
    FixedWorkUnitBudgetConfig,
    ExpectedWorkUnitBudgetConfig,
    PipelineWorkUnitBudgetConfig,
]


class WorkUnitTimeConfig(_ConfigBaseModel):
    """One WorkUnit identity's exact time calculation."""

    kind: str = Field(min_length=1, description="persisted WorkUnit kind")
    name: str = Field(min_length=1, description="persisted WorkUnit business name")
    budget: WorkUnitBudgetConfig = Field(discriminator="kind")
    retry: int = Field(
        default=0,
        ge=0,
        description="how many extra runs a unit of this type gets after a "
        "terminal failure (0 = one run only; 2 = up to Run_3). A retry re-runs "
        "the SAME unit on the SAME input, with a fresh budget and a fresh "
        "directory; nothing about the failure is passed to it.",
    )

    @property
    def identity(self) -> tuple[str, str]:
        return self.kind, self.name

    def cap_seconds(
        self,
        *,
        expected_minutes: float | None,
        pipeline_minutes: int,
    ) -> float:
        if self.budget.kind == "pipeline":
            return pipeline_minutes * 60.0
        return self.budget.minutes_for(expected_minutes=expected_minutes) * 60.0

    @property
    def uses_expected_duration(self) -> bool:
        return self.budget.kind == "expected"

    def maximum_seconds(self, *, pipeline_minutes: int) -> float:
        if self.budget.kind == "pipeline":
            return pipeline_minutes * 60.0
        if self.budget.kind == "fixed":
            return self.budget.minutes * 60.0
        return self.budget.max_minutes * 60.0


class WorkUnitTimeConfigError(ValueError):
    """A WorkUnit has no exact user-configured time calculation."""


class StartupUserInputError(RuntimeError):
    """Base for a startup failure caused by USER INPUT -- a bad config value -- rather than an internal aibuildai bug.

    The ``cli`` composition root catches this whole class BEFORE its generic pre-run crash handler and reports it like a config error: a short actionable message on stderr naming the user's mistake + a clean exit code (2), never a raw traceback nor the "bug in aibuildai" banner. Each subclass is raised at the startup point that detects it (``UnknownSearchKindError`` in ``engine.builtin``) and stays fail-loud -- never a silent skip. Kept distinct from ``WorkUnitTimeConfigError`` above, which is a ``ValueError`` already surfaced by the cli's config-validation boundary."""


class RunConfig(_ConfigBaseModel):
    """Inputs and the manager-generated identity for one run."""

    run_id: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{32}$",
        description="global run id generated by aibuildai when the run starts",
        json_schema_extra={"generated": True},
    )
    kubernetes_start_mode: bool | None = Field(
        default=None,
        description="whether this run started inside a Kubernetes Pod",
        json_schema_extra={"generated": True},
    )
    task_name: str = Field(
        json_schema_extra={"placeholder": "my-task"},
        description="short task id (names the output dir)",
    )
    conda_env: str | None = Field(
        default=None,
        description=(
            "optional host conda environment cloned into this run's Program "
            "environment before Setup; cloning needs conda initialized in the "
            "launching shell. A missing base creates a fresh Python 3.11 "
            "environment with the product's own tooling, no conda needed; unset "
            "means look for an environment named by run.task_name"
        ),
    )
    data_root: _NormalizedPath = Field(
        json_schema_extra={"placeholder": "/path/to/task-folder"},
        description=(
            "the user's task folder: the whole run input. It holds what the "
            "task asks for in any readable form plus every material the task "
            "needs. Setup reads it, freezes it as the run's README, and writes "
            "the run's score program; it is read-only for the whole run"
        ),
    )
    setup_from_scratch: bool = Field(
        default=False,
        description=(
            "when true, SETUP prepares the task itself: the task statement comes "
            "from run.task_prompt and the task folder ships no data, so SETUP "
            "follows its llm.system_instructions to obtain the data + evaluation "
            "spec, carve a held-out answer key, and author the score program + "
            "README from scratch. Default false: the task folder holds the "
            "statement and the data, and SETUP only wraps them"
        ),
    )
    task_prompt: str | None = Field(
        default=None,
        description=(
            "the task statement for a setup_from_scratch run: what to solve, in "
            "the user's own words (e.g. the competition to enter). SETUP copies it "
            "verbatim into the run's README, so every later role reads exactly "
            "this text. REQUIRED when run.setup_from_scratch is true and REJECTED "
            "when it is false -- with setup_from_scratch false the statement lives "
            "in the run.data_root task folder instead"
        ),
    )
    playground_root: _NormalizedPath = Field(
        json_schema_extra={"placeholder": "/path/to/playground"},
        description="writable run-output root",
    )
    budget: RunBudgetConfig = Field(default_factory=RunBudgetConfig)

    @field_validator("task_name")
    @classmethod
    def _validate_task_name(cls, value: str) -> str:
        """task_name names one output subdirectory, never a path.

        Reject values that cannot materialize as one filename component so failures surface at the config boundary. Output relocation uses playground_root or data_root instead.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("run.task_name must name a non-empty output subdirectory.")
        if (
            stripped in (".", "..")
            or "/" in value
            or "\\" in value
            or "\0" in value
            or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        ):
            raise ValueError(
                f"run.task_name must name one output subdirectory, not a path: "
                f"{value!r} is not a usable filename component. Relocate output via "
                f"playground_root or data_root instead."
            )
        if len(value.encode("utf-8")) > _MAX_PATH_COMPONENT_BYTES:
            raise ValueError(
                f"run.task_name must fit within {_MAX_PATH_COMPONENT_BYTES} UTF-8 "
                f"bytes as one output-directory name: {value!r} is too long."
            )
        return value

    def require_run_id(self) -> str:
        if self.run_id is None:
            raise AssertionError("the final run config has no run.run_id")
        return self.run_id

    def require_kubernetes_start_mode(self) -> bool:
        if self.kubernetes_start_mode is None:
            raise AssertionError(
                "the final run config has no run.kubernetes_start_mode"
            )
        return self.kubernetes_start_mode

    @field_validator("conda_env")
    @classmethod
    def _validate_conda_env(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "run.conda_env must be a non-empty conda env name when set."
            )
        if stripped in (".", "..") or "/" in stripped or "\\" in stripped:
            raise ValueError(
                f"run.conda_env must be a conda env name, not a path: {value!r}"
            )
        return stripped

    @property
    def program_environment_base(self) -> str:
        """Return the named environment cloned before Setup, or the task name."""
        return self.conda_env or self.task_name

    @model_validator(mode="after")
    def _validate_task_statement_source(self) -> "RunConfig":
        """Exactly ONE source states the task, and the mode picks which.

        ``run.instruction`` was deleted because a statement field and a task folder could BOTH describe the task, leaving no answer to which one the run optimizes. This keeps that property while letting a from-scratch run state its task in the config: the mode selects the source, so the two can never disagree and there is nothing to reconcile.
        """
        if self.setup_from_scratch and not (self.task_prompt or "").strip():
            raise ValueError(
                "run.task_prompt is required when run.setup_from_scratch is true: "
                "it IS the task statement, because the task folder ships none in "
                "that mode."
            )
        if not self.setup_from_scratch and self.task_prompt is not None:
            raise ValueError(
                "run.task_prompt is only read when run.setup_from_scratch is true. "
                "With setup_from_scratch false the task statement lives in the "
                "run.data_root task folder; drop run.task_prompt or turn the mode on."
            )
        return self


# Server-side web tools: the API endpoint runs the search/fetch, not the model.
_SERVER_SIDE_WEB_TOOLS: frozenset[str] = frozenset({"WebSearch", "WebFetch"})

# Endpoint hosts known to implement them, for an endpoint the operator points at
# with AIBUILDAI_BASE_URL. Anthropic: native. DeepSeek's official
# Anthropic-compat endpoint: its compatibility table lists web_search_tool_result
# and server tool use as supported
# (https://api-docs.deepseek.com/guides/anthropic_api).
_WEB_TOOL_CAPABLE_HOSTS: tuple[str, ...] = ("anthropic.com", "deepseek.com")


class AutoRouting(_ConfigBaseModel):
    """The router how-knobs under ``llm.auto``. Its mere presence opts a run into the subagent router; the fields steer HOW it decides."""

    strategy: Literal["static", "llm", "composite"] = Field(
        default="llm",
        description="which strategy the router consults: static = built-in skill policy "
        "lookup; llm = one Claude call; composite = static first, llm "
        "as fallback.",
    )
    mode: Literal["stable", "aggressive"] = Field(
        default="stable",
        description="stable = exploit (pick the cheapest proven-good model); "
        "aggressive = explore (try cheaper untested models).",
    )
    per_execution: bool = Field(
        default=True,
        description="re-decide routing per execution (else one run-level decision)",
    )
    roles: Literal["all"] | list[str] = Field(
        default="all",
        description="the Agent roles whose models the router chooses. all = every "
        "active Agent except the router; a non-empty list selects an exact subset. "
        "Startup writes the resolved, name-sorted list into the final config.",
        json_schema_extra={"example": ["designer", "coder", "reviser"]},
    )
    allowed_models: list[str] | None = Field(
        default=None,
        description="the model ids the router may choose from. null = "
        "no allowed-model restriction (the router picks any known "
        "model; the Router Output's own validation still enforces "
        "full per-role coverage and rejects unknown ids). A non-empty list restricts "
        "the router to exactly those ids (rendered into the router prompt "
        "as its menu, and enforced by that same Output validation). Every entry must be a "
        "known model id on the same endpoint as default.model; `default` "
        "itself need not appear (the router runs on it and is not routed).",
        json_schema_extra={
            "example": textwrap.dedent("""\
            allowed_models:
              - claude-haiku-4-5
              - claude-sonnet-5
              - claude-opus-4-8""")
        },
    )

    @field_validator("roles")
    @classmethod
    def _validate_roles(
        cls, value: Literal["all"] | list[str]
    ) -> Literal["all"] | list[str]:
        if value == "all":
            return value
        if not value or "" in value or len(set(value)) != len(value):
            raise ValueError(
                "llm.auto.roles must be all or a non-empty list of unique, "
                "non-empty Agent roles"
            )
        return value

    @field_validator("allowed_models")
    @classmethod
    def _validate_allowed_models(cls, value: list[str] | None) -> list[str] | None:
        # null disables the allowed model list; an explicit empty list is a config
        # error (the ambiguity between "no allowed model list" and "an empty allowed model
        # list" is rejected loud rather than silently treated as either).
        if value is None:
            return value
        if not value:
            raise ValueError(
                "llm.auto.allowed_models is an empty list: use null to disable "
                "the allowed model list, or list at least one model id to restrict it."
            )
        unknown = [m for m in value if canonical_key_for(m) is None]
        if unknown:
            raise ValueError(
                f"llm.auto.allowed_models contains unknown model id(s) {unknown}: "
                f"every allowed model must be a model id the catalog knows (pricing / "
                f"context). Check spelling, or refresh the model catalog."
            )
        # An allowed model must ALSO be routable on our endpoints (the shape the
        # Router Output validation accepts and validate_router_models keeps). A
        # catalog-known but unroutable id (e.g. an embedding model, or an
        # old-format dated claude id) would otherwise pass that validation yet be
        # silently dropped downstream and degraded to the default model. Reject it
        # up front with a clear error instead.
        unroutable = [
            m
            for m in value
            if canonical_key_for(m) is not None and not is_routable_model_id(m)
        ]
        if unroutable:
            raise ValueError(
                f"llm.auto.allowed_models contains model id(s) {unroutable} the "
                f"catalog knows but the router cannot route: each allowed model must be a "
                f"claude-* / bare deepseek-* / bare gpt-* / provider/model id on the same endpoint "
                f"as default.model."
            )
        return value


class _ModelProfileBase(_ConfigBaseModel):
    """Shared base of the two profile shapes: it carries the bare-string sugar (``X`` -> ``{model: X}``) both accept. ``RoleModelProfile`` is the COMPLETE profile (``model`` required); ``RoleModelProfileOverride`` is the PARTIAL one (every field optional, unset fields cascade). They are one concept — "which model a role runs on plus how it reasons" — in two optionalities."""

    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_string(cls, raw: Any) -> Any:
        # `coder: claude-opus` / `default: claude-haiku` -> {model: <id>}.
        return {"model": raw} if isinstance(raw, str) else raw


class RoleModelProfile(_ModelProfileBase):
    """A role's COMPLETE reasoning profile: which MODEL it runs on plus the model-coupled reasoning knobs (``effort`` / ``max_thinking_tokens``). You never pick a model without owning its reasoning depth, so both live in one profile. ``llm.default`` is such a profile; ``llm.by_role`` entries are partial :class:`RoleModelProfileOverride` profiles that cascade onto it."""

    model: str = Field(
        json_schema_extra={"placeholder": "claude-haiku-4-5-20251001"},
        description="model id this role runs on (bare deepseek-* targets DeepSeek; "
        "bare gpt-* targets OpenAI; provider/model = OpenRouter; else "
        "Anthropic)",
    )
    effort: Effort | None = Field(
        default=None,
        description="reasoning-effort knob. null = unset (no --effort flag, the "
        "binary default applies); set low/medium/high/xhigh/max to "
        "steer reasoning depth.",
    )
    max_thinking_tokens: int | None = Field(
        default=None,
        ge=0,
        description="null lets the model choose thinking depth, guided by effort; "
        "0 disables extended thinking; a positive value sets the budget",
    )

    @property
    def effort_str(self) -> "str | None":
        """``effort`` as the plain SDK EffortLevel string, or None when unset — the form the backend forwards as ``--effort`` (None sends no flag)."""
        return self.effort.value if self.effort is not None else None


class RoleModelProfileOverride(_ModelProfileBase):
    """A PARTIAL :class:`RoleModelProfile` for one ``llm.by_role`` entry: any subset of its fields. A field left unset (null) cascades from the layer below (the router's model pick, then ``llm.default``); a set field overrides it."""

    model: str | None = Field(
        default=None,
        description="override the role's model id (unset = cascade from default / router)",
    )
    effort: Effort | None = Field(
        default=None,
        description="override the role's reasoning effort (unset = cascade from default)",
    )
    max_thinking_tokens: int | None = Field(
        default=None,
        ge=0,
        description="override the role's thinking budget (unset = cascade from default)",
    )


class LlmConfig(_ConfigBaseModel):
    """Per-role LLM reasoning profiles: which model each role runs on and how it reasons. ``default`` is the base profile (the router itself runs on it, and every role cascades its unset fields from it); ``by_role`` are per-role overrides; ``auto`` opts into the subagent router. A bare string ``llm: X`` is sugar for ``{default: {model: X}}``.

    ``profile_for(role)`` resolves a role's complete profile with precedence User(``by_role``) > Router(``auto``) > ``default``, cascading field by field."""

    default: RoleModelProfile = Field(
        description="the base reasoning profile: model (required) plus reasoning "
        "knobs. The router itself runs on this model, and every role "
        "cascades its unset fields from here.",
    )
    by_role: dict[str, RoleModelProfileOverride] = Field(
        default_factory=dict,
        description="per-role profile overrides, keyed by role ('coder', "
        "'designer', ...). Each entry is a PARTIAL profile (any subset "
        "of model/effort/max_thinking_tokens); unset fields cascade "
        "from `default` (and the router's model pick). Empty = every "
        "role uses `default`. Coexists with `auto` as the "
        "highest-precedence layer.",
        json_schema_extra={
            "example": textwrap.dedent("""\
            by_role:
              designer: {model: claude-opus-4-8, effort: high}
              coder: {model: claude-sonnet-5}
              reviser: {effort: medium}""")
        },
    )
    auto: AutoRouting | None = Field(
        default=None,
        description="opt into the subagent Router with its built-in model-routing skill. "
        "Coexists with `by_role`: the router decides each role's MODEL, "
        "then a `by_role` override wins field-by-field "
        "(User > Router > default).",
        json_schema_extra={
            "example": textwrap.dedent("""\
            auto:
              strategy: llm
              mode: stable
              per_execution: true
              roles: all          # or an exact list such as [designer, coder]
              allowed_models:    # null = unrestricted; a list restricts the menu
                - claude-haiku-4-5
                - claude-sonnet-5
                - claude-opus-4-8""")
        },
    )
    retry_ceiling_s: float | None = Field(
        default=None,
        gt=0,
        description="seconds of provider-outage patience before a paused run stops; unset waits forever",
    )
    system_instructions: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "extra run instructions keyed by agent role; each role receives only "
            "the text under its own name"
        ),
        json_schema_extra={
            "example": textwrap.dedent("""\
            setup: |
              Install every package the task needs.
            designer: |
              Use a pretrained model when it fits the task.
            worker: |
              Load the planned pretrained weights.""")
        },
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_string(cls, raw: Any) -> Any:
        # The one-liner sugar: `llm: claude-haiku` -> {default: {model: claude-haiku}}.
        if isinstance(raw, str):
            return {"default": raw}
        return raw

    def profile_for(
        self, role: str, *, routed_model: "str | None" = None
    ) -> RoleModelProfile:
        """The role's COMPLETE reasoning profile, precedence User > Router > Default.

        The layers, each overriding the previous field by field:
          3. ``default`` — the base profile (lowest).
          2. ``routed_model`` — the subagent router's per-role MODEL pick for this role (a runtime decision the caller resolves from Router; the router decides only the model, never effort/thinking). None = the router had no opinion (or is off).
          1. ``by_role[role]`` — the user's explicit per-role override (highest): every field it sets wins, unset fields cascade down.

        The router role always resolves to ``default`` (the run's base model) — the router never routes itself."""
        # This module cannot import an Agent class, so the name is written out.
        if role == "router":
            return self.default
        profile = self.default
        if routed_model is not None:
            profile = profile.model_copy(update={"model": routed_model})
        override = self.by_role.get(role)
        if override is not None:
            # Field-level cascade: every field the override sets to a concrete
            # (non-null) value wins; a field it leaves unset keeps the base value.
            updates = {
                name: value
                for name in ("model", "effort", "max_thinking_tokens")
                if (value := getattr(override, name)) is not None
            }
            profile = profile.model_copy(update=updates)
        return profile

    @property
    def router_on(self) -> bool:
        """True iff this run hands per-role model selection to the subagent router."""
        return self.auto is not None

    @property
    def endpoint_provider(
        self,
    ) -> "Literal['anthropic', 'openrouter', 'deepseek', 'openai']":
        """The provider/endpoint this run's models target, derived from ``default.model`` (the validator below guarantees every resolvable model agrees)."""
        return endpoint_provider_for_model(self.default.model)

    @model_validator(mode="after")
    def _assert_single_endpoint(self) -> "LlmConfig":
        """Single-endpoint invariant.

        A run talks to ONE endpoint (ANTHROPIC_BASE_URL is process-global), so every RESOLVABLE model id must map to the SAME provider: ``default.model``, every ``by_role[*].model`` override that pins one, AND every ``auto.allowed_models`` entry the router may pick. A yaml mixing e.g. a ``deepseek-*`` default with a ``claude-*`` role override (or an off-endpoint candidate) would otherwise 4xx-storm at the backend; we fail loud here.
        """
        role_models = [ov.model for ov in self.by_role.values() if ov.model is not None]
        allowed_models = self.auto.allowed_models if self.auto is not None else None
        providers = (
            {endpoint_provider_for_model(self.default.model)}
            | {endpoint_provider_for_model(m) for m in role_models}
            | {endpoint_provider_for_model(m) for m in (allowed_models or [])}
        )
        if len(providers) > 1:
            raise ValueError(
                f"llm models span multiple providers/endpoints {sorted(providers)}: "
                f"default={self.default.model!r}, by_role models={role_models!r}, "
                f"auto.allowed_models={allowed_models!r}. "
                "A run uses ONE endpoint (ANTHROPIC_BASE_URL); use models from a "
                "single provider (all claude-*, all bare deepseek-*, or all "
                "bare gpt-*, or all provider/model OpenRouter slugs)."
            )
        return self

    @model_validator(mode="after")
    def _assert_openai_models_are_catalog_known(self) -> "LlmConfig":
        """OpenAI ``default`` / ``by_role`` model ids must be catalog-known.

        An unknown bare ``gpt-*`` default would otherwise slip through config load and only surface as a failed request. Reject it here instead, scoped to the openai provider so claude short aliases (``sonnet``/``opus``, catalog-unknown by design) and deepseek stay untouched. ``auto.allowed_models`` are already checked by ``AutoRouting._validate_allowed_models``; this covers ``default`` + ``by_role``.
        """
        role_models = [ov.model for ov in self.by_role.values() if ov.model is not None]
        bad = [
            m
            for m in (self.default.model, *role_models)
            if endpoint_provider_for_model(m) == "openai"
            and (canonical_key_for(m) is None or not is_routable_model_id(m))
        ]
        if bad:
            raise ValueError(
                f"OpenAI model id(s) {sorted(set(bad))} are not in the model catalog "
                f"(unknown context/pricing) or are not routable. Every gpt-* model in "
                f"llm.default / llm.by_role must be a catalog-known, routable id. Check "
                f"spelling, or refresh the model catalog."
            )
        return self


class SearchConfig(_ConfigBaseModel):
    """Root Search selection and its business Input.

    ``kind`` selects the registered root Search class. ``input`` is validated directly against that class's own declared Input model -- that model is the sole authority for which parameters are accepted, their types, defaults, and validation; an unknown field fails at startup as ``search.input.<field>``. The canonical validated value becomes the root Search's frozen journaled Input."""

    kind: str = Field(
        default="tree",
        description="search method: how the pipeline explores the design space. "
        "Startup selects the registered Search type by this name; an "
        "unavailable name fails loud during startup.",
        json_schema_extra={"advanced": True},
    )
    input: dict[str, object] = Field(
        default_factory=dict,
        description="the selected Search's own business parameters, validated "
        "directly against that Search class's Input model (the sole authority "
        "for names, types, defaults, and validation). Each kind documents its "
        "own fields below; an unknown field fails at startup. The starter "
        "config derives this block's live value from the default kind's own "
        "Input model.",
    )


class SchedulerConfig(_ConfigBaseModel):
    """Scheduler host re-check cadence.

    Work waiting at the disk free-space floor wakes when another execution ends. ``recheck_seconds`` is the backup wake for a host change that happened outside this run."""

    recheck_seconds: int = Field(
        default=1800, ge=1, description="host re-check cadence (seconds)"
    )


class VerifierConfig(_ConfigBaseModel):
    """Per-role semantic verification (off by default).

    It decides only whether a role's semantic verifier is part of the run at
    all. Once a verifier is in a role's chain its VERDICT is authoritative and
    a negative one rejects the candidate, but a verifier that FAILED gave no
    verdict: that is logged and the chain goes on, so it neither rejects the
    candidate nor becomes the failure of the role that ran it. The
    deterministic checks a role runs for itself are its own acceptance
    contract and are never switchable from here."""

    enable: dict[str, bool] = Field(
        default_factory=dict,
        description="per-role semantic review. Set a role to true to include "
        "the reviewer that role composes; false or omission leaves it out. "
        "Empty = all off. An unknown role name fails at startup against the "
        "roles the configured Search actually composes a reviewer for, which "
        "the roles themselves declare -- so no list here can go stale against "
        "the build.",
        json_schema_extra={
            "example": textwrap.dedent("""\
            enable:
              coder: true
              designer: true""")
        },
    )


class SubmissionConfig(_ConfigBaseModel):
    """The run's budget for an EXTERNAL scoring oracle.

    Some tasks are judged by a service the run does not own — a competition leaderboard, a held-out grader behind an API — and that service is rate limited, so its score is scarce in a way the local score program is not. With no budget the run can only spend it the way the Finalizer does: once, at the very end, on the result selected from the local score.

    ``max_versions`` is that budget. Above 1 it activates the SUBMITTER, which spends the budget across the candidates the search produced while the Finalizer keeps ownership of the run's deliverable.

    The number bounds THIS RUN only. It is not a claim about Kaggle's own quota, which resets on its own clock and is shared with anything else using the account."""

    max_versions: int = Field(
        default=1,
        ge=1,
        description=(
            "notebook versions this run may push. One push is one version, and "
            "a version is what the external service scores. 1 (the default) "
            "leaves the run exactly as it is: the FINALIZER delivers, and any "
            "submitting it does comes from its own instructions. Above 1 "
            "activates the SUBMITTER, which spends up to this many versions "
            "on the candidates the search produced while the FINALIZER still "
            "delivers the selected result"
        ),
    )

    share_external_scores: bool = Field(
        default=False,
        description=(
            "feed external scores BACK into the search as advisory context. This "
            "does NOT control whether they are recorded: whenever the SUBMITTER "
            "runs it always writes every result it gets to "
            "external_scores.jsonl in its own run-home records dir, because that "
            "costs nothing and is the run's only durable record of what the "
            "external service returned. This flag decides only who READS it. "
            "False (the default) keeps information one-way: the SUBMITTER reads "
            "local scores to rank what to send and nothing it learns reaches the "
            "search. True lets the roles that draft the next candidates (the NB "
            "WORKER and the REVISER) also see which candidates the external oracle "
            "scored and how. It never affects RANKING -- the winner is still "
            "chosen by the local score program alone -- it only tells those two "
            "roles where the local proxy and the real oracle disagreed. Requires "
            "max_versions above 1, since with no SUBMITTER there are no "
            "external scores to share"
        ),
        json_schema_extra={"advanced": True},
    )

    @model_validator(mode="after")
    def _validate_sharing_needs_a_submitter(self) -> "SubmissionConfig":
        # Sharing reads what the SUBMITTER records. With max_versions at 1 that
        # role never runs, so the flag would silently do nothing -- and a silent
        # no-op is the shape that makes someone conclude the feature is broken.
        if self.share_external_scores and self.max_versions <= 1:
            raise ValueError(
                "submission.share_external_scores needs "
                "submission.max_versions above 1: the scores it shares are "
                "recorded by the SUBMITTER, which only runs above 1."
            )
        return self


class ProgressConfig(_ConfigBaseModel):
    """Prompt built-in solving roles to record metrics — on by default.

    Every materialized Agent and Program can import the same wandb-shaped ``aibuildai`` recorder. When this setting is on, the built-in coder and worker prompts introduce ``init`` / ``log`` / ``finish`` and each logged metric key becomes one live Web Workspace curve. The recorder appends to that WorkUnit attempt's ``metrics.jsonl``. Each ``init()`` opens one series segment, so one file can hold several segments and Web keeps their metric keys separate. Off only removes the prompt instruction; the shared recorder remains available to authored WorkUnits."""

    enable: bool = Field(
        default=True,
        description="introduce the shared aibuildai metric recorder "
        "(init / log / finish) in the built-in coder and worker prompts. Each "
        "logged metric key becomes one live Web Workspace curve in that WorkUnit "
        "attempt's metrics.jsonl. Each init call opens one separate segment. "
        "Off removes only the prompt instruction; every materialized Agent and "
        "Program can still import and use the recorder.",
    )


class WriterConfig(_ConfigBaseModel):
    """Writer paper settings. ``writer.enable`` controls the final paper after AGGREGATE. The Writer compiles each one itself with the tectonic its ``required_pdf`` declaration puts on its PATH. ``verifier.enable['writer']`` controls the optional faithfulness check."""

    enable: bool = Field(
        default=False,
        description="opt in the WRITEUP phase: after aggregation, write a NeurIPS "
        "paper describing the run and compile it to a PDF in its own "
        "artifacts. Off by default; the standalone "
        "`aibuildai write-paper <run-dir>` command runs regardless, and "
        "supplies the Writer's work_units time entry itself when the run "
        "archived none (a switched-off Writer needs no entry).",
    )


class MemoryConfig(_ConfigBaseModel):
    """Memory injected each run, and the operator's own memory dir.

    Named for what it carries. The phrase "knowledge base" belongs to the remote Kb MCP service (`mcps.kb`, base via AIBUILDAI_KB_BASE_URL), which is a different subsystem: it serves skill documents on request, while this one injects a corpus into every agent's system prompt.

    `memorize` reads the runs it summarizes from `run.playground_root`, which already declares where this run's output lives. There is no second field naming that directory here.
    """

    enable: bool = Field(
        default=False,
        description="inject memory into every agent's system prompt; the "
        "default false injects nothing and reads no corpus",
    )
    max_chars: int | None = Field(
        default=30000,
        gt=0,
        description="budget for the built-in memory body; the caution "
        "header, your own notes and the truncation marker sit outside "
        "this budget, so the text that reaches the agent can come back "
        "longer or shorter than it; null = no budget",
    )
    # The DECLARED default stays un-expanded (the generator emits raw declared
    # defaults, so no host path reaches example.yaml); validate_default expands
    # it at construction.
    user_dir: _NormalizedPath = Field(
        default="~/.aibuildai/memory",
        validate_default=True,
        description="user-managed memory dir (read every run)",
    )


def _registry_name(value: str) -> str:
    if not value or value != value.strip() or "\0" in value:
        raise ValueError(
            "registry names must be non-empty strings with no outer whitespace or NUL"
        )
    return value


def _role_list(value: list[str]) -> list[str]:
    roles = [role.strip() for role in value]
    if any(not role for role in roles):
        raise ValueError("role entries must not be blank")
    return roles


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


_RegistryName = Annotated[str, AfterValidator(_registry_name)]
_RoleList = Annotated[list[str], AfterValidator(_role_list)]
_ExtensionRoles = Literal["all"] | _RoleList
_NonBlankString = Annotated[str, AfterValidator(_non_blank)]


class _RoleScopedExtensionConfig(_ConfigBaseModel):
    roles: _ExtensionRoles = "all"


class ReferenceExtensionConfig(_RoleScopedExtensionConfig):
    type: Literal["reference"]


class PathExtensionConfig(_RoleScopedExtensionConfig):
    type: Literal["path"]
    path: _NonBlankString


class StdioMcpConfig(_RoleScopedExtensionConfig):
    type: Literal["stdio"]
    command: _NonBlankString
    args: list[str] | None = None
    env: dict[str, str] | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _reject_process_nul(self) -> "StdioMcpConfig":
        if "\0" in self.command or any("\0" in arg for arg in self.args or ()):
            raise ValueError("command and args must not contain NUL")
        if self.env and any(
            "\0" in key or "=" in key or "\0" in value
            for key, value in self.env.items()
        ):
            raise ValueError(
                "env keys must not contain '=' or NUL, and values must not contain NUL"
            )
        return self


class _RemoteMcpConfig(_RoleScopedExtensionConfig):
    url: _NonBlankString
    headers: dict[str, str] | None = None
    description: str | None = None


class HttpMcpConfig(_RemoteMcpConfig):
    type: Literal["http"]


class SseMcpConfig(_RemoteMcpConfig):
    type: Literal["sse"]


_PathRegistryValue = Annotated[
    ReferenceExtensionConfig | PathExtensionConfig,
    Field(discriminator="type"),
]
_McpRegistryValue = Annotated[
    ReferenceExtensionConfig | StdioMcpConfig | HttpMcpConfig | SseMcpConfig,
    Field(discriminator="type"),
]


# Starter time budgets for the generated example config, keyed by Agent role /
# unit kind. These tables own both the rows and their budgets.
_FIXED_60_MIN = {"kind": "fixed", "minutes": 60}
# A role whose Action owns durable children spends its Local Budget on the
# subtree it starts, so it is budgeted for the run, not for its own turns.
# A role whose starter budget is not the plain hour, by name. Every other role
# a package declares gets the hour below; nothing here lists which roles exist.
_STARTER_BUDGET_BY_ROLE: dict[str, dict[str, object]] = {
}
_FIXED_2_MIN = {"kind": "fixed", "minutes": 2}
_STARTER_BUDGET_BY_UNIT: dict[str, dict[str, object]] = {
    "training": {
        "kind": "expected",
        "min_minutes": 30,
        "max_minutes": 600,
        "slack_fraction": 0.3,
    },
    "score_program": _FIXED_2_MIN,
}


def _work_units_placeholder() -> list[dict[str, object]]:
    """The starter work_units list, written by the packages themselves.

    Every offered Search package declares the roles and Programs it can run, so
    the starter config budgets exactly those and a package that gains or loses
    a role cannot leave a stale row here. A run still budgets only what the
    kind it selected declares; this list exists so the starter config validates
    whichever kind the user then chooses. Rendering the starter config is the
    one operation that reads every offered package.
    """
    from engine.builtin import offered_kinds, search_type_for_kind

    roles: list[str] = []
    units: list[str] = []
    for kind in offered_kinds():
        search_type = search_type_for_kind(kind)
        roles.extend(search_type.declared_role_names())
        units.extend(search_type.declared_unit_names())
    return [
        {
            "kind": "llm_agent",
            "name": role,
            "budget": _STARTER_BUDGET_BY_ROLE.get(role, _FIXED_60_MIN),
            "retry": 0,
        }
        for role in sorted(set(roles))
    ] + [
        {
            "kind": "program",
            "name": unit,
            "budget": _STARTER_BUDGET_BY_UNIT.get(unit, _FIXED_60_MIN),
            "retry": 0,
        }
        for unit in sorted(set(units))
    ]


def endpoint_disallowed_tools(llm: LlmConfig) -> frozenset[str]:
    """Built-in tools this endpoint cannot serve, so no role may hold them.

    Server-side web tools run on the resolved ``ANTHROPIC_BASE_URL``. A generic Anthropic-compatible gateway serves neither WebSearch nor WebFetch.

    Read the resolved endpoint, not the operator's ``AIBUILDAI_BASE_URL`` intent. Call this inside ``model_endpoint``, after that endpoint has been resolved.
    """
    base = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if not base and llm.endpoint_provider != "anthropic":
        raise AssertionError(
            f"endpoint_disallowed_tools reached with provider "
            f"{llm.endpoint_provider!r} and no ANTHROPIC_BASE_URL; "
            "model_endpoint should have rejected that run already"
        )
    if endpoint_is_anthropic(base):
        return frozenset()
    host = (urlparse(base).hostname or "").lower()
    serves_web = any(
        host == known or host.endswith("." + known) for known in _WEB_TOOL_CAPABLE_HOSTS
    )
    return frozenset() if serves_web else _SERVER_SIDE_WEB_TOOLS


class AgentConfig(_ConfigBaseModel):
    # Plain pydantic model (extra="forbid" inherited from _ConfigBaseModel).
    # Config is YAML-only: startup/config_loader.py loads it via
    # AgentConfig.model_validate(<dict from yaml>). There is no CLI/env field
    # source — an un-migrated legacy flat key surfaces as a pydantic
    # "extra inputs are not permitted" error rather than silently defaulting.

    # Required sub-models (no default): run identity (task_name/data_root/
    # playground_root) and the model name must be supplied.
    run: RunConfig
    llm: LlmConfig
    work_units: list[WorkUnitTimeConfig] = Field(
        min_length=1,
        description="one required time calculation for every WorkUnit type used "
        "by the run. `retry` beside a budget says how many extra runs a unit of "
        "that type gets after a terminal failure; 0 is one run only.",
        # The stub narrows json_schema_extra values to JsonValue, but pydantic
        # stores the dict verbatim at runtime; _placeholder() calls this
        # callable at render time.
        json_schema_extra=cast(JsonDict, {"placeholder": _work_units_placeholder}),
    )

    search: SearchConfig
    disallowed_tools: list[str] = Field(
        default_factory=list,
        description="Built-in tool names disabled for EVERY role and their spawned "
        "sub-agents (e.g. ['WebSearch', 'WebFetch']). Empty (the default) "
        "disables nothing. A task derived from a paper may set this to "
        "['WebSearch', 'WebFetch'] so the paper's method cannot be looked up "
        "as a shortcut. A resolved skill, sub-agent, or MCP grant requires "
        "Skill, Agent and Task, or mcp__* respectively; blocking a "
        "required invocation tool is rejected instead of listing an "
        "extension that cannot run.",
    )

    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    verifier: VerifierConfig = Field(default_factory=VerifierConfig)
    progress: ProgressConfig = Field(default_factory=ProgressConfig)
    submission: SubmissionConfig = Field(default_factory=SubmissionConfig)
    writer: WriterConfig = Field(default_factory=WriterConfig)
    resources: ResourcesConfig
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    mcps: dict[_RegistryName, _McpRegistryValue] = Field(
        default_factory=dict,
        description=(
            "MCP server registry. Every entry requires `type`: `reference`, `stdio`, `http`,\n"
            "or `sse`. `roles` is optional and defaults to all active Agent roles. A reference\n"
            "uses the registry key as a product-owned built-in name. A stdio entry requires\n"
            "`command`; http and sse require `url`. ${VAR} is expanded at runtime in\n"
            "command, url, env, and headers. Built-in: `kb`."
        ),
        json_schema_extra={
            "example": textwrap.dedent("""\
            mcps:
              kb: { type: reference }
              my_tool: { type: stdio, command: python, args: [-m, my_tool.server], roles: [coder] }
              my_http: { type: http, url: https://example.test/mcp, headers: { Authorization: "Bearer ${TOKEN}" } }""")
        },
    )
    plugins: dict[_RegistryName, _PathRegistryValue] = Field(
        default_factory=dict,
        description=(
            "Plugin registry. Every entry requires `type: reference` for a shipped plugin or\n"
            "`type: path` with `path` for a user plugin. `roles` is optional and defaults to all\n"
            "active Agent roles. A grant gives those roles every skill and bundled Agent in the\n"
            "plugin. Empty keeps only the implicit core plugin."
        ),
        json_schema_extra={
            "example": textwrap.dedent("""\
            plugins:
              aibuildai-builtin: { type: reference }
              my-team-playbook: { type: path, path: ./plugins/my-team-playbook, roles: [coder] }""")
        },
    )
    agents: dict[_RegistryName, _PathRegistryValue] = Field(
        default_factory=dict,
        description=(
            "Standalone sub-agent registry. Every entry requires `type: reference` for a shipped\n"
            "agent or `type: path` with `path` for a user agent. `roles` is optional and adds to\n"
            "the built-in defaults."
        ),
        json_schema_extra={
            "example": textwrap.dedent("""\
            agents:
              mcp-subagent: { type: reference, roles: [router] }
              my-critic: { type: path, path: ./agents/critic.md, roles: [reviser] }""")
        },
    )
    skills: dict[_RegistryName, _PathRegistryValue] = Field(
        default_factory=dict,
        description=(
            "Per-skill role grants. Every entry requires `type: reference` for a qualified skill\n"
            "from an enabled plugin or `type: path` with `path` for a user skill. `roles` is\n"
            "optional and adds to built-in and plugin grants."
        ),
        json_schema_extra={
            "example": textwrap.dedent("""\
            skills:
              aibuildai-builtin:ensemble: { type: reference, roles: [router] }
              my-own-skill: { type: path, path: ./skills/my-own-skill, roles: [coder] }""")
        },
    )

    @model_validator(mode="after")
    def _reject_duplicate_work_unit_times(self) -> "AgentConfig":
        identities = [entry.identity for entry in self.work_units]
        duplicates = sorted(
            {identity for identity in identities if identities.count(identity) > 1}
        )
        if duplicates:
            raise ValueError(f"duplicate work_units identities: {duplicates!r}")
        return self

    @property
    def output_base_dir(self) -> str:
        return f"{self.run.playground_root}/{self.run.task_name}"

    @property
    def train_file_name(self) -> str:
        return f"{self.run.task_name}-train.py"

    @property
    def task_folder(self) -> str:
        """The user's whole task folder: the run's only data input.

        Setup binds it whole, because it must see the answers to write the score program. No other role binds it: a candidate reads the run's public dir instead, which setup fills itself, so there is no separate "public" subdir convention inside the task folder -- what a candidate may see is a per-run decision setup records, not a layout the user has to follow.
        """
        return self.run.data_root

    @property
    def submitter_on(self) -> bool:
        """Whether the SUBMITTER runs beside the search for this run.

        One derivation, read everywhere: the role's work_units identity, the startup time requirement, and the role dispatch must agree, or a run either demands a time entry for a role it never launches or launches one it never budgeted."""
        return self.submission.max_versions > 1

    def work_unit_time_or_none(self, kind: str, name: str) -> "WorkUnitTimeConfig | None":
        """The configured row for one WorkUnit identity, or None where there is none.

        A package the run selects configures its own units, so "no row" is an
        ordinary answer about them rather than a mistake, and a caller that
        can live without one asks for it here. ``work_unit_time_for`` is the
        same lookup for a caller that cannot live without one: it raises, and
        the cli reports that as the user config error it is. One lookup, two
        callers, and each says which of the two it is at the call."""
        identity = (kind, name)
        for entry in self.work_units:
            if entry.identity == identity:
                return entry
        return None

    def work_unit_time_for(self, kind: str, name: str) -> WorkUnitTimeConfig:
        entry = self.work_unit_time_or_none(kind, name)
        if entry is None:
            raise WorkUnitTimeConfigError(
                f"work_units has no time config for {(kind, name)!r}"
            )
        return entry

    # NOTE: the run's public data dir is per-run, not shared. Its path is
    # run-scoped (it needs the resolved timestamp) and lives on RunPaths
    # (public_dir), NOT here.
