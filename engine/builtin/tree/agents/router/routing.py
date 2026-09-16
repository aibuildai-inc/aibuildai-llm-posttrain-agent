"""The Router policy, decisions, and model lookup for one run."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from engine.capability import ExecutionCapability
from engine.builtin.tree.agents.router.agent import RouterAgent
from engine.builtin.tree.agents.router.io import (
    RouterExecutionContext,
    RouterInput,
    RouterPriorResult,
)
from engine.setup_artifacts import task_readme
from infra.model_catalog import (
    canonical_key_for,
    endpoint_provider_for_model,
    is_routable_model_id,
)

from engine.builtin.aibuildai.io import task_folder_files
from engine.builtin.aibuildai.search import product_runtime

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.builtin.tree.io import Started
    from engine.composite import Composite
    from engine.builtin.tree.agents.router.io import RouterOutput
    from engine.builtin.aibuildai.search import AIBuildAISearch
    from engine.work_unit.agent.plugins import EnableConfig

logger = logging.getLogger(__name__)


MODEL_ROUTING_SKILL = "aibuildai-builtin:model-routing"
RoutingEntry = tuple[str | None, dict[str, str]]

_MODEL_ID = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1)]


def _named(value: str) -> str:
    if value != value.strip():
        raise ValueError("must not be padded with whitespace")
    return value


_Named = Annotated[_MODEL_ID, AfterValidator(_named)]


class _StaticRouting(BaseModel):
    """One static routing choice: a default model, per-role models, or both."""

    model_config = ConfigDict(extra="forbid")

    default: _Named | None = None
    by_role: dict[_Named, _Named] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _states_a_choice(self) -> "_StaticRouting":
        if self.default is None and not self.by_role:
            raise ValueError("must set default or by_role")
        return self

    def entry(self) -> RoutingEntry:
        return self.default, dict(self.by_role)


class _StaticFamily(BaseModel):
    """One named family of tasks that share a routing choice."""

    model_config = ConfigDict(extra="forbid")

    name: _Named
    member_tasks: list[_Named] = Field(min_length=1)
    routing: _StaticRouting


class _StaticPolicy(BaseModel):
    """The shipped model-routing policy file, as the product reads it."""

    model_config = ConfigDict(extra="forbid")

    tasks: dict[_Named, _StaticRouting]
    families: list[_StaticFamily]

    @model_validator(mode="after")
    def _each_task_belongs_to_one_family(self) -> "_StaticPolicy":
        seen: set[str] = set()
        for family in self.families:
            for task in family.member_tasks:
                if task in seen:
                    raise ValueError(
                        f"task {task!r} belongs to more than one static family"
                    )
                seen.add(task)
        return self

    def by_task(self) -> tuple[dict[str, RoutingEntry], dict[str, RoutingEntry]]:
        """The exact-task choices and the family-inherited ones."""
        return (
            {name: routing.entry() for name, routing in self.tasks.items()},
            {
                task: family.routing.entry()
                for family in self.families
                for task in family.member_tasks
            },
        )


def routing_decision_problem(
    decision: dict[str, str],
    routed_roles: tuple[str, ...],
    *,
    allowed_models: "list[str] | None",
    provider: str,
) -> str | None:
    """Explain why a routing choice cannot be used, or return ``None``."""
    extra = sorted(set(decision) - set(routed_roles))
    if extra:
        return "model was selected for role(s) this run does not route: " + ", ".join(
            extra
        )
    missing = [role for role in routed_roles if not decision.get(role)]
    if missing:
        return "no model was selected for role(s): " + ", ".join(missing)
    selected = {role: decision[role] for role in routed_roles}
    invalid = [
        f"{role}={model!r}"
        for role, model in selected.items()
        if canonical_key_for(model) is None or not is_routable_model_id(model)
    ]
    if invalid:
        return "unknown or unroutable model id(s): " + ", ".join(sorted(invalid))
    wrong_provider = [
        f"{role}={model!r}"
        for role, model in selected.items()
        if endpoint_provider_for_model(model) != provider
    ]
    if wrong_provider:
        return "model id(s) use another provider: " + ", ".join(sorted(wrong_provider))
    if allowed_models is not None:
        allowed = set(allowed_models)
        outside = [
            f"{role}={model!r}"
            for role, model in selected.items()
            if model not in allowed
        ]
        if outside:
            return (
                f"model id(s) are outside llm.auto.allowed_models={allowed_models!r}: "
                + ", ".join(sorted(outside))
            )
    return None


def validate_router_models(
    output: "RouterOutput",
    routed_roles: tuple[str, ...],
    *,
    config: "AgentConfig",
) -> dict[str, str]:
    """Convert a verified Router output into its complete model mapping."""
    models = {role: model.strip() for role, model in output.models}
    auto = config.llm.auto
    if auto is None:
        raise AssertionError("Router output validation requires llm.auto")
    problem = routing_decision_problem(
        models,
        routed_roles,
        allowed_models=auto.allowed_models,
        provider=config.llm.endpoint_provider,
    )
    if problem is not None:
        raise AssertionError(f"Router verifier accepted an invalid choice: {problem}")
    return {role: models[role] for role in routed_roles}


class Router:
    """Own the Router policy and every model decision for one run."""

    def __init__(
        self,
        skill_dir: Path,
        *,
        config: "AgentConfig",
        per_execution: bool,
        router_capability: "ExecutionCapability",
    ):
        """Freeze at startup everything a routing decision reads.

        ``config`` is the run's configuration as it stood when the Router was
        constructed, kept here on purpose: a routing decision then reads the
        object it was built from and never asks the running root for one."""
        policy_path = Path(skill_dir) / "references" / "static-policy.json"
        if not policy_path.is_file():
            raise FileNotFoundError(
                f"model-routing skill has no static policy: {policy_path}"
            )
        policy = _StaticPolicy.model_validate_json(
            policy_path.read_text(encoding="utf-8")
        )
        self._tasks, self._family_tasks = policy.by_task()
        self._per_execution = per_execution
        self._config = config
        self._router_capability = router_capability
        self._run_models: dict[str, str] = {}
        self._owner_models: dict[str, dict[str, str]] = {}

    @classmethod
    def prepare(
        cls,
        config: "AgentConfig",
        *,
        enable: "EnableConfig",
        router_capability: ExecutionCapability,
    ) -> "Router | None":
        """Load Router policy and check a static choice before run creation."""
        auto = config.llm.auto
        if auto is None:
            return None
        if auto.roles == "all":
            raise AssertionError("llm.auto.roles was not resolved at startup")
        routed_roles = tuple(auto.roles)
        visible = enable.skills_for_role(RouterAgent.name)
        if MODEL_ROUTING_SKILL not in visible:
            raise AssertionError(
                f"Router is missing its built-in skill {MODEL_ROUTING_SKILL!r}"
            )
        plugin_name, skill_name = MODEL_ROUTING_SKILL.split(":", 1)
        plugin_paths = [
            path for path, name in enable.plugin_names.items() if name == plugin_name
        ]
        if len(plugin_paths) != 1:
            raise AssertionError(
                f"expected one {plugin_name!r} plugin, found {plugin_paths!r}"
            )
        router = cls(
            plugin_paths[0] / "skills" / skill_name,
            config=config,
            per_execution=auto.per_execution,
            router_capability=router_capability,
        )
        decision = router._static_choice(routed_roles)
        if decision is not None:
            router._run_models = decision
        return router

    def _static_models(
        self,
        *,
        task_name: str,
        routed_roles: tuple[str, ...],
    ) -> dict[str, str]:
        """Return the exact task or named-family choice for this run."""
        if not task_name:
            return {}
        entry = self._tasks.get(task_name)
        if entry is None:
            entry = self._family_tasks.get(task_name)
        if entry is None:
            return {}
        default, by_role = entry
        out: dict[str, str] = {}
        for role in routed_roles:
            model = by_role.get(role, default)
            if model is not None:
                out[role] = model
        return out

    def _request(
        self, *, routed_roles: tuple[str, ...], execution: "RouterExecutionContext | None"
    ) -> RouterAgent:
        """One Router invocation, run-level or per execution.

        The two differ only by the execution facts they carry, so they are one
        request here rather than two copies that can drift apart."""
        config = self._config
        auto = config.llm.auto
        if auto is None:
            raise AssertionError("Router requires llm.auto")
        return RouterAgent(
            input=RouterInput(
                task_name=config.run.task_name,
                # The Router is granted the raw task folder, never the public
                # view: it runs before SETUP freezes one, and again per design.
                readme=task_readme(
                    config, product_runtime().run_paths, public=False
                ).strip()[:4000],
                provider=config.llm.endpoint_provider,
                mode=auto.mode,
                execution=execution,
                routed_roles=routed_roles,
                allowed_models=None
                if auto.allowed_models is None
                else tuple(auto.allowed_models),
            ),
        )

    def _static_choice(self, routed_roles: tuple[str, ...]) -> "dict[str, str] | None":
        """The shipped static choice for this run, when it can be used at all."""
        config = self._config
        auto = config.llm.auto
        if auto is None or auto.strategy not in ("static", "composite"):
            return None
        decision = self._static_models(
            task_name=config.run.task_name, routed_roles=routed_roles
        )
        problem = routing_decision_problem(
            decision,
            routed_roles,
            allowed_models=auto.allowed_models,
            provider=config.llm.endpoint_provider,
        )
        if problem is None:
            return decision
        if auto.strategy == "static":
            raise ValueError(
                f"llm.auto.strategy='static' cannot route this run: {problem}. "
                "Change the static policy or use composite/llm routing."
            )
        logger.info(
            f"[{RouterAgent.name}] static choice ignored; the Router will choose "
            "every role: %s",
            problem,
        )
        return None

    def model_for(self, *, role: str, owner_path: "str | None") -> "str | None":
        """Return the Router layer's model choice for one Agent."""
        if role == RouterAgent.name:
            return None
        routed: str | None = None
        if self._per_execution and owner_path is not None:
            routed = self._owner_models.get(owner_path, {}).get(role)
        return routed or self._run_models.get(role) or None

    async def decide_run_models(self, search: "AIBuildAISearch") -> None:
        """Choose and keep the run-level role-to-model map.

        ``search`` is the running root Search, whose ``ctx`` starts the Router."""
        config = self._config
        auto = config.llm.auto
        if auto is None:
            raise AssertionError("Router requires llm.auto")
        if auto.roles == "all":
            raise AssertionError("llm.auto.roles was not resolved at startup")
        routed_roles = tuple(auto.roles)
        if self._run_models:
            logger.info(
                f"[{RouterAgent.name}] startup static decision: %s", self._run_models
            )
            return
        if auto.strategy == "static":
            raise AssertionError("static routing passed startup without a decision")
        run_router = self._request(routed_roles=routed_roles, execution=None)
        result = await (
            await search.ctx.spawn(
                run_router.run,
                "Start the assigned work.",
                read=(task_folder_files(),),
                capability=self._router_capability,
            )
        ).result()
        self._run_models = validate_router_models(
            result,
            routed_roles,
            config=config,
        )
        logger.info(f"[{RouterAgent.name}] run-level decision: %s", self._run_models)

    async def decide_models(
        self,
        search: "AIBuildAISearch",
        execution: "Composite",
        *,
        context: "RouterExecutionContext",
    ) -> None:
        """Choose one owner's models from facts supplied by that owner.

        ``search`` is the Search that owns ``execution``."""
        config = self._config
        auto = config.llm.auto
        if auto is None or not self._per_execution:
            return
        if execution.path in self._owner_models:
            return
        if auto.roles == "all":
            raise AssertionError("llm.auto.roles was not resolved at startup")
        routed_roles = tuple(auto.roles)
        try:
            decision = self._static_choice(routed_roles)
        except ValueError as problem:
            raise RuntimeError(
                f"Static Router choice became invalid for {execution.path}: {problem}"
            ) from problem
        if decision is not None:
            self._owner_models[execution.path] = decision
            return

        router = self._request(routed_roles=routed_roles, execution=context)
        # The prior results the Router weighs arrive as its own Input; the
        # Actions that produced them belong to sibling Composites, not to this
        # one, so no upstream edge names them here.
        result = await (
            await execution.ctx.spawn(
                router.run,
                "Start the assigned work.",
                read=(task_folder_files(),),
                capability=self._router_capability,
                capture_failure=True,
            )
        ).result()
        if result.failed is True:
            raise RuntimeError(
                f"Router failed for {execution.path}: {result.reason}"
            )
        decision = validate_router_models(result, routed_roles, config=config)
        if not decision:
            raise RuntimeError(f"Router returned no model decision for {execution.path}")
        self._owner_models[execution.path] = decision


async def route_candidate(
    search: "AIBuildAISearch",
    candidate: "Composite",
    *,
    parents: tuple[str, ...],
    prior: "tuple[Started, ...]",
) -> None:
    """Choose one candidate's models, when this run routes at all.

    How the Search's own records are stated to the Router lives here, with the
    Router that reads them: every prior fact is one the owning Search kept
    when it started that work, and a run with no Router does nothing."""
    router = product_runtime().router
    if router is None:
        return
    await cast("Router", router).decide_models(
        search,
        candidate,
        context=RouterExecutionContext(
            path=candidate.path,
            parent_paths=parents,
            status="pending",
            input=cast(
                "dict[str, JsonValue]", candidate.input.model_dump(mode="json")
            ),
            prior_results=tuple(
                RouterPriorResult(
                    path=started.composite.path,
                    parent_paths=started.parents,
                    status="done" if started.output is not None else "running",
                    metric=started.metric,
                    reviser_output=started.reviser_output,
                )
                for started in prior
                if started.composite.path != candidate.path
            ),
        ),
    )
