"""Private entry point for one Program worker process."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import signal
import sys
from pathlib import Path

from engine.durable_execution import (
    ActionRecord,
    _action_spec,
    _decoded_request,
    rebuild_execution,
    resolve_execution_type,
)
from engine.work_unit.program.base import Program


def main() -> None:
    request_path, result_path = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    generated = request["generated_definition"]
    if generated is not None:
        from engine.generated_definitions import activate_generated_definition

        activate_generated_definition(
            Path(generated["run_home"]),
            module_name=generated["module_name"],
            package_relpath=generated["package_relpath"],
            requested_module=request["type"].partition(":")[0],
        )
    program_type = resolve_execution_type(request["type"])
    if not issubclass(program_type, Program):
        raise TypeError(f"{request['type']} is not a Program")
    # The worker rebuilds a recorded Program the way the run state does: through
    # validation, never through the constructor, so it declares no topology.
    program = rebuild_execution(program_type, request["input"])
    if not isinstance(program, Program):
        raise TypeError(f"{request['type']} is not a Program")
    # The one Action this process was launched for, exactly as the run
    # recorded it. Its method, its request, what it may spend and the cards it
    # was placed on are read from here and are inferred nowhere.
    action = ActionRecord.model_validate(request["action_record"])
    method_name = action.method
    # The snapshot this Action started from. An Action that leaves the state
    # equal to it publishes no commit, so a slow read cannot overwrite what a
    # concurrent Action of the same identity committed meanwhile.
    snapshot = copy.deepcopy(request["state"])
    program._bind_worker(
        request["scratch_dir"],
        request["artifacts_dir"],
        request["state"],
        action,
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: program._request_stop())
    # One ordinary bound method call with the recorded request; an async Action
    # runs on this worker's own event loop. An exception is left to leave the
    # process: the interpreter prints it to this same log, and its copy carries
    # the outer frames a print here would leave out, so printing it here too
    # only put the whole traceback in the Failure reason twice.
    spec = _action_spec(program_type, method_name)
    result = getattr(program, method_name)(
        *_decoded_request(program_type, method_name, action.request)
    )
    if inspect.isawaitable(result):
        result = asyncio.run(_await(result))
    output = spec.output_adapter.validate_python(result)
    result_path.write_text(
        json.dumps(
            {
                "output": output.terminal_payload(),
                "state": program.state if program.state != snapshot else None,
            },
            allow_nan=False,
        ),
        encoding="utf-8",
    )


async def _await(result: object) -> object:
    return await result  # type: ignore[misc]


if __name__ == "__main__":
    main()
