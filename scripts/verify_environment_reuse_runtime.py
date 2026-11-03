"""Install the real Program runtime into two consecutive dependency-sharing runs."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from engine.program_environment import ProgramEnvironment


async def verify(root: Path) -> dict[str, object]:
    deadline = time.monotonic() + 300
    def remaining() -> float:
        return max(0.0, deadline - time.monotonic())
    first = ProgramEnvironment(
        run_home=root / ('20261103T000001Z_' + uuid.uuid4().hex), conda_env_name='runtime-check')
    second = ProgramEnvironment(
        run_home=root / ('20261103T000002Z_' + uuid.uuid4().hex), conda_env_name='runtime-check')
    try:
        first.prefix.parent.mkdir(parents=True)
        subprocess.run([sys.executable, '-m', 'venv', str(first.prefix)], check=True, timeout=60)
        await first.prepare(remaining)
        code = "import sysconfig; from pathlib import Path; (Path(sysconfig.get_path('purelib'))/'shared_dependency.py').write_text('VALUE = 731\\n')"
        subprocess.run([first.python, '-I', '-c', code], check=True, timeout=15)
        await first.publish(remaining)
        first.close()
        started = time.monotonic()
        await second.prepare(remaining)
        elapsed = time.monotonic() - started
        output = json.loads(subprocess.check_output([
            second.python, '-I', '-c',
            "import json,sys,engine,shared_dependency; from importlib.metadata import version; "
            "print(json.dumps({'prefix':sys.prefix,'runtime_source':engine.__file__,"
            "'version':version('aibuildai'),'dependency_source':shared_dependency.__file__,'value':shared_dependency.VALUE}))"],
            text=True, timeout=15))
        if (Path(output['prefix']) != second.prefix or output['value'] != 731
                or not Path(output['runtime_source']).is_relative_to(second.prefix)
                or not Path(output['dependency_source']).is_relative_to(first.prefix)):
            raise AssertionError(f'wrong runtime or dependency origin: {output}')
        return dict(passed=True, second_prepare_seconds=round(elapsed, 4),
                    runtime_installed_in_child=True, dependency_shared_without_copy=True,
                    runtime_version=output['version'])
    finally:
        second.close()
        first.close()


def main() -> None:
    cache = Path.cwd() / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='environment-runtime-', dir=cache) as directory:
        previous = os.environ.get('PIP_CACHE_DIR')
        os.environ['PIP_CACHE_DIR'] = str(cache / 'pip')
        try:
            result = asyncio.run(verify(Path(directory)))
        finally:
            if previous is None:
                os.environ.pop('PIP_CACHE_DIR', None)
            else:
                os.environ['PIP_CACHE_DIR'] = previous
    print(json.dumps(result))


if __name__ == '__main__':
    main()
