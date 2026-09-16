"""Resolve typed plugin, sub-agent, and skill entries into role-scoped paths.

Pydantic owns each registry entry's tagged syntax. This module owns built-in discovery, filesystem resolution, active-role validation, implicit grants, and add-only unions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

import yaml


if TYPE_CHECKING:
    from config import AgentConfig, PathExtensionConfig, ReferenceExtensionConfig
    from engine.work_unit.agent.base import Agent

# Default extension scope is an explicit product decision, not "all roles minus
# exceptions", so a new Agent is named here or it gets nothing.
# Worker follows Coder and Reviser because it does the work both used to do,
# and its reviewer follows theirs. Manager remains explicit-only, preserving
# its narrow base responsibility.
_MODELING_ROLES: tuple[str, ...] = (
    "setup",
    "designer",
    "judge",
    "selector",
    "coder",
    "worker",
    "reviser",
    "aggregator",
    "coder_review",
    "designer_review",
    "reviser_review",
    "worker_review",
)

# The single bundled core plugin. Its built-in skills and generic MCP sub-agent
# live under it on disk (plugins/<marketplace>/aibuildai-builtin/).
BUILTIN_PLUGIN_NAME: str = "aibuildai-builtin"

# Built-in skills' BASE role assignment, keyed by ``<plugin>:<skill>`` qualified
# name. This is skill add-source 1 (see :func:`_resolve_skill_roles`): the bundled
# plugin's per-skill base scope. The three modeling-methodology skills serve the
# modeling-pipeline roles; ``research-paper-writing`` serves the four document producers.
# This is the skills-kind member of the
# ``lookup_builtin_*`` family (see :func:`builtin_skill_default_roles`). The user
# can only ADD to it (a ``skills`` / ``plugins`` grant unions more roles on); the
# three add-sources never subtract, so a built-in skill is never removed from a
# role this table gives it. It is NOT a separate scoping mechanism — per-role
# skill visibility is resolved in ONE place, :func:`_resolve_skill_roles`, for
# built-in and user skills alike. With no user grant, per-role visibility is
# designer / coder / reviser / worker see the methodology skills. A concrete
# Agent that declares ``required_pdf`` sees research-paper-writing; no role-name
# list repeats that declaration. Router sees only model-routing. meta and
# meta_review see meta-search-design: the reviewer judges public-API use
# against the same reference pages the author wrote from.
# Static keys match their bundled on-disk skills. Research-paper-writing is the
# one derived default and is handled by :func:`builtin_skill_default_roles`.
# All role strings are valid Agent role names.
_BUILTIN_SKILL_DEFAULT_ROLES: dict[str, tuple[str, ...]] = {
    f"{BUILTIN_PLUGIN_NAME}:checkpoint-selection": _MODELING_ROLES,
    f"{BUILTIN_PLUGIN_NAME}:data-augmentation": _MODELING_ROLES,
    f"{BUILTIN_PLUGIN_NAME}:ensemble": _MODELING_ROLES,
    f"{BUILTIN_PLUGIN_NAME}:model-routing": ("router",),
    f"{BUILTIN_PLUGIN_NAME}:meta-search-design": ("meta", "meta_review"),
}


def builtin_skill_default_roles(
    qualified_name: str,
    agent_types: "dict[str, type[Agent]]",
) -> "tuple[str, ...] | None":
    """Base roles for a built-in skill ``<plugin>:<skill>``, or None.

    Document authoring is derived from each active Agent class. A shared role
    name such as ``worker`` therefore cannot grant the skill to a different
    Agent class in another Search.
    """
    if qualified_name == f"{BUILTIN_PLUGIN_NAME}:research-paper-writing":
        return tuple(
            role
            for role in sorted(agent_types)
            if agent_types[role].required_pdf is not None
        )
    return _BUILTIN_SKILL_DEFAULT_ROLES.get(qualified_name)


# Core plugins always enabled unless the user lists them explicitly. The single
# bundled plugin is auto-injected so its skills are discovered; its per-role MOUNT
# set is then DERIVED (the union of its skills' effective roles) in
# :func:`_resolve_skill_roles`, so it is never a second source of truth. CRITICAL:
# this injection makes the plugin discoverable but does NOT feed skill add-source 3
# — :func:`resolve_enable_config` captures source 3 (``user_plugin_roles``) from
# the user-explicit ``config.plugins`` BEFORE this injection runs, so the built-in
# skills' base visibility stays add-source 1 only. The provisional role tuple
# has no roles; skill resolution computes the mount from active Agent classes
# and user grants.
IMPLICIT_PLUGINS: dict[str, tuple[str, ...]] = {
    BUILTIN_PLUGIN_NAME: (),
}

# The one built-in sub-agent. It carries no ``mcpServers`` frontmatter because
# each role binds it to that role's full MCP set at resolution time. Local
# plugin skills stay on the Main Agent's Skill tool.
MCP_SUBAGENT_NAME: str = "mcp-subagent"


# Built-in standalone sub-agents' BASE role assignment (agent add-source 1) — the
# agent mirror of :data:`_BUILTIN_SKILL_DEFAULT_ROLES`. The MCP sub-agent
# defaults to the modeling roles — the SAME explicit 12-role scope the
# modeling-methodology skills serve. This makes it DEFAULT-ON for the modeling pipeline (waking the
# ``## Plugins`` dispatch prompt), unioned (never overridden) by an ``agents:``
# grant. Keys MUST be the built-in agents' frontmatter names (resolved to
# ``agents/*.md`` paths via :func:`lookup_builtin_agent`); role strings are valid
# Agent role names.
_BUILTIN_AGENT_DEFAULT_ROLES: dict[str, tuple[str, ...]] = {
    MCP_SUBAGENT_NAME: _MODELING_ROLES,
}


@dataclass(frozen=True)
class ResolvedPlugin:
    """One plugin selected for a role: ``name`` is the product handle used in ``<plugin>:<skill>`` references, ``path`` the absolute plugin directory."""

    name: str
    path: str


@dataclass(frozen=True)
class SubAgent:
    """One resolved sub-agent a role may dispatch: the product facts the provider's agent definition needs, and nothing of the root launch (no confinement, output schema, hooks, cwd, or plugin mounts). ``model`` None inherits the dispatching role's model."""

    name: str
    description: str
    prompt: str
    tools: tuple[str, ...]
    model: str | None


@dataclass(frozen=True)
class EnableConfig:
    """Resolved per-role enablement view for all three assignable kinds.

    Plugin and Agent keys are resolved paths. Skill keys are qualified names. User plugin roles stay separate because bundle Agents follow explicit plugin grants, while plugin mounts follow effective skill roles.
    """

    plugins: dict[Path, tuple[str, ...]] = field(default_factory=dict)
    plugin_names: dict[Path, str] = field(default_factory=dict)
    agents: dict[Path, tuple[str, ...]] = field(default_factory=dict)
    agent_names: dict[Path, str] = field(default_factory=dict)
    skill_roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    user_plugin_roles: dict[Path, tuple[str, ...]] = field(default_factory=dict)
    user_plugin_names: dict[Path, str] = field(default_factory=dict)

    def all_plugins(self) -> list[ResolvedPlugin]:
        """All resolved plugin directories, independent of role."""
        return [
            ResolvedPlugin(name=self.plugin_names[p], path=str(p)) for p in self.plugins
        ]

    def plugins_for_role(self, role: str) -> list[ResolvedPlugin]:
        """Absolute plugin directories MOUNTED for *role* (skill-derived), in registry order — used for the SDK ``plugins=`` mount + skill visibility."""
        return [
            ResolvedPlugin(name=self.plugin_names[p], path=str(p))
            for p, roles in self.plugins.items()
            if role in roles
        ]

    def explicit_plugins_for_role(self, role: str) -> list[ResolvedPlugin]:
        """Plugin directories the user EXPLICITLY listed in ``plugins:`` for *role* (``user_plugin_roles``, NOT the skill-derived mount). This is agent add-source 3's scope: the bundle agents a role gets are exactly those of the bundles the user mounted for that role — a ``skills`` widen of a bundle's skill never moves the bundle's agents (Model 1)."""
        return [
            ResolvedPlugin(name=self.user_plugin_names[p], path=str(p))
            for p, roles in self.user_plugin_roles.items()
            if role in roles
        ]

    def agents_for_role(self, role: str) -> dict[Path, str]:
        """Absolute Agent paths and names (source 1+2) enabled for *role*."""
        return {
            path: self.agent_names[path]
            for path, roles in self.agents.items()
            if role in roles
        }

    def skills_for_role(self, role: str) -> list[str]:
        """``<plugin>:<skill>`` qualified handles advertised + SDK-enabled for *role*, in resolution order. This IS the SDK ``skills=`` list and the ``## Skills`` visibility set — both consume it, so they never drift."""
        return [qn for qn, roles in self.skill_roles.items() if role in roles]


