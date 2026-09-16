"""Reusable policy values declared by concrete Agent classes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from engine.builtin.aibuildai.search import AIBuildAISearch

ALL_BUILTIN_TOOLS: list[str] = [
    "Task",
    "Agent",
    "Bash",
    "BashOutput",
    "KillBash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "ExitPlanMode",
    "ListMcpResources",
    "ReadMcpResource",
    "Skill",
    # Base permission for roots that may receive MCP servers. Runtime resolution
    # also adds it when an extension requires it; each child gets an explicit
    # tool list derived from the MCP specs bound to that child.
    "mcp__*",
]

READONLY_TOOLS: list[str] = ["Read", "Glob", "Grep", "Bash", "Skill"]
SETUP_TOOLS: list[str] = READONLY_TOOLS + [
    "Task",
    "BashOutput",
    "KillBash",
    "TodoWrite",
]
# The CITATION-VERIFICATION tools: read-only work plus the four tools a role
# needs to decide whether a cited source, metric, or library capability is real
# -- WebSearch and WebFetch for the open literature, mcp__* to reach a papers
# MCP, and Agent to dispatch a sub-agent that does the lookup. Every role whose
# mandate is "fail an invented source" carries this set; the comment at each
# such policy says which of its checks needs it. With no MCP server configured
# the mcp__* entry is naturally inert.
JUDGE_READONLY_TOOLS: list[str] = READONLY_TOOLS + [
    "WebFetch",
    "WebSearch",
    "mcp__*",
    "Agent",
]
READONLY_TOOLS_WITH_SCRATCH: list[str] = READONLY_TOOLS + ["Write", "Edit"]
JUDGE_TOOLS_WITH_SCRATCH: list[str] = JUDGE_READONLY_TOOLS + ["Write", "Edit"]


class SystemDir(Enum):
    """The host toolchain directories a role's own commands need.

    Launch infrastructure, not application authority: an environment builder
    writes the Conda root, and a role that runs task Python reads the package
    cache. Nothing a Search grants appears here, and nothing here names a
    product, a run resource, or a role."""

    CONDA_ROOT = "conda_root"
    CONDA_PACKAGES = "conda_packages"


class ModelRouter(Protocol):
    """What the engine's Agent launch boundary asks of a package's Router.

    Declared here, on the CONSUMER side, so nothing in the engine imports the
    package that provides it: model routing is a Search package's own concrete
    role, and only the two questions below cross back into engine-owned launch
    policy."""

    async def decide_run_models(self, search: "AIBuildAISearch") -> None:
        """Choose the run-level role-to-model map before any Agent launches."""
        ...

    def model_for(self, *, role: str, owner_path: str | None) -> str | None:
        """The routed model for one role under one owner, or None."""
        ...


@dataclass(frozen=True)
class RolePolicy:
    """The launch contract of one concrete Agent role: its tools, and the host toolchain it needs.

    What the role may READ and WRITE is not here. Filesystem authority belongs to the exact Action a Search starts, which declares it as ``ctx.spawn(..., read=..., write=...)``; a role's own scratch and artifacts are its family's, prepared for every launch without anyone declaring them. This type therefore says only what cannot be decided at a composition site: which tools the model may call, whether its commands run the run's task Python, whether it may sleep, and which host Conda directories its own commands reach.
    """

    tools: tuple[str, ...]
    system_read: tuple[SystemDir, ...] = ()
    system_write: tuple[SystemDir, ...] = ()
    blocks_long_sleep: bool = False
    task_environment: bool = False
