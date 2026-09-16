"""Source entry point."""

import os
import re
import sys


def main() -> None:
    if sys.argv[1:2] == ["__resume_run"]:
        if len(sys.argv) != 3 or re.fullmatch(r"[0-9a-f]{32}", sys.argv[2]) is None:
            raise SystemExit("usage: aibuildai __resume_run RUN_ID")
        from aibuildai_version import preload_source_runtime

        preload_source_runtime()
        from infra.host_resource.cgroup import run_unit_name
        from infra.process.transient_unit import unit_main_pid

        run_id = sys.argv[2]
        try:
            main_pid = unit_main_pid(run_unit_name(run_id))
        except RuntimeError as error:
            raise SystemExit(
                "aibuildai: __resume_run may run only inside its active Run service"
            ) from error
        if main_pid != os.getpid():
            raise SystemExit(
                "aibuildai: __resume_run may run only as its Run service main process"
            )
        from cli_impl import run_existing_process

        run_existing_process(run_id)
        return
    if len(sys.argv) == 3 and sys.argv[1] == "__nvml_query":
        from infra.host_resource.nvml import main as run_nvml_query

        raise SystemExit(run_nvml_query(sys.argv[2]))
    if len(sys.argv) > 1 and sys.argv[1] == "__kaggle_submission_mcp":
        sys.argv.pop(1)
        from infra.kaggle_submission_mcp import main as run_kaggle_submission_mcp

        run_kaggle_submission_mcp()
        return
    if len(sys.argv) == 4 and sys.argv[1] == "__program_worker":
        sys.argv.pop(1)
        from engine.work_unit.program.worker import main as run_program_worker

        run_program_worker()
        return
    # Workspace backends and projection workers are read-only viewers: no
    # config, no run, no systemd re-exec of their own.
    if len(sys.argv) == 3 and sys.argv[1] == "__web_backend":
        socket_path = sys.argv.pop(2)
        sys.argv.pop(1)
        from output.web.service import main as run_web_service

        run_web_service(socket_path)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "__web_projection":
        from output.web.projection_worker import main as run_web_projection

        run_web_projection(sys.argv[2:])
        return
    from aibuildai_version import run_cli

    run_cli()


if __name__ == "__main__":
    main()
