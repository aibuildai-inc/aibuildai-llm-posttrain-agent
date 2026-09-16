"""CLI entry point for the RunState beam-search ML workflow.

Argument parsing and YAML loading live in :mod:`startup.config_loader`. This module is the composition root: a thin shell that wires the concrete services to engine, wraps the whole process in :class:`infra.process.process_lifecycle.ProcessLifecycle` (re-exec into a systemd service and process cleanup), and dispatches the parsed command to a :class:`Command`. Commands return a :class:`CommandOutcome`; the root finalizes it (rendering any summary) after the ``ProcessLifecycle`` block exits.
"""

import abc
import asyncio
import logging
import os
import signal
import sys
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from config import (
    StartupUserInputError,
    WorkUnitTimeConfigError,
)
from engine.durable_execution import latest_run_state, run_store
# RunPinMismatch lives in engine.event.store (not bootstrap, which is
# import-heavy) precisely so it is cheap to import at module top here.
from engine.event.store import (
    RunPinMismatch,
)
from engine.failure import Failure
from engine.mcp import (
    McpStartupError,
    RunMcpClient,
    resolve_mcp_definitions,
)
from engine.paths import (
    RunPaths,
    RunTimestamp,
)
from engine.run_state import RunState
from engine.run_query import read_run_head
from infra.process.env_scrub import scrub_claude_feature_env
from infra.process.process_lifecycle import ProcessLifecycle
from infra.postgresql import PostgreSQLUnavailable
from infra.host_resource.cgroup import (
    HostCapabilityUnavailable,
    kubernetes_start_mode,
    run_unit_name,
)
from output.report import RunReport
from startup import config_loader, run_config_store, run_launch
from startup.config_loader import format_validation_error, load_config
from startup.example_yaml import example_yaml_text
from startup.logging_config import configure_logging
from startup.model_endpoint import model_endpoint
from startup.resource_materializer import materialize_to_tmpfs

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from config import AgentConfig
    from engine.event.store import SearchApplication
    from engine.builtin.aibuildai.search import AIBuildAISearch
    from engine.work_unit.agent.policy import ModelRouter


logger = logging.getLogger(__name__)


def _check_task_folder_exists(config: "AgentConfig") -> None:
    """Reject configs whose ``run.data_root`` is not a real directory.

    ``run.data_root`` IS the task folder now: the run reads it whole and expects no layout inside it, so the only thing to check here is that the folder is there. Without this sub-second filesystem check, a typo in the path burns the whole SETUP budget on a folder that does not exist (``conda create``, ``pip install torch``, a large download) and then crashes with a generic ``RuntimeError: Setup failed: failed(timeout)`` that never names the underlying cause. We catch it here before any LLM call, so the user learns of the typo for free rather than for a paid 2-minute SETUP timeout.
    """
    if os.path.isdir(config.task_folder):
        return

    raise ValueError(
        "\n".join(
            [
                # No `config error:` prefix here: the cli() boundary already prepends a
                # single `error: ` when it renders this message, so embedding another
                # prefix produced a doubled `error: config error: ...`.
                f"no task folder found at {config.task_folder!r}",
                "  run.data_root must name the task folder itself: the directory "
                "holding what the task means",
                "  plus every material it needs. The run reads that folder whole and "
                "expects no layout inside it.",
                f"  run.data_root:   {config.run.data_root}",
            ]
        )
    )


def run_memorize_command(config: "AgentConfig") -> None:
    """Composition root for the memorize command.

    Resolves the memory store here. Does NOT touch resource limits, dataset checks, startup checks, router, resume, run-dir-lock, or the loop — those are run-command concerns only.
    """
    from memory.memorize import run_memorize
    from memory.store import MemoryStore

    # The runs to summarize live under run.playground_root, which every config
    # already declares as this run's output root. No second field names it.
    store = MemoryStore.user(config.memory.user_dir)
    result = asyncio.run(
        run_memorize(
            task_name=config.run.task_name,
            playground_root=config.run.playground_root,
            store=store,
            model=config.llm.default.model,
        )
    )
    print(
        f"[memorize] read {result['runs_read']} run(s) for "
        f"'{config.run.task_name}' -> {result['task_memory_path']}"
    )


