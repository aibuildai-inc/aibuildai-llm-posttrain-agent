"""Build backend hooks without an engine import."""

from __future__ import annotations

from pathlib import Path

import logging
import re
from typing import Callable

from infra.backends.spec import HookSpec
from infra.host_resource.command_launcher import (
    command_self_enroll_launcher,
    reap_command_cgroup,
)
from infra.util.paths import canonical_path, is_within

logger = logging.getLogger(__name__)


_DANGEROUS_FILE_OP_RE = re.compile(
    r"\b(?:rm|mv|cp|dd|shred|truncate|nano|emacs)\b"
    r"|"
    r"\bsed\s+-i\b"
    r"|"
    r"\bvim?\b"
)

_UNSAFE_KILL_RE = re.compile(
    r"\bkillall\b"
    r"|\bpkill\b"
    r"|\bxargs\b.*\bkill\b"
    r"|\bkill\s+(?!-?\d)"
)
_TEE_RE = re.compile(r"\btee\b")
_REDIRECT_RE = re.compile(r">{1,2}")
_SLEEP_RE = re.compile(r"\bsleep\s+(\d+)")
_HARMLESS_REDIRECT_RE = re.compile(
    r"[012]?&?>>?\s*/dev/null|[012]?>&[012-]|[012]?<&[012-]"
)


_MAX_SLEEP_SECS = 3600  # 1 hour — long enough for coarse training-progress polling
# PM 6274696ca rationale: background training runs are typically 30 minutes to
# several hours, so a tight cap (e.g. 30s) forces the coder to spend an LLM turn
# on every poll — that's where most of the per-coder cost comes from. Allowing
# up to one hour lets the coder check a 4-hour run ~4 times instead of ~480 times.
# The cap is still in place to catch obvious typos like `sleep 99999`.




# Claude-Code 2.x Agent/Task dispatch params that break the product's
# synchronous-inline sub-agent contract. The product's consolidation requires
# the Task tool result to BE the sub-agent's final reply and the sub-agent's
# messages to stream back via parent_tool_use_id. isolation="worktree" dies at
# init when the run home is not a git repo; run_in_background returns a
# launch receipt instead of the reply and detaches the message stream. model is
# owned by the AgentDefinition built from product config/sub-agent frontmatter,
# not by the parent model's tool input. These are model-visible schema params the
# product never honors.
_FORBIDDEN_DISPATCH_PARAMS = ("isolation", "run_in_background", "model")










def make_protect_bash_operations_spec(data_dir: str, output_base_dir: str) -> HookSpec:
    """PreToolUse hook on Bash that blocks dangerous file operations on the data directory and file output outside the output directory."""

    async def protect_bash_operations(
        input_data: dict, tool_use_id: "str | None", context: object
    ):
        command = input_data["tool_input"].get("command", "")
        data_dir_normalized = str(canonical_path(data_dir)).rstrip("/") + "/"
        if (
            _DANGEROUS_FILE_OP_RE.search(command)
            and data_dir_normalized.rstrip("/") in command
        ):
            return {
                "hookSpecificOutput": {
                    "hookEventName": input_data["hook_event_name"],
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Cannot perform file operations on protected data directory via bash. Use Read tool for reading. Protected: {data_dir}",
                }
            }
        allowed_dir = output_base_dir.rstrip("/") + "/"
        cmd_stripped = _HARMLESS_REDIRECT_RE.sub("", command)
        has_redirect = ("<<" not in cmd_stripped) and _REDIRECT_RE.search(cmd_stripped)
        has_tee = _TEE_RE.search(cmd_stripped)
        if has_redirect or has_tee:
            if allowed_dir not in command:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": input_data["hook_event_name"],
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"File output via bash redirection must target {allowed_dir}. Use Write/Edit tools instead.",
                    }
                }
        return {}


    return HookSpec(callback=protect_bash_operations, tool_name_glob="Bash")


def make_block_long_sleep_spec() -> HookSpec:
    async def block_long_sleep(
        input_data: dict, tool_use_id: "str | None", context: object
    ):
        """Block sleep commands longer than ``_MAX_SLEEP_SECS`` (1 hour)."""
        command = input_data["tool_input"].get("command", "")
        for match in _SLEEP_RE.finditer(command):
            seconds = int(match.group(1))
            if seconds > _MAX_SLEEP_SECS:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": input_data["hook_event_name"],
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"sleep {seconds} blocked (max {_MAX_SLEEP_SECS}s). "
                            "For long waits, use TaskOutput(block=true) which "
                            "wakes when output appears, or pick a sleep duration "
                            f"of at most {_MAX_SLEEP_SECS}s."
                        ),
                    }
                }
        return {}

    return HookSpec(callback=block_long_sleep, tool_name_glob="Bash")


