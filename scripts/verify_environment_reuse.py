"""Executed cross-run environment checks; all temporary files stay in .cache."""
from __future__ import annotations

import asyncio
import json
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile

for switch, module_name in (
    ('--implementation', 'engine.environment_reuse'),
    ('--program-environment-implementation', 'engine.program_environment'),
    ('--executor-implementation', 'infra.exec.local'),
):
    if switch in sys.argv:
        index = sys.argv.index(switch)
        implementation = Path(sys.argv[index + 1])
        del sys.argv[index:index + 2]
        spec = importlib.util.spec_from_file_location(module_name, implementation)
        if spec is None or spec.loader is None:
            raise RuntimeError('environment implementation cannot be loaded')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

from engine.environment_reuse import EnvironmentReuse

MEASUREMENTS: list[dict[str, object]] = []


class ReuseChecks(unittest.TestCase):
    def setUp(self) -> None:
        root = Path.cwd() / '.cache'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='environment-reuse-', dir=root)
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        self.environments: list[EnvironmentReuse] = []
        self.addCleanup(self.close_environments)

    def close_environments(self) -> None:
        for environment in reversed(self.environments):
            environment.close()

    def environment(self, number: int, name: str = 'example') -> EnvironmentReuse:
        home = self.task / (f'20261103T{number:06d}Z_' + uuid.uuid4().hex)
        environment = EnvironmentReuse(home, name)
        environment.claim()
        self.environments.append(environment)
        return environment

    def python(self, environment: EnvironmentReuse, code: str) -> str:
        return subprocess.check_output(
            [str(environment.prefix / 'bin/python'), '-I', '-c', code], text=True,
        ).strip()

    def base(self) -> EnvironmentReuse:
        environment = self.environment(1)
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(environment.prefix)], check=True)
        site = Path(self.python(environment, "import sysconfig; print(sysconfig.get_path('purelib'))"))
        (site / 'reuse_probe.py').write_text("VALUE = 'parent'\n")
        package = site / 'reuse_probe-1.0.dist-info'
        package.mkdir()
        (package / 'METADATA').write_text('Metadata-Version: 2.1\nName: reuse-probe\nVersion: 1.0\n')
        (package / 'entry_points.txt').write_text('[console_scripts]\nreuse-probe = reuse_probe:main\n')
        (site / 'reuse_probe.py').write_text("VALUE = 'parent'\ndef main():\n    print(VALUE)\n")
        asyncio.run(environment.publish(lambda: 30.0))
        environment.close()
        return environment

    def overlay(self, number: int) -> EnvironmentReuse:
        environment = self.environment(number)
        selected = environment.select()
        self.assertIsNotNone(selected)
        if selected is None:
            raise AssertionError('no reusable environment')
        asyncio.run(environment.create_overlay(selected, lambda: 30.0))
        return environment

    def test_second_and_third_run_share_packages_and_isolate_changes(self) -> None:
        base = self.base()
        second = self.overlay(2)
        result = json.loads(self.python(second, 'import json,reuse_probe,sys; print(json.dumps([reuse_probe.VALUE,reuse_probe.__file__,sys.prefix]))'))
        self.assertEqual(result[0], 'parent')
        self.assertTrue(Path(result[1]).is_relative_to(base.prefix))
        self.assertEqual(Path(result[2]), second.prefix)
        site = Path(self.python(second, "import sysconfig; print(sysconfig.get_path('purelib'))"))
        (site / 'reuse_probe.py').write_text("VALUE = 'child'\ndef main():\n    print(VALUE)\n")
        self.assertEqual(self.python(base, 'import reuse_probe; print(reuse_probe.VALUE)'), 'parent')
        self.assertEqual(subprocess.check_output([str(second.prefix / 'bin/reuse-probe')], text=True).strip(), 'child')
        asyncio.run(second.publish(lambda: 30.0))
        second.close()
        third = self.overlay(3)
        self.assertEqual(self.python(third, 'import reuse_probe; print(reuse_probe.VALUE)'), 'child')
        self.assertEqual(third.inherited_prefixes, (second.prefix, base.prefix))
        self.assertFalse(any(third.prefix.rglob('reuse_probe.py')))

    def test_active_source_is_skipped_and_inherited_source_cannot_resume(self) -> None:
        base = self.base()
        writer = EnvironmentReuse(base.run_home, 'example')
        writer.claim()
        self.addCleanup(writer.close)
        child = self.environment(2)
        self.assertIsNone(child.select())
        asyncio.run(writer.publish(lambda: 30.0))
        writer.close()
        self.assertEqual(child.select(), base.prefix)
        with self.assertRaises(BlockingIOError):
            writer.claim()
        child.close()
        writer.claim()

    def test_another_process_cannot_write_an_inherited_base(self) -> None:
        import engine
        base = self.base()
        child = self.overlay(2)
        implementation = sys.modules[EnvironmentReuse.__module__].__file__
        code = (
            'import importlib.util,sys; from pathlib import Path; '
            "s=importlib.util.spec_from_file_location('tested_reuse',sys.argv[1]); "
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            "e=m.EnvironmentReuse(Path(sys.argv[2]),'example'); "
            'exec("try:\\n e.claim()\\nexcept BlockingIOError:\\n sys.exit(42)\\nelse:\\n e.close()")'
        )
        env = {**os.environ, 'PYTHONPATH': str(Path(engine.__file__).parent.parent)}
        def attempt() -> int:
            return subprocess.run([sys.executable, '-c', code, str(implementation), str(base.run_home)],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode
        self.assertEqual(attempt(), 42)
        self.assertTrue(base.ready_path.exists())
        child.close()
        self.assertEqual(attempt(), 0)

    def test_different_base_and_incomplete_setup_are_not_reused(self) -> None:
        base = self.base()
        different = self.environment(2, 'different')
        self.assertIsNone(different.select())
        base.ready_path.unlink()
        same = self.environment(3)
        self.assertIsNone(same.select())

    def test_resume_restores_ancestor_leases(self) -> None:
        base = self.base()
        child = self.overlay(2)
        child.close()
        resumed = EnvironmentReuse(child.run_home, 'example')
        self.addCleanup(resumed.close)
        resumed.claim()
        resumed.restore()
        self.assertEqual(resumed.inherited_prefixes, (base.prefix,))
        self.assertEqual(self.python(resumed, 'import reuse_probe; print(reuse_probe.VALUE)'), 'parent')
        with self.assertRaises(BlockingIOError):
            EnvironmentReuse(base.run_home, 'example').claim()

    def test_manifest_cannot_grant_private_run_paths(self) -> None:
        base = self.base()
        value = json.loads(base.ready_path.read_text())
        value['inherited_prefixes'] = [str(base.run_home / 'private')]
        base.ready_path.write_text(json.dumps(value))
        child = self.environment(2)
        with self.assertLogs('engine.environment_reuse', level='WARNING'):
            self.assertIsNone(child.select())
        self.assertEqual(child.inherited_prefixes, ())

    def test_interpreter_launch_preserves_overlay_and_directory_alias(self) -> None:
        from infra.exec.local import resolve_interpreter_path
        self.base()
        child = self.overlay(2)
        alias = self.task / 'directory-alias'
        alias.symlink_to(child.run_home, target_is_directory=True)
        python = resolve_interpreter_path(str(alias / 'program-environment/env/bin/python'))
        prefix = subprocess.check_output([python, '-I', '-c', 'import sys; print(sys.prefix)'], text=True).strip()
        self.assertEqual(Path(prefix), child.prefix)

    def test_interrupted_overlay_is_not_ready_and_restores_leases(self) -> None:
        base = self.base()
        child = self.environment(2)
        self.assertEqual(child.select(), base.prefix)
        lineage = child.root / 'inheritance.json'
        lineage.write_text(json.dumps(dict(schema=1, base_name='example', status='creating',
                                          inherited_prefixes=[str(base.prefix)])))
        self.assertFalse(child.restore())

    def test_fresh_fallback_discards_interrupted_ancestry(self) -> None:
        from unittest.mock import AsyncMock, patch
        from engine.program_environment import ProgramEnvironment
        from infra.process.command import run_captured
        base = self.base()
        child = self.environment(2)
        self.assertEqual(child.select(), base.prefix)
        (child.root / 'inheritance.json').write_text(json.dumps(dict(
            schema=1, base_name='example', status='creating',
            inherited_prefixes=[str(base.prefix)])))
        child.close()
        base.ready_path.unlink()
        program = ProgramEnvironment(run_home=child.run_home, conda_env_name='example')
        self.addCleanup(program.close)
        program._reuse.claim()
        self.assertFalse(program._reuse.restore())
        async def fresh_environment(*args: object) -> object:
            return await run_captured([sys.executable, '-m', 'venv', '--without-pip',
                                       str(program.prefix)], 30.0)
        # Exercise the actual fallback while substituting a real local venv
        # for the external Conda solver; no package network access is needed.
        with patch('engine.program_environment.resolve_task_interpreter_or_none', AsyncMock(return_value=None)), \
                patch('engine.program_environment.owned', return_value=Path(sys.executable)), \
                patch('engine.program_environment.micromamba_root_prefix', return_value=self.task), \
                patch('engine.program_environment.run_captured', side_effect=fresh_environment):
            asyncio.run(program._create(lambda: 30.0))
        program.close()
        resumed = EnvironmentReuse(child.run_home, 'example')
        self.addCleanup(resumed.close)
        resumed.claim()
        self.assertTrue(resumed.restore())
        self.assertEqual(resumed.inherited_prefixes, ())
        self.assertTrue((resumed.prefix / 'bin/python').is_file())

    def test_real_package_install_and_child_upgrade_keep_parent_bytes(self) -> None:
        import time
        first = self.environment(1)
        subprocess.run([sys.executable, '-m', 'venv', str(first.prefix)], check=True)
        def wheel(version: str, payload: bytes) -> Path:
            path = self.task / f'cached_demo-{version}-py3-none-any.whl'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('cached_demo/__init__.py', f"VALUE = {version!r}\ndef main():\n    print(VALUE)\n")
                archive.writestr('cached_demo/data.bin', payload)
                archive.writestr(f'cached_demo-{version}.dist-info/METADATA', f'Metadata-Version: 2.1\nName: cached-demo\nVersion: {version}\n')
                archive.writestr(f'cached_demo-{version}.dist-info/WHEEL', 'Wheel-Version: 1.0\nGenerator: environment-reuse-check\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                archive.writestr(f'cached_demo-{version}.dist-info/entry_points.txt', '[console_scripts]\ncached-demo = cached_demo:main\n')
                archive.writestr(f'cached_demo-{version}.dist-info/RECORD', '')
            return path
        first_wheel = wheel('1.0', b'prepared dependency\n' * 900000)
        def install(environment: EnvironmentReuse, path: Path) -> None:
            subprocess.run([str(environment.prefix / 'bin/python'), '-I', '-m', 'pip',
                            '--disable-pip-version-check', '--no-cache-dir', 'install', '--no-index', str(path)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        install(first, first_wheel)
        data = Path(self.python(first, "import cached_demo; print(cached_demo.__file__)"))
        before = hashlib.sha256(data.read_bytes()).hexdigest()
        asyncio.run(first.publish(lambda: 30.0))
        first.close()
        started = time.monotonic()
        second = self.overlay(2)
        elapsed = time.monotonic() - started
        self.assertEqual(subprocess.check_output([str(second.prefix / 'bin/cached-demo')], text=True).strip(), '1.0')
        self.assertFalse(any(second.prefix.rglob('data.bin')))
        first_bytes = sum(p.stat().st_blocks * 512 for p in first.prefix.rglob('*') if p.is_file() and not p.is_symlink())
        second_bytes = sum(p.stat().st_blocks * 512 for p in second.prefix.rglob('*') if p.is_file() and not p.is_symlink())
        self.assertLess(second_bytes, first_bytes // 10)
        install(second, wheel('2.0', b'updated'))
        self.assertEqual(self.python(second, 'import cached_demo; print(cached_demo.VALUE)'), '2.0')
        self.assertEqual(self.python(first, 'import cached_demo; print(cached_demo.VALUE)'), '1.0')
        self.assertEqual(hashlib.sha256(data.read_bytes()).hexdigest(), before)
        measurement: dict[str, object] = dict(measurement='real dependency reuse', base_bytes=first_bytes,
                           overlay_bytes=second_bytes, overlay_seconds=round(elapsed, 4))
        MEASUREMENTS.append(measurement)
        print(json.dumps(measurement))


if __name__ == '__main__':
    report = None
    if '--report' in sys.argv:
        index = sys.argv.index('--report')
        report = Path(sys.argv[index + 1])
        del sys.argv[index:index + 2]
    result = unittest.main(exit=False).result
    if report is not None:
        report.write_text(json.dumps(dict(passed=result.wasSuccessful(), checks=result.testsRun,
                                          measurements=MEASUREMENTS), indent=2) + '\n')
    sys.exit(0 if result.wasSuccessful() else 1)
