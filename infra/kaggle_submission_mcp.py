"""The built-in Kaggle MCP: a counted relay for the ``kaggle`` CLI.

The agent sends the command it would type. Only ``kernels push`` is counted, because one push is one notebook version, and a competition caps how many versions a team may spend.
"""

from __future__ import annotations

import argparse
import os
import sys

from mcp.server.mcpserver import MCPServer
from infra.process.command import run_captured


SERVER = MCPServer("kaggle")
_MAX_VERSIONS = 0
_VERSIONS_USED = 0
# Kaggle says a push made a version by naming its number. It reports a refused
# push on stdout with exit code 0, so the exit code cannot tell the two apart.
_PUSHED = "successfully pushed"
# One push wears four names, because the CLI gives ``kernels`` the alias ``k``
# and ``push`` the alias ``update``, and it takes options such as ``-W`` before
# the command. Every spelling makes a version, so every spelling is counted.
_PUSH_GROUPS = ("kernels", "k")
_PUSH_COMMANDS = ("push", "update")


# The tool description is an explicit literal, not the docstring: the release
# build Cython-compiles this module with docstrings stripped, and the server's
# docstring fallback would then hand the model an empty description.
@SERVER.tool(description="Run one kaggle CLI command and return what it printed.")
async def run(
    argv: list[str], timeout_s: float | None = None
) -> dict[str, object]:
    """Run one ``kaggle`` CLI command and return what it printed.

    The dict is the tool's structured content. ``versions_used`` appears only when a push really spent a version; the run reads that key to count the spend, so nothing else may carry it.
    """
    global _VERSIONS_USED
    named = [word for word in argv if not word.startswith("-")][:2]
    pushing = (
        len(named) == 2 and named[0] in _PUSH_GROUPS and named[1] in _PUSH_COMMANDS
    )
    if pushing and _VERSIONS_USED >= _MAX_VERSIONS:
        return {"refused": f"all {_MAX_VERSIONS} notebook version(s) are used"}
    if timeout_s is None:
        raise AssertionError("Kaggle command has no Clock-derived timeout")
    done = await run_captured(["kaggle", *argv], timeout_s)
    result: dict[str, object] = {
        "stdout": done.stdout,
        "stderr": done.stderr,
        "returncode": done.returncode,
    }
    if pushing and _PUSHED in done.stdout:
        _VERSIONS_USED += 1
        result["versions_used"] = _VERSIONS_USED
        result["versions_remaining"] = _MAX_VERSIONS - _VERSIONS_USED
    return result


def main() -> None:
    global _MAX_VERSIONS, _VERSIONS_USED
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-versions", type=int, required=True)
    parser.add_argument("--versions-used", type=int, required=True)
    args = parser.parse_args()
    _MAX_VERSIONS = args.max_versions
    _VERSIONS_USED = args.versions_used
    SERVER.run()
    if getattr(sys, "frozen", False):
        sys.stderr.flush()
        os._exit(0)


if __name__ == "__main__":
    main()