def make_block_unsafe_kill_spec() -> HookSpec:
    async def block_unsafe_kill(
        input_data: dict, tool_use_id: "str | None", context: object
    ):
        """Only allow `kill <pid>` with a numeric PID. Block killall, pkill, and pattern-based kills."""
        command = input_data["tool_input"].get("command", "")
        if _UNSAFE_KILL_RE.search(command):
            return {
                "hookSpecificOutput": {
                    "hookEventName": input_data["hook_event_name"],
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Only `kill <numeric_pid>` is allowed. killall, pkill, and pattern-based kill commands are prohibited.",
                }
            }
        return {}

    return HookSpec(callback=block_unsafe_kill, tool_name_glob="Bash")




def make_command_cgroup_reaper_spec(commands_cgroup: str) -> HookSpec:
    """PostToolUse hook on Bash that removes the per-command cgroup once the command has exited, so a long run does not accumulate one empty cgroup per Bash tool call.

    ``commands_cgroup`` is the HOST path -- this hook runs in the ORCHESTRATOR, outside the sandbox, which is the only place the removal is possible: a command cannot rmdir the cgroup it is a member of, and cannot move itself out into ``commands/`` either (an inner cgroup with controllers enabled refuses a process with EBUSY). Note this is the host path, NOT the in-sandbox ``commands_root()`` the enroll hook splices."""

    async def command_cgroup_reaper(
        input_data: dict, tool_use_id: "str | None", context: object
    ):
        if tool_use_id:
            reap_command_cgroup(Path(commands_cgroup), tool_use_id)
        return {}

    return HookSpec(callback=command_cgroup_reaper, tool_name_glob="Bash")


def make_kaggle_timeout_spec(remaining_s: Callable[[], float]) -> HookSpec:
    """Give each Kaggle CLI launch the current relative limit."""
    return _make_timeout_spec(remaining_s, "mcp__kaggle__run", "timeout_s", 1.0, False)


def make_command_timeout_spec(remaining_s: Callable[[], float]) -> HookSpec:
    """Cap each Bash command with the current relative limit."""
    return _make_timeout_spec(remaining_s, "Bash", "timeout", 1000.0, True)


def _make_timeout_spec(
    remaining_s: Callable[[], float],
    tool: str,
    field: str,
    scale: float,
    cap: bool,
) -> HookSpec:
    async def set_timeout(
        input_data: dict, tool_use_id: "str | None", context: object
    ) -> dict:
        del tool_use_id, context
        tool_input = input_data["tool_input"]
        live = remaining_s() * scale
        if live <= 0:
            raise TimeoutError
        requested = tool_input.get(field)
        value = min(requested, live) if cap and requested is not None else live
        return {
            "hookSpecificOutput": {
                "hookEventName": input_data["hook_event_name"],
                "updatedInput": {
                    **tool_input,
                    field: value if scale == 1.0 else int(value),
                },
            }
        }

    return HookSpec(
        callback=set_timeout,
        tool_name_glob=tool,
    )


def make_command_self_enroll_spec(commands_root: str) -> HookSpec:
    """PreToolUse hook on Bash that rewrites each command to self-enroll into its own per-command cgroup. Wired whenever the unit's Confinement carries a commands cgroup (Confinement.delegates_commands()), sandboxed or not. ``tool_use_id`` keys the cgroup name (``command_self_enroll_launcher``), and the SDK always supplies one for a PreToolUse hook -- a missing id is a contract break, so this fails loud rather than falling back to a `$$`-derived tag.

    ``commands_root`` is where the command creates that cgroup, in the coordinates the command itself will see (``Confinement.commands_root()``). It comes from the unit's Confinement and never from a constant here: the same hook serves the sandboxed and unsandboxed launch, which see the same cgroup at different paths."""

    async def command_self_enroll(
        input_data: dict, tool_use_id: "str | None", context: object
    ):
        command = input_data["tool_input"].get("command", "")
        if not tool_use_id:
            raise AssertionError(
                f"PreToolUse Bash hook requires a tool_use_id (SDK contract); got {tool_use_id!r}"
            )
        return {
            "hookSpecificOutput": {
                "hookEventName": input_data["hook_event_name"],
                "updatedInput": {
                    **input_data["tool_input"],
                    "command": command_self_enroll_launcher(
                        command, tool_use_id, commands_root
                    ),
                },
            }
        }


    return HookSpec(callback=command_self_enroll, tool_name_glob="Bash")