def resolve_extension_roles(
    roles: str | list[str],
    name: str,
    known_roles: tuple[str, ...],
) -> tuple[str, ...]:
    """Expand ``all`` and reject roles that are not active for this run."""
    if roles == "all":
        return known_roles
    unknown = [role for role in roles if role not in known_roles]
    if unknown:
        raise ValueError(
            f"extension {name!r}: unknown role(s) {unknown}. "
            f"Known roles: {list(known_roles)}"
        )
    return tuple(roles)


def lookup_builtin_plugin(name: str) -> "Path | None":
    """Return the shipped built-in plugin directory for *name*.

    Discovery scans non-hidden marketplace plugin directories by directory name.
    """
    # pathlib ``glob('*/x')`` DOES match dot-prefixed entries (unlike the
    # shell), so a dot-prefixed marketplace would leak in — every part of the
    # plugin dir's path relative to the root is checked for a leading dot.
    root = _core_plugins_root()
    for plugin_dir in sorted(p for p in root.glob("*/*") if p.is_dir()):
        if any(part.startswith(".") for part in plugin_dir.relative_to(root).parts):
            continue
        if (
            not (plugin_dir / "skills").is_dir()
            and not (plugin_dir / "agents").is_dir()
        ):
            continue
        if plugin_dir.name == name:
            return plugin_dir.resolve()
    return None


