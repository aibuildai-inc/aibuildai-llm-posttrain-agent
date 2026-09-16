"""Build one Agent's AgentSpec: its launch facts and exact Confinement, resolved once."""

from __future__ import annotations

import getpass
import inspect
import json
import os
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.types import Tool
from infra.util.paths import canonical_path, is_within

from config import AgentConfig, RoleModelProfile, endpoint_disallowed_tools
from engine.durable_execution import (
    latest_run_state,
    run_clock,
    run_configs,
    run_program_environment,
    running_product_search,
)
from engine.file_grants import granted_paths
from engine.setup_artifacts import task_readme
from infra.backends.spec import AgentSpec, HookSpec
from infra.backends.hooks import (
    make_block_long_sleep_spec,
    make_block_unsafe_kill_spec,
    make_command_cgroup_reaper_spec,
    make_command_self_enroll_spec,
    make_force_sync_dispatch_spec,
    make_command_timeout_spec,
    make_kaggle_timeout_spec,
    make_protect_bash_operations_spec,
    make_protect_input_files_spec,
    make_restrict_write_directory_spec,
)
from engine.work_unit.agent.policy import RolePolicy, SystemDir
from engine.mcp import tool_result, tool_server
from engine.work_unit.agent.executions_mcp import executions_mcp_server
from engine.work_unit.agent.plugins import (
    MCP_SUBAGENT_NAME,
    SubAgent,
    discover_plugins_and_agents,
)
from engine.work_unit.metric_recorder import install_metric_recorder
from infra.host_resource.confinement import OS_READ_BASELINE, Confinement
from infra.host_resource.sandbox_coverage import assert_paths_covered
from infra.process.conda_env import conda_root
from infra.sandbox.bwrap import device_dev_nodes
from infra.sandbox.gpu_nodes import device_minors, enumerate_nvidia_cap_nodes
from infra.owned_tools import owned
from memory.reader import get_memory_context

if TYPE_CHECKING:
    # Annotation-only. Importing Agent at runtime would close a cycle through
    # AgentSpec assembly.
    from engine.builtin.aibuildai.search import AIBuildAISearch
    from engine.work_unit.agent.base import Agent

# --- Helpers ---------------------------------------------------------------


