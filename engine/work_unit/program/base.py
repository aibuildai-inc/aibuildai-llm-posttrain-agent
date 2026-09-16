"""The fixed managed-process driver for code WorkUnits."""

from __future__ import annotations

import inspect
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Callable,
    ClassVar,
    Generic,
    cast,
    final,
)

from pydantic import ConfigDict, Field, PrivateAttr, TypeAdapter
from typing_extensions import TypeVar

from engine.execution_output import SuccessfulOutput
from engine.failure import Failure, FailureKind
from engine.durable_execution import (
    ActionRecord,
    _action_spec,
    _decoded_request,
    latest_run_state,
    run_clock,
    run_configs,
    run_program_environment,
)
from engine.file_grants import granted_paths
from engine.work_unit.base import (
    WorkUnit,
    WorkUnitRuntimeRecord,
    run_work_unit_attempt,
)
from engine.work_unit.events import ProgramAttemptRecorded
from engine.work_unit.metric_recorder import install_metric_recorder
from infra.exec.executor import Executor
from infra.exec.outcome import ExecOutcome, InfraReason, KillCause
from infra.exec.spec import IOManifest, ProgramExecSpec
from infra.fs.artifact_dir import prepare_dir

ProgramInputT = TypeVar("ProgramInputT")

_PROGRAM_EXECUTOR: Executor | None = None


def bind_program_executor(executor: Executor) -> None:
    """Bind the one Executor every Program runs through.

    Bootstrap constructs it unconditionally; nothing selects among
    implementations, and this binding is the only way a Program reaches it."""
    global _PROGRAM_EXECUTOR
    _PROGRAM_EXECUTOR = executor


@dataclass(frozen=True)
class Policy:
    """Substrate-independent runtime rules a Program cannot enforce itself."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    stop_grace_seconds: float = 5.0
    require_gpu_use: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.stop_grace_seconds, bool)
            or not isinstance(self.stop_grace_seconds, (int, float))
            or not math.isfinite(self.stop_grace_seconds)
            or self.stop_grace_seconds < 0
        ):
            raise ValueError("stop_grace_seconds must be finite and at least 0")
        if type(self.require_gpu_use) is not bool:
            raise TypeError("require_gpu_use must be bool")


class ProgramRuntimeRecord(WorkUnitRuntimeRecord):
    """Process facts owned only by Program."""

    # The worker exit code of each Action's last settled Attempt, by ordinal.
    return_codes: dict[int, int | None] = Field(default_factory=dict)
    state: dict[str, object] = Field(default_factory=dict)


def _outcome_failure(outcome: ExecOutcome, name: str) -> Failure:
    if outcome.infra_reason is not InfraReason.NONE:
        return Failure(
            kind=FailureKind.INFRA,
            reason=f"{name} could not run: {outcome.infra_diagnostic()}",
        )
    if outcome.kill_cause is KillCause.MEMORY:
        return Failure(
            kind=FailureKind.MEMORY_CAP, reason=f"{name} was killed at its memory cap"
        )
    if outcome.gpu_unused:
        return Failure(
            kind=FailureKind.GPU_UNUSED,
            reason=f"{name} received a GPU but did not use it",
        )
    if outcome.timed_out:
        return Failure(
            kind=FailureKind.TIMEOUT, reason=f"{name} reached its wall-clock boundary"
        )
    # A Program always streams both of its streams to its own program.log, so
    # the crash reason -- the traceback the interpreter prints as the worker
    # dies -- is the end of that file, and nothing else here carries it. Read
    # it here, at the one place a worker outcome becomes a Failure: the run
    # directory that holds the log is disposable, and on a cloud node it is
    # destroyed with the instance, so a reason left in the file is a reason lost.
    if outcome.log_path is None:
        raise AssertionError(f"{name} worker reported no log path")
    log = Path(outcome.log_path)
    text = ""
    if log.is_file():
        size = log.stat().st_size
        with log.open("rb") as handle:
            # The end of the file only. It also holds everything the Program
            # printed while it ran, which is as large as that run was long.
            handle.seek(max(0, size - 2000))
            raw = handle.read()
        text = raw.decode("utf-8", errors="replace").strip()
        if size > len(raw):
            # The read began inside the file, so drop its partial first line.
            text = text.partition("\n")[2] or text
    detail = outcome.detail or text or f"the worker wrote nothing to {log.name}"
    return Failure(
        kind=FailureKind.CRASH,
        reason=f"{name} worker exited {outcome.return_code}: {detail}",
    )


def _artifacts_summary(artifacts: Path, limit: int = 8) -> str:
    """One line naming the top-level entries of a Program's attempt directory."""
    try:
        # The result file and the log are the framework's own; listing them
        # would make every attempt directory look non-empty. Only what the
        # Program itself wrote answers "was anything produced".
        entries = sorted(
            (
                e
                for e in artifacts.iterdir()
                if not e.name.startswith(".program-") and e.name != "program.log"
            ),
            key=lambda e: e.name,
        )
    except OSError:
        return f"attempt directory {artifacts} could not be listed"
    if not entries:
        return f"the Program wrote nothing under {artifacts}"
    parts = []
    for entry in entries[:limit]:
        try:
            if entry.is_dir():
                count = sum(1 for _ in entry.iterdir())
                parts.append(f"{entry.name}/ ({count} entries)")
            else:
                parts.append(f"{entry.name} ({entry.stat().st_size} bytes)")
        except OSError:
            parts.append(f"{entry.name} (unreadable)")
    more = f", and {len(entries) - limit} more" if len(entries) > limit else ""
    return f"attempt directory holds: {', '.join(parts)}{more}"