def lookup_builtin_agent(name: str) -> "Path | None":
    """Return the shipped standalone Agent file for *name*.

    Discovery scans non-hidden Agent files by frontmatter name.
    """
    # Same dot-dir caveat as the plugin walk in ``lookup_builtin_plugin``.
    root = _core_plugins_root()
    for md in sorted(root.glob("*/*/agents/*.md")):
        if any(part.startswith(".") for part in md.relative_to(root).parts):
            continue
        spec = _parse_agent_file(md.read_text(), md.stem)
        if spec is not None and spec["name"] == name:
            return md.resolve()
    return None


def _resolve_path(raw: str, base_dir: Path) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        p = base_dir / p
    return p.resolve()


def _validate_plugin_path(resolved: Path, name: str) -> None:
    """Refuse a plugin directory the Claude CLI could not load as the plugin ``name``.

    The manifest is validated once here, at startup, as a user-fixable configuration error: the directory must exist and carry ``.claude-plugin/plugin.json``, a JSON object whose required ``name`` is the product handle this plugin is mounted under, so a ``<plugin>:<skill>`` reference resolves to the same plugin the CLI loads. The official minimal manifest is exactly ``{"name": ...}``; nothing beyond the reference is required."""
    if not resolved.is_dir():
        raise ValueError(
            f"plugins registry entry {name!r}: resolved path is not a directory: {resolved}"
        )
    manifest = resolved / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        raise ValueError(f"plugin {name!r} carries no .claude-plugin/plugin.json: {manifest}")
    try:
        meta = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"plugin manifest {manifest} is not valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"plugin manifest {manifest} must be a JSON object")
    if meta.get("name") != name:
        raise ValueError(
            f"plugin manifest {manifest} names {meta.get('name')!r}, not the "
            f"plugin handle {name!r} it is mounted under"
        )


def _validate_agent_path(resolved: Path, raw: str) -> None:
    if not resolved.is_file():
        raise ValueError(
            f"agents registry entry {raw!r}: resolved path is not a file: {resolved}"
        )
    if resolved.suffix != ".md":
        raise ValueError(
            f"agents registry entry {raw!r}: resolved path must end in .md: {resolved}"
        )


def _project_root() -> Path:
    """Return the project-root directory used to anchor relative YAML paths.

    This is the tmpfs root populated by
    :func:`startup.resource_materializer.materialize_to_tmpfs`, which
    contains a ``plugins/`` subdirectory mirroring the source layout.

    Resources are always materialized before any plugin is resolved, so this
    root always exists.
    """
    # materialized_root lives in startup; engine importing it
    # is a legal downward edge. Kept function-local to avoid a load-order cycle.
    from startup.resource_materializer import materialized_root

    mat = materialized_root()
    if mat is None:
        raise AssertionError("plugin resolution ran before resources were materialized")
    return mat


def _core_plugins_root() -> Path:
    """Return the on-disk plugins root for implicit plugin resolution."""
    return _project_root() / "plugins"


def _find_implicit_plugin_path(name: str) -> Path:
    """Locate ``plugins/<marketplace>/<name>`` under the core plugins root."""
    root = _core_plugins_root()
    matches = list(root.glob(f"*/{name}"))
    if not matches:
        raise ValueError(f"implicit core plugin {name!r} not found under {root}")
    if len(matches) > 1:
        raise ValueError(
            f"implicit core plugin {name!r} resolves to multiple paths "
            f"under {root}: {matches}"
        )
    resolved = matches[0].resolve()
    _validate_plugin_path(resolved, name)
    return resolved


