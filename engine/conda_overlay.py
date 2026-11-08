"""A run-owned Python overlay over one explicitly configured host environment."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
from collections.abc import Callable

from infra.process.command import run_captured
from infra.util.paths import is_within


async def create_conda_overlay(
    base_python: str, prefix: Path, remaining_s: Callable[[], float],
) -> Path:
    """Use the named interpreter and its packages without copying its prefix."""
    executable = Path(base_python)
    base_python = str(executable.parent.resolve() / executable.name)
    base = Path(base_python).parent.resolve().parent
    probe = await run_captured([base_python, '-I', '-B', '-c',
        "import json,sysconfig; from importlib.metadata import entry_points; "
        "print(json.dumps({'sites':list(dict.fromkeys([sysconfig.get_path('purelib'),sysconfig.get_path('platlib')])),"
        "'commands':sorted({e.name for e in entry_points(group='console_scripts')})}))"], remaining_s())
    if probe.returncode:
        raise ValueError('could not inspect the configured Conda environment')
    info = json.loads(probe.stdout)
    sites = info['sites']
    if not sites or any(not is_within(path, base) for path in sites):
        raise ValueError('configured environment package paths escape its prefix')
    created = await run_captured([base_python, '-I', '-B', '-m', 'venv',
                                  '--system-site-packages', str(prefix)], remaining_s())
    if created.returncode:
        raise ValueError(f'could not create Conda overlay: {(created.stderr or created.stdout)[-2000:]}')
    python = prefix/'bin/python'
    probe = await run_captured([str(python), '-I', '-B', '-c',
        "import sysconfig; print(sysconfig.get_path('purelib'))"], remaining_s())
    site = Path(probe.stdout.strip())
    if probe.returncode or not is_within(site, prefix):
        raise ValueError('overlay interpreter does not use its own package directory')
    (site/'_aibuildai_conda_base.pth').write_text(
        ''.join(f'import site; site.addsitedir({path!r})\n' for path in sites))
    for name in info['commands']:
        if not name or Path(name).name != name or '\\' in name:
            raise ValueError('invalid configured environment console command')
        target = prefix/'bin'/name
        if target.exists():
            continue
        code = ("import sys; from importlib.metadata import entry_points; "
                f"sys.argv[0]={name!r}; sys.exit(next(e for e in entry_points(group='console_scripts') if e.name=={name!r}).load()())")
        target.write_text(f'#!/bin/sh\nexec {shlex.quote(str(python))} -I -B -c {shlex.quote(code)} "$@"\n')
        target.chmod(0o755)
    return base