def prepare_run(
    config: "AgentConfig",
    *,
    resume_run_timestamp: "str | None" = None,
) -> tuple[
    "type[AIBuildAISearch]",
    RunPaths,
    "ModelRouter | None",
    "RunMcpClient",
]:
    """Run-command startup sequence: everything between config-load and the loop.

    Validates required fields + the dataset dir, runs fail-closed startup checks, builds the Router policy, resolves MCP and plugin inputs, creates the run home, and installs logging. Returns the checked AIBuildAISearch class and the live owners it will receive.
    """
    # Required fields are enforced at AgentConfig() construction (run.* /
    # llm.default.model have no defaults). This loop guards against an
    # explicitly-empty value.
    if not config.run.task_name:
        raise ValueError("YAML missing required field: run.task_name")
    if not config.run.data_root:
        raise ValueError("YAML missing required field: run.data_root")
    if not config.run.playground_root:
        raise ValueError("YAML missing required field: run.playground_root")
    if not config.llm.default.model:
        raise ValueError("YAML missing required field: llm.default.model")

    _check_task_folder_exists(config)

    # data_root and playground_root are canonicalized by
    # RunConfig._normalize_root_path at the pydantic boundary.

    from infra.fs.pathing import _assert_posix_path_semantics
    from infra.host_resource.confinement import probe_unprivileged_userns
    from infra.host_resource.gpu_alloc import configure_visible_devices

    _assert_posix_path_semantics(
        Path(config.output_base_dir),
        purpose="aibuildai run outputs",
    )
    configure_visible_devices(
        config.resources.cuda_visible_devices,
    )
    if config.resources.sandbox.enable:
        # The host prerequisites of bwrap, and nothing else: whether the
        # launcher the product generates is well-formed is a property of the
        # source, proved once per change, not a check every run repeats.
        probe_unprivileged_userns()

    from engine.builtin import search_type_for_kind
    from engine.work_unit.agent import prompt
    from engine.work_unit.agent.plugins import (
        resolve_enable_config,
        validate_agent_roles,
        validate_plugin_config,
    )
    search_method_type = search_type_for_kind(config.search.kind)
    active_agent_types = search_method_type.active_agent_types(config)
    try:
        prompt.search_template_for(search_method_type)
        for agent_type in active_agent_types.values():
            agent_template = agent_type.prompt_template
            assert agent_template is not None
            prompt.agent_template_for(agent_type)
    except Exception as exc:  # noqa: BLE001 -- user templates execute here
        raise StartupUserInputError(
            f"invalid selected prompt template: {type(exc).__name__}: {exc}"
        ) from exc
    known_roles = tuple(sorted(active_agent_types))
    validate_agent_roles(config, known_roles)
    search_method_type.validate_config(config)

    enable = resolve_enable_config(config, active_agent_types)
    # Routing is one package's own concrete role. A package that declares no
    # Router answers None, and a configuration that asks for routing under it
    # is refused here instead of being quietly ignored.
    router = search_method_type.prepare_router(config, enable=enable)
    if config.llm.router_on and router is None:
        raise StartupUserInputError(
            f"llm.auto asks this run to route models, but search.kind="
            f"{config.search.kind!r} declares no Router. Remove llm.auto or "
            "select a search kind that has one."
        )
    mcp_definitions = resolve_mcp_definitions(config, known_roles)
    validate_plugin_config(
        config,
        enable,
        mcp_definitions.keys(),
        known_roles,
    )

    # Resolve the run directory only after startup has checked the complete
    # selected composition. Invalid configuration must not create a run home.
    # The Run ID itself was allocated at the outer launch boundary (cli()),
    # before this process re-execed into its own systemd unit: the directory
    # suffix, the archived config, the root DBOS workflow, and the unit all
    # carry that one identity. The unit name is the single-writer boundary;
    # there is no directory lock.
    if resume_run_timestamp is None:
        # Consume the marker: it names exactly one launch, and a child
        # process or a later fresh run must never inherit this identity.
        run_id = os.environ.pop("AIBUILDAI_RUN_ID", None)
        if not run_id:
            raise AssertionError("a fresh run has no allocated Run ID")
        timestamp = RunTimestamp.new(run_id)
    else:
        timestamp = RunTimestamp.parse(resume_run_timestamp)
    run_paths = RunPaths.from_output_base(
        config.output_base_dir,
        timestamp,
    )

    # The manager-owned siblings of the run workspace, plus the
    # candidate-visible public dir. All are bound writable into a role's sandbox
    # (setup fills public/ and private/, the finalizer writes the deliverable),
    # and a bwrap write bind needs its dir to already exist, so the run home
    # creates them here with the rest of its own layout.
    Path(run_paths.public_dir).mkdir(parents=True, exist_ok=True)
    Path(run_paths.private_dir).mkdir(parents=True, exist_ok=True)
    Path(run_paths.deliverable_dir).mkdir(parents=True, exist_ok=True)

    # The final config owns the fixed global identity. User YAML cannot set it.
    if resume_run_timestamp is None:
        config.run = type(config.run).model_validate(
            {
                **config.run.model_dump(),
                "run_id": timestamp.run_id,
                "kubernetes_start_mode": kubernetes_start_mode(),
            }
        )
    elif config.run.require_run_id() != timestamp.run_id:
        raise StartupUserInputError(
            f"run {config.run.require_run_id()} predates the one-identity "
            "run directory shape and cannot be resumed by this build; open "
            "it in the local Web Workspace"
        )


    # Archive the resolved config before birth. The bootstrap publishes the Run
    # to the XDG index only after RunOpened commits. A failed pre-birth launch is
    # therefore not visible to Workspace.
    if resume_run_timestamp is None:
        run_config_store.save_run_config(config, str(run_paths.run_home))

    # Stage 2: the run's own stderr handler replaces the early bootstrap one.
    configure_logging()

    return (
        search_method_type,
        run_paths,
        router,
        RunMcpClient(mcp_definitions),
    )