def inject_implicit_plugins(
    plugins: dict[Path, tuple[str, ...]],
    plugin_names: dict[Path, str],
    known_roles: tuple[str, ...],
) -> tuple[dict[Path, tuple[str, ...]], dict[Path, str]]:
    """Augment *plugins* with the :data:`IMPLICIT_PLUGINS` that are absent.

    User-explicit entries win: if a plugin whose resolved name is in the implicit map appears in *plugins* already, it is left untouched (including any restricted role scope). Only implicit plugins missing from *plugins* are resolved via :func:`_find_implicit_plugin_path` and injected with the role tuple :data:`IMPLICIT_PLUGINS` declares for them.
    """
    existing_names = set(plugin_names.values())
    augmented = dict(plugins)
    augmented_names = dict(plugin_names)
    role_set = set(known_roles)
    for name, roles in IMPLICIT_PLUGINS.items():
        if name in existing_names:
            continue
        path = _find_implicit_plugin_path(name)
        augmented[path] = tuple(role for role in roles if role in role_set)
        augmented_names[path] = name
    return augmented, augmented_names


def resolve_enable_config(
    config: "AgentConfig",
    agent_types: "dict[str, type[Agent]]",
) -> EnableConfig:
    """Build an :class:`EnableConfig` from the plugins / agents / skills registries.

    Relative paths use the materialized project root. ``agent_types`` is the
    active name-to-class map, so class-owned declarations can scope skills
    without a second role-name table. Explicit plugin roles are captured before
    implicit injection because only user grants widen every skill and bundle
    Agent in that plugin.
    """
    base = _project_root()
    known_roles = tuple(sorted(agent_types))
    plugins, plugin_names = _resolve_registry_paths(
        config.plugins,
        kind="plugins",
        lookup_builtin=lookup_builtin_plugin,
        validator=_validate_plugin_path,
        base=base,
        known_roles=known_roles,
    )
    # add-source 3 for BOTH skills and agents: the user-explicit ``plugins`` roles,
    # captured BEFORE implicit injection so the injected built-in / synth plugins
    # contribute zero to source 3 (a built-in skill/agent's base scope is source 1
    # only — the merge-floor trap). Stored on EnableConfig so the assembly +
    # startup scopes agent source 3 (discover_agent_specs) by this EXPLICIT mount
    # via explicit_plugins_for_role, NOT the skill-derived mount.
    user_plugin_roles = dict(plugins)
    user_plugin_names = dict(plugin_names)
    agents, agent_names = _resolve_agent_roles(config, base, known_roles)
    plugins, plugin_names = inject_implicit_plugins(
        plugins,
        plugin_names,
        known_roles,
    )
    # Resolve every BYO path from the same base as the other path-bearing
    # registries. Startup owns the file work and receives final paths.
    byo = [
        (name, _resolve_path(entry.path, base))
        for name, entry in config.skills.items()
        if entry.type == "path"
    ]
    from startup.resource_materializer import materialize_user_skills

    synth = materialize_user_skills(byo)
    if synth is not None:
        plugins[synth] = ()
        plugin_names[synth] = "aibuildai-user"
    skill_roles, mount = _resolve_skill_roles(
        config,
        plugins,
        plugin_names,
        user_plugin_roles,
        agent_types,
        known_roles,
    )
    return EnableConfig(
        plugins=mount,
        plugin_names=plugin_names,
        agents=agents,
        agent_names=agent_names,
        skill_roles=skill_roles,
        user_plugin_roles=user_plugin_roles,
        user_plugin_names=user_plugin_names,
    )


def _order_roles(
    roles: "set[str]",
    known_roles: tuple[str, ...],
) -> tuple[str, ...]:
    """Stable role ordering: members of *roles* in active Agent role order. Shared by the skill and agent three-source unions so both render deterministically."""
    return tuple(role for role in known_roles if role in roles)


def _resolve_agent_roles(
    config: "AgentConfig",
    base: Path,
    known_roles: tuple[str, ...],
) -> tuple[dict[Path, tuple[str, ...]], dict[Path, str]]:
    """Union built-in Agent defaults with typed standalone Agent grants.

    Bundle Agents stay in discovery and follow explicit plugin roles, so they are not duplicated here.
    """
    agent_roles: dict[Path, set[str]] = {}
    agent_names: dict[Path, str] = {}
    for name, roles in _BUILTIN_AGENT_DEFAULT_ROLES.items():
        path = lookup_builtin_agent(name)
        if path is None:
            raise AssertionError(
                f"built-in agent {name!r} in _BUILTIN_AGENT_DEFAULT_ROLES has no "
                f"on-disk agents/*.md (lookup_builtin_agent returned None)"
            )
        agent_roles.setdefault(path, set()).update(
            role for role in roles if role in known_roles
        )
        agent_names[path] = name
    registry_paths, registry_names = _resolve_registry_paths(
        config.agents,
        kind="agents",
        lookup_builtin=lookup_builtin_agent,
        validator=_validate_agent_path,
        base=base,
        known_roles=known_roles,
    )
    for path, roles in registry_paths.items():
        agent_roles.setdefault(path, set()).update(roles)
        agent_names[path] = registry_names[path]
    return (
        {p: _order_roles(rs, known_roles) for p, rs in agent_roles.items()},
        agent_names,
    )


