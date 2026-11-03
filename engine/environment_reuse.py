"""Dependency-only inheritance between runs of the same task on one host.

Availability is a cache hint, never inherited task readiness. A live run holds
an exclusive environment lease; descendants hold shared leases on every base.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from pathlib import Path
import platform
import shlex
import stat
from collections.abc import Callable

from engine.paths import RunTimestamp
from infra.process.command import run_captured
from infra.util.paths import is_within

logger = logging.getLogger(__name__)
RUNTIME_PYTHON_FLOOR = (3, 11)
RUNTIME_PYTHON_CEILING = (3, 14)


def _record(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('environment metadata must be an owned regular file')
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError('environment metadata must be an object')
    return value


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _lock(path: Path, shared: bool) -> int:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('environment lease must be an owned regular file')
        fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class EnvironmentReuse:
    """Keep environment bases alive and construct small, independently writable overlays."""

    def __init__(self, run_home: Path, base_name: str) -> None:
        self.run_home = run_home.resolve()
        self.root = self.run_home / 'program-environment'
        self.prefix = self.root / 'env'
        self.ready_path = self.root / 'available.json'
        self._lineage = self.root / 'inheritance.json'
        self._base_name = base_name
        self._writer: int | None = None
        self._readers: list[int] = []
        self.inherited_prefixes: tuple[Path, ...] = ()

    def claim(self) -> None:
        if self._writer is not None:
            return
        if self.root.is_symlink() or self.prefix.is_symlink():
            raise ValueError('a run environment cannot be a directory symlink')
        self.root.mkdir(parents=True, exist_ok=True)
        self._writer = _lock(self.root / 'usage.lock', shared=False)
        self.ready_path.unlink(missing_ok=True)

    def close(self) -> None:
        for descriptor in self._readers:
            os.close(descriptor)
        self._readers.clear()
        if self._writer is not None:
            os.close(self._writer)
            self._writer = None

    def release_bases(self) -> None:
        self._lease(())

    def record_creation(self, *, ready: bool) -> None:
        """Persist current ancestry and distinguish partial prefixes from completed creation."""
        _write(self._lineage, dict(schema=1, base_name=self._base_name,
                                  status='ready' if ready else 'creating',
                                  inherited_prefixes=[str(p) for p in self.inherited_prefixes]))

    def _prefixes(self, values: object) -> tuple[Path, ...]:
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError('invalid inherited environment prefixes')
        prefixes = tuple(Path(v) for v in values)
        if len(set(prefixes)) != len(prefixes) or self.prefix in prefixes:
            raise ValueError('cyclic or duplicated environment lineage')
        for prefix in prefixes:
            home = prefix.parent.parent
            if (prefix != home / 'program-environment/env'
                    or home.parent != self.run_home.parent
                    or not RunTimestamp.is_canonical(home.name)
                    or prefix.resolve() != prefix
                    or not (prefix / 'bin/python').is_file()):
                raise ValueError('inherited paths must be canonical sibling run environments')
        return prefixes

    def _lease(self, prefixes: tuple[Path, ...]) -> None:
        descriptors: list[int] = []
        try:
            for prefix in prefixes:
                descriptors.append(_lock(prefix.parent / 'usage.lock', shared=True))
        except BaseException:
            for descriptor in descriptors:
                os.close(descriptor)
            raise
        for descriptor in self._readers:
            os.close(descriptor)
        self._readers = descriptors
        self.inherited_prefixes = prefixes

    def restore(self) -> bool:
        """Reacquire bases before using an existing overlay; false means interrupted creation."""
        if not self._lineage.exists():
            return True
        value = _record(self._lineage)
        if value.get('schema') != 1 or value.get('base_name') != self._base_name:
            raise ValueError('environment lineage does not match this run')
        prefixes = self._prefixes(value.get('inherited_prefixes'))
        self._lease(prefixes)
        return value.get('status') == 'ready'

    def select(self) -> Path | None:
        """Select the latest compatible, Setup-approved base that has no live writer."""
        for home in sorted(self.run_home.parent.iterdir(), reverse=True):
            if home == self.run_home or not RunTimestamp.is_canonical(home.name):
                continue
            ready = home / 'program-environment/available.json'
            if not ready.is_file():
                continue
            try:
                value = _record(ready)
                if (value.get('schema') != 1 or value.get('base_name') != self._base_name
                        or value.get('platform') != platform.platform()
                        or not RUNTIME_PYTHON_FLOOR <= tuple(value.get('python_version', ())) < RUNTIME_PYTHON_CEILING):
                    continue
                prefixes = self._prefixes([str(home / 'program-environment/env'), *value.get('inherited_prefixes', [])])
                self._lease(prefixes)
                if _record(ready) != value:
                    self.release_bases()
                    continue
                return prefixes[0]
            except BlockingIOError:
                self.release_bases()
                continue
            except (OSError, ValueError, TypeError):
                self.release_bases()
                logger.warning('Ignoring invalid environment cache metadata for %s', home.name)
        return None

    async def create_overlay(self, base: Path, remaining_s: Callable[[], float]) -> None:
        """Create an overlay without copying dependency files or running a package solver."""
        if not self.inherited_prefixes or self.inherited_prefixes[0] != base:
            raise ValueError('the selected base must have an active shared lease')
        self.record_creation(ready=False)
        probe = await run_captured([str(base / 'bin/python'), '-I', '-c',
            "import json,sysconfig; from importlib.metadata import entry_points; "
            "print(json.dumps({'sites':list(dict.fromkeys([sysconfig.get_path('purelib'),sysconfig.get_path('platlib')])),"
            "'commands':sorted({e.name for e in entry_points(group='console_scripts')})}))"], remaining_s())
        if probe.returncode:
            raise ValueError('the inherited environment interpreter cannot be queried')
        info = json.loads(probe.stdout)
        created = await run_captured([str(base / 'bin/python'), '-I', '-m', 'venv',
                                      '--without-pip', '--system-site-packages', str(self.prefix)], remaining_s())
        if created.returncode:
            raise ValueError('could not create an inherited environment overlay')
        python = self.prefix / 'bin/python'
        checked = await run_captured([str(python), '-I', '-c',
                                     "import sysconfig; print(sysconfig.get_path('purelib'))"], remaining_s())
        if checked.returncode:
            raise ValueError('the environment overlay interpreter cannot be queried')
        site = Path(checked.stdout.strip())
        if not is_within(site, self.prefix):
            raise ValueError('the overlay interpreter selected an unexpected prefix')
        for path in info['sites']:
            if not is_within(path, base):
                raise ValueError('a dependency site must belong to the selected environment')
        (site / '_aibuildai_inherited.pth').write_text(
            ''.join(f'import site; site.addsitedir({path!r})\n' for path in info['sites']))
        # Entry points use THIS interpreter, so child-only upgrades take effect.
        for name in info['commands']:
            if not name or Path(name).name != name or '\\' in name:
                raise ValueError('invalid inherited console entry point')
            target = self.prefix / 'bin' / name
            if target.exists():
                continue
            code = ("import sys; from importlib.metadata import entry_points; "
                    f"sys.argv[0]={name!r}; sys.exit(next(e for e in entry_points(group='console_scripts') if e.name=={name!r}).load()())")
            target.write_text(f'#!/bin/sh\nexec {shlex.quote(str(python))} -I -c {shlex.quote(code)} "$@"\n')
            target.chmod(0o755)
        self.record_creation(ready=True)

    async def publish(self, remaining_s: Callable[[], float]) -> None:
        """Publish availability only after the caller's Setup verification succeeded."""
        if self._writer is None:
            raise ValueError('publishing requires the run environment writer lease')
        probe = await run_captured([
            str(self.prefix / 'bin/python'), '-I', '-c',
            'import json,sys; print(json.dumps(list(sys.version_info[:2])))'], remaining_s())
        if probe.returncode:
            raise ValueError('the prepared environment interpreter cannot be queried')
        version = json.loads(probe.stdout)
        _write(self.ready_path, dict(schema=1, base_name=self._base_name,
                                    python_version=version, platform=platform.platform(),
                                    inherited_prefixes=[str(p) for p in self.inherited_prefixes]))
