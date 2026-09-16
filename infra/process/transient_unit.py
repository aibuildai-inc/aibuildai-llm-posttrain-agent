"""Start and stop bounded transient systemd user units."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path


def render_environment_file(environment: dict[str, str]) -> str:
    """Render one complete systemd EnvironmentFile without losing values."""
    lines: list[str] = []
    for name, value in environment.items():
        if "\n" in value:
            raise ValueError(
                f"the environment variable {name} contains a newline, which cannot "
                "be handed to a systemd service. Unset it, or give it a value on one "
                "line, and start the run again."
            )
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{name}="{escaped}"\n')
    return "".join(lines)


def start_transient_user_unit(
    *,
    unit: str,
    command: Sequence[str],
    environment: dict[str, str],
    environment_file: str | Path | None = None,
    properties: tuple[str, ...] = (),
) -> None:
    """Start ``command`` in one collected user unit."""
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        raise RuntimeError("systemd-run is not on PATH")
    started = subprocess.run(
        [
            systemd_run,
            "--user",
            "--quiet",
            f"--unit={unit}",
            "--collect",
            "--service-type=exec",
            *(f"--property={property}" for property in properties),
            *(
                (f"--property=EnvironmentFile={environment_file}",)
                if environment_file is not None
                else ()
            ),
            *(f"--setenv={name}={value}" for name, value in environment.items()),
            "--",
            *command,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        raise RuntimeError(
            f"transient user unit {unit} could not start: {started.stderr.strip()}"
        )


def _unit_property(unit: str, name: str) -> str:
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        raise RuntimeError("systemctl is not on PATH")
    shown = subprocess.run(
        [
            systemctl,
            "--user",
            "show",
            "--value",
            "--property",
            name,
            f"{unit}.service",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    value = shown.stdout.strip()
    if shown.returncode != 0 or not value:
        raise RuntimeError(f"transient user unit {unit} has no {name}")
    return value


def unit_active(unit: str) -> bool:
    """Whether the transient user unit is active, activating, or deactivating."""
    return _unit_property(unit, "ActiveState") in (
        "active",
        "activating",
        "deactivating",
    )


def unit_main_pid(unit: str) -> int:
    """Return the main PID of one active transient user unit."""
    raw = _unit_property(unit, "MainPID")
    if not raw.isdigit() or int(raw) <= 0:
        raise RuntimeError(f"transient user unit {unit} has no active main process")
    return int(raw)


def stop_transient_user_unit(unit: str) -> None:
    """Stop one transient user unit if it still exists."""
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        raise RuntimeError("systemctl is not on PATH")
    stopped = subprocess.run(
        [systemctl, "--user", "stop", f"{unit}.service"],
        check=False,
        capture_output=True,
        text=True,
    )
    if stopped.returncode == 0:
        return
    if _unit_property(unit, "LoadState") != "not-found":
        raise RuntimeError(
            f"transient user unit {unit} could not stop: {stopped.stderr.strip()}"
        )
