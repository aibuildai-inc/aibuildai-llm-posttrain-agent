"""AgentSpec launch facts -> ClaudeAgentOptions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    HookMatcher,
    McpSdkServerConfig,
)

from infra.backends.spec import AgentSpec, HookSpec


def _claude_schema(value: Any) -> Any:
    """Convert schema nodes for the CLI; preserve property names and literal values."""
    if not isinstance(value, dict):
        return value
    if "prefixItems" in value:
        # The CLI validates the schema in strict mode and rejects the draft
        # 2020-12 keyword prefixItems, which Pydantic emits for a tuple field
        # (a tuple[str, float] Output ended run 30150992).
        # A tuple becomes an array whose items may be any of the tuple's
        # member schemas; minItems and maxItems keep its length, and the
        # Output type still validates the value the model returns.
        value = dict(value)
        members = value.pop("prefixItems")
        value["items"] = {"anyOf": members} if len(members) > 1 else members[0]
    result = {}
    for key, item in sorted(value.items()):
        if key == "description":
            continue
        if key in {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies"}:
            item = {name: _claude_schema(schema) for name, schema in item.items()}
        elif key in {"allOf", "anyOf", "oneOf"} or (key == "items" and isinstance(item, list)):
            item = [_claude_schema(schema) for schema in item]
        elif key in {
            "items", "additionalItems", "additionalProperties", "unevaluatedItems",
            "unevaluatedProperties", "contains", "propertyNames", "not", "if",
            "then", "else", "contentSchema",
        }:
            item = _claude_schema(item)
        result[key] = item
    return result


def _hook_matchers(hooks: Sequence[HookSpec]) -> list[HookMatcher]:
    """One HookMatcher per tool-name pattern, in first-seen order, carrying every callback registered for it."""
    grouped: dict[str, list] = {}
    for hook in hooks:
        grouped.setdefault(hook.tool_name_glob, []).append(hook.callback)
    return [HookMatcher(matcher=glob, hooks=callbacks) for glob, callbacks in grouped.items()]


def spec_to_options(
    spec: AgentSpec,
    *,
    cli_path: str,
    resume_session_id: str | None,
    stderr_callback: Any,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions from the launch facts plus this session's extras.

    The per-session facts the launch value does not carry are attached here: ``permission_mode="bypassPermissions"`` for non-interactive execution; ``cli_path``, the conversation-owned launcher that carries the anonymous prompt descriptor across the SDK process boundary and applies the spec's Confinement when it has one; ``setting_sources=[]`` so no user or project plugin spills in; ``resume``, the session continuation id; and ``stderr``, the per-session line callback.

    ``spec.plugins`` mounts as ``ClaudeAgentOptions(plugins=...)`` and, when non-empty, ``skills=`` lists the role-scoped ``<plugin>:<skill>`` handles the system prompt's ``## Skills`` section advertises (both come from the same role filter, so they never drift). The SDK ``skills`` list is a context filter: only the listed handles resolve, and the Skill tool rejects any other; without both fields it returns ``Unknown skill`` for every handle the prompt advertises, because ``setting_sources=[]`` suppresses the CLI's own discovery.
    """
    if not spec.cwd:
        raise AssertionError("Claude requires AgentSpec.cwd")
    # ``tools`` is the availability knob: left at the SDK default every
    # role would expose the full CLI built-in set. MCP availability is governed
    # by mcp_servers, so mcp__* stays out of the built-in list.
    builtin_tools = [t for t in spec.tools if not t.startswith("mcp__")]
    env = dict(spec.env)
    # The CLI would hand a Kb or Kaggle call that runs past 120 s back to the
    # model as a background receipt; its backgroundTasksDisabled read of this
    # variable short-circuits that deadline (bundled CLI 2.1.251 source), so
    # the call returns its result. Sub-agents stay in the foreground through
    # AgentDefinition(background=False), and the process entry scrubs every
    # inherited CLAUDE* feature flag (infra/process/env_scrub.py).
    env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"

    kwargs: dict[str, Any] = dict(
        system_prompt={"type": "preset", "preset": "claude_code"} if spec.use_default_system_prompt else None,
        cli_path=cli_path,
        tools=builtin_tools,
        allowed_tools=list(spec.tools),
        model=spec.model,
        add_dirs=list(spec.add_dirs),
        cwd=spec.cwd,
        env=env,
        permission_mode="bypassPermissions",
        setting_sources=[],
        # Only the servers this options object names exist for the CLI; it
        # reads no MCP configuration from the filesystem.
        strict_mcp_config=True,
        # Foreground sub-agents stream their text and thinking to the parent
        # session under their spawning Agent tool's id (parent_tool_use_id),
        # so the nested transcript holds what the sub-agent said, not only
        # what it called.
        forward_subagent_text=True,
        resume=resume_session_id,
        stderr=stderr_callback,
    )
    # Reasoning effort: set it ONLY when explicitly configured (spec.effort not
    # None). When unset we send no effort, so the binary's own default applies
    # and a run that does not set llm.default.effort is unchanged (Claude included).
    if spec.effort is not None:
        kwargs["effort"] = spec.effort
    # None lets the model choose thinking depth; effort guides that choice.
    # Request summary text so the transcript can record more than a signature.
    if spec.max_thinking_tokens == 0:
        kwargs["thinking"] = {"type": "disabled"}
    elif spec.max_thinking_tokens is None:
        kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
    else:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": spec.max_thinking_tokens,
            "display": "summarized",
        }
    if spec.plugins:
        # Startup validated each mounted plugin's manifest; here a resolved
        # directory is simply a native local plugin mount.
        kwargs["plugins"] = [{"type": "local", "path": path} for path in spec.plugins.values()]
        # Role-scoped skill handles, the set the system prompt's ## Skills
        # section lists; the SDK list is a context filter, so every other
        # <plugin>:<skill> stays hidden.
        kwargs["skills"] = list(spec.skills)
    # The role's final JSON Schema becomes the CLI's structured output format.
    # The terminal ResultMessage.structured_output is the one candidate the
    # conversation reports with the turn completion.
    if spec.output_schema is not None:
        kwargs["output_format"] = {
            "type": "json_schema",
            "schema": _claude_schema(spec.output_schema),
        }
    hooks = {
        key: _hook_matchers(registered)
        for key, registered in (("PreToolUse", spec.pre_tool_hooks), ("PostToolUse", spec.post_tool_hooks))
        if registered
    }
    if hooks:
        kwargs["hooks"] = hooks
    if spec.mcp_servers:
        # Each standard server is served to the CLI over the SDK's native
        # in-memory bridge; every CallToolResult it returns reaches the model whole.
        kwargs["mcp_servers"] = {
            server.name: McpSdkServerConfig(type="sdk", name=server.name, instance=server)
            for server in spec.mcp_servers
        }
    if spec.sub_agents:
        # The child reaches the parent's live MCP mounts through ``tools``: the
        # SDK AgentDefinition has no allowed_tools, so tools IS the availability
        # allowlist. Server-qualified MCP patterns select the granted mounts.
        # It carries no ``mcpServers``: under strict_mcp_config the CLI 2.1.251
        # discards every agent-scoped MCP entry and emits an "agent MCP server
        # blocked" notification per spawn, while the parent's mounts still
        # serve the child (paid probe). background=False is the
        # public statement that the sub-agent runs in the foreground and
        # returns its findings to the parent.
        kwargs["agents"] = {
            sub.name: AgentDefinition(
                description=sub.description,
                prompt=sub.prompt,
                tools=list(sub.tools),
                model=sub.model,
                background=False,
            )
            for sub in spec.sub_agents
        }
    if spec.max_turns is not None:
        kwargs["max_turns"] = spec.max_turns
    # Partial-message streaming carries each message's SETTLED usage on the raw
    # message_delta event -- the only per-turn settlement channel that exists on
    # BOTH the Anthropic route and an OpenAI-protocol adapter endpoint (the
    # ResultMessage usage.iterations breakdown is EMPTY on an adapter endpoint).
    # The event adapter's RequestStream reads these StreamEvents.
    kwargs["include_partial_messages"] = True
    # The SDK reads the CLI stream one JSON message at a time and refuses any
    # message longer than max_buffer_size (default 1 MiB). One Read of a 191 KB
    # PNG produced a longer message and ended a run as CLIJSONDecodeError
    #. 64 MiB holds an image tool result and the largest text result
    # the tools can return.
    kwargs["max_buffer_size"] = 64 * 1024 * 1024
    return ClaudeAgentOptions(**kwargs)