def _resolve_skill_roles(
    config: "AgentConfig",
    plugins: dict[Path, tuple[str, ...]],
    plugin_names: dict[Path, str],
    user_plugin_roles: dict[Path, tuple[str, ...]],
    agent_types: "dict[str, type[Agent]]",
    known_roles: tuple[str, ...],
) -> "tuple[dict[str, tuple[str, ...]], dict[Path, tuple[str, ...]]]":
    """Resolve per-(plugin, skill) effective roles + the derived plugin mount set.

    Effective roles are the add-only union of built-in defaults, skill grants, and explicit plugin grants. The plugin mount is the union of its effective skill roles. References must name skills from enabled plugins; path entries are already in the synthetic user plugin.
    """
    name_to_path: dict[str, Path] = {plugin_names[p]: p for p in plugins}
    skills_by_plugin: dict[str, set[str]] = {
        name: {s["name"] for s in _enumerate_plugin_skills(path)}
        for name, path in name_to_path.items()
    }

    def _skill_exists(qualified_name: str) -> bool:
        if ":" not in qualified_name:
            return False
        pname, sname = qualified_name.split(":", 1)
        return sname in skills_by_plugin.get(pname, set())

    from startup.resource_materializer import USER_SKILL_PLUGIN_NAME

    skills_grants: dict[str, tuple[str, ...]] = {}
    for name, entry in config.skills.items():
        roles = resolve_extension_roles(entry.roles, name, known_roles)
        if entry.type == "reference":
            if not _skill_exists(name):
                raise ValueError(
                    f"skills: reference {name!r} matches no skill from an enabled plugin"
                )
            qn = name
        else:
            if _skill_exists(name):
                raise ValueError(
                    f"skills: {name!r} is supplied by an enabled plugin and cannot "
                    "be replaced by a path entry"
                )
            qn = f"{USER_SKILL_PLUGIN_NAME}:{name}"
        skills_grants[qn] = roles
    enumerated = {f"{pn}:{sn}" for pn, sns in skills_by_plugin.items() for sn in sns}
    unresolved = sorted(qn for qn in skills_grants if qn not in enumerated)
    if unresolved:
        raise ValueError(
            f"skills: grant(s) {unresolved} match no enabled skill "
            f"(BYO not materialized, un-enabled plugin, or typo)"
        )

    skill_roles: dict[str, tuple[str, ...]] = {}
    mount: dict[Path, tuple[str, ...]] = dict(plugins)
    for pname, path in name_to_path.items():
        snames = skills_by_plugin.get(pname, set())
        if not snames:
            continue
        plugin_grant = set(user_plugin_roles.get(path, ()))  # source 3 (user-explicit)
        union: set[str] = set()
        for sname in sorted(snames):
            qn = f"{pname}:{sname}"
            eff = set(builtin_skill_default_roles(qn, agent_types) or ())  # source 1
            eff |= set(skills_grants.get(qn, ()))  # source 2
            eff |= plugin_grant  # source 3
            skill_roles[qn] = _order_roles(eff, known_roles)
            union |= eff
        mount[path] = _order_roles(union, known_roles)
    return skill_roles, mount


def _resolve_registry_paths(
    raw: "dict[str, ReferenceExtensionConfig | PathExtensionConfig]",
    *,
    kind: str,
    lookup_builtin: "Callable[[str], Path | None]",
    validator: "Callable[[Path, str], None]",
    base: Path,
    known_roles: tuple[str, ...],
) -> tuple[dict[Path, tuple[str, ...]], dict[Path, str]]:
    """Resolve a typed plugin or Agent registry to paths, names, and active roles."""
    out: dict[Path, tuple[str, ...]] = {}
    names: dict[Path, str] = {}
    for name, entry in raw.items():
        roles = resolve_extension_roles(entry.roles, name, known_roles)
        builtin = lookup_builtin(name)
        if entry.type == "reference":
            if builtin is None:
                raise ValueError(f"{kind}: unknown built-in reference {name!r}")
            resolved = builtin
        else:
            if builtin is not None:
                raise ValueError(
                    f"{kind}: built-in {name!r} cannot be replaced by a path entry"
                )
            resolved = _resolve_path(entry.path, base)
        validator(resolved, name)
        if resolved in out:
            raise ValueError(
                f"{kind}: duplicate resolved path {resolved} (from {name!r})"
            )
        out[resolved] = roles
        names[resolved] = name
    return out, names