def make_force_sync_dispatch_spec() -> HookSpec:
    """PreToolUse hook on the Task tool stripping isolation/run_in_background so sub-agent dispatch stays synchronous-inline.

    Claude-CLI-specific by construction: it strips the Claude CLI Task tool's isolation/run_in_background params, which only that tool exposes."""

    async def force_sync_inline_dispatch(
        input_data: dict, tool_use_id: "str | None", context: object
    ):
        """Strip Task dispatch params that would break synchronous-inline dispatch."""
        tool_input = input_data["tool_input"]
        if not any(k in tool_input for k in _FORBIDDEN_DISPATCH_PARAMS):
            return {}
        updated = {
            k: v for k, v in tool_input.items() if k not in _FORBIDDEN_DISPATCH_PARAMS
        }
        logger.warning(
            "[force_sync_inline_dispatch] stripped %s from sub-agent dispatch",
            sorted(set(tool_input) - set(updated)),
        )
        # 'updatedInput' is the SDK-typed field (claude_agent_sdk types.py
        # HookSpecificOutput).
        return {
            "hookSpecificOutput": {
                "hookEventName": input_data["hook_event_name"],
                "updatedInput": updated,
            }
        }

    return HookSpec(callback=force_sync_inline_dispatch, tool_name_glob="Task")






def _write_tool_path(tool_input: dict) -> str:
    file_path = tool_input.get("file_path")
    if file_path is None:
        file_path = tool_input.get("notebook_path", "")
    if not isinstance(file_path, str):
        return ""
    return file_path


def make_restrict_write_directory_spec(cwd: str, *allowed_dirs: str) -> HookSpec:
    """PreToolUse hook on the write tools that refuses a write outside the designated output directories."""
    normalized = [d.rstrip("/") + "/" for d in allowed_dirs]

    async def restrict_write_directory(
        input_data: dict, tool_use_id: "str | None", context: object
    ):
        file_path = _write_tool_path(input_data["tool_input"])
        resolved_path = Path(file_path)
        if not resolved_path.is_absolute():
            resolved_path = Path(cwd) / resolved_path
        if not any(is_within(resolved_path, d) for d in normalized):
            return {
                "hookSpecificOutput": {
                    "hookEventName": input_data["hook_event_name"],
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Files can only be written to {normalized}. Attempted "
                        f"path: {file_path} (resolved to {canonical_path(resolved_path)})"
                    ),
                }
            }
        return {}


    return HookSpec(callback=restrict_write_directory, tool_name_glob="Write|Edit|NotebookEdit")


def make_protect_input_files_spec(data_dir: str) -> HookSpec:
    """PreToolUse hook on the write tools that refuses a modification of any file under ``data_dir``."""

    async def protect_input_files(
        input_data: dict, tool_use_id: "str | None", context: object
    ):
        file_path = _write_tool_path(input_data["tool_input"])
        if file_path and is_within(file_path, data_dir):
            return {
                "hookSpecificOutput": {
                    "hookEventName": input_data["hook_event_name"],
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Cannot modify read-only data directory: {file_path}",
                }
            }
        return {}


    return HookSpec(callback=protect_input_files, tool_name_glob="Write|Edit|NotebookEdit")


# The settled read interface, exactly. A key that NAMES a place is checked as a
# path; a key that says WHERE to look is a path written sideways and is checked
# as one. Grep's ``pattern`` is in neither list on purpose: it is the text Grep
# searches FOR, and checking it as a path refuses a legitimate search for a
# string that starts with a slash. Read.file_path is required by the tool; Glob
# and Grep leave ``path`` out to mean "the session's own directory", which is
# the safe root itself.
_READ_ROOT_PATH_KEYS = {"Read": ("file_path",), "Glob": ("path",), "Grep": ("path",)}
_READ_ROOT_PATTERN_KEYS = {"Glob": ("pattern",), "Grep": ("glob",)}


