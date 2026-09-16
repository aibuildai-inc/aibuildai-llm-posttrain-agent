"""YAML-only config loading for aibuildai.

CLI surface: aibuildai run <yaml_path>       [run the pipeline] aibuildai memorize <yaml_path>  [run the memorize command] aibuildai -V | --version        [print version, exit]

The YAML path is positional and required under the subcommand. There are no ``--`` flags for individual fields and no env-var field source: ``AgentConfig`` is a plain pydantic model, populated from the YAML mapping alone via ``model_validate``, falling back to defaults declared on the class. The previous ``--config PATH`` + ``AIBUILDAI_*`` field-override chain (and pydantic-settings) is gone.

``AIBUILDAI_IN_SERVICE``, ``AIBUILDAI_RUN_ID``, and ``AIBUILDAI_RESUME_RESULT_FILE`` are internal process-launch markers. They are not config field sources.
"""
from __future__ import annotations

import argparse
import re
import reprlib
import sys
from pathlib import Path
from typing import NoReturn

import yaml
from pydantic import ValidationError

from config import AgentConfig
from aibuildai_version import version_string



def format_validation_error(exc: ValidationError) -> str:
    """Render a pydantic ``ValidationError`` as a readable, actionable message.

    Per error: the dotted field path and what was expected (missing / wrong type / unknown key). No pydantic internals, no traceback, no URLs.

    ``cli.py`` catches the ``ValidationError`` at the config-load boundary (where ``load_config`` is called) and prints this rendering instead of letting the raw pydantic traceback reach the terminal. ``load_config`` itself still raises the unwrapped ``ValidationError`` for programmatic callers.
    """
    errors = exc.errors()
    lines = [f"invalid config -- {len(errors)} problem(s) found:"]
    for err in errors:
        dotted = ".".join(str(p) for p in err["loc"]) or "<root>"
        etype = err["type"]
        if etype == "missing":
            lines.append(f"  - {dotted}: required field is missing")
        elif etype == "extra_forbidden":
            # No rename guidance, ever: when an old key is rejected, the plain
            # unknown-key line is the whole answer: hint tables grow by one
            # entry per rename, without bound.
            lines.append(f"  - {dotted}: unknown key (not a valid config field)")
        else:
            input_value = err.get("input")
            input_note = ""
            if input_value is None or isinstance(input_value, (str, int, float, bool)):
                input_note = f" (got {reprlib.repr(input_value)})"
            lines.append(
                f"  - {dotted}: {err['msg']}"
                f"{input_note}")
    return "\n".join(lines)


_INVALID_CHOICE_RE = re.compile(r"invalid choice: '([^']*)'")
# pydantic renders a Literal mismatch as "Input should be 'a' or 'b'".
_INPUT_SHOULD_BE = re.compile(r"Input should be ((?:'[^']*'(?:, | or )?)+)$")


def _missing_run_hint(message: str) -> str | None:
    """Return a hint pointing at ``aibuildai run <path>`` when argparse rejected a command token shaped like a config path (the common ``aibuildai config.yaml`` slip that drops the ``run`` subcommand); else None."""
    match = _INVALID_CHOICE_RE.search(message)
    if match is None:
        return None
    token = match.group(1)
    looks_like_config = (
        token.endswith((".yaml", ".yml")) or "/" in token or Path(token).exists()
    )
    if not looks_like_config:
        return None
    return (
        f"'{token}' looks like a config file, not a command. "
        "To run the pipeline, add the 'run' subcommand:\n"
        f"    aibuildai run {token}"
    )


class _CommandParser(argparse.ArgumentParser):
    """ArgumentParser that appends a ``run``-subcommand hint when the first token is a config path passed without ``run``. Every process parses through :func:`parse_args`, so this override covers every command."""

    def error(self, message: str) -> NoReturn:
        hint = _missing_run_hint(message)
        if hint is not None:
            self.print_usage(sys.stderr)
            self.exit(2, f"{self.prog}: error: {message}\n\n{hint}\n")
        super().error(message)


def _bootstrap_parser() -> argparse.ArgumentParser:
    """Subcommand parser: ``run <yaml>`` | ``memorize <yaml>``, plus -V/--version.

    Any other token (e.g. ``--task-name spoof``) is unknown and argparse raises SystemExit; there is deliberately no field-level CLI override path.
    """
    p = _CommandParser(
        prog="aibuildai",
        description="Autonomous ML pipeline: design, train, and evaluate "
                    "models from a YAML task config.",
    )
    p.add_argument("-V", "--version", action="version",
                   version=version_string())
    sub = p.add_subparsers(dest="command", required=True)
    command_help = {
        "run": "Run the ML pipeline on the task described by the YAML config",
        "memorize": "Summarize the task's past runs into your editable user "
                    "memory (memory.user_dir)",
    }
    for command in ("run", "memorize"):
        sp = sub.add_parser(command, help=command_help[command])
        sp.add_argument("yaml_path", type=str,
                        help="Path to YAML config file (required)")
    # write-paper runs the WRITEUP stage on a finished run whatever writer.enable
    # says.
    wp = sub.add_parser(
        "write-paper",
        help="Write a NeurIPS paper describing a finished run and compile it "
             "to a PDF")
    wp.add_argument("run_dir", type=str,
                    help="Path to the run directory (the agent-visible run/ root)")
    wp.add_argument("--review", action="store_true",
                    help="Also run the writer_review faithfulness gate "
                         "(re-drafts when a metric/claim does not match the "
                         "artifacts)")
    sub.add_parser("config",
                   help="Print a starter YAML config with every field and its "
                        "default to stdout")
    sub.add_parser("setup",
                   help="Pre-warm the AIBuildAI-owned tools (run does this "
                        "automatically; useful before an expensive run)")
    return p




def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse one process command line once."""
    if argv is None:
        argv = sys.argv[1:]
    return _bootstrap_parser().parse_args(argv)


def load_config(args: argparse.Namespace) -> tuple[str, AgentConfig]:
    """Load the parsed YAML path and return (command, AgentConfig).

    command is one of "run" or "memorize".
    """
    yaml_path = Path(args.yaml_path).resolve()
    if not yaml_path.is_file():
        raise FileNotFoundError(f"config file not found: {yaml_path}")
    with open(yaml_path) as f:
        loaded = yaml.safe_load(f)
    if loaded is not None and not isinstance(loaded, dict):
        raise ValueError(
            f"{yaml_path}: top-level must be a mapping, got {type(loaded).__name__}"
        )
    raw = loaded or {}

    run = raw.get("run")
    if isinstance(run, dict):
        generated = next(
            (
                name
                for name in ("run_id", "kubernetes_start_mode")
                if name in run
            ),
            None,
        )
        if generated is not None:
            raise ValueError(
                f"run.{generated} is generated by aibuildai and must not be set in YAML"
            )

    # YAML is the only field source — no CLI, no env override. The nested
    # mapping maps directly onto the sub-models. ``extra="forbid"`` on
    # AgentConfig rejects any un-migrated legacy flat key with a clear
    # "extra inputs are not permitted" error. The raw pydantic
    # ``ValidationError`` propagates unwrapped (a documented contract); cli.py
    # catches it at the load boundary and renders it readably via
    # ``format_validation_error`` so a config mistake never dumps a pydantic
    # traceback onto the terminal.
    return args.command, AgentConfig.model_validate(raw)
