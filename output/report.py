"""Render the post-run summary and return its exit code."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from infra.host_resource.oom import detect_run_oom
from output.run_helpers import declared_delivered_paths
from output.run_cost import compute_run_cost
from engine.builtin.aibuildai.io import SearchOutput, SearchResult
from engine.builtin.aibuildai.programs.score import ScoreOutput

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.run_state import RunState
    from engine.paths import RunPaths


def selected_result(run_state: "RunState") -> SearchOutput | None:
    output = run_state.search._run_output()
    return output.output if isinstance(output, SearchResult) else None


def _format_run_cost(run_state: "RunState") -> str:
    """The run's total spend for the exit summary, in the compact ``$X.XX`` form."""
    run_cost = compute_run_cost(run_state)
    marker = " (est)" if run_cost.is_estimate else ""
    return f"${run_cost.headline_total:.2f}{marker}"


_EXIT_CODE = {
    "completed": 0,
    "interrupted": 130,
    "sigterm": 130,
    # Keep an internal bug distinct from expected non-completion.
    "crashed": 70,
    "incomplete": 1,  # expected failure; the saved Search says if it can resume
}


def _run_headline(status: str, error: str | None) -> str:
    return {
        "completed": "Run finished successfully",
        "interrupted": (
            f"Run interrupted: {error}"
            if error is not None
            else "Run interrupted by user (SIGINT / Ctrl-C)"
        ),
        "sigterm": "Run terminated by SIGTERM (kill or systemd)",
        "crashed": f"Run failed: {error}",
        "incomplete": f"Run failed: {error}",
    }[status]


def find_partial_outputs(run_state: "RunState | None") -> list[str]:
    """Return Search result and WorkUnit directories that contain work."""
    if run_state is None:
        return []
    outputs: dict[str, None] = {}

    def add(path_value: str) -> None:
        path = Path(path_value)
        if path.is_dir() and any(path.iterdir()):
            outputs.setdefault(str(path), None)

    # Every scored attempt directory the journal itself names: a generic
    # observer reads the Outputs the run recorded and asks no algorithm what it
    # produced.
    for record in run_state.records.values():
        for action in record.actions:
            output = action.output
            if isinstance(output, ScoreOutput):
                add(output.output_dir)
    for unit in run_state.walk_units():
        for action in unit.record.actions:
            add(unit.artifacts_dir_at(run_state, action.ordinal, max(1, action.attempts)))
    return list(outputs)


class RunReport:
    """Render the post-run summary and return the status exit code."""

    def __init__(
        self,
        config: "AgentConfig",
        run_paths: "RunPaths",
        run_state: "RunState | None",
        *,
        status: str,
        error: str | None = None,
    ):
        if status not in _EXIT_CODE:
            raise ValueError(
                f"unknown status {status!r}; expected one of {list(_EXIT_CODE)}"
            )
        self.config = config
        self.run_paths = run_paths
        self.run_state = run_state
        self.status = status
        self.error = error

    def write(self) -> int:
        """Render the summary to stdout/stderr and return the process exit code."""
        headline = _run_headline(self.status, self.error)

        run_id = self.config.run.require_run_id()

        # Read the kernel record because an OOM kill arrives as SIGTERM.
        _oom = detect_run_oom()

        progress = ""
        if self.run_state is not None:
            result = selected_result(self.run_state)
            if result is not None:
                progress = f"  Selected result: {result.output_dir}\n"

        run_home = self.run_paths.run_home
        deliverable = run_home / "deliverable"

        print()
        print(f"  {headline}")
        # Name every cgroup the kernel says an OOM killed.
        if _oom.oom_killed:
            print()
            print("  OUT-OF-MEMORY: the kernel OOM killer reaped these:")
            for hit in _oom.killed:
                ceiling = (
                    f" (ceiling {hit.memory_max} bytes)"
                    if hit.memory_max is not None
                    else ""
                )
                print(f"    - {hit.cgroup}{ceiling}")
            print("    To fix: raise the matching `resources.work_unit` memory limit.")
            print(
                "    If the run limit was hit, raise `resources.run.memory_max_gb` or"
            )
            print(
                "    lower `search.input.parallel` to reduce how many WorkUnits overlap;"
            )
            print("    also keep the training data pipeline streaming/memmapped rather")
            print("    than loaded into RAM.")
        print()
        print(f"  Run ID:    {run_id}")
        if self.status == "completed":
            print("  To view:   open the local Web Workspace")
        elif self.run_state is not None:
            print(f"  To resume run {run_id}, open the local Web Workspace.")
        print(f"  Output:    {run_home}")
        if self.run_state is not None:
            print(f"  Cost:      {_format_run_cost(self.run_state)}")
        if progress:
            print(progress, end="")
        # Report only paths the Finalizer declared as delivered.
        declared = declared_delivered_paths(self.run_state)
        if declared:
            print(f"  Deliverable: {deliverable}")
            for path in sorted(Path(name).name for name in declared):
                print(f"    {path}")
        else:
            print(f"  Deliverable: not produced ({deliverable})")
        # Keep unfinished WorkUnit output findable without calling it a deliverable.
        if not declared:
            outputs = find_partial_outputs(self.run_state)
            if outputs:
                if self.status == "crashed":
                    print("  Partial work was preserved in the recorded WorkUnit paths:")
                else:
                    print("  Partial work found (not the deliverable):")
                for path in outputs:
                    print(f"    {path}")
        if self.status == "crashed":
            print(file=sys.stderr)
            print(
                "  Please file an issue: "
                "https://github.com/aibuildai-inc/aibuildai-llm-posttrain-agent/issues",
                file=sys.stderr,
            )
        print()

        return _EXIT_CODE[self.status]