class Program(WorkUnit[ProgramInputT], Generic[ProgramInputT]):
    """One state-owning typed program inside the fixed managed worker."""

    _intermediate: ClassVar[bool] = True
    kind: ClassVar[str] = "program"
    policy: ClassVar[Policy] = Policy()
    _worker_scratch: str | None = PrivateAttr(default=None)
    _worker_artifacts: str | None = PrivateAttr(default=None)
    _worker_state: dict[str, object] | None = PrivateAttr(default=None)
    _stop_requested: bool = PrivateAttr(default=False)
    _proposed_return_code: int | None = PrivateAttr(default=None)
    _proposed_consumed_s: float = PrivateAttr(default=0.0)
    _proposed_state: dict[str, object] | None = PrivateAttr(default=None)

    @property
    def _runtime(self) -> ProgramRuntimeRecord:
        runtime = super()._runtime
        if not isinstance(runtime, ProgramRuntimeRecord):
            raise AssertionError(f"{self.path!r} has no Program runtime record")
        return runtime

    def _new_family_record(self) -> ProgramRuntimeRecord:
        return ProgramRuntimeRecord(state=self._initial_state())

    def _initial_state(self) -> dict[str, object]:
        """Return this Program identity's initial durable state."""
        return {}

    @property
    def return_code(self) -> int | None:
        """The worker exit code of this Action's last settled Attempt."""
        return self._runtime.return_codes.get(self.ctx.ordinal)

    @property
    def state(self) -> dict[str, object]:
        """Return the state this Program's own Action process may change.

        Committed state is reachable only from inside the Action that is
        allowed to change it. Orchestration code holding the identity cannot
        reach the committed dictionary, so no write can escape the journal or
        the Attempt boundary."""
        if self._worker_state is None:
            raise AssertionError(
                f"{type(self).__name__}.state belongs to its own Action process"
            )
        return self._worker_state

    @property
    def scratch_dir(self) -> str:
        """Writable scratch directory; the worker's own one inside a fresh worker."""
        return self._worker_scratch or super().scratch_dir

    @property
    def artifacts_dir(self) -> str:
        """Result files directory; the worker's own one inside a fresh worker."""
        return self._worker_artifacts or super().artifacts_dir

    @property
    @final
    def stop_requested(self) -> bool:
        """Whether the runtime asked this Program to stop."""
        return self._stop_requested

    def _bind_worker(
        self,
        scratch_dir: str,
        artifacts_dir: str,
        state: dict[str, object],
        action: "ActionRecord",
    ) -> None:
        """Bind the worker process to the exact Action it runs and grant that one call.

        The record is the whole binding: the worker has no journal, so what
        this Action may spend, the cards it was placed on, and the files it
        was granted all come from the one record the orchestrator started it
        for. Nothing here is inferred a second time."""
        self._worker_scratch = scratch_dir
        self._worker_artifacts = artifacts_dir
        self._worker_state = state
        self._worker_action = action
        self._action_permit = action.method

    def _request_stop(self) -> None:
        self._stop_requested = True

    async def _invoke_action(
        self, method_name: str, request: object | None
    ) -> SuccessfulOutput | Failure:
        # The marked method runs inside the worker process, never here: the
        # Attempt loop below drives that process with the same decoded request.
        return await self._run(*_decoded_request(type(self), method_name, request))

    @final
    async def execute(self, *request: object) -> SuccessfulOutput | Failure:
        # The worker reads the request file below, which carries the same
        # canonical journal payload this Action decoded.
        del request
        outcome = await self._execute_worker()
        self._proposed_return_code = outcome.return_code
        self._proposed_consumed_s = outcome.consumed_s
        return self._read_result(outcome, self._action.method)

    def _worker_timeout_s(self) -> float:
        """The longest this Attempt's process may run.

        This Action's own clock when the call that started it declared one,
        and otherwise everything that still bounds it -- the identity's
        lifetime remainder and the Run. A process has to be started with a
        real number, so a launch that nothing bounds takes the run's own wall
        clock rather than no deadline at all."""
        bounds = [
            seconds
            for seconds in (
                self.capability.wall_clock_seconds,
                run_clock().effective_remaining_s(self),
            )
            if seconds is not None and math.isfinite(seconds)
        ]
        if bounds:
            return min(bounds)
        return run_configs().run.budget.wall_clock_minutes * 60.0

    async def _prepare_live_resources(self) -> None:
        """Leave physical placement to the Program Executor."""

    async def _release_live_resources(self) -> None:
        """The Program Executor releases its own placement before returning."""

    async def _run_attempt(self, *request: object) -> dict[str, object]:
        return cast(
            dict[str, object],
            await self.ctx.step(run_work_unit_attempt, self.path, preemptible=True),
        )

    def _read_result(
        self, outcome: ExecOutcome, method: str
    ) -> SuccessfulOutput | Failure:
        # Class formation already rejected a concrete WorkUnit without a
        # non-empty name; this only narrows the type.
        name = cast(str, type(self).name)
        if (
            outcome.infra_reason is not InfraReason.NONE
            or outcome.kill_cause is not KillCause.NONE
            or outcome.timed_out
            or outcome.return_code != 0
            or outcome.output_limit_exceeded
        ):
            return _outcome_failure(outcome, name)
        result_path = Path(self.artifacts_dir) / ".program-result.json"
        if not result_path.is_file():
            return _outcome_failure(outcome, name)
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        # None when the Action left the state it started from unchanged.
        self._proposed_state = cast(dict[str, object] | None, payload["state"])
        output = cast(
            SuccessfulOutput | Failure,
            _action_spec(type(self), method).output_adapter.validate_python(
                payload["output"]
            ),
        )
        # require_gpu_use guards a SUCCESS that never trained: a worker that
        # exits 0 claiming an Output while the card it was given stayed idle.
        # A Program that already reports its own Failure keeps it. That reason
        # is the diagnosable one -- "sft_r1 exited 1 and wrote no report" --
        # and the idle card is merely its consequence; replacing it with
        # "received a GPU but did not use it" hid a launcher bug from the
        # Search three attempts running, which read three identical opaque
        # stalls and replanned from scratch instead of fixing the launch.
        if outcome.gpu_unused and not isinstance(output, Failure):
            return _outcome_failure(outcome, name)
        if isinstance(output, Failure) and output.kind is FailureKind.ARTIFACT_MISSING:
            # The Program says nothing usable was produced. Its own check may be
            # what is wrong: one run's trainer read `dir` from a manifest whose
            # script wrote `path`, reported this kind, and the Search never
            # scored the eight checkpoints sitting in that directory. Saying
            # what IS there costs a scandir and lets the reader tell a spurious
            # report from an empty one. The kind is unchanged; only the reason grows.
            return Failure(
                kind=output.kind,
                reason=f"{output.reason}\n{_artifacts_summary(Path(self.artifacts_dir))}",
                code=output.code,
            )
        return output

    async def _execute_worker(self) -> ExecOutcome:
        """Run this Action in a fresh worker process and report its outcome.

        Every fact it needs comes from this identity's own Action: there is one
        way to run a Program, so nothing here is overridable. The one caller
        that used to run a Program outside the graph, on another unit's clock
        and resources, was replaced by an ordinary durable Program."""
        # Imported here, not at module scope: importing the engine must never
        # import a Definition package, so the arrow to the product shell exists
        # only while a product run is actually being served.
        state = self._runtime.state
        timeout_s = self._worker_timeout_s()
        remaining_s = lambda: run_clock().effective_remaining_s(self)  # noqa: E731
        # The worker imports the Program runtime out of this environment, so
        # it may not start while that environment is being written. The run's
        # own lifecycle prepares it at the start; a resume prepares it again
        # in a new process at the same moment durable retry relaunches this
        # worker, and the two raced on one directory until the worker died
        # importing a module out of the site-packages under it. Asking here
        # is what orders them: whoever is already preparing finishes first,
        # and an environment this process has prepared answers at once.
        await run_program_environment().prepare(remaining_s)
        scratch = Path(self.scratch_dir)
        artifacts = Path(self.artifacts_dir)
        # This Action's own grants, exactly as its caller declared them, plus
        # the two directories this Program owns whatever it was granted. A
        # string inside the typed Input is business data and never authority,
        # whatever the host happens to have at that spelling.
        read_paths = granted_paths(self._action.read, running=self.path)
        write_paths = tuple(
            dict.fromkeys(
                (
                    str(scratch),
                    str(artifacts),
                    *granted_paths(self._action.write, running=self.path),
                )
            )
        )
        metric_dir = artifacts / "full"
        install_metric_recorder(str(scratch))
        prepare_dir(str(metric_dir))
        request_path = scratch / ".program-request.json"
        result = artifacts / ".program-result.json"
        generated_definition = None
        module_name = type(self).__module__.partition(".")[0]
        if module_name.startswith("aibuildai_meta_"):
            package_relpath = dict(latest_run_state().generated_definitions).get(
                module_name
            )
            if package_relpath is None:
                raise AssertionError(
                    f"generated Program package {module_name!r} was not published"
                )
            run_home = Path(latest_run_state().run_home)
            from engine.generated_definitions import _definition_package_dir

            package_dir = _definition_package_dir(run_home, package_relpath)
            generated_definition = {
                "run_home": str(run_home),
                "module_name": module_name,
                "package_relpath": package_relpath,
            }
            read_paths = (
                *read_paths,
                str(package_dir),
            )
        request_path.write_text(
            json.dumps(
                {
                    "type": type(self).type_key(),
                    "input": TypeAdapter(type(self).input_type()).dump_python(
                        self.input, mode="json"
                    ),
                    "state": state,
                    "scratch_dir": str(scratch),
                    "artifacts_dir": str(artifacts),
                    # The recorded Action itself travels with the request:
                    # the worker rebuilds the Program from this JSON and has
                    # no journal, so this is where its declaration, its exact
                    # cards and its file grants come from.
                    "action_record": self._action.model_dump(mode="json"),
                    "generated_definition": generated_definition,
                }
            ),
            encoding="utf-8",
        )
        read_paths = tuple(dict.fromkeys(read_paths))
        spec = ProgramExecSpec(
            module="engine.work_unit.program.worker",
            args=(str(request_path), str(result)),
            cwd=str(scratch),
            io_manifest=IOManifest(
                read_paths=read_paths, write_paths=write_paths
            ),
            capability=self.capability,
            timeout_s=min(timeout_s, remaining_s()),
            stop_wait_seconds=type(self).policy.stop_grace_seconds,
            log_path=str(artifacts / "program.log"),
            unit_tag=f"program-{type(self).name}",
            require_gpu_use=type(self).policy.require_gpu_use,
            extra_env={
                "OUTPUT_DIR": str(metric_dir),
                "PYTHONPATH": os.pathsep.join(
                    path
                    for path in (
                        str(scratch),
                        os.environ.get("PYTHONPATH", ""),
                    )
                    if path
                ),
            },
        )
        executor = _PROGRAM_EXECUTOR
        if executor is None:
            raise AssertionError("Program Executor is not bound")
        # A durable Attempt is placed under its exact Action, never under the
        # identity: two Actions of one Program may run at once.
        return await executor.run_program(
            spec,
            owner_path=self.placement_key(self.path, self.ctx.ordinal),
            remaining_s=remaining_s,
        )

    def apply_event(self, event: object, ts: float) -> bool:
        if isinstance(event, ProgramAttemptRecorded):
            self._runtime.return_codes[event.ordinal] = event.return_code
            if event.commit_state:
                self._runtime.state = event.state
        else:
            return super().apply_event(event, ts)
        return True

    def _emit_attempt_facts(
        self, emit: Callable[..., None], output: SuccessfulOutput | Failure
    ) -> None:
        emit(
            ProgramAttemptRecorded,
            self,
            ordinal=self.ctx.ordinal,
            return_code=self._proposed_return_code,
            state=self._runtime.state
            if self._proposed_state is None
            else self._proposed_state,
            # Only a successful Action that changed the state it started from
            # publishes a commit: a failed Attempt keeps its staged state out
            # of the journal, and an Action that changed nothing must not
            # re-commit a snapshot a concurrent Action has already superseded.
            commit_state=not isinstance(output, Failure)
            and self._proposed_state is not None,
        )

    def _reset_for_retry(self, ordinal: int) -> None:
        self._runtime.return_codes.pop(ordinal, None)
        self._proposed_return_code = None
        self._proposed_consumed_s = 0.0
        self._proposed_state = None

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if not cls._is_concrete_definition():
            return
        if cls.kind != "program":
            raise AssertionError("Program.kind is fixed to 'program'")
        if "execute" in cls.__dict__:
            raise AssertionError(f"{cls.__name__} cannot replace Program.execute()")
        if inspect.isabstract(cls):
            raise AssertionError(
                f"{cls.__name__} is abstract; a shared Program base declares "
                "_intermediate: ClassVar[bool] = True"
            )
        if not isinstance(cls.policy, Policy):
            raise AssertionError(f"{cls.__name__}.policy must be a Policy")
