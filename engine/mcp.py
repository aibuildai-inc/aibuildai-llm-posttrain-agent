"""Load and run one AIBuildAI run's configured MCP servers through the official SDK."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Literal, cast

import anyio
import anyio.to_thread
import httpx2
import jsonschema
from mcp.client import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from engine.base import WorkflowBaseModel
from infra.host_resource.cgroup import make_killable_preamble, safe_tag
from infra.host_resource.tree import enter_cgroup_preamble, kill_tree
from infra.kb_service import (
    kb_bearer_header,
    kb_mcp_url,
)

if TYPE_CHECKING:
    from config import AgentConfig
    from infra.host_resource.controller import HostResourceController

logger = logging.getLogger(__name__)

__all__ = ["McpStartupError", "RunMcpClient", "tool_result", "tool_server"]

ToolHandler = Callable[[dict], Awaitable[CallToolResult]]


def tool_result(text: str, is_error: bool = False) -> CallToolResult:
    """One tool's whole answer to the model: what it reads, and whether that tool failed.

    This is the only place a product tool's answer becomes an MCP result, so a caller that
    produces an answer without knowing MCP -- the durable crossing, which must not -- hands its
    text and failure flag to this and stays generic."""
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


def tool_server(name: str, tools: Sequence[tuple[Tool, ToolHandler]]) -> Server:
    """One standard in-process MCP server publishing ``tools``, the whole seam a product tool crosses to reach the model.

    The Claude implementation mounts the Server over the SDK's in-memory transport under ``name``; the model sees each ``Tool`` exactly as declared, and every ``CallToolResult`` a handler returns goes to the model whole. The server owns the one failure semantic: arguments are validated against the tool's own input schema, and invalid input, an unknown tool name, or a handler exception answers as the tool's error result the model reads, never as a protocol error.
    """
    by_name = {tool.name: (tool, handler) for tool, handler in tools}

    async def on_list_tools(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=[tool for tool, _handler in tools])

    async def on_call_tool(
        ctx: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult:
        entry = by_name.get(params.name)
        if entry is None:
            return tool_result(f"Tool '{params.name}' not found", True)
        tool, handler = entry
        arguments = dict(params.arguments or {})
        try:
            jsonschema.validate(instance=arguments, schema=tool.input_schema)
            return await handler(arguments)
        except jsonschema.ValidationError as exc:
            return tool_result(f"Input validation error: {exc.message}", True)
        except Exception as exc:  # noqa: BLE001 - the model reads tool failures
            return tool_result(f"{type(exc).__name__}: {exc}", True)

    return Server(name, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


class McpStartupError(RuntimeError):
    """One or more configured MCP servers failed the startup check."""


def _lookup_builtin_mcp(name: str) -> "dict[str, object] | None":
    """Return the reserved Kb or Kaggle server definition."""
    if name == "kb":
        definition: dict[str, object] = {
            "type": "http",
            "url": kb_mcp_url(),
        }
        headers = kb_bearer_header()
        if headers:
            definition["headers"] = headers
        return definition
    if name == "kaggle":
        return {"type": "stdio", "command": sys.executable, "args": ["-m", "infra.kaggle_submission_mcp"]}
    return None


def resolve_mcp_definitions(
    config: "AgentConfig",
    known_roles: tuple[str, ...],
) -> dict[str, dict]:
    """Resolve typed MCP entries into runtime definitions."""
    from engine.work_unit.agent.plugins import resolve_extension_roles

    servers: dict[str, dict] = {}
    grants: dict[str, tuple[str, ...]] = {}
    for name, entry in config.mcps.items():
        grants[name] = resolve_extension_roles(entry.roles, name, known_roles)
        builtin = _lookup_builtin_mcp(name)
        if entry.type == "reference":
            if builtin is None:
                raise ValueError(f"mcps: unknown built-in reference {name!r}")
            definition = builtin
        else:
            if builtin is not None:
                raise ValueError(
                    f"mcps: built-in {name!r} cannot be replaced by a server definition"
                )
            definition = entry.model_dump(exclude={"roles"}, exclude_none=True)
        servers[name] = _expand_server_environment(definition)
    if config.submitter_on and not any(
        name == "kaggle" and "submitter" in roles for name, roles in grants.items()
    ):
        raise ValueError(
            "submission.max_versions above 1 needs mcps.kaggle granted to "
            "the submitter role"
        )
    if "kaggle" in servers:
        kaggle = dict(servers["kaggle"])
        kaggle["args"] = [
            *kaggle["args"],
            "--max-versions",
            str(config.submission.max_versions),
        ]
        servers["kaggle"] = kaggle
    return servers


def mcp_servers_for_role(
    config: "AgentConfig",
    role: str,
    known_roles: tuple[str, ...],
) -> set[str]:
    """Return the configured MCP names granted to one role."""
    from engine.work_unit.agent.plugins import resolve_extension_roles

    return {
        name
        for name, entry in config.mcps.items()
        if role in resolve_extension_roles(entry.roles, name, known_roles)
    }


def _expand_server_environment(raw: dict[str, object]) -> dict:
    """Expand environment references in one structurally valid MCP definition."""
    server = dict(raw)
    for field in ("command", "url"):
        if value := server.get(field):
            server[field] = os.path.expandvars(cast(str, value))
    for field in ("env", "headers"):
        if values := server.get(field):
            server[field] = {
                key: os.path.expandvars(value)
                for key, value in cast(dict[str, str], values).items()
            }
    return server


class McpServerStatus(WorkflowBaseModel):
    """The persisted startup result for one configured MCP server."""

    type: str
    url: str
    status: Literal["ok", "unreachable"]
    error: str | None = None


class RunMcpClient:
    """Own the configured MCP servers for one run process epoch.

    Each MCP 2 ``Client`` owns its connection's protocol session and transport for exactly the ``async with`` it lives in. This class owns which servers exist, the run Clock, local-server cgroups, Kaggle version accounting, and the persisted startup status: the configured servers' clients live on one exit stack from startup to shutdown, and a Kb call opens its own client for that call alone.
    """

    def __init__(self, mcp_servers: dict[str, dict]) -> None:
        self._servers = mcp_servers
        self._remaining_s: Callable[[], float] | None = None
        self._record_kaggle_version: Callable[[], None] | None = None
        self._stack = AsyncExitStack()
        self._clients: dict[str, Client] = {}
        self._cgroups: dict[str, Path] = {}
        self._host: HostResourceController | None = None
        self._entered = False
        self._active = False
        self.validated_mcps: dict[str, McpServerStatus] = {}
        self._tools_by_server: dict[str, tuple[Tool, ...]] = {}
        self.kaggle_versions_used = 0

    def _bind_host_resources(self, host: HostResourceController) -> None:
        """Put each local MCP server in one exact run cgroup."""
        if self._entered:
            raise AssertionError("MCP host resources must be bound before startup")
        self._host = host

    def _bind_clock(self, remaining_s: Callable[[], float]) -> None:
        """Read all MCP launch limits from the run's one Clock."""
        if self._entered or self._remaining_s is not None:
            raise AssertionError("MCP Clock must be bound once before startup")
        self._remaining_s = remaining_s

    def _bind_kaggle_version_recorder(self, record: Callable[[], None]) -> None:
        """Save each successful Kaggle push in the run journal."""
        if self._entered or self._record_kaggle_version is not None:
            raise AssertionError("Kaggle version recorder must be bound once")
        self._record_kaggle_version = record

    def _clock_s(self) -> float:
        if self._remaining_s is None:
            raise AssertionError("MCP Clock is not bound")
        remaining_s = self._remaining_s()
        if remaining_s <= 0:
            raise TimeoutError("MCP work cannot start after the Clock expires")
        return remaining_s

    def servers_for_role(
        self,
        config: "AgentConfig",
        role: str,
        known_roles: tuple[str, ...],
    ) -> list[Server]:
        """Return the Clock-bound tools granted to one Agent WorkUnit, one standard in-process server per configured server."""
        if not self._active:
            raise RuntimeError("MCP servers are not ready before client startup")
        allowed = mcp_servers_for_role(config, role, known_roles)
        return [
            tool_server(
                name,
                [(tool, self._handler(name, tool)) for tool in self._tools_by_server[name]],
            )
            for name in self._servers
            if name in allowed
        ]

    def _handler(
        self,
        server_name: str,
        tool: Tool,
    ) -> ToolHandler:
        """Build one tool call that returns to this run's client for every invocation."""

        async def call(arguments: dict) -> CallToolResult:
            # A transport or protocol failure propagates as it is; the serving
            # ``tool_server`` turns it into the error result the model reads,
            # so no second taxonomy is built here.
            async with asyncio.timeout(self._clock_s()), AsyncExitStack() as stack:
                if server_name == "kb":
                    client = await self._enter_client(
                        stack, "kb", dict(self._servers["kb"])
                    )
                else:
                    client = self._clients[server_name]
                result = await client.call_tool(
                    tool.name, arguments, read_timeout_seconds=self._clock_s()
                )
            if (
                server_name == "kaggle"
                and tool.name == "run"
                and not result.is_error
                and "versions_used" in (result.structured_content or {})
            ):
                if self._record_kaggle_version is None:
                    raise AssertionError("Kaggle version recorder is not bound")
                self._record_kaggle_version()
            return result

        return call

    async def __aenter__(self) -> "RunMcpClient":
        if self._entered:
            raise AssertionError("MCP client was already entered")
        self._clock_s()
        self._entered = True
        await self._stack.__aenter__()
        try:
            for name in self._servers:
                await self._connect_one(name)
            self.validated_mcps = dict(sorted(self.validated_mcps.items()))
            failed = {
                name: status
                for name, status in self.validated_mcps.items()
                if status.status != "ok"
            }
            if failed:
                details = "; ".join(
                    f"{name}: {status.status} ({status.error})"
                    for name, status in failed.items()
                )
                raise McpStartupError(
                    f"configured MCP server startup failed: {details}"
                )
        except BaseException as startup_error:
            try:
                await self._stack.aclose()
            except Exception as cleanup_error:  # noqa: BLE001 - preserve startup error
                startup_error.add_note(f"MCP cleanup failed: {cleanup_error}")
            raise
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._active = False
        self._clients.clear()
        try:
            await self._stack.aclose()
        except Exception as cleanup_error:  # noqa: BLE001 - keep the body result
            if exc is None:
                raise
            exc.add_note(f"MCP cleanup failed: {cleanup_error}")
            logger.error(  # noqa: TRY400 - the body error keeps its traceback
                "MCP cleanup failed while another error was active: %s", cleanup_error
            )

    @property
    def active(self) -> bool:
        """Return whether this client is ready inside its live scope."""
        return self._active

    async def _connect_one(self, name: str) -> None:
        server = dict(self._servers[name])
        server_type = server["type"]
        url = "" if server_type == "stdio" else self._servers[name]["url"]
        try:
            remaining_s = self._clock_s()
        except TimeoutError:
            self.validated_mcps[name] = McpServerStatus(
                type=server_type,
                url=url,
                status="unreachable",
                error="the Clock expired before MCP initialization",
            )
            return
        try:
            async with asyncio.timeout(remaining_s):
                client = await self._enter_client(self._stack, name, server)
                tools: list[Tool] = []
                cursor: str | None = None
                while True:
                    with anyio.fail_after(self._clock_s()):
                        result = await client.list_tools(cursor=cursor)
                    tools.extend(result.tools)
                    if result.next_cursor is None:
                        break
                    cursor = result.next_cursor
                if not tools:
                    raise ValueError(f"MCP server {name!r} publishes no tools")
                names = [tool.name for tool in tools]
                if len(names) != len(set(names)):
                    raise ValueError(
                        f"MCP server {name!r} publishes duplicate tool names"
                    )
        except Exception as exc:  # noqa: BLE001 - startup records every failure
            # One typed startup failure. The SDK's own text names the cause;
            # a transport wraps it in an ExceptionGroup, whose first leaf is
            # the fault.
            leaf: BaseException = exc
            while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
                leaf = leaf.exceptions[0]
            error = f"{type(leaf).__name__}: {leaf}"
            self.validated_mcps[name] = McpServerStatus(
                type=server_type, url=url, status="unreachable", error=error
            )
            logger.warning("MCP server '%s' is not reachable: %s", name, error)
            return
        self._clients[name] = client
        self._tools_by_server[name] = tuple(tools)
        self.validated_mcps[name] = McpServerStatus(type=server_type, url=url, status="ok")

    async def _enter_client(
        self,
        stack: AsyncExitStack,
        name: str,
        server: dict,
    ) -> Client:
        """Enter one MCP 2 Client on ``stack`` over the official transport the definition names.

        Whatever the transport needs beside it (a local server's cgroup and stderr file, a Streamable HTTP client) enters the same stack first, so it outlives the Client and closes right after it.
        """
        timeout_s = self._clock_s()
        server_type = server["type"]
        if server_type == "stdio":
            args = list(server.get("args") or [])
            if name == "kaggle":
                args += ["--versions-used", str(self.kaggle_versions_used)]
            command = server["command"]
            if self._host is not None:
                local_names = sorted(
                    server_name
                    for server_name, definition in self._servers.items()
                    if definition["type"] == "stdio"
                )
                number = local_names.index(name) + 1
                safe_name = safe_tag(name.encode("ascii", "replace").decode())[:80]
                cgroup = self._host.framework_subprocess_cgroup(
                    f"mcp-{number:04d}-{safe_name}"
                )
                self._cgroups[name] = cgroup
                stack.push_async_callback(self._kill_cgroup, name)
                preamble = (
                    make_killable_preamble() + "\n" + enter_cgroup_preamble(cgroup)
                )
                args = [
                    "-c",
                    f'{preamble}\nexec "$@"',
                    f"mcp-{name}",
                    command,
                    *args,
                ]
                command = "/bin/sh"
            transport = stdio_client(
                StdioServerParameters(
                    command=command,
                    args=args,
                    env={**os.environ, **(server.get("env") or {})},
                ),
                errlog=sys.stderr,
            )
        elif server_type == "sse":
            transport = sse_client(
                server["url"],
                headers=server.get("headers"),
                timeout=timeout_s,
                sse_read_timeout=timeout_s,
            )
        else:
            # The declared headers (the Kb grant, a user's own) travel only on
            # a caller-supplied HTTP client.
            http_client = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers=server.get("headers"),
                    timeout=httpx2.Timeout(timeout_s, read=timeout_s),
                    follow_redirects=True,
                )
            )
            transport = streamable_http_client(server["url"], http_client=http_client)
        # The product froze each server's tool set at startup; the SDK's own
        # response cache would be a second, hidden authority for it.
        return await stack.enter_async_context(
            Client(transport, read_timeout_seconds=timeout_s, mode="auto", cache=None)
        )

    async def _kill_cgroup(self, name: str) -> None:
        await anyio.to_thread.run_sync(kill_tree, self._cgroups.pop(name))