# The blame-neutral exit-summary headline for an unexpected internal bug.
_UNEXPECTED_BUG_MESSAGE = (
    "an unexpected error occurred (this is a bug in aibuildai, not your code)."
)

# Boundary case 1: a crash in composition-root setup. The one-liner
# carries no file pointer.
_PRE_RUN_CRASH_MESSAGE = (
    "aibuildai: an unexpected error occurred before the run could start — "
    "this is a bug in aibuildai, not your code or data. Please file an issue "
    "at https://github.com/aibuildai-inc/aibuildai-llm-posttrain-agent/issues"
)

# The same shape for an auth command, which runs before any run
# exists and so cannot point at a run home.


# The exit codes every boundary in this module answers with, and the one
# question that picks between them: whose move is it next?
#
#   0    the command did what was asked.
#   1    the work ran and did not complete -- a run that failed on its own terms.
#   2    the USER can fix it: a bad config value, an unusable resume, a sign-in
#        that needs starting again. Printed as `error: <sentence>`, never a
#        traceback, because the sentence IS the fix.
#   70   AIBUILDAI is broken: nothing the user did explains it. Printed as the
#        blame-neutral one-liner, with the raw traceback only on a non-frozen
#        developer run.
#   130  the user pressed Ctrl-C.
#
# The distinction that matters is 2 against 70, and it is about the READER, not
# about severity: 70 tells a person to file an issue and wait, so spending it on
# something they could have fixed in one command is a real cost to them.
#
# BSD sysexits.h EX_SOFTWARE: "an internal software error has been detected".
# The dedicated exit code for "the tool itself broke", distinct from the
# expected-non-completion code (1).
_EX_SOFTWARE = 70


class CommandOutcome(abc.ABC):
    """A command's result, finalized by the composition root after ProcessLifecycle teardown. It renders any user-facing summary and returns the process exit code."""

    @abc.abstractmethod
    def finalize(self) -> int: ...


class RunOutcome(CommandOutcome):
    """run command's result: holds the data RunReport needs; renders in finalize()."""

    def __init__(
        self,
        config: "AgentConfig",
        run_paths: RunPaths,
        run_state: "RunState | None",
        *,
        status: str,
        error: "str | run_launch.WebSafeDiagnostic | None" = None,
    ):
        self.config = config
        self.run_paths = run_paths
        self.run_state = run_state
        self.status = status
        self.error = error

    def finalize(self) -> int:
        return RunReport(
            self.config,
            self.run_paths,
            self.run_state,
            status=self.status,
            error=None if self.error is None else str(self.error),
        ).write()


class ExitCodeOutcome(CommandOutcome):
    """No-render result carrying only an exit code (memorize command)."""

    def __init__(self, code: int):
        self.code = code

    def finalize(self) -> int:
        return self.code


class Command(abc.ABC):
    """A CLI command. Receives the loaded config and returns a CommandOutcome; the composition root finalizes it into the process exit code after lifecycle teardown. Commands perform no process-level effects and never call sys.exit."""

    @abc.abstractmethod
    def execute(self, config: "AgentConfig") -> CommandOutcome: ...