def _extension_tool_requirements(
    *,
    has_skills: bool,
    has_sub_agents: bool,
    mcp_tools: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    requirements: dict[str, tuple[str, ...]] = {}
    if has_skills:
        requirements["skills"] = ("Skill",)
    if has_sub_agents:
        requirements["agents"] = ("Agent", "Task")
    if mcp_tools:
        requirements["mcps"] = mcp_tools
    return requirements


def validate_extension_tool_conflicts(
    owner: str,
    *,
    has_skills: bool,
    has_sub_agents: bool,
    has_mcp_servers: bool,
    disallowed_tools: Iterable[str],
    disallowed_source: str,
) -> None:
    """Reject an extension when its call tool is denied."""
    requirements = _extension_tool_requirements(
        has_skills=has_skills,
        has_sub_agents=has_sub_agents,
        mcp_tools=("mcp__*",) if has_mcp_servers else (),
    )
    denied = set(disallowed_tools)
    conflicts = {
        registry: tuple(tool for tool in tools if tool in denied)
        for registry, tools in requirements.items()
        if any(tool in denied for tool in tools)
    }
    if not conflicts:
        return
    details = "; ".join(
        f"{registry} require {list(tools)}" for registry, tools in conflicts.items()
    )
    raise ValueError(
        f"{owner}: {disallowed_source} conflicts with resolved extension "
        f"registries: {details}"
    )


def _resolve_tool_names(
    owner: str,
    base_tools: Iterable[str],
    *,
    has_skills: bool,
    has_sub_agents: bool,
    mcp_tools: tuple[str, ...],
    disallowed_tools: Iterable[str],
    disallowed_source: str,
) -> tuple[str, ...]:
    """Build the tools that one AgentSpec can call."""
    validate_extension_tool_conflicts(
        owner,
        has_skills=has_skills,
        has_sub_agents=has_sub_agents,
        has_mcp_servers=bool(mcp_tools),
        disallowed_tools=disallowed_tools,
        disallowed_source=disallowed_source,
    )
    requirements = _extension_tool_requirements(
        has_skills=has_skills,
        has_sub_agents=has_sub_agents,
        mcp_tools=mcp_tools,
    )
    denied = set(disallowed_tools)
    tool_names = [tool for tool in base_tools if tool not in denied]
    for tools in requirements.values():
        for tool in tools:
            if tool not in tool_names and tool not in denied:
                tool_names.append(tool)
    return tuple(tool_names)


def build_sub_agent_specs(
    agent_specs: list[dict],
    parent_tools: Sequence[str],
    configured_mcp_names: Iterable[str],
) -> tuple[SubAgent, ...]:
    """Resolve the parsed sub-agent specs into the sub-agents a role may dispatch. A child reaches the parent's live MCP mounts through its tool grant alone: the built-in MCP sub-agent gets every configured server granted to the role, any other child the servers its frontmatter names, and ``validate_plugin_config`` has already refused a name the role is not granted."""
    configured = set(configured_mcp_names)
    out: list[SubAgent] = []
    for spec in agent_specs:
        name = spec["name"]
        grants = configured if name == MCP_SUBAGENT_NAME else set(spec.get("mcp_servers") or [])
        tools = _resolve_tool_names(
            f"sub-agent {name!r}",
            (tool for tool in parent_tools if tool not in {"Skill", "Agent", "Task"} and not tool.startswith("mcp__")),
            has_skills=False,
            has_sub_agents=False,
            mcp_tools=tuple(f"mcp__{server}__*" for server in sorted(grants)),
            disallowed_tools=spec.get("disallowed_tools") or [],
            disallowed_source="frontmatter disallowedTools",
        )
        out.append(
            SubAgent(
                name=name,
                description=spec.get("description") or name,
                prompt=spec["prompt"],
                tools=tools,
                model=spec.get("model") or None,
            )
        )
    return tuple(out)


# The bundled CLI's auto-background defeat is not applied here:
# the process entry scrubs inherited CLAUDE* feature flags and the Claude
# translation sets the one positive pin. The per-design spec env carries only
# the cache/log/PATH redirects below.


async def _default_env(
    search: "AIBuildAISearch",
    *,
    policy: RolePolicy,
    write_face: str,
    cache_root: str,
) -> dict[str, str]:
    """Build cache, log, temporary-file, and task-Python environment values."""
    env = {
        # Pin the bundled claude binary's reply-size ceiling too, for the same
        # reason. Left unset, the binary picks it from a per-model table it was
        # built with, and a model id newer than that build falls through to a
        # 32000 fallback -- half of what the ids it knows are given. A role
        # whose one required reply is larger than the ceiling then cannot
        # finish at all: every attempt stops at the ceiling and is discarded,
        # so the role burns its whole wall clock and returns nothing.
        # Ask for 128000; the binary clamps this down to whatever the model in
        # use really allows, so one value is right for every model.
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "128000",
    }
    if policy.task_environment:
        program_python = run_program_environment().python
        env["PATH"] = (
            f"{Path(program_python).parent}{os.pathsep}{os.environ.get('PATH', '')}"
        )
    face_cache_root = f"{write_face}/.cache"
    # TMPDIR must stay SHORT. PyTorch DataLoader / multiprocessing bind AF_UNIX
    # sockets under $TMPDIR (e.g. $TMPDIR/pymp-xxxx/listener-xxxx-xxxx) and the
    # kernel caps sun_path at 108 bytes. The run dir is deep (~90 chars), so a
    # TMPDIR under the run cache overflows that limit and every num_workers>0
    # DataLoader dies with "OSError: AF_UNIX path too long" (smoke hangs at step
    # 0 → coder burns its whole budget retrying → design times out). Anchor TMPDIR
    # at a short /tmp path keyed by the run id; these are ephemeral IPC sockets /
    # tempfiles, not run artifacts, so output-containment (the coder's output
    # dir) is unaffected. Everything else still redirects into a .cache subtree.
    # Namespaced by OS user, NOT under playground_root (user-supplied, may be
    # deep enough to overflow the 108-byte sun_path limit): a fixed shared
    # /tmp/aibuildai collides between users on one host (the 2nd user can't
    # mkdir under the 1st user's 0775 dir).
    tmp_root = f"/tmp/aibuildai-{getpass.getuser()}/{Path(cache_root).parent.name}"
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "TMPDIR": tmp_root,
            # Content-addressed, shared per RUN (anchor: <run home>/.cache).
            "XDG_CACHE_HOME": cache_root,
            "HUGGINGFACE_HUB_CACHE": f"{cache_root}/huggingface/hub",
            "HF_DATASETS_CACHE": f"{cache_root}/huggingface/datasets",
            "TRANSFORMERS_CACHE": f"{cache_root}/huggingface/transformers",
            "TORCH_HOME": f"{cache_root}/torch",
            "TRITON_CACHE_DIR": f"{cache_root}/triton",
            "NUMBA_CACHE_DIR": f"{cache_root}/numba",
            # Per-design state / results (anchor: write_face).
            "XDG_DATA_HOME": f"{face_cache_root}/data",
            "XDG_STATE_HOME": f"{face_cache_root}/state",
            "MPLCONFIGDIR": f"{face_cache_root}/matplotlib",
            "WANDB_DIR": write_face,
            "WANDB_CACHE_DIR": f"{face_cache_root}/wandb",
            "MLFLOW_TRACKING_URI": f"file://{write_face}/mlruns",
            "PIP_CACHE_DIR": f"{face_cache_root}/pip",
            "UV_CACHE_DIR": f"{face_cache_root}/uv",
        }
    )
    return env


