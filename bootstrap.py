"""Assembly factory — the only place that wires concrete implementations together.

Engine code reaches the model only through ``infra.backends.claude.conversation.Conversation``, directly or through the ``infra.backends.one_shot`` helpers that run one for a single prompt; it sees the errors that raises from ``infra.backends.claude.errors`` and the ``infra.backends`` spec and hooks, observes the conversation through the Conversation's observer, and never imports an SDK type or the SDK client.
"""

from __future__ import annotations

import atexit
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, cast, TYPE_CHECKING, TypedDict

from infra.model_catalog import endpoint_is_anthropic
from engine.event.base import Event
from engine.event.events import RunConfigRecorded, RunEpochOpened, RunWarning
from engine.run_state import RunConfigRecord, RunState
from engine.paths import RunPaths
from engine.program_environment import ProgramEnvironment
from engine.event import store as event_store
from engine.builtin.aibuildai.capability import product_capabilities as build_product_capabilities
from engine.builtin.aibuildai.search import AIBuildAISearch
from engine.work_unit.agent.base import Agent
from engine.work_unit.program.base import bind_program_executor
from infra.exec.local import LocalExecutor
from infra.host_resource.controller import HostResourceController
from infra.host_resource.cgroup import kubernetes_start_mode
from output.transcript import Transcript, TranscriptProjection
from infra.util.clock import Clock

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.event.events import RunEpochOpened
    from engine.mcp import RunMcpClient
    from engine.work_unit.agent.policy import ModelRouter


class _RunPins(TypedDict):
    """The `RunOpened` ambient-condition pins, precisely typed per field (a bare ``dict[str, str | None]`` would widen `schema_version` to `str | None` and make every `RunOpened(**pins)` construction and pin comparison untyped)."""

    app_version: str
    gpu_visibility: "str | None"
    archive_location: str
    schema_version: int


def _current_pins(configs: "AgentConfig", run_home: str) -> _RunPins:
    """The ambient conditions computed the SAME way at every process start. The fresh-run branch pins these into the atomic first journal commit; the resume-verify branch (`_verify_store_pins`) recomputes them fresh and compares them with `RunOpened`. Sharing this ONE function keeps write and check in step.

    Excludes `cwd` (a `RunEpochOpened`, not `RunOpened`, pin -- the caller reads it separately from the first process-start record)."""
    from aibuildai_version import APP_VERSION
    from startup.run_config_store import RUN_CONFIG_FILENAME

    visible = configs.resources.cuda_visible_devices
    return _RunPins(
        app_version=APP_VERSION,
        # The run's visible GPU set (resources.cuda_visible_devices);
        # None = every host card. The external CUDA_VISIBLE_DEVICES env
        # var is refused at startup, so the field IS the visibility fact. A
        # resume under a different visible set is a pin mismatch: claims recorded
        # in the journal were made inside the old visible set.
        gpu_visibility=(
            ",".join(str(i) for i in visible) if visible is not None else None
        ),
        archive_location=str(Path(run_home) / RUN_CONFIG_FILENAME),
        schema_version=event_store.SCHEMA_VERSION,
    )


def _verify_store_pins(
    store: "event_store.SearchApplication",
    configs: "AgentConfig",
    run_home: str,
) -> None:
    """Verify the journal's ambient-condition pins.

    The ``RunOpened`` fields and the first ``RunEpochOpened.cwd`` must match the current process before it commits a new process-start record.
    """
    import dataclasses
    import os

    run_opened = event_store.read_run_opened_fields(store)
    if run_opened is None:
        raise AssertionError(
            "resume requires a journaled RunOpened and RunEpochOpened genesis"
        )

    current = _current_pins(configs, run_home)
    mismatches: "list[tuple[str, object, object]]" = []
    mismatches.extend(
        (name, run_opened[name], value)
        for name, value in current.items()
        if run_opened[name] != value
    )

    if mismatches:
        event_store.raise_pin_mismatch(mismatches)

    first_epoch_event = event_store.read_first_epoch(store)
    if first_epoch_event is None:
        raise AssertionError(
            "resume requires a journaled RunOpened and RunEpochOpened genesis"
        )
    first_epoch = dataclasses.asdict(first_epoch_event)

    current_cwd = os.getcwd()
    if first_epoch["cwd"] != current_cwd:
        mismatches.append(("cwd", first_epoch["cwd"], current_cwd))

    if mismatches:
        event_store.raise_pin_mismatch(mismatches)


def _record_resume_epoch(
    store: "event_store.SearchApplication",
    state: RunState,
    *,
    ts: float,
) -> None:
    """Record one later process start after its pins passed.

    Only the epoch fact: DBOS owns the unfinished position and resumes it
    in place, so a resume writes no retry command and reopens nothing."""
    record_startup_events(
        store,
        state,
        ((RunEpochOpened, None, {"cwd": os.getcwd()}),),
        ts=ts,
    )