@asynccontextmanager
async def _interrupt_signals(
    request: "Callable[[str], None]",
) -> "AsyncIterator[None]":
    """Map SIGINT and SIGTERM onto one interrupt request callback.

    asyncio's ``loop.add_signal_handler`` delivers the callback at a safe loop-iteration boundary (never mid-bytecode, unlike a synchronous ``signal.signal`` handler that can fire between any two ops, including inside asyncio internals). Both signals converge on the same request path (REQUEST); the caller stays alive to JOIN the root workflow, and the durable runtime CLOSES only after that join. The handlers are removed in finally so they never leak past this scope.
    """
    running = asyncio.get_running_loop()
    running.add_signal_handler(signal.SIGINT, request, "ctrl-c")
    running.add_signal_handler(signal.SIGTERM, request, "sigterm")
    try:
        yield
    finally:
        running.remove_signal_handler(signal.SIGINT)
        running.remove_signal_handler(signal.SIGTERM)


class RunCommand(Command):
    def execute(
        self,
        config: "AgentConfig",
        *,
        resume_run_timestamp: "str | None" = None,
        launch_environment_file: "Path | None" = None,
    ) -> CommandOutcome:
        (
            search_method_type,
            run_paths,
            router,
            mcp_client,
        ) = prepare_run(
            config,
            resume_run_timestamp=resume_run_timestamp,
        )

        from engine.status import TerminalStatus

        search: "AIBuildAISearch | None" = None
        # What assembly built for this run. The CLI owns the run, so it keeps
        # the journal state and the event store it needs at teardown rather
        # than reaching into the Search for them.
        run_state: "RunState | None" = None
        store: "SearchApplication | None" = None
        signal_reason: str | None = None
        try:
            from bootstrap import build_search_for_production

            async def build_and_run() -> None:
                nonlocal search, run_state, store, signal_reason
                supervisor = asyncio.current_task()
                assert supervisor is not None

                def request(reason: str) -> None:
                    # Cooperative suspend is for ONE case: a root workflow task
                    # is still running and nothing has been asked yet. Every
                    # other interrupt forces teardown by cancelling the joining
                    # supervisor -- a repeat of any earlier interrupt, a signal
                    # before the root workflow starts, and a signal after the
                    # root has returned, where only the epoch JOIN is left and
                    # a suspend request could never be recorded. The saved
                    # suspend request covers a Ctrl-C key that suspended
                    # through the display, so its next press escalates exactly
                    # like a signal does.
                    nonlocal signal_reason
                    repeated = signal_reason is not None
                    if signal_reason is None:
                        signal_reason = reason
                    pipeline = None if search is None else search._pipeline_task
                    if (
                        repeated
                        or search is None
                        or pipeline is None
                        or pipeline.done()
                        or run_state is None
                        or run_state.aibuildai_search.run_suspend_requested
                        is not None
                    ):
                        logger.warning("forced teardown: %s", reason)
                        supervisor.cancel()
                        return
                    search.suspend(reason)

                async with _interrupt_signals(request):
                    search = await build_search_for_production(
                        config,
                        search_method_type,
                        run_paths,
                        router,
                        mcp_client,
                        launch_environment_file=launch_environment_file,
                    )
                    run_state, store = latest_run_state(), run_store()
                    if (
                        resume_run_timestamp is not None
                        and search._run_output() is not None
                    ):
                        # The launch validates: a run with its final result
                        # has nothing left to resume.
                        raise StartupUserInputError(
                            f"run {config.run.require_run_id()} has finished; "
                            "open it in the local Web Workspace"
                        )
                    from engine.search.base import run_search

                    await run_search(search)

            asyncio.run(build_and_run())
            assert search is not None
            terminal = run_state.aibuildai_search.terminal_status()
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Every interrupt lands here after the JOIN. The saved suspend
            # request names the reason whenever the run reached the point of
            # recording one; before that, the signal that actually arrived
            # does, so a SIGTERM during startup is never reported as a
            # Ctrl-C. Process cleanup is owned by ProcessLifecycle.
            request = (
                run_state.aibuildai_search.run_suspend_requested
                if search is not None
                else None
            )
            failed: "tuple[str, Failure] | None" = (
                run_state.latest_recoverable_failure()
                if search is not None
                else None
            )
            if request is None and signal_reason is None and failed is not None:
                # A recoverable Failure ended this epoch: the run stays
                # unfinished and a later Web Resume continues it in place.
                path, failure = failed
                return RunOutcome(
                    config,
                    run_paths,
                    run_state,
                    status="incomplete",
                    error=(
                        f"{path} failed recoverably ({failure.reason}); "
                        "the run is unfinished and can be resumed from the "
                        "Web Workspace"
                    ),
                )
            reason = (signal_reason or "ctrl-c") if request is None else request.reason
            return RunOutcome(
                config,
                run_paths,
                run_state,
                status="sigterm" if reason == "sigterm" else "interrupted",
                # A pause asked for from the Web Workspace says so in the run's
                # end state, so the page and the terminal agree on why it stopped.
                error=(
                    "paused from the Web Workspace"
                    if reason == "web"
                    else None
                    if reason in ("ctrl-c", "sigterm")
                    else reason
                ),
            )
        except HostCapabilityUnavailable:
            # A capability this run REQUIRES that the host does not provide, decided
            # by root outside this process: a USER-ENVIRONMENT condition, not an
            # internal bug. Re-raise so ProcessLifecycle.run renders the plain,
            # actionable message + EX_UNAVAILABLE, instead of the generic except
            # below swallowing it into a status="crashed" "bug in aibuildai" banner.
            #
            # The member that actually arrives here is CgroupWriteRejected from the
            # resource-tree bootstrap. The family's other members --
            # WorkerCgroupUnavailable and ProcessNotKillable -- are
            # raised inside ProcessLifecycle.__enter__, so they never enter this try
            # and reach the same renderer by the shorter road. The clause names the
            # family rather than one member because the rule is the family's.
            raise
        except PostgreSQLUnavailable as error:
            logger.error("PostgreSQL unavailable: %s", error)  # noqa: TRY400 — expected refusal, not an unexpected traceback.
            raise
        except RunPinMismatch:
            # A resume whose journaled environment pins (app_version,
            # gpu_visibility, cwd, ...) no longer match the
            # CURRENT process is a user-caused refusal, not an internal
            # bug: re-raise so the cli() boundary renders the pin-mismatch
            # message naming every changed pin, instead of the generic except
            # below folding it into a status="crashed" "bug in aibuildai" banner.
            raise
        except WorkUnitTimeConfigError:
            # A missing exact WorkUnit time entry is a user configuration error,
            # including when Search construction reads one before Search.run can
            # persist a terminal. Let the CLI's ValueError boundary print the
            # exact identity and exit 2 without an internal-bug banner.
            raise
        except McpStartupError:
            details = "; ".join(
                f"{name}: {status.status}"
                for name, status in mcp_client.validated_mcps.items()
                if status.status != "ok"
            )
            return RunOutcome(
                config,
                run_paths,
                run_state,
                status="incomplete",
                error=run_launch.WebSafeDiagnostic(
                    f"configured MCP server startup failed: {details}"
                ),
            )
        except Exception:  # noqa: BLE001
            # Reached only when an exception propagates out of Search.run(), or
            # when setup fails before Search.run() starts. An ordinary
            # in-pipeline bug is recorded as a Failure and lands in the `else`
            # branch below. The traceback reaches stderr through the normal
            # Python logger.
            logger.exception("Run crashed")
            return RunOutcome(
                config,
                run_paths,
                run_state,
                status="crashed",
                error=_UNEXPECTED_BUG_MESSAGE,
            )
        else:
            if terminal == TerminalStatus.COMPLETED:
                return RunOutcome(
                    config,
                    run_paths,
                    run_state,
                    status="completed",
                )
            if terminal == TerminalStatus.FAILED_UNEXPECTED:
                # Search.run() logged the traceback and persisted the
                # FAILED/UNEXPECTED state, then returned cleanly. Map it to
                # status="crashed" (exit 70) under the blame-neutral banner.
                return RunOutcome(
                    config,
                    run_paths,
                    run_state,
                    status="crashed",
                    error=_UNEXPECTED_BUG_MESSAGE,
                )
            # A non-completed terminal includes expected execution dead ends.
            # Search saved their reason and returned cleanly, so they land here
            # rather than in `except`. The root Action's own Failure carries
            # that reason ("Aggregator skipped: ...") — a plain sentence, no
            # traceback — so the summary headlines the persisted result and
            # falls back to the terminal name only when none was recorded.
            output = search._run_output()
            return RunOutcome(
                config,
                run_paths,
                run_state,
                status="incomplete",
                error=(
                    output.reason
                    if isinstance(output, Failure)
                    else f"terminal status: {terminal.value}"
                ),
            )
        finally:
            if search is not None:
                try:
                    if (
                        not config.run.require_kubernetes_start_mode()
                        and read_run_head(
                            config.run.require_run_id(), run_paths.run_home
                        ).result
                        is not None
                    ):
                        run_launch.remove_run_launch_context(
                            config.run.require_run_id()
                        )
                except Exception as error:  # noqa: BLE001 — terminal result already persisted; cleanup must not rewrite it.
                    logger.exception(
                        "final Run launch-context cleanup failed: %s", error
                    )
                # Every record already committed at save. Closing only frees the
                # connection pool and remains independent from result cleanup.
                if store is not None:
                    store.close()