def make_strict_read_root_spec(*roots: str) -> HookSpec:
    """PreToolUse hook on the read tools that refuses every path outside the allowed roots, the first of which is also the session's cwd.

    A directory handed to the model is not a boundary: it says where to start, and the session may still name any path it can reach. A file inside it can also NAME an outside path in its text -- a saved Agent transcript quotes absolute paths -- so "the files here are safe" does not make the reader safe.

    It is a hook rather than the permission callback on purpose: the CLI treats a read as harmless and auto-approves it, so ``can_use_tool`` is never asked about one.

    Fail-closed on every branch:

    - every key that can carry a path, all of them, not the first one found, and only as a plain string, so a value of some other type counts as no path named at all;
    - every search pattern that says WHERE to look, absolute or relative, because ``../outside/*`` is a path written sideways;
    - and, BEFORE resolving one, any search pattern carrying glob syntax that can hide where it points -- ``..`` or a brace. Resolving such a pattern as a literal path cannot answer what it would reach: a metacharacter can cancel the two dots (``**/../*.txt`` resolves back inside the root), and a brace holds several paths in one string where only the whole string gets resolved, so ``{run-1,/etc}/*`` has no ``..``, is not itself absolute, lands inside the root as one literal -- and then the tool expands it and reads ``/etc``. The trade is that this also refuses a pattern whose two dots or braces are part of a NAME (``v1..2.json``, ``{a,b}.csv``): glob syntax cannot tell the two apart, and the refusal says so rather than claiming the pattern stepped out;
    - a relative path, resolved against the first root rather than whatever directory the product process was started in. That root is also the session's cwd, so a call that names no directory at all already searches inside the boundary and is allowed;
    - a key that is present but is not a non-empty string, because a value of another type is a value this cannot check;
    - and the hook itself raising. A hook that raises is executed anyway by the CLI, which turns one unexpected value into an open door.

    Symlinks resolve before the comparison, so a link inside a root cannot point out of every root, and ``~`` expands before the relative/absolute decision, because the tools expand it too.

    Several roots are allowed because what a session may see is a set of real directories, not one staged copy of them: a path is allowed when it resolves inside ANY of them, and refused when it resolves inside none.

    Which tools the session may call at all is ``AgentSpec.tools``; this answers where they may look.
    """
    if not roots:
        raise ValueError("a strict read boundary needs at least one root")
    allowed = tuple(canonical_path(root) for root in roots)
    resolved = allowed[0]

    def _deny(reason: str, event: str) -> dict:
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    async def strict_read_root(
        input_data: dict, tool_use_id: "str | None", context: object
    ) -> dict:
        event = "PreToolUse"
        try:
            event = input_data.get("hook_event_name") or event
            tool = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input") or {}

            def stated(keys: "tuple[str, ...]") -> "list[str] | None":
                """The values these keys name, or None when one is present but uncheckable."""
                named = []
                for key in keys:
                    if key not in tool_input:
                        continue
                    value = tool_input[key]
                    if not isinstance(value, str) or not value:
                        return None
                    named.append(value)
                return named

            patterns = stated(_READ_ROOT_PATTERN_KEYS.get(tool, ()))
            paths = stated(_READ_ROOT_PATH_KEYS.get(tool, ()))
            if patterns is None or paths is None:
                return _deny(
                    f"{tool} named a place as something other than a non-empty "
                    "string, which cannot be checked against this session's root",
                    event,
                )
            for raw in patterns:
                hidden = next((c for c in ("..", "{", "}") if c in raw), None)
                if hidden is not None:
                    return _deny(
                        f"a search pattern may not contain {hidden!r}: {raw}. Glob "
                        "syntax can hide where such a pattern points -- a brace "
                        "holds several paths in one string, and two dots may be a "
                        "step out of the directory -- so the pattern cannot be "
                        "checked as one path and is refused",
                        event,
                    )
            for raw in (*paths, *patterns):
                # expanduser BEFORE deciding relative: `~` is not an absolute
                # path to pathlib, so it would be joined under the root and
                # allowed -- and Grep and Glob expand it themselves afterwards.
                requested = Path(raw).expanduser()
                target = canonical_path(
                    requested if requested.is_absolute() else resolved / requested
                )
                if not any(is_within(target, root) for root in allowed):
                    return _deny(
                        f"{target} is outside "
                        + ", ".join(str(root) for root in allowed),
                        event,
                    )
            return {}
        except Exception as exc:  # noqa: BLE001 -- a hook that raises is a hook that allows
            return _deny(
                f"this call could not be checked: {type(exc).__name__}: {exc}", event
            )

    return HookSpec(
        callback=strict_read_root,
        tool_name_glob="Read|Glob|Grep",
    )