def record_startup_events(
    store: "event_store.SearchApplication",
    state: "RunState",
    entries: tuple[tuple[type["Event"], str | None, dict[str, object]], ...],
    *,
    ts: float,
) -> None:
    """Record facts settled before this run has a durable runtime."""

    def build(emit: Callable[..., None]) -> None:
        for event_type, target, facts in entries:
            emit(event_type, target, **facts)

    store.save_pending(
        build,
        state=state,
        ts=ts,
        live=False,
        workflow_path=None,
        workflow_step=None,
    )


def _run_config_record(configs: "AgentConfig") -> RunConfigRecord:
    """Build every first-start fact before the journal commit point."""
    return RunConfigRecord(
        run_id=configs.run.require_run_id(),
        task_name=configs.run.task_name,
        model=configs.llm.default.model,
        cost_budget_usd=configs.run.budget.cost_usd,
        run_budget_s=configs.run.budget.wall_clock_minutes * 60,
        work_units=[entry.model_dump(mode="json") for entry in configs.work_units],
        models_routed=bool(configs.llm.by_role) or configs.llm.router_on,
        # The endpoint identity is read here, after endpoint environment
        # resolution, from the one endpoint authority.
        is_anthropic=endpoint_is_anthropic(os.environ.get("ANTHROPIC_BASE_URL", "")),
    )


def _with_settled_time_config(
    configs: "AgentConfig", run_config: RunConfigRecord
) -> "AgentConfig":
    """Use this run's first Run Budget and Local Budget rules."""
    minutes = run_config.run_budget_s / 60.0
    if not minutes.is_integer():
        raise AssertionError("the settled Run Budget is not a whole minute")
    data = configs.model_dump(mode="python")
    data["run"]["budget"]["wall_clock_minutes"] = int(minutes)
    data["work_units"] = run_config.work_units
    return type(configs).model_validate(data)


def _record_host_capability_warnings(
    store: "event_store.SearchApplication",
    run_state: "RunState",
    host: HostResourceController,
    ts: float,
) -> None:
    """Record what this host could not give the run after Search is ready.

    A host capability the product asked for and did not get is said, not hidden.

    ``RunWarning`` is the right channel and it already exists: a notable state RECORDED rather than rejected, applied to ``RunState.run_warnings``, shown in the Web view and in the run-end summary, and reconstructable on replay.

    Assembly built both the store and the journal state, so it passes them here
    rather than reading them back off the Search it just bound.
    """
    if kubernetes_start_mode():
        record_startup_events(
            store,
            run_state,
            (
                (
                    RunWarning,
                    None,
                    {
                        "source": "host-resources",
                        "category": "kubernetes-start-mode",
                        "message": (
                            "kubernetes start mode: no per-WorkUnit kernel cap is in force. "
                            "The Pod's own cgroup bounds the whole run's memory, processes, "
                            "and cpu; inside it, units compete unbounded."
                        ),
                    },
                ),
            ),
            ts=ts,
        )
    if not kubernetes_start_mode() and not host.cpu_limits_available():
        record_startup_events(
            store,
            run_state,
            (
                (
                    RunWarning,
                    None,
                    {
                        "source": "host-resources",
                        "category": "cpu-controller-undelegated",
                        "message": (
                            "no cpu cap is in force: the systemd user manager did not delegate the "
                            "'cpu' controller, so this run bounds memory and processes but cannot "
                            "bound cpu. Candidates compete for the host's cores with everything else on it."
                        ),
                    },
                ),
            ),
            ts=ts,
        )