# --- Factory ---------------------------------------------------------------


def _profile_for_role(
    search: "AIBuildAISearch", role: str, owner_path: str | None
) -> RoleModelProfile:
    """The role's fully-resolved reasoning profile (model + effort + max_thinking_tokens): ``config.llm.profile_for(role)`` with the subagent router's per-role MODEL pick spliced in as the middle (Router) layer of the User > Router > Default precedence.

    The Router decides only the model. A role's effort and thinking values still come from its profile. The Router keeps per-design choices ahead of the one run-level choice without writing into the user's config."""
    # Imported here, not at module scope: importing the engine must never
    # import a Definition package, so the arrow to the product shell exists
    # only while a product run is actually being served.
    from engine.builtin.aibuildai.search import product_runtime
    # Both routing maps are keyed by the same role strings the user writes under
    # llm.by_role, and an Agent's name is what indexes them. A name absent from
    # either map is not routed, so profile_for falls back to default.
    router = product_runtime().router
    routed = (
        None if router is None else router.model_for(role=role, owner_path=owner_path)
    )
    return run_configs().llm.profile_for(role, routed_model=routed)


def tectonic_bin_dir(agent_type: "type[Agent]") -> str | None:
    """Return the PDF producer's owned compiler directory."""
    return (
        str(owned("tectonic").parent) if agent_type.required_pdf is not None else None
    )