class WritePaperCommand(Command):
    """`aibuildai write-paper <run-dir>`: write a NeurIPS paper describing a FINISHED run and compile it to a PDF, without re-running the pipeline.

    Reuses the production assembly on the existing run. WriterAgent is a standalone DurableExecution, so the finished AIBuildAISearch stays DONE."""

    def __init__(self, run_dir: "Path") -> None:
        self._run_dir = run_dir

    def execute(self, config: "AgentConfig") -> CommandOutcome:
        run_timestamp = self._run_dir.resolve().name
        (
            search_method_type,
            run_paths,
            router,
            mcp_client,
        ) = prepare_run(config, resume_run_timestamp=run_timestamp)
        search: "AIBuildAISearch | None" = None
        store: "SearchApplication | None" = None
        paper: str | None = None
        try:
            from bootstrap import build_search_for_production

            async def build_and_write() -> None:
                nonlocal search, store, paper
                task = asyncio.current_task()
                assert task is not None

                def request(_reason: str) -> None:
                    task.cancel()

                async with _interrupt_signals(request):
                    search = await build_search_for_production(
                        config,
                        search_method_type,
                        run_paths,
                        router,
                        mcp_client,
                        launch_environment_file=None,
                    )
                    store = run_store()
                    paper = await search.write_paper()

            asyncio.run(build_and_write())
        except (asyncio.CancelledError, KeyboardInterrupt):
            print("aibuildai: write-paper interrupted", file=sys.stderr)
            return ExitCodeOutcome(130)
        except Exception as e:  # noqa: BLE001 — surface a clean CLI error, no traceback to the user
            print(
                f"aibuildai: write-paper failed: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
            return ExitCodeOutcome(1)
        finally:
            if search is not None:
                if store is not None:
                    store.close()
        # The Writer's own artifact is the paper: the command reports the path
        # that execution owns, because no run-global copy of it exists.
        pdf = None if paper is None else Path(paper)
        if pdf is not None and pdf.is_file() and pdf.read_bytes()[:4] == b"%PDF":
            print(f"paper written: {pdf}")
            return ExitCodeOutcome(0)
        print("aibuildai: write-paper produced no PDF", file=sys.stderr)
        return ExitCodeOutcome(1)


class MemorizeCommand(Command):
    def execute(self, config: "AgentConfig") -> CommandOutcome:
        run_memorize_command(config)
        return ExitCodeOutcome(0)


def _prepare_stored_state_types(run_home: str) -> None:
    """Load the classes named by one run before its state is restored."""
    from engine.builtin import load_run_definitions

    config = run_config_store.load_run_config(run_home)
    load_run_definitions(config.search.kind)


COMMANDS: dict[str, type[Command]] = {
    "run": RunCommand,
    "memorize": MemorizeCommand,
}


def _execute_configured(
    config: "AgentConfig",
    action: "Callable[[], CommandOutcome]",
) -> CommandOutcome:
    """Run the shared tool, endpoint, and model startup gates."""

    from infra.owned_tools import setup_owned_tools
    from infra.postgresql import ensure_postgresql

    setup_owned_tools()
    ensure_postgresql()
    with model_endpoint(config.llm):
        return action()


def _record_existing_run_failure(
    result_file: "Path | None", diagnostic: run_launch.WebSafeDiagnostic
) -> None:
    """Write one safe Resume failure without changing the Run result."""
    if result_file is None:
        return
    try:
        run_launch.write_resume_failure(result_file, diagnostic)
    except Exception:  # noqa: BLE001 — diagnostic failure must not replace the original failure.
        logger.exception("private Resume result could not be written")


def run_existing_process(run_id: str) -> None:
    """Enter one existing Run process that its startup launcher already created."""
    scrub_claude_feature_env()
    result_file: Path | None = None
    try:
        from startup.run_target_resolution import resolve_run_target

        run = resolve_run_target(run_id)
        result_file = run_launch.claim_resume_result(run_id)

        def execute() -> CommandOutcome:
            materialize_to_tmpfs()
            config = run_config_store.load_run_config(run.run_home)

            def resume() -> CommandOutcome:
                return RunCommand().execute(
                    config,
                    resume_run_timestamp=Path(run.run_home).name,
                )

            return _execute_configured(config, resume)

        outcome = ProcessLifecycle.run_already_in_service(execute)
        exit_code = outcome.finalize()
        if exit_code != 0:
            diagnostic = (
                outcome.error
                if isinstance(outcome, RunOutcome)
                and isinstance(outcome.error, run_launch.WebSafeDiagnostic)
                else run_launch.WebSafeDiagnostic(
                    "the resumed Run stopped before its live member became ready"
                )
            )
            _record_existing_run_failure(result_file, diagnostic)
        raise SystemExit(exit_code)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        diagnostic = run_launch.WebSafeDiagnostic(
            "Resume was interrupted before the Run became live"
        )
        _record_existing_run_failure(result_file, diagnostic)
        print(f"aibuildai: {diagnostic}", file=sys.stderr)
        raise SystemExit(130) from None
    except (
        HostCapabilityUnavailable,
        PostgreSQLUnavailable,
        RunPinMismatch,
        run_launch.WebSafeDiagnostic,
    ) as error:
        diagnostic = error if isinstance(error, run_launch.WebSafeDiagnostic) else run_launch.WebSafeDiagnostic(str(error) or "Resume startup was refused")
        _record_existing_run_failure(result_file, diagnostic)
        print(f"aibuildai: {error}", file=sys.stderr)
        raise SystemExit(69 if isinstance(error, HostCapabilityUnavailable) else 2) from None
    except Exception as error:  # noqa: BLE001 — private process boundary.
        known = isinstance(error, (OSError, StartupUserInputError, ValueError))
        diagnostic = run_launch.WebSafeDiagnostic(
            "Resume startup failed; inspect the Run log"
            if known
            else "Resume startup failed unexpectedly"
        )
        if not known:
            traceback.print_exc()
        _record_existing_run_failure(result_file, diagnostic)
        print(f"aibuildai: {error}" if known else _PRE_RUN_CRASH_MESSAGE, file=sys.stderr)
        raise SystemExit(2 if known else _EX_SOFTWARE) from None


def cli() -> None:
    scrub_claude_feature_env()
    # Parsing has no YAML or other side effect. Each process keeps this one
    # result. A systemd re-exec is a new process and parses its own command
    # line once.
    args = config_loader.parse_args()
    command = args.command
    # setup is a thin pre-warm shortcut around the SAME owned-tools path every
    # run-like command executes automatically; it owns no logic of its own and
    # run never depends on it having been called. Routed before ProcessLifecycle
    # like the other config-free commands.
    if command == "setup":
        from infra.owned_tools import setup_owned_tools

        try:
            setup_owned_tools()
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        print("aibuildai: owned tools are ready")
        return

    # `config` prints a starter YAML (every field + its default) to stdout: pure
    # introspection, no YAML load / systemd service. Routed
    # before ProcessLifecycle and the auth commands.
    if command == "config":
        print(example_yaml_text(), end="")
        return

    # write-paper carries a run-dir (not a YAML); capture its args
    # here and build the config from the run dir inside ProcessLifecycle below.
    write_paper_run_dir = None
    if command == "write-paper":
        write_paper_run_dir = Path(args.run_dir)
        write_paper_review = bool(args.review)

    # One Run identity at the outer launch boundary, before the run process
    # exists. The deterministic systemd unit is named after it, so the
    # supervisor itself refuses a second process for the same active run. A
    # fresh run allocates the identity here and carries it across the service
    # re-exec in the env marker; write-paper reuses its existing identity.
    # Commands without a Run ID (memorize) keep a
    # process-instance unit name.
    run_unit = None
    launch_run_id = None
    if command == "run":
        launch_run_id = os.environ.get("AIBUILDAI_RUN_ID") or uuid.uuid4().hex
        os.environ["AIBUILDAI_RUN_ID"] = launch_run_id
        run_unit = run_unit_name(launch_run_id)
    elif write_paper_run_dir is not None:
        paper_home = Path(os.path.normpath(str(Path.cwd() / write_paper_run_dir)))
        run_unit = run_unit_name(RunTimestamp.parse(paper_home.name).run_id)

    try:
        def execute_command(
            fresh_environment_file: "Path | None",
        ) -> CommandOutcome:
            if command == "run":
                if launch_run_id is None:
                    raise AssertionError("a fresh Run has no allocated Run ID")
                launch_environment_file = fresh_environment_file
            else:
                launch_environment_file = None
            # Resource freezing belongs to process launch, not to either config
            # loader. Do it after ProcessLifecycle has re-execed into the run's
            # systemd service and before fresh, resumed, or write-paper config
            # construction can resolve any shipped filesystem-shaped resource.
            materialize_to_tmpfs()

            if write_paper_run_dir is not None:
                # write-paper: the archived run_config.json is the config SSOT
                # (the same archive `resume` reloads). recover_config_from_rundir
                # reads it back; only the run's LOCATION (task_name +
                # playground_root) follows the passed dir, so a copied run dir
                # writes its paper in place while every setting stays verbatim.
                from startup.run_target_resolution import recover_config_from_rundir

                try:
                    config = recover_config_from_rundir(
                        write_paper_run_dir, review_writer=write_paper_review
                    )
                except (FileNotFoundError, ValueError) as e:
                    print(f"error: {e}", file=sys.stderr)
                    sys.exit(2)
            else:
                try:
                    loaded_command, config = load_config(args)
                except ValidationError as e:
                    print(f"error: {format_validation_error(e)}", file=sys.stderr)
                    sys.exit(2)
                if loaded_command != command:
                    raise AssertionError("the parsed command changed while loading config")

            def dispatch() -> CommandOutcome:
                if write_paper_run_dir is not None:
                    return WritePaperCommand(write_paper_run_dir).execute(config)
                if command == "run":
                    return RunCommand().execute(
                        config,
                        launch_environment_file=launch_environment_file,
                    )
                return COMMANDS[command]().execute(config)

            try:
                return _execute_configured(config, dispatch)
            except ValueError as error:
                print(f"error: {error}", file=sys.stderr)
                raise SystemExit(2) from None

        outcome = ProcessLifecycle.run(execute_command, unit_name=run_unit)
    except KeyboardInterrupt:
        # Ctrl-C anywhere in the pre-lifecycle run-startup region -- config load / endpoint-env / resource-limit setup precede the
        # command's own run loop. A KeyboardInterrupt here is an
        # expected user action, not a bug: stop cleanly with the standard interrupt
        # exit code instead of letting this BaseException reach the frozen-build
        # bootloader (raw traceback + "Failed to execute script"). The
        # ProcessLifecycle service owns the child processes, so its teardown
        # cleans them up as the interrupt leaves the with-block. The run loop's
        # own SIGINT handling (RunCommand.execute maps Ctrl-C to an "interrupted"
        # outcome) runs deeper and returns normally, so it never reaches here -- this
        # boundary does not disturb it.
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
    except (RunPinMismatch, PostgreSQLUnavailable) as exc:
        # A resume whose journaled environment pins no longer match the CURRENT
        # process is reported like a config error (the actionable
        # message plainly on stderr + a clean exit code), never the
        # blame-neutral "bug in aibuildai" banner nor a raw traceback. Arrives
        # here via RunCommand.execute's re-raise past its own generic except.
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except StartupUserInputError as exc:
        # A startup failure caused by USER INPUT -- a broken user extension file
        # or an unregistered config value such as search.kind --
        # not an internal bug. Report it like a config error: the actionable
        # message naming the user's mistake plainly on stderr + a clean exit code
        # (2), never the blame-neutral "bug in aibuildai" banner nor a raw
        # traceback. These arise in prepare_run (extension
        # import / search-kind selection), which runs before RunCommand.execute's
        # own try block, so they propagate directly. This clause precedes the
        # generic `except Exception` below so the user-input class wins over the
        # bug-banner path.
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception:  # noqa: BLE001
        # Boundary case 1: an UNEXPECTED crash in composition-root
        # setup. Print the blame-neutral one-liner (+ the raw traceback) and exit 70. Every sys.exit above
        # raises SystemExit, which is a BaseException, not an Exception, so it
        # propagates unchanged (clean CLI errors keep their codes); KeyboardInterrupt
        # (also a BaseException) is handled by the boundary just above.
        traceback.print_exc()
        print(_PRE_RUN_CRASH_MESSAGE, file=sys.stderr)
        sys.exit(_EX_SOFTWARE)
    # ProcessLifecycle.__exit__ has returned after the run's child processes were
    # handled by its service. Render the result on the ordinary terminal.
    sys.exit(outcome.finalize())
