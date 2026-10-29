"""Offline source trust-boundary checks for the Linux runtime builder."""
import importlib.util
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "build_linux_runtime", Path(__file__).resolve().parents[2] / "tools/release/build_linux_runtime.py")
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name).resolve()
        self.git("init", "-q")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-q", "--allow-empty", "-m", "fixture")
        self.git("remote", "add", "origin", runtime.REPOSITORY)
        revision = self.git("rev-parse", "HEAD")
        patched = patch.object(runtime, "REVISION", revision)
        patched.start()
        self.addCleanup(patched.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.source, check=True, capture_output=True,
                              text=True).stdout.strip()

    def test_clean_source_records_full_revision_and_origin(self):
        record = runtime.verify_source(self.source)
        self.assertEqual(record, {"repository": runtime.REPOSITORY, "revision": runtime.REVISION})

    def test_wrong_revision_is_rejected(self):
        with patch.object(runtime, "REVISION", "0" * 40):
            with self.assertRaisesRegex(ValueError, "source revision must"):
                runtime.verify_source(self.source)

    def test_wrong_origin_is_rejected(self):
        self.git("remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaisesRegex(ValueError, "source origin must"):
            runtime.verify_source(self.source)

    def test_untracked_source_is_rejected(self):
        (self.source / "unexpected.cpp").write_text("unreviewed")
        with self.assertRaisesRegex(ValueError, "must be clean"):
            runtime.verify_source(self.source)

    def test_tracked_edits_are_rejected(self):
        path = self.source / "tracked.cpp"
        path.write_text("reviewed")
        self.git("add", "tracked.cpp")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-q", "-m", "tracked fixture")
        path.write_text("unreviewed")
        with patch.object(runtime, "REVISION", self.git("rev-parse", "HEAD")):
            with self.assertRaisesRegex(ValueError, "must be clean"):
                runtime.verify_source(self.source)

    def test_assume_unchanged_tracked_edits_are_rejected(self):
        path = self.source / "tracked.cpp"
        path.write_text("reviewed")
        self.git("add", "tracked.cpp")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-q", "-m", "tracked fixture")
        with patch.object(runtime, "REVISION", self.git("rev-parse", "HEAD")):
            self.git("update-index", "--assume-unchanged", "tracked.cpp")
            path.write_text("unreviewed")
            self.assertEqual(self.git("status", "--porcelain"), "")
            with self.assertRaisesRegex(ValueError, "assume-unchanged"):
                runtime.verify_source(self.source)

    def test_skip_worktree_tracked_edits_are_rejected(self):
        path = self.source / "tracked.cpp"
        path.write_text("reviewed")
        self.git("add", "tracked.cpp")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-q", "-m", "tracked fixture")
        with patch.object(runtime, "REVISION", self.git("rev-parse", "HEAD")):
            self.git("update-index", "--skip-worktree", "tracked.cpp")
            path.write_text("unreviewed")
            self.assertEqual(self.git("status", "--porcelain"), "")
            with self.assertRaisesRegex(ValueError, "skip-worktree"):
                runtime.verify_source(self.source)

    def test_ignored_custom_configuration_is_rejected(self):
        (self.source / ".git/info/exclude").write_text("custom.py\n")
        (self.source / "custom.py").write_text("vulkan = False\n")
        with self.assertRaisesRegex(ValueError, "custom.py"):
            runtime.verify_source(self.source)

    def test_jobs_limit_is_checked_before_build(self):
        with (patch.object(runtime.os, "geteuid", return_value=1000),
              patch.object(runtime.platform, "machine", return_value="x86_64")):
            with self.assertRaisesRegex(ValueError, "between 1 and 8"):
                runtime.build(self.source, self.source.parent / "runtime", 9)

    def test_build_records_only_verified_runtime(self):
        built = self.source / "bin/godot.linuxbsd.editor.x86_64"
        built.parent.mkdir()
        elf = bytearray(20)
        elf[:6] = b"\x7fELF\x02\x01"
        elf[18] = 62
        built.write_bytes(elf)
        with tempfile.TemporaryDirectory() as output:
            with (patch.object(runtime.os, "geteuid", return_value=1000),
                  patch.object(runtime.platform, "machine", return_value="x86_64"),
                  patch.object(runtime, "verify_source", return_value={
                      "repository": runtime.REPOSITORY, "revision": runtime.REVISION}),
                  patch.object(runtime, "capture", side_effect=[
                      "gcc fixture", "g++ fixture", str(built), runtime.IDENTITY]),
                  patch.object(runtime.subprocess, "run") as run):
                binary, pin_path = runtime.build(self.source, Path(output), 8)
            pin = json.loads(pin_path.read_text())
            self.assertEqual(pin["sha256"], hashlib.sha256(binary.read_bytes()).hexdigest())
            self.assertEqual(pin["identity"], runtime.IDENTITY)
            self.assertEqual(pin["schema"], "eawr-linux-runtime-pin/1")
            command = run.call_args.args[0]
            self.assertEqual(command[:len(runtime.SCONS)], runtime.SCONS)
            for option in ("target=editor", "lto=none", "-j8"):
                self.assertIn(option, command)
            self.assertEqual(run.call_args.kwargs["env"]["BUILD_NAME"], "eawr")
            self.assertTrue(binary.stat().st_mode & 0o111)
            self.assertTrue(Path(pin["build"]["source_record"]).is_file())

    def test_failed_build_never_emits_pin(self):
        with tempfile.TemporaryDirectory() as output:
            with (patch.object(runtime.os, "geteuid", return_value=1000),
                  patch.object(runtime.platform, "machine", return_value="x86_64"),
                  patch.object(runtime, "capture", side_effect=["gcc", "g++", __file__]),
                  patch.object(runtime, "verify_source", return_value={}),
                  patch.object(runtime.subprocess, "run", side_effect=subprocess.CalledProcessError(2, "scons"))):
                with self.assertRaises(subprocess.CalledProcessError):
                    runtime.build(self.source, Path(output), 8)
            self.assertFalse((Path(output) / "runtime-pin.json").exists())
            self.assertTrue((Path(output) / "build.log").exists())

    def test_wrong_runtime_identity_never_emits_pin(self):
        built = self.source / "bin/godot.linuxbsd.editor.x86_64"
        built.parent.mkdir()
        header = bytearray(20)
        header[:6] = b"\x7fELF\x02\x01"
        header[18] = 62
        built.write_bytes(header)
        with tempfile.TemporaryDirectory() as output:
            with (patch.object(runtime.os, "geteuid", return_value=1000),
                  patch.object(runtime.platform, "machine", return_value="x86_64"),
                  patch.object(runtime, "verify_source", return_value={}),
                  patch.object(runtime, "capture", side_effect=["gcc", "g++", str(built), "stock-godot"]),
                  patch.object(runtime.subprocess, "run")):
                with self.assertRaisesRegex(ValueError, "runtime identity must"):
                    runtime.build(self.source, Path(output), 8)
            self.assertFalse((Path(output) / "runtime-pin.json").exists())
            self.assertFalse((Path(output) / runtime.RUNTIME_NAME).exists())

    def test_missing_static_cpp_fails_before_scons_and_preserves_outputs(self):
        for missing in ("libstdc++.a", str(self.source / "missing/libstdc++.a")):
            with self.subTest(compiler_result=missing), tempfile.TemporaryDirectory() as output:
                log = Path(output) / "build.log"
                log.write_text("previous build log")
                incremental = self.source / "existing-object.o"
                incremental.write_bytes(b"incremental object")
                with (patch.object(runtime.os, "geteuid", return_value=1000),
                      patch.object(runtime.platform, "machine", return_value="x86_64"),
                      patch.object(runtime, "verify_source", return_value={}),
                      patch.object(runtime, "capture", side_effect=["gcc", "g++", missing]) as capture,
                      patch.object(runtime.subprocess, "run") as run):
                    with self.assertRaisesRegex(ValueError, "missing static C\\+\\+ runtime.*libstdc\\+\\+-static"):
                        runtime.build(self.source, Path(output), 8)
                run.assert_not_called()
                self.assertEqual(capture.call_args.args[0], ["g++", "-print-file-name=libstdc++.a"])
                self.assertEqual(log.read_text(), "previous build log")
                self.assertEqual(incremental.read_bytes(), b"incremental object")
                self.assertFalse((Path(output) / "runtime-pin.json").exists())


if __name__ == "__main__":
    unittest.main()