def _parse_frontmatter(content: str) -> tuple[dict, str] | None:
    """Parse one Markdown file's YAML frontmatter and body."""
    if not content.startswith("---\n"):
        return None
    rest = content[len("---\n") :]
    end = rest.find("\n---\n")
    if end == -1:
        if not rest.endswith("\n---"):
            return None
        end = len(rest) - len("\n---")
        body_start = len(rest)
    else:
        body_start = end + len("\n---\n")
    try:
        meta = yaml.safe_load(rest[:end])
    except yaml.YAMLError:
        return None
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        return None
    return meta, rest[body_start:].lstrip("\n")


def _text_frontmatter_field(meta: dict, field: str, source: str) -> str:
    if field not in meta:
        return ""
    value = meta[field]
    if not isinstance(value, str):
        raise ValueError(f"{source}: {field} must be a string")
    return value.strip()


def _list_frontmatter_field(meta: dict, field: str, source: str) -> list[str]:
    if field not in meta:
        return []
    value = meta[field]
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        raise ValueError(f"{source}: {field} must be a string or list of strings")
    if any(not item.strip() for item in items):
        raise ValueError(f"{source}: {field} entries must not be blank")
    return [item.strip() for item in items]


def _parse_agent_file(content: str, default_name: str) -> dict | None:
    """Parse one agent markdown file's YAML frontmatter and body.

    Frontmatter delimiter is the literal ``---`` line. The first ``---`` line opens the block; the second closes it; everything after is body.

    Returns ``None`` if the file lacks YAML frontmatter or it cannot be parsed. Otherwise returns a dict with keys:
        - ``name`` (defaults to ``default_name`` if frontmatter omits it)
        - ``description`` (string, empty if absent)
        - ``model`` (string or None)
        - ``disallowed_tools`` (list of tool names, empty if absent)
        - ``mcp_servers`` (list of MCP server names, empty if absent)
        - ``prompt`` (the markdown body, leading newlines stripped)

    The ``disallowedTools`` frontmatter field accepts either a comma-separated string (``Write, Edit``) or a YAML list of strings.

    The ``mcpServers`` frontmatter field (camelCase, following the SDK convention) is converted to the snake_case ``mcp_servers`` key. It accepts a YAML inline list (``[arxiv, scholar]``), a YAML block list, or a comma-separated string. Other values fail at startup. Full inline server definitions are not supported here.
    """
    parsed = _parse_frontmatter(content)
    if parsed is None:
        return None
    meta, body = parsed
    source = f"agent {default_name!r}"
    supported = {"name", "description", "model", "disallowedTools", "mcpServers"}
    unknown = sorted(
        repr(field)
        for field in meta
        if not isinstance(field, str) or field not in supported
    )
    if unknown:
        raise ValueError(f"{source}: unknown frontmatter field(s): {unknown}")
    name = _text_frontmatter_field(meta, "name", source)
    if "name" in meta and not name:
        raise ValueError(f"{source}: name must not be blank")

    return {
        "name": name or default_name,
        "description": _text_frontmatter_field(meta, "description", source),
        "model": _text_frontmatter_field(meta, "model", source) or None,
        "disallowed_tools": _list_frontmatter_field(meta, "disallowedTools", source),
        "mcp_servers": _list_frontmatter_field(meta, "mcpServers", source),
        "prompt": body,
    }


def discover_agent_specs(plugins: list[ResolvedPlugin]) -> list[dict]:
    """Walk each plugin's ``aibuildai_agents/*.md`` and return parsed sub-agent specs.

    Each spec is a dict with the keys produced by :func:`_parse_agent_file` plus two parent-plugin fields:
        - ``plugin_name`` — the parent plugin's frontmatter name
        - ``agent_path`` — the resolved Agent Markdown path

    This is the **single source of truth** for system-prompt rendering and AgentSpec sub-agent registration. Production builders call this once per session and thread the result through both consumers, avoiding double disk reads.

    Missing or malformed YAML frontmatter is a startup error. Files with no ``name`` field default to the file basename.
    """
    specs: list[dict] = []
    for p in plugins:
        plugin_name = p.name
        agents_dir = Path(p.path) / "aibuildai_agents"
        if not agents_dir.is_dir():
            continue
        for agent_file in sorted(agents_dir.glob("*.md")):
            spec = _parse_agent_file(agent_file.read_text(), agent_file.stem)
            if spec is None:
                raise ValueError(
                    f"plugin {plugin_name!r}: agent file {agent_file} "
                    "lacks parseable YAML frontmatter"
                )
            spec["plugin_name"] = plugin_name
            spec["agent_path"] = str(agent_file.resolve())
            specs.append(spec)
    return specs


