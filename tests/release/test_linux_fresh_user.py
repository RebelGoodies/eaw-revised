"""Host-independent contracts for the shared nine-case Linux package gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/release/fresh_user'))
import fresh_user_player as harness
import run_linux_fresh_user as runner
from count_tests import count_tests


class LinuxFreshUserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / 'release.AppDir'
        self.package.mkdir()
        (self.package / 'EAWR').write_text('payload')
        self.args = argparse.Namespace(package=self.package, kind='player',
            expected_sha256=runner.artifact_sha256(self.package), game_root=self.root / 'game',
            source=self.root / 'source', out=self.root / 'out')

    def sources(self):
        self.args.game_root.mkdir()
        (self.args.game_root / 'config.meg').touch()
        (self.args.source / 'apps/viewer').mkdir(parents=True)
        for name in ('test_battle_input.py', 'test_pad_capture.py', 'test_skirmish_setup.py'):
            file = self.args.source / 'tests/presentation/renderer' / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()

    def test_windows_import_still_collects_exactly_nine_cases(self):
        with patch.dict(os.environ, {'EAWR_JOB_RELEASE_PLATFORM': 'windows-x64'}):
            self.assertEqual(count_tests(ROOT / 'tools/release/fresh_user/fresh_user_player.py'), 9)
        names = unittest.defaultTestLoader.getTestCaseNames(harness.PlayerFreshUser)
        self.assertEqual(len(names), 9)
        self.assertTrue(all(name.startswith(f'test_{index:02d}_') for index, name in enumerate(names, 1)))

    def test_hash_covers_names_contents_and_rejects_symlinks(self):
        before = runner.artifact_sha256(self.package)
        (self.package / 'EAWR').rename(self.package / 'AppRun')
        self.assertNotEqual(before, runner.artifact_sha256(self.package))
        (self.package / 'AppRun').write_text('changed')
        changed = runner.artifact_sha256(self.package)
        self.assertNotEqual(before, changed)
        (self.package / 'link').symlink_to('AppRun')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            runner.artifact_sha256(self.package)
        file = self.root / 'test.AppImage'
        file.write_bytes(b'actual image')
        self.assertEqual(runner.artifact_sha256(file), hashlib.sha256(b'actual image').hexdigest())

    def test_preflight_requires_graphics_sources_and_matching_digest(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'graphics desktop'):
                runner.preflight(self.args)
        with patch.dict(os.environ, {'DISPLAY': ':99'}):
            with self.assertRaisesRegex(ValueError, 'game sources'):
                runner.preflight(self.args)
            self.sources()
            self.assertEqual(runner.preflight(self.args), self.args.expected_sha256)
            (self.package / 'EAWR').write_text('tamper')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                runner.preflight(self.args)
            (self.args.source / 'tests/presentation/renderer/test_battle_input.py').unlink()
            with self.assertRaisesRegex(ValueError, 'fixture sources missing'):
                runner.preflight(self.args)

    def test_clean_environment_has_no_host_godot_or_library_search_path(self):
        work = self.root / 'work'
        work.mkdir()
        with patch.dict(os.environ, {'EAWR_GODOT_EXECUTABLE': '/host/godot', 'PYTHONPATH': '/host',
                                   'LD_LIBRARY_PATH': '/host/lib', 'DISPLAY': ':5'}):
            env = runner.clean_environment(work, self.package)
        self.assertNotIn('EAWR_GODOT_EXECUTABLE', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertEqual(env['LD_LIBRARY_PATH'], str(self.package / 'lib'))
        self.assertEqual(env['DISPLAY'], ':5')
        self.assertEqual(env['PATH'], '/usr/bin:/bin')
        for name in ('HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'TMPDIR'):
            self.assertEqual(list(Path(env[name]).iterdir()), [])

    def test_appdir_copy_is_readonly_and_original_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = runner.extract_package(self.package, Path(temporary))
            self.assertIn('Tést dir', str(package))
            self.assertEqual(package.stat().st_mode & 0o222, 0)
            self.assertEqual((package / 'EAWR').stat().st_mode & 0o222, 0)
            self.assertEqual(runner.artifact_sha256(package), self.args.expected_sha256)
        self.assertTrue((self.package / 'EAWR').stat().st_mode & 0o200)

    def test_appimage_extracts_actual_image_without_host_runtime(self):
        image = self.root / 'release.AppImage'
        image.write_bytes(b'image')
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            def extract(command, **kwargs):
                self.assertEqual(command, [str(work / image.name), '--appimage-extract'])
                self.assertEqual(kwargs['env']['PATH'], '/usr/bin:/bin')
                self.assertNotIn('LD_LIBRARY_PATH', kwargs['env'])
                (work / 'squashfs-root').mkdir()
                (work / 'squashfs-root/AppRun').write_text('packaged')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch.object(runner.subprocess, 'run', side_effect=extract):
                package = runner.extract_package(image, work)
            self.assertEqual((package / 'AppRun').read_text(), 'packaged')

    def test_changed_artifact_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'SHA256 differs'):
                runner.extract_package(self.package, Path(temporary), '0' * 64)

    def test_renderer_helper_run_replaces_binary_project_and_environment(self):
        case = harness.PlayerFreshUser('test_08_buy_and_place_reinforcement')
        case.package = self.package
        case.godot = self.package / 'godot/bundled'
        case.rig_godot = Path('/rig/godot')
        case.env = {'PATH': '/usr/bin:/bin'}
        case.records = []
        with patch.object(subprocess, 'run') as original:
            case._original_run = original
            case.helper_run(['/rig/godot', '--path', '/source/project'])
        command = original.call_args.args[0]
        self.assertEqual(command, [str(case.godot), '--path', str(self.package / 'project')])
        self.assertEqual(original.call_args.kwargs['env'], case.env)
        with patch.object(subprocess, 'Popen') as original:
            case._original_popen = original
            case.helper_popen(['/rig/godot', '--path', '/source/project'])
        self.assertEqual(original.call_args.args[0], command)
        self.assertEqual(original.call_args.kwargs['env'], case.env)

    def test_package_checksums_detect_modified_and_extra_files(self):
        case = harness.PlayerFreshUser('test_01_archive_integrity')
        case.package = self.package
        digest = hashlib.sha256(b'payload').hexdigest()
        (self.package / 'SHA256SUMS').write_text(f'{digest}  EAWR\n')
        case.check_package_sums()
        (self.package / 'EAWR').write_text('tampered')
        with self.assertRaises(AssertionError):
            case.check_package_sums()
        (self.package / 'EAWR').write_text('payload')
        (self.package / 'extra').touch()
        with self.assertRaises(AssertionError):
            case.check_package_sums()

    def test_linux_debug_contract_checks_all_tools_and_embedded_symbols(self):
        sys.path.insert(0, str(ROOT / 'tools/release'))
        import package_linux_player as linux
        case = harness.PlayerFreshUser('test_01_archive_integrity')
        case.package = self.package
        case.kind = 'debug'
        case.extension = linux.EXTENSION
        case.godot = self.package / linux.GODOT_NAME
        elf = b'\x7fELF\x02\x01' + b'\0' * 12 + b'\x3e\0'
        for name in ('eawr-launcher', 'eawr_mods_service', 'eawr_loadcheck',
                     'project/bin/' + linux.EXTENSION, linux.GODOT_NAME,
                     *('tools/' + tool for tool in linux.release.TOOLS)):
            file = self.package / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(elf)
            file.chmod(0o755)
        for name, content in (('AppRun', linux.APP_RUN), ('EAWR', linux.PLAYER_START),
                              ('EAWR (Safe render thread)', linux.SAFE_START),
                              ('EAWR Mods', linux.MODS_START), ('run.sh', '#!/bin/sh\n')):
            (self.package / name).write_text(content)
            (self.package / name).chmod(0o755)
        digest = hashlib.sha256(elf).hexdigest()
        runtime = {'platform': 'linux-x64', 'variant': 'debug', 'configuration': 'Debug',
                   'godot': {'sha256': digest}, 'tools_sha256': dict.fromkeys(linux.release.TOOLS, digest)}
        def launch(name, command):
            output = linux.GODOT_IDENTITY if name == 'godot-version' else '.debug_info .debug_line'
            return subprocess.CompletedProcess(command, 0, output, ''), self.root
        with patch.object(case, 'run_process', side_effect=launch) as launches:
            case.check_linux_runtime(runtime)
            self.assertEqual(launches.call_count, len(linux.release.TOOLS) + 2)
        with patch.object(case, 'run_process', return_value=(
                subprocess.CompletedProcess([], 0, linux.GODOT_IDENTITY, ''), self.root)):
            with self.assertRaises(AssertionError):
                case.check_linux_runtime(runtime)  # A stripped ELF cannot pass.
        runtime['tools_sha256'].pop(linux.release.TOOLS[0])
        with patch.object(case, 'run_process', side_effect=launch):
            with self.assertRaises(AssertionError):
                case.check_linux_runtime(runtime)

    def test_runner_requires_all_nine_without_skips_and_matching_receipt(self):
        # Mock only the GPU suite. Exercise extraction, isolation, verdict and cleanup.
        self.args.source = ROOT
        digest = self.args.expected_sha256
        for count, skipped, receipt, expected_exit in ((9, False, True, 0), (8, False, True, 1),
                                                      (9, True, True, 1), (9, False, False, 1)):
            with self.subTest(count=count, skipped=skipped, receipt=receipt):
                self.args.out = self.root / f'out-{count}-{skipped}-{receipt}'
                class MockGpu(unittest.TestCase):
                    def runTest(inner):
                        package = Path(os.environ['EAWR_JOB_RELEASE_EXTRACTED'])
                        self.assertTrue(package.is_dir())
                        self.assertNotIn('/host/godot', os.environ.values())
                        if receipt:
                            (self.args.out / 'fresh-user-receipt.json').write_text(json.dumps({
                                'archive_sha256': digest, 'kind': self.args.kind}))
                        if skipped:
                            inner.skipTest('missing GPU')
                suite = unittest.TestSuite(MockGpu() for _ in range(count))
                with patch.object(runner, 'preflight', return_value=digest), patch.object(
                        runner.unittest.defaultTestLoader, 'loadTestsFromTestCase', return_value=suite):
                    self.assertEqual(runner.run(self.args), expected_exit)
                verdict = json.loads((self.args.out / 'fresh-user-verdict.json').read_text())
                self.assertEqual(verdict['cases_run'], count)
                self.assertEqual(verdict['archive_sha256'], digest)


if __name__ == '__main__':
    unittest.main()
