"""Offline packaging contracts. No Godot/native builds or network access."""
import argparse
import importlib.util
import io
import json
import os
import platform
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("package_linux_player", ROOT / "tools/release/package_linux_player.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def elf(path, content=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    header[18:20] = struct.pack("<H", 62)
    path.write_bytes(header + content)
    return path


class LinuxPlayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="linux player spaces ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "viewer"
        self.commit = "a" * 40
        identity = ("1.0+" + self.commit + "+gcsync1").encode()
        elf(self.project / "bin" / MODULE.EXTENSION, identity)
        (self.project / "project.godot").write_text("config_version=5\n")
        (self.project / "bin/foreign.dll").write_text("not shipped")
        (self.project / ".godot").mkdir()
        (self.project / ".godot/stale").write_text("not shipped")
        self.godot = elf(self.root / "runtime")
        self.launcher = elf(self.root / "launcher")
        self.service = elf(self.root / "service", identity)
        self.loadcheck = elf(self.root / "loadcheck")
        self.pin = self.root / "runtime-pin.json"
        self.pin_data = {"schema": "eawr-linux-runtime-pin/1", "identity": MODULE.GODOT_IDENTITY,
                         "sha256": MODULE.release.sha256_file(self.godot),
                         "source": {"repository": "https://github.com/Thom-Ernst/godot.git",
                                    "revision": "fad06b57944fbf17833fe3c65dc9ff1d7446c639"},
                         "build": {"command": "scons platform=linuxbsd target=editor arch=x86_64"}}
        self.pin.write_text(json.dumps(self.pin_data))
        self.args = argparse.Namespace(godot=self.godot, runtime_pin=self.pin, launcher=self.launcher,
                                       mods_service=self.service, loadcheck=self.loadcheck,
                                       expect_identity=self.commit, library_root=[], library_notices=None,
                                       with_mods_manager=False, development=False, debug=False,
                                       build_dir=None, viewer_build_dir=None, tool_record={"sha256": "test-tool"})
        self.folder = self.root / "player.AppDir"
        self.folder.mkdir()
        self.enter(patch.object(MODULE.release, "VIEWER_PROJECT", self.project))
        self.enter(patch.object(MODULE.release.export_package, "bundle_licenses",
                                side_effect=lambda folder: (folder / "licenses").mkdir()))

    def enter(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    @staticmethod
    def importer(godot, project):
        for name in MODULE.release.PREIMPORT_FILES:
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("imported")
        (project / ".godot/editor").mkdir()
        (project / "bin/~import.so").write_text("temporary")

    def fake_run(self, command, **kwargs):
        if command[1:] == ["--version"]:
            return MODULE.GODOT_IDENTITY
        if command[0] == "readelf":
            if command[1] == "--section-headers":
                return " [1] .debug_info PROGBITS\n [2] .debug_line PROGBITS"
            return "There is no dynamic section in this file."
        self.fail(f"unexpected command: {command}")

    def test_runtime_requires_exact_fork_hash_and_provenance(self):
        with patch.object(MODULE, "run", return_value=MODULE.GODOT_IDENTITY):
            self.assertEqual(MODULE.verify_runtime(self.godot, self.pin), self.pin_data)
            self.godot.write_bytes(self.godot.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                MODULE.verify_runtime(self.godot, self.pin)
        self.godot.write_bytes(self.godot.read_bytes()[:-7])
        with patch.object(MODULE, "run", return_value="4.7.2.stable.official"):
            with self.assertRaisesRegex(ValueError, "exactly"):
                MODULE.verify_runtime(self.godot, self.pin)
        self.pin_data["source"]["revision"] = "fad06b579"
        self.pin.write_text(json.dumps(self.pin_data))
        with self.assertRaisesRegex(ValueError, "source revision"):
            MODULE.verify_runtime(self.godot, self.pin)

    def test_runtime_rejects_same_prefix_revision_and_noncanonical_repository(self):
        for field, value in (("revision", "fad06b579" + "0" * 31),
                             ("repository", "https://example.invalid/fork"),
                             ("repository", "https://github.com/Thom-Ernst/godot")):
            pin = dict(self.pin_data, source=dict(self.pin_data["source"], **{field: value}))
            self.pin.write_text(json.dumps(pin))
            with self.subTest(field=field, value=value), patch.object(MODULE, "run") as probe:
                with self.assertRaisesRegex(ValueError, "source " + field):
                    MODULE.verify_runtime(self.godot, self.pin)
                probe.assert_not_called()

    def test_stage_import_modes_manifest_and_payload(self):
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            MODULE.stage(self.args, self.folder, self.importer)
        for name in ("AppRun", "EAWR", "EAWR (Safe render thread)", "EAWR Mods", "run.sh",
                     "eawr-launcher", "eawr_mods_service", "eawr_loadcheck", MODULE.GODOT_NAME):
            self.assertEqual((self.folder / name).stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.folder / "run.sh").read_bytes(), (MODULE.release.ROOT / "run.sh").read_bytes())
        self.assertFalse((self.folder / "tools").exists())
        self.assertFalse((self.folder / "DEBUG-BUILD.md").exists())
        self.assertFalse((self.folder / "project/bin/foreign.dll").exists())
        self.assertFalse((self.folder / "project/.godot/stale").exists())
        self.assertFalse((self.folder / "project/.godot/editor").exists())
        self.assertFalse((self.folder / "project/bin/~import.so").exists())
        manifest = json.loads((self.folder / "runtime.json").read_text())
        self.assertEqual(manifest["godot"], self.pin_data)
        self.assertFalse(manifest["development"])
        self.assertFalse(manifest["build_dirty"])
        self.assertEqual(manifest["platform"], "linux-x64")
        self.assertEqual(manifest["variant"], "player")
        self.assertNotIn("configuration", manifest)
        self.assertEqual(len(manifest["elf_audit"]), 5)
        for line in (self.folder / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, MODULE.release.sha256_file(self.folder / name))

    def test_import_missing_cache_and_retail_payload_fail(self):
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(RuntimeError, "pre-import"):
                MODULE.stage(self.args, self.folder, lambda *args: None)
        (self.project / "retail.meg").write_bytes(b"retail")
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, "game or generated"):
                MODULE.stage(self.args, self.folder, self.importer)

    def test_manager_blocked(self):
        self.args.with_mods_manager = True
        with self.assertRaisesRegex(ValueError, "blocked"):
            MODULE.stage(self.args, self.folder)

    def prepare_debug(self):
        self.args.debug = True
        self.args.build_dir = self.root / "Debug tools"
        self.args.viewer_build_dir = self.root / "Debug viewer"
        for directory in (self.args.build_dir, self.args.viewer_build_dir):
            directory.mkdir(exist_ok=True)
            (directory / "CMakeCache.txt").write_text("CMAKE_BUILD_TYPE:STRING=Debug\n")
        for name in MODULE.release.TOOLS:
            elf(self.args.build_dir / "apps" / name / name, ("1.0+" + self.commit + "+gcsync1").encode())
        self.debug_extension = elf(self.args.viewer_build_dir / "artifacts/Debug" / MODULE.EXTENSION,
                                   ("1.0+" + self.commit + "+gcsync1 debug snapshot").encode())

    def test_debug_uses_build_snapshot_after_global_release_overwrite(self):
        self.prepare_debug()
        global_extension = self.project / "bin" / MODULE.EXTENSION
        elf(global_extension, ("1.0+" + "b" * 40 + "+gcsync1 release with DWARF").encode())
        with patch.object(MODULE, "run", side_effect=self.fake_run) as commands, \
                patch.object(MODULE, "verify_player_identity", wraps=MODULE.verify_player_identity) as identity:
            MODULE.stage(self.args, self.folder, self.importer)
        inspected = identity.call_args.args[1]
        self.assertIn((self.debug_extension, True), inspected)
        self.assertNotIn((global_extension, True), inspected)
        dwarf_inputs = [call.args[0][-1] for call in commands.call_args_list
                        if call.args[0][1] == "--section-headers"]
        self.assertIn(str(self.debug_extension), dwarf_inputs)
        self.assertNotIn(str(global_extension), dwarf_inputs)
        self.assertEqual((self.folder / "project/bin" / MODULE.EXTENSION).read_bytes(),
                         self.debug_extension.read_bytes())
        self.debug_extension.unlink()
        # Even a matching global extension with source symbols is not a build-local Debug artifact.
        elf(global_extension, ("1.0+" + self.commit + "+gcsync1 release with DWARF").encode())
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, "snapshot.*rebuild"):
                MODULE.stage(self.args, self.folder, self.importer)
        elf(self.debug_extension, ("1.0+" + "b" * 40 + "+gcsync1 stale snapshot").encode())
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, "not the packaged commit"):
                MODULE.stage(self.args, self.folder, self.importer)

    @unittest.skipUnless(shutil.which("cmake") and shutil.which("c++"), "CMake and C++ compiler required")
    def test_viewer_post_build_snapshot_is_configuration_local(self):
        cmake = (ROOT / "apps/viewer/CMakeLists.txt").read_text()
        start = cmake.index("add_custom_command(TARGET eawr_viewer POST_BUILD")
        snapshot_command = cmake[start:cmake.index("\n)", start) + 2]
        source = self.root / "snapshot fixture"
        source.mkdir()
        shared_bin = self.root / "shared project bin"
        (source / "viewer.cpp").write_text('extern "C" int fixture() { return 42; }\n')
        (source / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.25)\nproject(SnapshotFixture LANGUAGES CXX)\n'
            'add_library(eawr_viewer SHARED viewer.cpp)\n'
            'set_target_properties(eawr_viewer PROPERTIES PREFIX "" SUFFIX ""\n'
            f'    OUTPUT_NAME "{MODULE.EXTENSION}" LIBRARY_OUTPUT_DIRECTORY "{shared_bin.as_posix()}"\n'
            f'    RUNTIME_OUTPUT_DIRECTORY "{shared_bin.as_posix()}")\n{snapshot_command}\n')
        snapshots = {}
        for configuration in ("Debug", "Release"):
            build = self.root / (configuration + " fixture build")
            for command in (["cmake", "-S", str(source), "-B", str(build), f"-DCMAKE_BUILD_TYPE={configuration}"],
                            ["cmake", "--build", str(build), "--config", configuration, "--target", "eawr_viewer"]):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            snapshot = build / "artifacts" / configuration / MODULE.EXTENSION
            snapshots[configuration] = snapshot.read_bytes()
            self.assertEqual(snapshots[configuration], (shared_bin / MODULE.EXTENSION).read_bytes())
        self.assertNotEqual(snapshots["Debug"], snapshots["Release"])
        self.assertEqual((self.root / "Debug fixture build/artifacts/Debug" / MODULE.EXTENSION).read_bytes(),
                         snapshots["Debug"])

    def test_debug_payload_retains_symbols_tools_and_identity_checks(self):
        self.prepare_debug()
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            MODULE.stage(self.args, self.folder, self.importer)
        manifest = json.loads((self.folder / "runtime.json").read_text())
        self.assertEqual(manifest["variant"], "debug")
        self.assertEqual(manifest["configuration"], "Debug")
        self.assertEqual(manifest["symbols"], "embedded DWARF")
        self.assertFalse(manifest["development"])
        self.assertIn("GDB", (self.folder / "DEBUG-BUILD.md").read_text())
        self.assertEqual((self.folder / "project/bin" / MODULE.EXTENSION).read_bytes(),
                         self.debug_extension.read_bytes())
        for name in MODULE.release.TOOLS:
            binary = self.folder / "tools" / name
            self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
            self.assertEqual(binary.read_bytes(), (self.args.build_dir / "apps" / name / name).read_bytes())
            self.assertEqual(manifest["tools_sha256"][name], MODULE.release.sha256_file(binary))
        self.assertEqual((self.folder / "SHA256SUMS").read_text(), MODULE.payload_sums(self.folder))
        stale = self.args.build_dir / "apps" / MODULE.release.TOOLS[0] / MODULE.release.TOOLS[0]
        elf(stale, ("1.0+" + "b" * 40 + "+gcsync1").encode())
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, "not the packaged commit"):
                MODULE.stage(self.args, self.folder, self.importer)

    def test_debug_requires_explicit_debug_builds_tools_and_dwarf(self):
        self.prepare_debug()
        extension = self.debug_extension
        for option in ("build_dir", "viewer_build_dir"):
            original = getattr(self.args, option)
            setattr(self.args, option, None)
            with self.assertRaisesRegex(ValueError, "requires --"):
                MODULE.debug_inputs(self.args, extension)
            setattr(self.args, option, original)
            cache = original / "CMakeCache.txt"
            for configuration in ("Release", "RelWithDebInfo", ""):
                cache.write_text(f"CMAKE_BUILD_TYPE:STRING={configuration}\n")
                with self.assertRaisesRegex(ValueError, "CMAKE_BUILD_TYPE=Debug"):
                    MODULE.debug_inputs(self.args, extension)
            cache.unlink()
            with self.assertRaises(FileNotFoundError):
                MODULE.debug_inputs(self.args, extension)
            cache.write_text("CMAKE_BUILD_TYPE:STRING=Debug\n")
        tool = self.args.build_dir / "apps" / MODULE.release.TOOLS[0] / MODULE.release.TOOLS[0]
        tool.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "not built"):
            MODULE.debug_inputs(self.args, extension)
        elf(tool)
        for stripped in (extension, tool):
            def run(command, **kwargs):
                if command[-1] == str(stripped) and command[1] == "--section-headers":
                    return " [1] .symtab SYMTAB\n [2] .debug_line PROGBITS"
                return self.fake_run(command, **kwargs)
            with self.subTest(stripped=stripped), patch.object(MODULE, "run", side_effect=run):
                with self.assertRaisesRegex(ValueError, "embedded DWARF"):
                    MODULE.debug_inputs(self.args, extension)

    def test_stale_service_build_identity_rejected(self):
        elf(self.service, ("1.0+" + "b" * 40 + "+gcsync1").encode())
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, "not the packaged commit"):
                MODULE.stage(self.args, self.folder, self.importer)

    def dirty_binaries(self):
        binaries = [self.project / "bin" / MODULE.EXTENSION, self.service]
        if hasattr(self, "debug_extension"):
            binaries.append(self.debug_extension)
        for binary in binaries:
            elf(binary, ("0.1.0+" + self.commit + "+dirty+gcsync2").encode())

    def test_default_rejects_dirty_binaries(self):
        self.dirty_binaries()
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            with self.assertRaisesRegex(ValueError, "not the packaged commit"):
                MODULE.stage(self.args, self.folder, self.importer)

    def test_development_permits_matching_dirty_binaries_and_records_state(self):
        self.dirty_binaries()
        self.args.development = True
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            MODULE.stage(self.args, self.folder, self.importer)
        manifest = json.loads((self.folder / "runtime.json").read_text())
        self.assertTrue(manifest["development"])
        self.assertTrue(manifest["build_dirty"])
        self.assertEqual(manifest["build_commit"], self.commit)

    def test_development_identity_still_requires_commit_identity_and_consistent_dirty_state(self):
        binaries = [(self.project / "bin" / MODULE.EXTENSION, True), (self.service, True),
                    (self.launcher, False), (self.loadcheck, False)]
        self.assertFalse(MODULE.verify_player_identity(self.commit, binaries, True))
        elf(self.launcher, ("0.1.0+" + self.commit + "+dirty+gcsync2").encode())
        self.assertTrue(MODULE.verify_player_identity(self.commit, binaries, True))
        elf(self.launcher)
        for expected in (None, "", "f4d315b3", "z" * 40):
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, "full 40-character"):
                MODULE.verify_player_identity(expected, binaries, True)
        elf(self.service)
        with self.assertRaisesRegex(ValueError, "no build identity"):
            MODULE.verify_player_identity(self.commit, binaries, True)
        elf(self.service, ("0.1.0+" + self.commit + "+dirty+gcsync2").encode())
        with self.assertRaisesRegex(ValueError, "disagree on dirty state"):
            MODULE.verify_player_identity(self.commit, binaries, True)
        self.dirty_binaries()
        for binary in (self.service, self.launcher):
            original = binary.read_bytes()
            elf(binary, ("0.1.0+" + "b" * 40 + "+dirty+gcsync2").encode())
            with self.subTest(binary=binary), self.assertRaisesRegex(ValueError, "not the packaged commit"):
                MODULE.verify_player_identity(self.commit, binaries, True)
            binary.write_bytes(original)

    def test_bundled_libraries_require_license_notices(self):
        def audit(folder, roots):
            elf(folder / "lib/libapproved.so.1")
            return []
        with patch.object(MODULE, "run", side_effect=self.fake_run), patch.object(MODULE, "audit_libraries", audit):
            with self.assertRaisesRegex(ValueError, "library-notices"):
                MODULE.stage(self.args, self.folder, self.importer)

    def test_app_run_relocation_safe_mode_and_argument_boundaries(self):
        (self.folder / "AppRun").write_text(MODULE.APP_RUN)
        (self.folder / "AppRun").chmod(0o755)
        # A shell stand-in captures the native launcher's exact argument/environment contract.
        launcher = self.folder / "eawr-launcher"
        launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$APPDIR" "$LD_LIBRARY_PATH" "$@"\n')
        launcher.chmod(0o755)
        result = subprocess.run([str(self.folder / "AppRun"), "--safe-render-thread", "--", "a b", "*"],
                                cwd="/", env={"PATH": os.environ["PATH"]}, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(), [str(self.folder), str(self.folder / "lib"),
                                                     "--safe-render-thread", "--", "a b", "*"])

    def test_public_wrappers_preserve_environment_and_arguments(self):
        with patch.object(MODULE, "run", side_effect=self.fake_run):
            MODULE.stage(self.args, self.folder, self.importer)
        launcher = self.folder / "eawr-launcher"
        launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$APPDIR" "$LD_LIBRARY_PATH" "$@"\n')
        workshop = self.root / "Workshop mods"
        (workshop / "123/Data").mkdir(parents=True)
        env = dict(os.environ, EAWR_PLAYER="/invalid/override", EAWR_WORKSHOP_DIR=str(workshop),
                   LD_LIBRARY_PATH="/existing libraries")
        for name, expected, selection in (("EAWR", [], ""),
                                          ("EAWR (Safe render thread)", ["--safe-render-thread"], ""),
                                          ("EAWR Mods", [], "\n"),
                                          ("EAWR Mods", ["--eawr-mod-root", str(workshop / "123")], "1\n")):
            with self.subTest(entry=name, selection=selection):
                result = subprocess.run([str(self.folder / name), "--", "a b", "*"], cwd="/", env=env,
                                        input=selection, capture_output=True, text=True, check=True)
                forwarded = ([*expected, "--", "a b", "*"] if name != "EAWR Mods"
                             else ["--", "a b", "*", *expected])
                self.assertEqual(result.stdout.splitlines()[-(2 + len(forwarded)):],
                                 [str(self.folder), str(self.folder / "lib") + ":/existing libraries", *forwarded])

    def test_library_closure_bundles_transitive_libraries_and_audits_each_elf(self):
        elf(self.folder / "eawr-launcher")
        libs = self.root / "libs"
        first = elf(libs / "libfirst.so.1")
        second = elf(libs / "libsecond.so.1")
        def run(command, **kwargs):
            if command[0] == "readelf":
                return "(NEEDED)" if "libsecond" not in command[-1] else "no dynamic section"
            return f"libfirst.so.1 => {first} (0x1234)\nlibsecond.so.1 => {second} (0x2345)"
        with patch.object(MODULE, "run", side_effect=run):
            audit = MODULE.audit_libraries(self.folder, [libs])
        self.assertEqual(len(audit), 3)
        self.assertEqual((self.folder / "lib/libsecond.so.1").read_bytes(), second.read_bytes())

    def test_library_audit_rejects_missing_unapproved_host_and_rpath(self):
        elf(self.folder / "eawr-launcher")
        outside = elf(self.root / "liboutside.so.1")
        for output, message in (("libmissing.so => not found", "missing shared"),
                                (f"liboutside.so.1 => {outside} (0x1234)", "outside --library-root")):
            with self.subTest(output=output), patch.object(MODULE, "run", side_effect=["(NEEDED)", output]):
                with self.assertRaisesRegex(ValueError, message):
                    MODULE.audit_libraries(self.folder, [])
        for rpath in ("/tmp/build", "$ORIGIN/../../escape", ""):
            with patch.object(MODULE, "run", return_value=f"(RUNPATH) Library runpath: [{rpath}]"):
                with self.assertRaisesRegex(ValueError, "search path"):
                    MODULE.audit_libraries(self.folder, [])
        with patch.object(MODULE, "run", return_value="(NEEDED) Shared library: [/tmp/libbad.so]"):
            with self.assertRaisesRegex(ValueError, "non-relocatable ELF dependency"):
                MODULE.audit_libraries(self.folder, [])
        elf(self.folder / "lib/libc.so.6")
        with self.assertRaisesRegex(ValueError, "host ABI"):
            MODULE.audit_libraries(self.folder, [])

    @unittest.skipUnless(platform.system() == "Linux" and platform.machine() == "x86_64"
                         and shutil.which("readelf") and shutil.which("ldd") and Path("/bin/true").exists(),
                         "Linux x86_64 ELF tools required")
    def test_real_system_elf_audit(self):
        MODULE.copy_executable(Path("/bin/true"), self.folder / "probe")
        audit = MODULE.audit_libraries(self.folder, [])
        self.assertEqual(audit[0]["elf"], "probe")
        self.assertTrue(all("host" in item for item in audit[0]["dependencies"]))

    def test_invalid_pin_and_wrong_architecture_rejected(self):
        for value in (None, [], {"schema": "other"}):
            self.pin.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                MODULE.verify_runtime(self.godot, self.pin)
        data = bytearray(self.godot.read_bytes())
        data[18:20] = struct.pack("<H", 183)
        self.godot.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "x86_64"):
            MODULE.is_elf(self.godot)

    def prepare_cli(self, build=None):
        self.cli_tool = elf(self.root / "appimagetool")
        self.cli_runtime = elf(self.root / "appimage-runtime")
        self.output = self.root / "release"
        self.cli_calls = []
        def run(command, **kwargs):
            self.cli_calls.append(command)
            if command[0] == str(self.cli_tool):
                if command[1] == "--version":
                    return "appimagetool test-build"
                (build or self.build_appimage)(command)
                return "created"
            return self.fake_run(command, **kwargs)
        self.enter(patch.object(MODULE, "run", side_effect=run))
        self.enter(patch.object(MODULE, "import_project", self.importer))
        return ["--godot", str(self.godot), "--runtime-pin", str(self.pin), "--launcher", str(self.launcher),
                "--mods-service", str(self.service), "--loadcheck", str(self.loadcheck),
                "--appimagetool", str(self.cli_tool), "--appimagetool-sha256", MODULE.release.sha256_file(self.cli_tool),
                "--appimage-runtime", str(self.cli_runtime),
                "--appimage-runtime-sha256", MODULE.release.sha256_file(self.cli_runtime),
                "--version", "v1", "--expect-identity", self.commit, "--out", str(self.output)]

    @staticmethod
    def build_appimage(command):
        image = elf(Path(command[-1]))
        data = bytearray(image.read_bytes())
        data[8:11] = b"AI\x02"
        image.write_bytes(data)

    def assert_image_checksum(self, image):
        self.assertTrue(image.is_file())
        self.assertEqual(image.with_suffix(".AppImage.sha256").read_text(),
                         f"{MODULE.release.sha256_file(image)}  {image.name}\n")

    def test_cli_rejects_unverified_tool_before_creating_output(self):
        argv = self.prepare_cli()
        argv[argv.index("--appimagetool-sha256") + 1] = "0" * 64
        self.assertEqual(MODULE.main(argv), 2)
        self.assertFalse(self.output.exists())

    def test_cli_rejects_tool_mutation_of_audited_payload(self):
        def mutate(command):
            self.build_appimage(command)
            (Path(command[-2]) / "tool-added-file").write_text("unhashed")
        argv = self.prepare_cli(mutate)
        self.assertEqual(MODULE.main(argv), 2)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cli_preserves_existing_checksum(self):
        argv = self.prepare_cli()
        output = self.output
        output.mkdir()
        existing_sums = output / "eaw-revised-v1-linux-x64-player.AppImage.sha256"
        existing_sums.write_text("existing release checksum")
        self.assertEqual(MODULE.main(argv), 2)
        self.assertEqual(existing_sums.read_text(), "existing release checksum")
        self.assertEqual(list(output.iterdir()), [existing_sums])

    def test_cli_preserves_output_symlinks_without_staging(self):
        argv = self.prepare_cli()
        for suffix in (".AppDir", ".AppImage", ".AppImage.sha256"):
            with self.subTest(existing_symlink=suffix):
                output = self.root / ("symlink" + suffix)
                output.mkdir()
                occupied = output / ("eaw-revised-v1-linux-x64-player" + suffix)
                occupied.symlink_to("missing-target")
                with patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                        patch.object(MODULE, "stage", wraps=MODULE.stage) as stage:
                    self.assertEqual(MODULE.main([*argv[:-1], str(output)]), 2)
                stage.assert_not_called()
                self.assertEqual(stdout.getvalue(), "")
                self.assertTrue(occupied.is_symlink())
                self.assertEqual(os.readlink(occupied), "missing-target")
                self.assertEqual(list(output.iterdir()), [occupied])

    def test_cli_preserves_competing_publications(self):
        def compete(command):
            self.build_appimage(command)
            if occupied.suffix == ".AppDir":
                occupied.mkdir()
                (occupied / "other-release").write_text("keep")
            else:
                occupied.write_text("other release image")
        argv = self.prepare_cli(compete)
        for suffix in (".AppDir", ".AppImage"):
            with self.subTest(competing_publication=suffix):
                output = self.root / ("race" + suffix)
                occupied = output / ("eaw-revised-v1-linux-x64-player" + suffix)
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    self.assertEqual(MODULE.main([*argv[:-1], str(output)]), 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(list(output.iterdir()), [occupied])
                if suffix == ".AppDir":
                    self.assertEqual(list(occupied.iterdir()), [occupied / "other-release"])
                    self.assertEqual((occupied / "other-release").read_text(), "keep")
                else:
                    self.assertEqual(occupied.read_text(), "other release image")

    def test_cli_preserves_files_added_to_appdir_claim_before_replacement(self):
        argv = self.prepare_cli()
        appdir = self.output / "eaw-revised-v1-linux-x64-player.AppDir"
        sentinel = appdir / "other-process-file"
        replace = Path.replace

        def populate_claim(source, target):
            self.assertEqual(target, appdir)
            sentinel.write_text("keep")
            return replace(source, target)

        with patch.object(Path, "replace", populate_claim), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(MODULE.main(argv), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(list(self.output.iterdir()), [appdir])
        self.assertEqual(list(appdir.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertFalse(appdir.with_suffix(".AppImage").exists())
        self.assertFalse(appdir.with_suffix(".AppImage.sha256").exists())

    def test_cli_rolls_back_failed_publication_and_allows_retry(self):
        argv = self.prepare_cli()
        for failure in ("checksum", 1, 2, 3):
            with self.subTest(publication_failure=failure):
                output = self.root / f"release-failure-{failure}"
                output.mkdir()
                sentinel = output / "user-file"
                sentinel.write_text("keep")
                failed_argv = [*argv[:-1], str(output)]
                write_text = Path.write_text
                replace = Path.replace
                link = os.link
                publications = []

                def fail_write(path, *args, **kwargs):
                    if path.name.endswith(".AppImage.sha256"):
                        raise OSError("injected checksum write failure")
                    return write_text(path, *args, **kwargs)

                def fail_publish(operation, source, target):
                    publications.append(target)
                    if len(publications) == failure:
                        raise OSError("injected publication failure")
                    return operation(source, target)

                stdout = io.StringIO()
                with patch.object(Path, "write_text", fail_write if failure == "checksum" else write_text), \
                        patch.object(Path, "replace", lambda source, target: fail_publish(replace, source, target)), \
                        patch.object(MODULE.os, "link", side_effect=lambda source, target: fail_publish(link, source, target)), \
                        patch("sys.stdout", stdout), patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    self.assertEqual(MODULE.main(failed_argv), 2)
                self.assertIn("injected", stderr.getvalue())
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(len(publications), 0 if failure == "checksum" else failure)
                self.assertEqual(list(output.iterdir()), [sentinel])
                self.assertEqual(sentinel.read_text(), "keep")
                self.assertEqual(MODULE.main(failed_argv), 0)
                self.assert_image_checksum(output / "eaw-revised-v1-linux-x64-player.AppImage")

    def test_cli_produces_image_with_verified_local_tool_and_runtime(self):
        argv = self.prepare_cli()
        self.assertEqual(MODULE.main(argv), 0)
        self.assertEqual(MODULE.main(argv), 2)  # Never overwrite an existing release.
        self.assert_image_checksum(self.output / "eaw-revised-v1-linux-x64-player.AppImage")
        build = next(command for command in self.cli_calls if "--runtime-file" in command)
        self.assertEqual(build[build.index("--runtime-file") + 1], str(self.cli_runtime))
        manifest = json.loads((self.output / "eaw-revised-v1-linux-x64-player.AppDir/runtime.json").read_text())
        self.assertEqual(manifest["appimagetool"]["sha256"], MODULE.release.sha256_file(self.cli_tool))
        self.assertEqual(manifest["appimagetool"]["appimage_runtime_sha256"], MODULE.release.sha256_file(self.cli_runtime))

    def test_cli_debug_requires_both_build_directories(self):
        argv = self.prepare_cli()
        for missing in ([], ["--build-dir", str(self.root)], ["--viewer-build-dir", str(self.root)]):
            with self.subTest(debug_missing=missing):
                self.assertEqual(MODULE.main([*argv, "--debug", *missing]), 2)

    def test_cli_debug_validation_and_publication_failures_allow_retry(self):
        argv = self.prepare_cli()
        self.prepare_debug()
        debug_argv = [*argv, "--debug", "--build-dir", str(self.args.build_dir),
                      "--viewer-build-dir", str(self.args.viewer_build_dir)]
        debug = self.output / "eaw-revised-v1-linux-x64-debug.AppDir"
        cache = self.args.viewer_build_dir / "CMakeCache.txt"
        cache.write_text("CMAKE_BUILD_TYPE:STRING=Release\n")
        self.assertEqual(MODULE.main(debug_argv), 2)
        self.assertFalse(debug.exists())
        cache.write_text("CMAKE_BUILD_TYPE:STRING=Debug\n")
        missing_tool = self.args.build_dir / "apps" / MODULE.release.TOOLS[0] / MODULE.release.TOOLS[0]
        tool_bytes = missing_tool.read_bytes()
        missing_tool.unlink()
        self.assertEqual(MODULE.main(debug_argv), 2)
        self.assertFalse(debug.exists())
        missing_tool.write_bytes(tool_bytes)
        with patch.object(MODULE.os, "link", side_effect=OSError("injected Debug publication failure")):
            self.assertEqual(MODULE.main(debug_argv), 2)
        self.assertFalse(debug.exists())
        self.assertFalse(debug.with_suffix(".AppImage").exists())
        self.assertFalse(debug.with_suffix(".AppImage.sha256").exists())
        self.assertEqual(MODULE.main(debug_argv), 0)
        self.assert_image_checksum(debug.with_suffix(".AppImage"))

    def test_cli_publishes_debug_and_development_variants(self):
        argv = self.prepare_cli()
        self.assertEqual(MODULE.main(argv), 0)
        self.prepare_debug()
        debug_argv = [*argv, "--debug", "--build-dir", str(self.args.build_dir),
                      "--viewer-build-dir", str(self.args.viewer_build_dir)]
        self.assertEqual(MODULE.main(debug_argv), 0)
        debug = self.output / "eaw-revised-v1-linux-x64-debug.AppDir"
        debug_manifest = json.loads((debug / "runtime.json").read_text())
        self.assertEqual(debug_manifest["configuration"], "Debug")
        self.assertEqual(debug_manifest["variant"], "debug")
        self.assertEqual(debug_manifest["platform"], "linux-x64")
        self.assertEqual(set(debug_manifest["tools_sha256"]), set(MODULE.release.TOOLS))
        self.assertTrue((debug / "DEBUG-BUILD.md").is_file())
        self.assert_image_checksum(debug.with_suffix(".AppImage"))
        self.dirty_binaries()
        self.assertEqual(MODULE.main([*argv, "--development"]), 0)
        self.assertEqual(MODULE.main([*debug_argv, "--development"]), 0)
        local_debug = self.output / "eaw-revised-v1-linux-x64-debug-development.AppDir"
        self.assertTrue(json.loads((local_debug / "runtime.json").read_text())["development"])
        self.assertEqual(len(list(self.output.glob("*.AppImage"))), 4)
        self.assertEqual(len(list(self.output.glob("*.AppImage.sha256"))), 4)
        development = self.output / "eaw-revised-v1-linux-x64-player-development.AppDir"
        self.assertTrue(development.is_dir())
        self.assertTrue(development.with_suffix(".AppImage").is_file())
        self.assertTrue(development.with_suffix(".AppImage.sha256").is_file())
        manifest = json.loads((development / "runtime.json").read_text())
        self.assertTrue(manifest["development"])
        self.assertTrue(manifest["build_dirty"])


if __name__ == "__main__":
    unittest.main()