async def compute_agent_spec(
    agent: "Agent",
    *,
    search_template: str,
    cgroups: tuple[str, str | None],
    device_indices: tuple[int, ...],
    timeout_s: float,
) -> AgentSpec:
    """Build one materialized Agent's launch value, once.

    Every launch fact is resolved here and written into the one ``AgentSpec`` the Conversation opens: the role's paths, plugins, sub-agents, tools and hooks, its prompt, its final environment (PATH included), and its exact ``Confinement``. Nothing is stored twice and no later step rewrites the value. ``cgroups`` is the unit's placed session and commands cgroup pair; ``device_indices`` is the WorkUnit's own live GPU claim, never read from saved Resources."""
    # Imported here, not at module scope: importing the engine must never
    # import a Definition package, so the arrow to the product shell exists
    # only while a product run is actually being served.
    from engine.builtin.aibuildai.search import product_runtime
    from engine.composite import Composite
    from engine.durable_execution import _action_spec

    # A role that declares task_environment is launched with this run's
    # Program interpreter on its own PATH, is told that interpreter in its
    # prompt, and has that environment bound into its sandbox, so its process
    # may not start while the environment is being written. The spec resolves
    # those three facts below, so asking here is what orders the Agent launch
    # against a preparation in flight: a resume prepares the environment again
    # in a new process at the same moment durable retry relaunches this role.
    await run_program_environment().prepare(
        lambda: run_clock().effective_remaining_s(agent)
    )

    parent_path = latest_run_state().records[agent.path].parent_path
    parent = (
        None
        if parent_path is None
        else latest_run_state().execution_for(parent_path)
    )
    owner = parent if isinstance(parent, Composite) else None
    owner_path = None if owner is None else owner.path
    agent_type = type(agent)
    unit = agent
    input = agent.input
    search = running_product_search()
    caller_execution_path = agent.path
    mem_cap_gb = (
        None
        if agent.capability.memory_max_gb is None
        else int(agent.capability.memory_max_gb)
    )
    budget_minutes = timeout_s / 60
    artifacts_dir = agent.artifacts_dir
    scratch_directory = agent.scratch_dir
    remaining_s = lambda: run_clock().effective_remaining_s(agent)  # noqa: E731

    config = run_configs()
    run_paths = product_runtime().run_paths
    public_dir = run_paths.public_dir
    active_agent_types = type(search).active_agent_types(config)
    known_roles = tuple(sorted(active_agent_types))
    role = agent_type.name
    policy = agent_type.policy
    if policy is None:
        raise AssertionError(f"{agent_type.__name__} has no RolePolicy")
    output_schema = _action_spec(agent_type, "run").success
    # This Action's own grants, and nothing else: the launch boundary resolves
    # what the composition site declared. Its own scratch and artifacts join
    # the write set because they are the family's, not a grant; /tmp joins the
    # read set because a private one exists in every sandbox.
    action = agent._action
    read_dirs = (*granted_paths(action.read, running=agent.path), "/tmp")
    write_dirs = tuple(
        dict.fromkeys(
            (
                scratch_directory,
                artifacts_dir,
                *granted_paths(action.write, running=agent.path),
            )
        )
    )
    # A role that owns the directory its session opens in says so itself; the
    # launch boundary holds no list of which roles are special, and every other
    # role starts in its own scratch.
    cwd = str(canonical_path(agent.launch_directory() or scratch_directory))
    # The role's task data source is what it was actually granted: with the
    # run's public data bound it reads the frozen view and README; a role
    # granted the raw task folder instead reads the folder.
    reads_public = public_dir in read_dirs
    readme = task_readme(config, run_paths, public=reads_public)
    data_dir = public_dir if reads_public else config.task_folder

    configured_mcp_servers = product_runtime().mcp_client.servers_for_role(
        config,
        role,
        known_roles,
    )
    # A role that declares its own @tool methods asks for the operator's servers
    # in this policy's OWN words, ``mcp__*``, because those servers default to
    # every active role and an arbitrary one of them may start work. Every role
    # that declares no tool keeps exactly what it inherited before. The product's
    # own mounts below are not the operator's and do not ask for that grant.
    mcp_servers = [
        *(configured_mcp_servers if not agent_type.tools or "mcp__*" in policy.tools else []),
        executions_mcp_server(search, caller_execution_path),
    ]
    if agent_type.tools:
        # The role's own @tool methods reach the model as one more standard
        # in-process server. The handler is the crossing itself: it carries the
        # call to the Workflow that owns this conversation, where the real
        # method runs.
        published = []
        for name, (request, _answer) in agent_type.tools.items():
            # A tool that declares no business request still publishes an input schema; it is the empty object.
            schema = request.json_schema() if request else {"type": "object", "properties": {}, "additionalProperties": False}
            declared = Tool(name=name, description=inspect.getdoc(getattr(agent_type, name)) or "", input_schema=schema)
            published.append((declared, partial(agent.ctx._request, tool_result, name)))
        mcp_servers.append(tool_server("tools", published))
    duplicate_mcp_names = sorted(
        name
        for name, count in Counter(server.name for server in mcp_servers).items()
        if count > 1
    )
    if duplicate_mcp_names:
        raise ValueError(
            f"role {role!r}: duplicate root MCP server name(s) "
            f"after combining configured and method-owned servers: "
            f"{duplicate_mcp_names}"
        )

    discovered, plugin_agent_specs, skill_handles = discover_plugins_and_agents(
        config,
        active_agent_types,
        role,
    )
    # An extension is reached with Skill, Agent or Task. A role that declares its
    # own tools and grants none of the three receives none, so an operator's
    # default ``roles: all`` cannot hand it a dispatch tool its policy never
    # named. The mount goes too: a mounted plugin may carry behaviour of its own.
    if agent_type.tools and not {"Skill", "Agent", "Task"}.intersection(policy.tools):
        discovered, plugin_agent_specs, skill_handles = [], [], []
    tool_names = list(
        _resolve_tool_names(
            f"role {role!r}",
            policy.tools,
            has_skills=bool(skill_handles),
            has_sub_agents=bool(plugin_agent_specs),
            mcp_tools=("mcp__*",) if mcp_servers else (),
            # Two sources of one denial: what the operator refused, and what
            # the endpoint cannot serve. Both remove a tool from every role, so
            # they resolve together and the backend receives one decided set.
            disallowed_tools=[
                *config.disallowed_tools,
                *endpoint_disallowed_tools(config.llm),
            ],
            disallowed_source="config.disallowed_tools / the endpoint",
        )
    )
    profile = _profile_for_role(search, role, owner_path)
    sub_agents = build_sub_agent_specs(
        plugin_agent_specs,
        tool_names,
        (server.name for server in configured_mcp_servers),
    )

    for path in (cwd, *write_dirs):
        Path(path).mkdir(parents=True, exist_ok=True)
    install_metric_recorder(cwd)

    from .plugins import visible_plugin_skills

    prompt_skills = visible_plugin_skills(
        discovered,
        visible_skills=set(skill_handles),
    )
    if not config.run.task_name:
        raise AssertionError(
            "compute_agent_spec: config.run.task_name must be non-empty; "
            "set run.task_name in YAML config."
        )
    memory_section = ""
    if config.memory.enable:
        memory_section = get_memory_context(
            config.run.task_name,
            max_chars=config.memory.max_chars,
            user_dir=config.memory.user_dir,
        )
    # AgentSpec.instructions is a plain str carrying just the "append" payload;
    # the Conversation re-wraps it into the SDK preset dict when it opens its client.
    # Deferred to avoid loading prompt rendering during module import.
    from engine.work_unit.agent.prompt import (
        INTEGRITY_VIOLATIONS,
        agent_template_for,
        render_prompt,
    )

    agent_template = agent_template_for(agent_type)
    full_output_dir = f"{artifacts_dir}/full"
    instructions = render_prompt(
        input=input,
        input_json=json.dumps(input.json_dict(), indent=2),
        schema_json=json.dumps(output_schema.business_json_schema(), indent=2),
        search_template=search_template,
        agent_template=agent_template,
        general_violations=[
            item for item in INTEGRITY_VIOLATIONS if item["id"] != "data_leakage"
        ],
        launch={
            "system_instructions": config.llm.system_instructions.get(role, ""),
            "readme": readme,
            "mem_cap": f"{mem_cap_gb}G" if mem_cap_gb is not None else "unlimited",
            # The resource facts a role used to read out of its own Input JSON.
            # They are fixed launch facts, so the builder renders them for both
            # entries instead of every Search author assembling them by hand.
            "training_mem_target_gb": config.resources.work_unit.limit_for(
                "program", "training"
            ).memory_max_gb,
            "budget_label": config.run.budget.pipeline_budget_label,
            "task_python": (
                run_program_environment().python
                if policy.task_environment
                else None
            ),
            # The role's own live claim, so a prompt says what this launch can
            # really use rather than what the host happens to own.
            "gpu_count": len(device_indices),
            "data_dir": data_dir,
            # What this exact Action was granted, so the model is told where
            # the resources it may use really are and nothing has to repeat
            # those addresses in business Input.
            "read_paths": read_dirs,
            "write_paths": write_dirs,
            "cwd": cwd,
            # Where this call's design writes its outputs. It is a launch
            # fact, not business Input: the framework picked the address, so a
            # design-backed Coder and a trial Coder read the same names
            # here and neither has to be told which one it is.
            "artifact_dir": artifacts_dir,
            "full_output_dir": full_output_dir,
            "smoke_output_dir": f"{artifacts_dir}/smoke",
            # The document this role promises, relative to its artifacts
            # directory: the same answer the gate reads, so the prompt and the
            # gate can never name different files (None: no document this
            # invocation, e.g. a Meta run with search.input.report off).
            "required_pdf": unit.required_pdf_for_run(),
            "attempt_dir": full_output_dir,
            "review_dir": scratch_directory,
            "kaggle_enabled": any(server.name == "kaggle" for server in mcp_servers),
            "plugins": discovered,
            "plugin_agent_specs": plugin_agent_specs,
            "prompt_skills": prompt_skills,
            "memory_section": memory_section,
            "work_unit_minutes": budget_minutes,
            # A role whose own prompt needs a fact only its package can name
            # says so itself. Generic launch knows read and write, never what
            # one product resource is called.
            **agent.prompt_facts(),
        },
    )

    hooks: list[HookSpec] = [make_force_sync_dispatch_spec()]
    hooks.append(make_restrict_write_directory_spec(cwd, *write_dirs))
    hooks.append(make_protect_input_files_spec(config.task_folder))
    hooks.append(make_block_unsafe_kill_spec())
    hooks.append(
        make_protect_bash_operations_spec(config.task_folder, config.output_base_dir)
    )
    if policy.blocks_long_sleep:
        hooks.append(make_block_long_sleep_spec())
    hooks.append(make_command_timeout_spec(remaining_s))
    if any(server.name == "kaggle" for server in mcp_servers):
        hooks.append(make_kaggle_timeout_spec(remaining_s))

    env = await _default_env(
        search,
        policy=policy,
        write_face=scratch_directory,
        cache_root=run_paths.cache_dir,
    )
    # The shared metric recorder reads this attempt-owned directory.
    env["OUTPUT_DIR"] = full_output_dir

    # --- The exact host confinement, and the environment that runs in it.
    # Launcher history belongs to the WorkUnit step, beside (not inside) its
    # model-owned scratch directory.
    step_dir = unit.run_dir
    launcher_dir = f"{step_dir}/launcher"
    session_path, commands_path = cgroups
    if commands_path is None:
        raise AssertionError(
            f"the {role} agent was not placed in the resource tree; the "
            f"WorkUnit must prepare resources before its launcher is rendered"
        )
    session_cgroup = Path(session_path)
    commands_cgroup = Path(commands_path)
    tool_bin_dir = tectonic_bin_dir(agent_type)
    if config.resources.sandbox.enable:
        conf = _sandboxed_confinement(
            policy,
            config,
            cwd=cwd,
            read_dirs=read_dirs,
            write_dirs=write_dirs,
            run_cache_dir=env.get("XDG_CACHE_HOME"),
            plugin_dirs=tuple(plugin.path for plugin in discovered),
            dev_nodes=await _device_nodes(
                device_indices,
                min(timeout_s, run_clock().effective_remaining_s(unit)),
            ),
            cgroup=session_cgroup,
            commands_cgroup=commands_cgroup,
            step_dir=step_dir,
            mask_paths=(launcher_dir,),
            task_env_prefix=_task_env_prefix(search, policy),
            tool_bin_dir=tool_bin_dir,
        )
        # The bound cards are numbered from zero inside bwrap.
        env["CUDA_VISIBLE_DEVICES"] = ",".join(
            str(index) for index in range(len(device_indices))
        )
        env["PATH"] = _sandbox_path(
            policy,
            task_env_prefix=_task_env_prefix(search, policy),
            tool_bin_dir=tool_bin_dir,
        )
        assert_paths_covered(conf, env=env, add_dirs=tuple(read_dirs), cwd=cwd)
    else:
        conf = Confinement(
            cgroup=session_cgroup,
            commands_cgroup=commands_cgroup,
            apply_bwrap=False,
            step_dir=step_dir,
        )
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(index) for index in device_indices)
        if device_indices:
            env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        # FIRST, not last: a host that already has its own tectonic on PATH
        # would otherwise shadow the one this run pins.
        if tool_bin_dir is not None:
            env["PATH"] = os.pathsep.join(
                part
                for part in (tool_bin_dir, env.get("PATH", os.environ.get("PATH", "")))
                if part
            )
    # The per-command cgroup hooks: the command enrolls itself at the path it
    # sees, and the orchestrator reaps the cgroup at the host path.
    hooks.append(make_command_self_enroll_spec(conf.commands_root()))

    return AgentSpec(
        name=role,
        instructions=instructions,
        # Per-unit model: the resolved profile's model (User by_role > Router
        # auto > default). The Router's run or per-design choice is spliced in
        # by _profile_for_role as the middle layer. This builder never imports
        # backends; routing-time LLM calls live in
        # ``engine.builtin.tree.agents.router.routing``.
        model=profile.model,
        tools=tuple(tool_names),
        # The SDK receives the Output type's own JSON Schema; the prompt above
        # carries the business schema of the same type.
        output_schema=output_schema.model_json_schema(),
        mcp_servers=tuple(mcp_servers),
        sub_agents=sub_agents,
        pre_tool_hooks=tuple(hooks),
        post_tool_hooks=(make_command_cgroup_reaper_spec(str(commands_cgroup)),),
        max_turns=agent_type.max_turns,
        cwd=cwd,
        add_dirs=list(read_dirs),
        write_dirs=list(write_dirs),
        env=env,
        plugins={plugin.name: plugin.path for plugin in discovered},
        skills=tuple(skill_handles),
        effort=profile.effort_str,
        max_thinking_tokens=profile.max_thinking_tokens,
        confinement=conf,
    )