async def build_search_for_production(
    configs: "AgentConfig",
    search_method_type: "type[AIBuildAISearch]",
    run_paths: RunPaths,
    router: "ModelRouter | None",
    mcp_client: "RunMcpClient",
    *,
    launch_environment_file: "Path | None",
) -> AIBuildAISearch:
    """Wire the selected Search into one production run."""
    # The one canonical typed root Input: the selected package completes it
    # here, where the run's paths are real, and a fresh run journals exactly
    # this value. No later stage reads search.input or configuration again.
    root_input = search_method_type.build_input(configs, run_paths)
    product_capabilities = build_product_capabilities(configs)
    # Persistent state. Startup mints a fresh directory timestamp or keeps the
    # resume target.
    # repository.get restores RunState from
    # a snapshot and event tail; a fresh store creates RunState. Each started
    # agent's Transcript below folds its own saved events back into memory.
    _run_home = str(run_paths.run_home)
    transcripts = TranscriptProjection(_run_home, write_files=True)

    def transcript_for(agent: "Agent") -> Transcript:
        return transcripts.for_agent(agent)

    configs.run.require_run_id()
    # Built before the journal opens: a fresh run's root capability records the
    # run's GPU grant, and the controller's allocator is what can count this
    # host's visible cards. acquire() still runs later, after RunState exists.
    host = HostResourceController(configs.resources)
    run_id = configs.run.require_run_id()
    fresh = launch_environment_file is not None or kubernetes_start_mode()
    store = event_store.open_search_store(run_id)
    opened = event_store.read_run_opened_fields(store)
    if fresh and opened is not None:
        store.close()
        raise AssertionError("a fresh Run aggregate already contains RunOpened")
    if not fresh and opened is None:
        store.close()
        raise event_store.RunPinMismatch(
            "resume refused: the Run journal has no RunOpened record"
        )
    if opened is not None:
        _verify_store_pins(
            store,
            configs,
            _run_home,
        )
        run_state = cast(
            RunState,
            event_store.restore_run_state(store, Path(_run_home)),
        )
        configs = _with_settled_time_config(configs, run_state.require_run_config())
        clock = Clock(run_state, run_state.require_run_config().run_budget_s)
        # Startup runs before the exploration window, where the global budget
        # constrains nothing; the probe subprocess still needs a finite
        # timeout, so the whole settled Run Budget is its ceiling.
        await host.probe_host_visible_gpu_count(
            lambda: run_state.require_run_config().run_budget_s
        )
    else:
        if launch_environment_file is None and not kubernetes_start_mode():
            raise AssertionError("a fresh local Run has no captured launch context")
        from startup.run_launch import (
            publish_run_launch_context,
            remove_run_launch_context,
        )

        pins = _current_pins(configs, _run_home)
        run_started_at_unix_s = time.time()
        run_config = _run_config_record(configs)
        clock = Clock.before_run_open(run_started_at_unix_s, run_config.run_budget_s)
        await host.probe_host_visible_gpu_count(lambda: run_config.run_budget_s)
        try:
            if launch_environment_file is not None:
                publish_run_launch_context(
                    configs.run.require_run_id(), launch_environment_file
                )
            run_state = RunState(
                run_id=run_id,
                run_home=_run_home,
                app_version=pins["app_version"],
                gpu_visibility=pins["gpu_visibility"],
                archive_location=pins["archive_location"],
                schema_version=pins["schema_version"],
                search_kind=configs.search.kind,
                search_input=root_input.model_dump(mode="json"),
                run_started_at_unix_s=run_started_at_unix_s,
                ts=0.0,
                timestamp=datetime.fromtimestamp(run_started_at_unix_s, UTC),
            )
            record_startup_events(
                store,
                run_state,
                (
                    (RunEpochOpened, None, {"cwd": os.getcwd()}),
                    (RunConfigRecorded, None, {"run_config": run_config}),
                ),
                ts=0.0,
            )
        except BaseException:
            if launch_environment_file is not None:
                remove_run_launch_context(configs.run.require_run_id())
            raise
        from infra.fs import run_index

        run_index.append_run(run_home=str(run_paths.run_home))
        clock.bind_run_state(run_state)

    if opened is not None:
        _record_resume_epoch(
            store,
            run_state,
            ts=clock.run_elapsed_s(),
        )
    # Backstop: release the journal handle on any catchable exit (a startup
    # failure between here and Search.run's teardown would otherwise leak it).
    # Same catchable-exit rationale as host.release below; close() is idempotent.
    atexit.register(store.close)

    # Build the run resource tree before anything can spawn into it.
    # Each WorkUnit writes its own saved limits when it prepares its live Scope.
    host.acquire(playground_root=Path(configs.run.playground_root))
    # The tree is built HERE, before anything can spawn into it. Search records
    # warnings about missing host features after it is constructed below.
    #
    # Backstop teardown for catchable exits outside Search.run()'s finally
    # (SIGTERM/SIGINT/normal/exception). Registered AFTER cli.py's atexit handlers so
    # The owner removes each resource on normal exit.
    # SIGKILL/OOM are uncatchable; systemd owns local process cleanup.
    atexit.register(host.release)

    program_environment = ProgramEnvironment(
        run_home=run_paths.run_home,
        conda_env_name=configs.run.program_environment_base,
    )
    bind_program_executor(
        LocalExecutor(
            host=host,
            environment=program_environment,
            sandbox=configs.resources.sandbox,
        )
    )

    # The run has one display: its live Web member.
    from output.web.live_member import LiveWebMember

    display = LiveWebMember(
        run_id=configs.run.require_run_id(),
        resource_history_path=run_paths.resource_history_path,
    )

    # Genesis events were already applied and saved by the calls above. There
    # is no second start-event application step here. Unfinished WorkUnits
    # repair themselves later on re-entry.

    search = run_state.search
    if not isinstance(search, search_method_type):
        raise AssertionError(
            f"stored AIBuildAISearch is {type(search).__name__}, expected "
            f"{search_method_type.__name__}"
        )
    transcripts.resume_all(event_store.read_events(store))

    # Bind live services once, to the exact durable Search root restored above.
    search.bind_runtime(
        configs=configs,
        run_paths=run_paths,
        product_capabilities=product_capabilities,
        router=router,
        mcp_client=mcp_client,
        run_state=run_state,
        clock=clock,
        store=store,
        display=display,
        transcript_for=transcript_for,
        host=host,
        program_environment=program_environment,
    )

    # The tree was built long before this point, but its warnings can only be SAID once
    # there is something to say them to.
    _record_host_capability_warnings(store, run_state, host, clock.run_elapsed_s())

    return search
