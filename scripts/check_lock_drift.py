"""Check that package lists and lock files agree."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parent.parent
VIA_PROJECT = "aibuildai (pyproject.toml)"


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _direct_requirements(extras: tuple[str, ...]) -> dict[str, Requirement]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    raw = list(data["project"]["dependencies"])
    for extra in extras:
        raw += data["project"]["optional-dependencies"][extra]

    direct: dict[str, Requirement] = {}
    for value in raw:
        requirement = Requirement(value)
        if requirement.marker is None or requirement.marker.evaluate():
            direct[_name(requirement.name)] = requirement
    return direct


def _lock_pins(lock_name: str) -> dict[str, tuple[str, bool]]:
    pins: dict[str, tuple[str, bool]] = {}
    current: str | None = None
    for line in (REPO_ROOT / lock_name).read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==(\S+)\s*$", line)
        if match:
            current = _name(match.group(1))
            pins[current] = (match.group(2), False)
            continue
        if current is not None and line.startswith((" ", "\t")):
            if VIA_PROJECT in line:
                pins[current] = (pins[current][0], True)
            continue
        current = None
    return pins


def _violations(
    direct: dict[str, Requirement],
    pins: dict[str, tuple[str, bool]],
) -> list[str]:
    found: list[str] = []
    for name, requirement in sorted(direct.items()):
        if name not in pins:
            found.append(f"{name}: missing from the lock")
            continue
        version, _ = pins[name]
        if not requirement.specifier.contains(version, prereleases=True):
            found.append(
                f"{name}: {version} does not match {requirement.specifier}"
            )
    for name, (version, via_project) in sorted(pins.items()):
        if via_project and name not in direct:
            found.append(f"{name}=={version}: no longer declared in pyproject.toml")
    return found


def main() -> int:
    lock_extras = {
        "requirements.lock": (),
        "requirements-dev.lock": ("dev",),
    }
    violations: list[str] = []
    for lock_name, extras in lock_extras.items():
        for violation in _violations(
            _direct_requirements(extras),
            _lock_pins(lock_name),
        ):
            violations.append(f"{lock_name}: {violation}")

    if violations:
        for violation in violations:
            print(violation)
        return 1
    print("lock files match pyproject.toml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