def _enumerate_plugin_skills(plugin_dir: Path) -> list[dict]:
    """Return the name and description of each skill under *plugin_dir*. A ``skills/`` directory with no SKILL.md files yields ``[]``."""
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        return []
    out: list[dict] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        parsed = _parse_frontmatter(skill_md.read_text())
        if parsed is None:
            raise ValueError(f"skill file {skill_md} lacks parseable YAML frontmatter")
        meta, _body = parsed
        source = f"skill file {skill_md}"
        supported = {"name", "description"}
        unknown = sorted(
            repr(field)
            for field in meta
            if not isinstance(field, str) or field not in supported
        )
        if unknown:
            raise ValueError(f"{source}: unknown frontmatter field(s): {unknown}")
        name = _text_frontmatter_field(meta, "name", source)
        if "name" in meta and not name:
            raise ValueError(f"{source}: name must not be blank")
        out.append(
            {
                "name": name or skill_md.parent.name,
                "description": _text_frontmatter_field(meta, "description", source),
            }
        )
    return out


def visible_plugin_skills(
    plugins: list[ResolvedPlugin],
    *,
    visible_skills: "set[str]",
) -> list[tuple[str, str, str]]:
    """Return the role-visible plugin, skill, and description facts."""
    skills: list[tuple[str, str, str]] = []
    for p in plugins:
        for skill in _enumerate_plugin_skills(Path(p.path)):
            if f"{p.name}:{skill['name']}" in visible_skills:
                skills.append((p.name, skill["name"], skill["description"]))
    return skills


def discover_plugins_and_agents(
    config: "AgentConfig",
    agent_types: "dict[str, type[Agent]]",
    role: str,
) -> tuple[list[ResolvedPlugin], list[dict], list[str]]:
    """Run the canonical plugin, agent, and skill discovery sequence.

    Encapsulates the discovery pipeline consumed by AgentSpec building and ``engine/work_unit/agent/spec.py``:

    1. resolve the role-scoped view via ``resolve_enable_config(config, agent_types)``,
    2. load the role's MOUNTED plugin entries (skill-derived, for ``plugins=`` + skills); collect the role's agents as the three-source union: source 1+2 from ``agents_for_role`` (built-in default table + ``agents:`` registry) + source 3 from :func:`discover_agent_specs` over the role's EXPLICIT-mount bundles (``explicit_plugins_for_role`` — the user's ``plugins:`` roles, NOT the skill mount, so a ``skills`` widen never drags a bundle's agents along),
    Returns ``(discovered_plugin_entries, agent_specs, visible_skills)`` where *visible_skills* is the role's resolved ``<plugin>:<skill>`` list (``EnableConfig.skills_for_role(role)``) — the SAME set the prompt's ``## Skills`` section and the SDK ``skills=`` list both consume. ``resolve_enable_config`` always returns a populated EnableConfig (implicit core plugins are injected), so the result is never trivially empty unless the role sees no plugins or agents.
    """
    enable = resolve_enable_config(config, agent_types)
    discovered = enable.plugins_for_role(role)
    # Source 3 bundle agents: discovered over the role's EXPLICIT-mount bundles
    # (user_plugin_roles), so a ``skills`` widen of a bundle's skill does NOT move
    # the bundle's agents. Source 1+2 come from agents_for_role (standalone).
    explicit = enable.explicit_plugins_for_role(role)
    agent_specs = discover_agent_specs(explicit) + load_standalone_agent_specs(
        enable.agents_for_role(role)
    )
    return discovered, agent_specs, enable.skills_for_role(role)


def load_standalone_agent_specs(
    agents: dict[Path, str],
) -> list[dict]:
    """Parse stand-alone (source 1+2) sub-agent ``.md`` files into spec dicts.

    Operates on the resolved ``EnableConfig.agents`` paths (built-in default table + ``agents:`` registry). The registry key is the default Agent name; an explicit different frontmatter name is invalid. Each spec gets an empty ``plugin_name`` and its resolved ``agent_path`` — there is no owning bundle (source-3 bundle agents carry their attribution from :func:`discover_agent_specs`). Missing or malformed frontmatter raises ``ValueError``.
    """
    specs: list[dict] = []
    for raw, expected_name in agents.items():
        path = Path(raw)
        if not path.is_file():
            raise ValueError(f"agents: resolved agent path is not a file: {path}")
        spec = _parse_agent_file(path.read_text(), expected_name)
        if spec is None:
            raise ValueError(
                f"agents: resolved agent path {path} lacks parseable YAML frontmatter"
            )
        if spec["name"] != expected_name:
            raise ValueError(
                f"agents registry entry {expected_name!r}: agent file {path} "
                f"declares name {spec['name']!r}"
            )
        spec["plugin_name"] = ""
        spec["agent_path"] = str(path.resolve())
        specs.append(spec)
    return specs


