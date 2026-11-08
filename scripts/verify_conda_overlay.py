"""Exercise configured-base overlays in real Python processes, under .cache."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

# Test the exact proposed Git-index versions without creating a second checkout.
if '--staged-index' in sys.argv:
    import types
    index = sys.argv.index('--staged-index')
    git_index = sys.argv[index + 1]
    del sys.argv[index:index + 2]
    for module_name in ['infra.host_resource.confinement', 'infra.exec.spec',
                        'infra.exec.confinement_build', 'engine.conda_overlay',
                        'engine.program_environment', 'infra.exec.local',
                        'engine.work_unit.agent.spec']:
        path = module_name.replace('.', '/') + '.py'
        source = subprocess.check_output(['git', 'show', ':' + path],
            env={**os.environ, 'GIT_INDEX_FILE': git_index}, text=True)
        module = types.ModuleType(module_name)
        module.__file__ = str(Path(path).resolve())
        sys.modules[module_name] = module
        exec(compile(source, module.__file__, 'exec'), module.__dict__)

from engine.program_environment import ProgramEnvironment


class CondaOverlayChecks(unittest.TestCase):
    def setUp(self) -> None:
        cache = Path.cwd()/'.cache'
        cache.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='conda-overlay-check-', dir=cache)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.base = self.root/'named-environment'
        subprocess.run([sys.executable, '-m', 'venv', str(self.base)], check=True)
        self.base_python = str(self.base/'bin/python')
        self.site = Path(self.run_python(self.base_python, "import sysconfig; print(sysconfig.get_path('purelib'))"))
        (self.site/'overlay_fixture.py').write_text("VALUE = 'base'\ndef main():\n print(VALUE)\n")
        metadata = self.site/'overlay_fixture-1.0.dist-info'
        metadata.mkdir()
        (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: overlay-fixture\nVersion: 1.0\n')
        (metadata/'entry_points.txt').write_text('[console_scripts]\noverlay-fixture = overlay_fixture:main\n')

    def run_python(self, executable: str, code: str) -> str:
        return subprocess.check_output([executable, '-I', '-B', '-c', code], text=True).strip()

    def overlay(self, name: str) -> ProgramEnvironment:
        environment = ProgramEnvironment(run_home=self.root/name, conda_env_name='named-environment')
        # Only Conda discovery is substituted: creation/imports/commands are real.
        with patch('engine.program_environment.resolve_task_interpreter_or_none', AsyncMock(return_value=self.base_python)), \
             patch('engine.program_environment.conda_executable', side_effect=AssertionError('configured environment must not be cloned')):
            asyncio.run(environment._create(lambda: 60.0))
        return environment

    def test_configured_base_is_shared_without_clone_or_previous_run(self) -> None:
        self.overlay('old-run')
        second = self.overlay('new-run')
        shutil.rmtree(self.root/'old-run')
        result = json.loads(self.run_python(second.python,
            "import json,sys,overlay_fixture; print(json.dumps([sys.prefix,overlay_fixture.VALUE,overlay_fixture.__file__]))"))
        self.assertEqual(result, [str(self.root/'new-run/program-environment/env'), 'base', str(self.site/'overlay_fixture.py')])
        self.assertFalse(any(Path(second.python).parent.parent.rglob('overlay_fixture.py')))
        self.assertFalse((self.root/'old-run').exists())
        self.assertEqual(subprocess.check_output([str(Path(second.python).parent/'overlay-fixture')], text=True).strip(), 'base')

    def test_overlay_install_and_console_command_leave_base_unchanged(self) -> None:
        before = {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.base.rglob('*') if p.is_file() and not p.is_symlink()}
        child = self.overlay('run')
        wheel = self.root/'overlay_fixture-2.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('overlay_fixture.py', "VALUE = 'child'\ndef main():\n print(VALUE)\n")
            archive.writestr('overlay_fixture-2.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: overlay-fixture\nVersion: 2.0\n')
            archive.writestr('overlay_fixture-2.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            archive.writestr('overlay_fixture-2.0.dist-info/entry_points.txt', '[console_scripts]\noverlay-fixture = overlay_fixture:main\n')
            archive.writestr('overlay_fixture-2.0.dist-info/RECORD', '')
        subprocess.run([child.python, '-I', '-B', '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)], check=True, capture_output=True)
        self.assertEqual(self.run_python(child.python, 'import overlay_fixture; print(overlay_fixture.VALUE)'), 'child')
        self.assertEqual(subprocess.check_output([str(Path(child.python).parent/'overlay-fixture')], text=True).strip(), 'child')
        self.assertEqual(self.run_python(self.base_python, 'import overlay_fixture; print(overlay_fixture.VALUE)'), 'base')
        after = {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.base.rglob('*') if p.is_file() and not p.is_symlink()}
        self.assertEqual(after, before)

    def test_base_stays_read_only_under_a_writable_parent_mount(self) -> None:
        from infra.host_resource.confinement import Confinement
        child = self.overlay('run')
        confinement = Confinement(cgroup=self.root, apply_bwrap=True,
            ro_paths=('/',), rw_paths=(str(self.root),),
            dev_nodes=('/dev/urandom',),
            read_only_overrides=tuple(str(p) for p in child.read_only_prefixes))
        code = "from pathlib import Path; Path(%r).write_text('bad')" % str(self.base/'forbidden')
        rejected = subprocess.run(confinement._exec_argv([child.python, '-I', '-B', '-c', code], {}), capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('Read-only file system', rejected.stderr)
        self.assertFalse((self.base/'forbidden').exists())
        code = "from pathlib import Path; Path(%r).write_text('ok')" % str(Path(child.python).parent.parent/'own-install')
        subprocess.run(confinement._exec_argv([child.python, '-I', '-B', '-c', code], {}), check=True)
        with self.assertRaises(AssertionError):
            confinement.require_writable(str(self.base/'forbidden'), owner='test')

    def test_resume_preserves_base_and_refuses_a_missing_base(self) -> None:
        child = self.overlay('run')
        resumed = ProgramEnvironment(run_home=self.root/'run', conda_env_name='named-environment')
        with patch('engine.program_environment.resolve_task_interpreter_or_none', AsyncMock(return_value=self.base_python)):
            asyncio.run(resumed._restore_overlay(lambda: 30.0))
        self.assertEqual(resumed.read_only_prefixes, (self.base,))
        with patch('engine.program_environment.resolve_task_interpreter_or_none', AsyncMock(return_value=None)):
            with self.assertRaisesRegex(Exception, 'base is missing or changed'):
                asyncio.run(resumed._restore_overlay(lambda: 30.0))
        self.assertTrue(Path(child.python).is_file())

    def test_agent_setup_cannot_write_the_named_base(self) -> None:
        from types import SimpleNamespace
        from typing import cast
        from config import AgentConfig
        from engine.work_unit.agent.policy import RolePolicy, SystemDir
        from engine.work_unit.agent.spec import _sandboxed_confinement
        child = self.overlay('run')
        config = cast(AgentConfig, SimpleNamespace(resources=SimpleNamespace(sandbox=SimpleNamespace(
            system_read_paths=['/'], system_write_paths=[]))))
        with patch('engine.work_unit.agent.spec.run_program_environment', return_value=child), \
             patch('engine.work_unit.agent.spec.conda_root', return_value=str(self.root)):
            conf = _sandboxed_confinement(
                RolePolicy(tools=(), task_environment=False, system_write=(SystemDir.CONDA_ROOT,)),
                config, cwd=str(self.root), read_dirs=(), write_dirs=(), run_cache_dir=None,
                plugin_dirs=(), dev_nodes=('/dev/urandom',), cgroup=self.root,
                commands_cgroup=self.root, step_dir=str(self.root), mask_paths=(),
                task_env_prefix=None, tool_bin_dir=None)
        code = "from pathlib import Path; Path(%r).write_text('bad')" % str(self.base/'forbidden')
        result = subprocess.run(conf._exec_argv([child.python, '-I', '-B', '-c', code], {}), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Read-only file system', result.stderr)

    def test_interpreter_directory_alias_preserves_the_overlay(self) -> None:
        from infra.exec.local import resolve_interpreter_path
        child = self.overlay('run')
        alias = self.root/'alias'
        alias.symlink_to(self.root/'run', target_is_directory=True)
        python = resolve_interpreter_path(str(alias/'program-environment/env/bin/python'))
        self.assertEqual(self.run_python(python, 'import sys; print(sys.prefix)'), str(child.prefix))

    def test_named_base_alias_is_not_required_inside_the_mount(self) -> None:
        from infra.host_resource.confinement import Confinement
        alias = self.root/'base-alias'
        alias.symlink_to(self.base, target_is_directory=True)
        child = ProgramEnvironment(run_home=self.root/'run', conda_env_name='named-environment')
        with patch('engine.program_environment.resolve_task_interpreter_or_none', AsyncMock(return_value=str(alias/'bin/python'))):
            asyncio.run(child._create(lambda: 60.0))
        conf = Confinement(cgroup=self.root, apply_bwrap=True,
            ro_paths=('/usr', '/etc', '/run', str(Path(sys.base_prefix).parent), str(Path(sys.prefix).resolve())),
            rw_paths=(str(child.prefix),), read_only_overrides=(str(self.base),),
            dev_nodes=('/dev/urandom',))
        result = subprocess.run(conf._exec_argv([child.python, '-I', '-B', '-c',
            'import overlay_fixture; print(overlay_fixture.VALUE)'], {}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'base')

    def test_unset_conda_keeps_legacy_named_task_clone(self) -> None:
        child = ProgramEnvironment(run_home=self.root/'run', conda_env_name='task-name', use_overlay=False)
        from infra.process.command import run_captured
        calls = []
        async def clone(command: list[str], timeout: float):
            if command[0] != 'conda':
                return await run_captured(command, timeout)
            calls.append(command)
            return await run_captured([sys.executable, '-m', 'venv', '--without-pip', str(child.prefix)], timeout)
        with patch('engine.program_environment.resolve_task_interpreter_or_none', AsyncMock(return_value=self.base_python)), \
             patch('engine.program_environment.conda_executable', return_value='conda'), \
             patch('engine.program_environment.run_captured', side_effect=clone):
            asyncio.run(child._create(lambda: 30.0))
        self.assertEqual(calls[0], ['conda', 'create', '--yes', '--prefix', str(child.prefix), '--clone', str(self.base)])
        self.assertEqual(child.read_only_prefixes, ())

    def test_unset_missing_task_base_keeps_owned_fresh_creation(self) -> None:
        child = ProgramEnvironment(run_home=self.root/'run', conda_env_name='task-name', use_overlay=False)
        from infra.process.command import run_captured
        calls = []
        async def fresh(command: list[str], timeout: float):
            calls.append(command)
            return await run_captured([sys.executable, '-m', 'venv', '--without-pip', str(child.prefix)], timeout)
        with patch('engine.program_environment.resolve_task_interpreter_or_none', AsyncMock(return_value=None)), \
             patch('engine.program_environment.owned', return_value=self.root/'owned-micromamba'), \
             patch('engine.program_environment.micromamba_root_prefix', return_value=self.root/'package-cache'), \
             patch('engine.program_environment.run_captured', side_effect=fresh):
            asyncio.run(child._create(lambda: 30.0))
        self.assertEqual(calls[0][0:4], [str(self.root/'owned-micromamba'), 'create', '--yes', '--no-rc'])
        self.assertEqual(calls[0][-2:], ['python=3.11', 'pip'])
        self.assertTrue(Path(child.python).is_file())
        self.assertEqual(child.read_only_prefixes, ())

    def test_prepare_rebuilds_an_interrupted_overlay(self) -> None:
        child = self.overlay('run')
        sentinel = child.prefix/'partial-install'
        sentinel.touch()
        (child.prefix.parent/'overlay.creating').touch()
        resumed = ProgramEnvironment(run_home=self.root/'run', conda_env_name='named-environment')
        with patch('engine.program_environment.resolve_task_interpreter_or_none', AsyncMock(return_value=self.base_python)), \
             patch.object(resumed, '_runtime_wheel', AsyncMock(return_value=self.root/'runtime.whl')), \
             patch.object(resumed, '_install_runtime', AsyncMock()):
            asyncio.run(resumed.prepare(lambda: 60.0))
        self.assertFalse(sentinel.exists())
        self.assertFalse((child.prefix.parent/'overlay.creating').exists())
        self.assertEqual(self.run_python(resumed.python, 'import overlay_fixture; print(overlay_fixture.VALUE)'), 'base')


if __name__ == '__main__':
    unittest.main()
