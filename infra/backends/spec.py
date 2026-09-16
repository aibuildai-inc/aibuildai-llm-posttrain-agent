"""AgentSpec: the product launch facts of one model conversation.

``Conversation`` opens its client from an AgentSpec. Every field is a fact the product decides on its own, independently of Claude: who the role is, what it may call, the final JSON Schema of its answer, where and under what confinement it runs, and which resolved extensions it was granted. The Claude implementation compiles these facts into native options; nothing here mirrors that option surface, so engine, memory, audit, and sibling code construct and read this value without an SDK type, and a denied tool, hook point, or extension is simply absent rather than present-and-disabled.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcp.server import Server

from infra.host_resource.confinement import Confinement

if TYPE_CHECKING:
    from engine.work_unit.agent.plugins import SubAgent


@dataclass(frozen=True)
class HookSpec:
    """One tool-hook registration: the async callback and the tool-name pattern it matches. Whether it runs before or after the tool is where the launch value lists it, ``pre_tool_hooks`` or ``post_tool_hooks``."""

    callback: Callable[..., Awaitable[dict]]
    tool_name_glob: str


@dataclass(frozen=True)
class AgentSpec:
    """The launch facts of one model conversation.

    ``tools`` is the resolved grant of tool names; MCP tools appear as their ``mcp__<server>__<tool>`` names. ``output_schema`` is the final JSON-native schema of the structured result, resolved by the owner that knows the Output type, or None for a conversation that answers in text. ``mcp_servers`` are standard in-process MCP servers (``engine.mcp.tool_server`` builds one), each mounted under its own name. ``sub_agents`` are the resolved sub-agent definitions the plugin owner produced. ``plugins`` maps each mounted plugin name to its absolute directory and ``skills`` lists the role-scoped ``<plugin>:<skill>`` handles the system prompt advertises, so the Skill tool resolves exactly those. ``effort`` and ``max_thinking_tokens`` are the role's reasoning profile. An unset effort uses the provider default. An unset thinking budget lets the model choose thinking depth; zero disables thinking, and a positive value sets the budget. ``use_default_system_prompt`` keeps the backend's own agent base prompt in front of ``instructions``; False makes ``instructions`` the entire system prompt, for a caller that is not one of the product's coding Agents. ``env``, ``add_dirs``, and ``cwd`` are the final launch environment, read roots, and working directory whether or not the launch is confined. ``confinement`` is the unit's exact host confinement, built by the same owner that built the rest of this value; None for a conversation that needs no host confinement.
    """

    name: str
    instructions: str
    model: str
    tools: tuple[str, ...]
    output_schema: dict[str, Any] | None = None
    mcp_servers: tuple[Server, ...] = ()
    sub_agents: tuple["SubAgent", ...] = ()
    pre_tool_hooks: tuple[HookSpec, ...] = ()
    post_tool_hooks: tuple[HookSpec, ...] = ()
    max_turns: int | None = None
    cwd: str | None = None
    add_dirs: list[str] = field(default_factory=list)
    write_dirs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    plugins: dict[str, str] = field(default_factory=dict)
    skills: tuple[str, ...] = ()
    use_default_system_prompt: bool = True
    effort: str | None = None
    max_thinking_tokens: int | None = None
    confinement: Confinement | None = None
