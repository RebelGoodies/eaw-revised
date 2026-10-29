"""Run the nine shared fresh-user cases on an actual Linux AppImage or AppDir.

python tools/release/fresh_user/run_linux_fresh_user.py --package <AppImage|AppDir> \
    --kind player --expected-sha256 <digest> --game-root <EaW> --source <checkout> --out <evidence>

AppDir digests use artifact_sha256(): sorted relative names (UTF-8, NUL terminated)
followed by each file's binary SHA256 digest. Symlinks are not accepted.
Requires a graphics desktop, the renderer helper sources, and readelf for debug.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import fresh_user_player


def artifact_sha256(path):
    if path.is_symlink():
        raise ValueError(f'symlink in artifact: {path}')
    if path.is_file():
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest()
    if not path.is_dir():
        raise ValueError(f'package does not exist: {path}')
    digest = hashlib.sha256()
    for file in sorted(path.rglob('*')):
        if file.is_symlink():
            raise ValueError(f'symlink in artifact: {file}')
        if file.is_file():
            digest.update(file.relative_to(path).as_posix().encode('utf-8') + b'\0')
            digest.update(bytes.fromhex(artifact_sha256(file)))
        elif not file.is_dir():
            raise ValueError(f'non-regular package member: {file}')
    return digest.hexdigest()


def clean_environment(work, package):
    env = {name: os.environ[name] for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY',
           'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR', 'LANG') if name in os.environ}
    env.update(PATH='/usr/bin:/bin', HOME=str(work / 'home'),
               XDG_CONFIG_HOME=str(work / 'config'), XDG_DATA_HOME=str(work / 'data'),
               XDG_CACHE_HOME=str(work / 'cache'), TMPDIR=str(work / 'tmp'),
               LD_LIBRARY_PATH=str(package / 'lib'))
    for name in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'TMPDIR'):
        Path(env[name]).mkdir(mode=0o700)
    return env


def extract_package(artifact, work, expected_sha256=None):
    expected_sha256 = expected_sha256 or artifact_sha256(artifact)
    if artifact.is_dir():
        package = work / 'EAWR Tést dir.AppDir'
        shutil.copytree(artifact, package)
        if artifact_sha256(package) != expected_sha256:
            raise ValueError('copied AppDir SHA256 differs from the validated artifact')
    else:
        image = work / artifact.name
        shutil.copy2(artifact, image)
        if artifact_sha256(image) != expected_sha256:
            raise ValueError('copied AppImage SHA256 differs from the validated artifact')
        image.chmod(0o500)
        result = subprocess.run([str(image), '--appimage-extract'], cwd=work,
                                env={'PATH': '/usr/bin:/bin', 'HOME': str(work)},
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ValueError('AppImage extraction failed: ' + result.stderr[-4000:])
        package = work / 'EAWR Tést dir.AppDir'
        (work / 'squashfs-root').rename(package)
    artifact_sha256(package)  # Reject symlinks before chmod or launching any payload.
    for file in package.rglob('*'):
        file.chmod(file.stat().st_mode & ~0o222)
    package.chmod(package.stat().st_mode & ~0o222)
    return package


def preflight(args):
    if not sys.platform.startswith('linux'):
        raise ValueError('Linux fresh-user acceptance requires a Linux host')
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise ValueError('a working graphics desktop (DISPLAY or WAYLAND_DISPLAY) is required')
    if not args.game_root.is_dir() or not any(
            file.is_file() and file.suffix.lower() == '.meg' for file in args.game_root.rglob('*')):
        raise ValueError('--game-root must contain installed EaW game sources (*.meg)')
    for relative in ('apps/viewer', 'tests/presentation/renderer/test_battle_input.py',
                     'tests/presentation/renderer/test_pad_capture.py',
                     'tests/presentation/renderer/test_skirmish_setup.py'):
        if not (args.source / relative).exists():
            raise ValueError('required renderer/game fixture sources missing: ' + str(args.source / relative))
    if args.kind == 'debug' and not Path('/usr/bin/readelf').is_file():
        raise ValueError('debug acceptance requires /usr/bin/readelf for embedded DWARF checks')
    digest = artifact_sha256(args.package)
    if digest != args.expected_sha256.lower():
        raise ValueError(f'package SHA256 {digest} does not match expected {args.expected_sha256}')
    return digest


def run(args):
    args.out.mkdir(parents=True, exist_ok=True)
    digest = preflight(args)
    with tempfile.TemporaryDirectory(prefix='eawr-fresh-user-') as temporary:
        work = Path(temporary)
        package = extract_package(args.package, work, digest)
        baseline = artifact_sha256(package)
        env = clean_environment(work, package)
        import_path = args.source / 'tools/release'
        sys.path.insert(0, str(import_path))
        import package_linux_player as linux
        settings = {'EAWR_JOB_RELEASE_PLATFORM': 'linux-x64',
                    'EAWR_JOB_RELEASE_SOURCE': str(args.source), 'EAWR_JOB_RELEASE_OUT': str(args.out),
                    'EAWR_JOB_RELEASE_PACKAGE': str(args.package), 'EAWR_JOB_RELEASE_KIND': args.kind,
                    'EAWR_JOB_RELEASE_EXTRACTED': str(package), 'EAWR_JOB_RELEASE_SHA256': digest,
                    'EAWR_JOB_RELEASE_ENV': json.dumps(env), 'EAWR_EAW_GAME_ROOT': str(args.game_root),
                    'EAWR_GODOT_EXECUTABLE': str(package / linux.GODOT_NAME)}
        with patch.dict(os.environ, {**env, **settings}, clear=True):
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(fresh_user_player.PlayerFreshUser)
            expected = suite.countTestCases()
            with (args.out / 'suites.log').open('w', encoding='utf-8') as log:
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        unchanged = baseline == artifact_sha256(package)
        receipt_path = args.out / 'fresh-user-receipt.json'
        receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
        receipt_ok = receipt.get('archive_sha256') == digest and receipt.get('kind') == args.kind
        passed = (expected == result.testsRun == 9 and result.wasSuccessful()
                  and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
                  and unchanged and receipt_ok)
        verdict = {'platform': 'linux-x64', 'kind': args.kind, 'package': str(args.package),
                   'artifact_type': 'AppDir' if args.package.is_dir() else 'AppImage',
                   'archive_sha256': digest, 'expected_cases': 9, 'cases_run': result.testsRun,
                   'failures': len(result.failures), 'errors': len(result.errors),
                   'skipped': len(result.skipped), 'package_unchanged': unchanged,
                   'receipt_matches_archive': receipt_ok, 'result': 'PASS 9/9' if passed else 'FAIL'}
        (args.out / 'fresh-user-verdict.json').write_text(json.dumps(verdict, indent=2) + '\n', encoding='utf-8')
        print(f"{verdict['result']}: {args.out / 'fresh-user-verdict.json'}")
        return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--kind', choices=('player', 'debug'), default='player')
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--game-root', required=True, type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    for name in ('package', 'game_root', 'source', 'out'):
        setattr(args, name, getattr(args, name).resolve())
    if args.out == args.package or args.package in args.out.parents:
        parser.error('--out must be outside the package')
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('--out must be empty so old receipts cannot be accepted')
    try:
        return run(args)
    except (OSError, ValueError, subprocess.SubprocessError, ImportError) as error:
        print(f'Linux fresh-user acceptance failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