def _task_env_prefix(search: "AIBuildAISearch", policy: RolePolicy) -> str | None:
    """The task Program environment prefix of a role that declares ``task_environment``, else None."""
    if not policy.task_environment:
        return None
    return str(Path(run_program_environment().python).resolve().parent.parent)


async def _device_nodes(
    device_indices: tuple[int, ...], timeout_s: float
) -> tuple[str, ...]:
    """The GPU device nodes of the unit's own claim; nothing for a unit without one."""
    if not device_indices:
        return ()
    return (
        *device_dev_nodes(await device_minors(device_indices, timeout_s)),
        *enumerate_nvidia_cap_nodes(),
    )


def _sandbox_path(
    policy: RolePolicy, *, task_env_prefix: str | None, tool_bin_dir: str | None
) -> str:
    """The PATH inside a sandboxed Agent session, from the prefixes this launch binds.

    In order: the owned compiler directory of a document producer (first, so a host tectonic cannot shadow the one this run pins), the task environment bin of a role that declares ``task_environment``, the manager environment bin, the Conda root bin for a role granted the Conda root (``conda`` itself lives there, not in a named environment's bin), then the system bins. A prefix that is not bound is not listed, so the PATH names only what the process can reach."""
    parts: list[str] = []
    if tool_bin_dir is not None:
        parts.append(tool_bin_dir.rstrip("/"))
    if task_env_prefix is not None:
        parts.append(f"{task_env_prefix.rstrip('/')}/bin")
    parts.append(f"{sys.prefix.rstrip('/')}/bin")
    if SystemDir.CONDA_ROOT in (
        *policy.system_read,
        *policy.system_write,
    ):
        parts.append(f"{conda_root().rstrip('/')}/bin")
    parts += ["/usr/bin", "/bin"]
    return os.pathsep.join(dict.fromkeys(parts))