def validate_agent_roles(
    config: "AgentConfig",
    known_roles: tuple[str, ...],
) -> None:
    """Check that each role setting names an active Agent role."""
    active = set(known_roles)
    invalid = {
        path: sorted(roles - active)
        for path, roles in {
            "llm.by_role": set(config.llm.by_role),
            "llm.system_instructions": set(config.llm.system_instructions),
            "verifier.enable": set(config.verifier.enable),
        }.items()
        if roles - active
    }
    if invalid:
        details = "; ".join(f"{path}: {roles}" for path, roles in invalid.items())
        raise ValueError(f"agent settings name inactive roles: {details}")


def validate_plugin_config(
    config: "AgentConfig",
    enable: EnableConfig,
    configured_mcp_names: Iterable[str],
    known_roles: tuple[str, ...],
) -> None:
    """Check plugin and sub-agent rules before the run starts."""
    from engine.mcp import mcp_servers_for_role
    from engine.work_unit.agent.spec import (
        validate_extension_tool_conflicts,
    )

    builtin_mcp_agent = lookup_builtin_agent(MCP_SUBAGENT_NAME)
    if builtin_mcp_agent is None:
        raise AssertionError(f"built-in agent {MCP_SUBAGENT_NAME!r} is missing")
    builtin_mcp_agent_path = builtin_mcp_agent
    all_specs = discover_agent_specs(enable.all_plugins()) + load_standalone_agent_specs(
        enable.agent_names
    )
    configured = set(configured_mcp_names)
    missing = [
        (spec["name"], sorted(set(spec.get("mcp_servers", [])) - configured))
        for spec in all_specs
        if set(spec.get("mcp_servers", [])) - configured
    ]
    if missing:
        raise ValueError(
            "sub-agents require MCP servers that are absent from config.mcps: "
            + "; ".join(f"{name} needs {names}" for name, names in missing)
        )
    for role in known_roles:
        role_specs = discover_agent_specs(enable.explicit_plugins_for_role(role)) + load_standalone_agent_specs(
            enable.agents_for_role(role)
        )
        role_mcp_names = mcp_servers_for_role(config, role, known_roles)
        validate_extension_tool_conflicts(
            f"role {role!r}",
            has_skills=bool(enable.skills_for_role(role)),
            has_sub_agents=bool(role_specs),
            has_mcp_servers=bool(role_mcp_names),
            disallowed_tools=config.disallowed_tools,
            disallowed_source="config.disallowed_tools",
        )
        seen: dict[str, str] = {}
        for spec in role_specs:
            if "plugin_name" not in spec:
                raise AssertionError(
                    f"role={role!r} sub-agent spec is missing "
                    f"'plugin_name'. spec keys: {sorted(spec.keys())}, "
                    f"spec: {spec!r}"
                )
            name = spec["name"]
            owner = spec["plugin_name"]
            if (
                name == MCP_SUBAGENT_NAME
                and Path(spec["agent_path"]).resolve() != builtin_mcp_agent_path
            ):
                raise ValueError(
                    f"sub-agent name {MCP_SUBAGENT_NAME!r} is reserved for the "
                    "built-in MCP sub-agent"
                )
            missing_role_grants = sorted(
                set(spec.get("mcp_servers", [])) - role_mcp_names
            )
            if missing_role_grants:
                raise ValueError(
                    f"sub-agent {name!r} is enabled for role {role!r}, but "
                    f"config.mcps does not grant that role: {missing_role_grants}"
                )
            validate_extension_tool_conflicts(
                f"sub-agent {name!r}",
                has_skills=False,
                has_sub_agents=False,
                has_mcp_servers=(
                    bool(role_mcp_names)
                    if name == MCP_SUBAGENT_NAME
                    else bool(spec.get("mcp_servers"))
                ),
                disallowed_tools=spec.get("disallowed_tools") or [],
                disallowed_source="frontmatter disallowedTools",
            )
            if name in seen:
                raise ValueError(
                    f"Duplicate sub-agent name {name!r} in role {role!r}: "
                    f"declared by plugins {seen[name]!r} and {owner!r}. "
                    f"Sub-agent names must be globally unique within a role "
                    f"because they are the dispatch key passed to the Agent "
                    f"tool; collisions silently overwrite one another in the "
                    f"SDK's ``agents=`` dict."
                )
            seen[name] = owner