def _sandboxed_confinement(
    policy: RolePolicy,
    config: "AgentConfig",
    *,
    cwd: str,
    read_dirs: Sequence[str],
    write_dirs: Sequence[str],
    run_cache_dir: str | None,
    plugin_dirs: Sequence[str],
    dev_nodes: tuple[str, ...],
    cgroup: Path,
    commands_cgroup: Path,
    step_dir: str,
    mask_paths: tuple[str, ...],
    task_env_prefix: str | None,
    tool_bin_dir: str | None,
) -> Confinement:
    """The exact bwrap view of one role: every path is one this role's policy, the operator, or this launch named.

    System paths: the operating-system read baseline, the operator's extra system paths, the owned compiler directory of a document producer, the manager environment (the interpreter the role's commands run, read-only), and the host Conda directories the policy declares -- read-only or writable exactly as declared, so only the role that builds environments (Setup) can write the Conda root. Role paths: its resolved read and write directories, the task environment prefix (writable for the environment builder, read-only for every other role that declares ``task_environment``), the run-level library cache (the content-addressed caches re-anchor at the run home, which under bwrap must be a writable bind for design-scoped roles whose run code dir is read-only), and the resolved plugin directories (the same plugins that reach the backend are bound, or the backend process cannot read the advertised skill files)."""
    ro_paths = [path for path in read_dirs if path != "/tmp"]
    rw = list(write_dirs)
    if task_env_prefix is not None:
        if SystemDir.CONDA_ROOT in policy.system_write:
            rw.append(task_env_prefix)
        else:
            ro_paths.append(task_env_prefix)
    if run_cache_dir is not None:
        rw.append(run_cache_dir)
    for plugin_dir in plugin_dirs:
        plugin_path = Path(plugin_dir).resolve()
        if not plugin_path.is_dir():
            raise AssertionError(
                f"plugin dir vanished before sandbox bind: {plugin_path}"
            )
        ro_paths.append(str(plugin_path))
    system_dirs = {
        SystemDir.CONDA_ROOT: conda_root(),
        SystemDir.CONDA_PACKAGES: f"{conda_root()}/pkgs",
    }
    system_read = [
        *OS_READ_BASELINE,
        *config.resources.sandbox.system_read_paths,
        *((tool_bin_dir,) if tool_bin_dir is not None else ()),
        sys.prefix,
        *(system_dirs[kind] for kind in policy.system_read),
    ]
    system_write = [
        *config.resources.sandbox.system_write_paths,
        *(system_dirs[kind] for kind in policy.system_write),
    ]
    conf = Confinement(
        apply_bwrap=True,
        system_read_paths=tuple(dict.fromkeys(system_read)),
        system_write_paths=tuple(dict.fromkeys(system_write)),
        ro_paths=tuple(ro_paths),
        rw_paths=tuple(rw),
        dev_nodes=dev_nodes,
        mask_paths=mask_paths,
        cgroup=cgroup,
        commands_cgroup=commands_cgroup,
        step_dir=step_dir,
    )
    # The host SDK resolves cwd before locating a saved conversation. Give
    # the sandbox that same path, with only the access already granted here.
    conf.require_readable(cwd, owner="Agent working directory")
    writable = any(
        is_within(cwd, path) for path in (*conf.system_write_paths, *conf.rw_paths)
    )
    return replace(
        conf,
        rw_paths=tuple(dict.fromkeys((*conf.rw_paths, cwd))) if writable else conf.rw_paths,
        ro_paths=conf.ro_paths if writable else tuple(dict.fromkeys((*conf.ro_paths, cwd))),
        # A read-only cwd can contain a masked child. Its physical spelling
        # must hide that child too; adding a mount must not expose it.
        mask_paths=tuple(dict.fromkeys((
            *conf.mask_paths,
            *(str(canonical_path(path)) for path in conf.mask_paths if is_within(path, cwd)),
        ))),
    )
