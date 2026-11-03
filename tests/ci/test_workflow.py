"""Build coverage contracts for source CI, independent of packaged releases."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[2]


class WorkflowCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())

    def test_native_presets_build_test_and_probe_the_same_configuration(self):
        job = self.workflow["jobs"]["build-and-test"]
        rows = job["strategy"]["matrix"]["include"]
        coverage = {(row["preset"], row["config"]) for row in rows}
        for preset in ("linux-x64-gcc", "windows-msvc"):
            for config in ("Release", "Debug"):
                self.assertIn((preset, config), coverage)
        for preset in ("linux-x64-clang", "linux-arm64-gcc", "windows-clang", ""):
            self.assertIn((preset, "Release"), coverage)
        steps = job["steps"]
        generate = next(step for step in steps if step.get("shell") == "python")
        self.assertNotIn("${{sourceDir}}", generate["run"])
        build = next(step for step in steps if step["name"] == "Configure, build and test (preset)")
        self.assertEqual(build["env"]["PRESET"], "ci-${{ matrix.preset }}-${{ matrix.config }}")
        self.assertEqual(build["env"]["CONFIG"], "${{ matrix.config }}")
        for command in (
            'cmake --preset "$PRESET"',
            'cmake --build "out/build/$PRESET" --config "$CONFIG"',
            'ctest --test-dir "out/build/$PRESET" -C "$CONFIG"',
            'verify_warnings_as_errors.py --root . --preset "$PRESET" --config "$CONFIG"',
        ):
            self.assertIn(command, build["run"])
        linux_install = next(step for step in steps if step["name"] == "Install the Linux toolchain")
        self.assertIn("libgtk-3-dev pkg-config", linux_install["run"])
        # Execute the real preset-generation step: the probe reuses this preset,
        # so a command-line --config alone cannot hide a Release Ninja cache.
        for row in rows:
            if not row["preset"]:
                continue
            arch = "ARM64" if "arm64" in row["preset"] else "X64"
            with self.subTest(preset=row["preset"], config=row["config"]), patch.dict(
                os.environ, PRESET=row["preset"], CONFIG=row["config"], RUNNER_ARCH=arch
            ), patch("pathlib.Path.write_text") as write:
                exec(compile(generate["run"], "ci-preset", "exec"), {})
                preset = json.loads(write.call_args.args[0])["configurePresets"][0]
                name = f"ci-{row['preset']}-{row['config']}"
                self.assertEqual(preset["name"], name)
                self.assertEqual(preset["inherits"], row["preset"])
                self.assertEqual(preset["binaryDir"], f"${{sourceDir}}/out/build/{name}")
                self.assertEqual(preset["cacheVariables"]["CMAKE_BUILD_TYPE"], row["config"])
                self.assertEqual(preset["cacheVariables"]["EAWR_DEBUG_SYMBOLS"], "OFF")
                if arch == "X64":
                    self.assertEqual(preset["cacheVariables"]["EAWR_BUILD_PLAYER"], "ON")
                    for key in ("EAWR_ENABLE_GNS", "EAWR_ENABLE_RELAY"):
                        self.assertEqual(preset["cacheVariables"][key], "ON")
                    self.assertEqual(preset["cacheVariables"]["EAWR_GNS_PROVIDER"], "FETCH")

    def test_viewer_builds_both_configurations_on_both_platforms(self):
        job = self.workflow["jobs"]["viewer"]
        matrix = job["strategy"]["matrix"]
        self.assertEqual(set(matrix["config"]), {"Release", "Debug"})
        self.assertEqual(set(matrix["platform"]), {"linux-x64", "windows-x64"})
        self.assertEqual({row["platform"] for row in matrix["include"]}, set(matrix["platform"]))
        for platform in ("Linux", "Windows"):
            step = next(step for step in job["steps"] if step["name"] == f"Build the viewer extension ({platform})")
            self.assertIn("-B out/build/viewer-${{ matrix.config }}", step["run"])
            self.assertIn("--config ${{ matrix.config }} --target eawr_viewer", step["run"])
            for flag in ("-DEAWR_ENABLE_GNS=ON", "-DEAWR_ENABLE_RELAY=ON", "-DEAWR_GNS_PROVIDER=FETCH"):
                self.assertIn(flag, step["run"])
            if platform == "Linux":
                self.assertIn("-DCMAKE_BUILD_TYPE=${{ matrix.config }}", step["run"])

    def test_source_ci_has_no_packaged_artifact_uploads(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        for job in self.workflow["jobs"].values():
            for step in job["steps"]:
                self.assertNotIn("upload-artifact", step.get("uses", ""))


if __name__ == "__main__":
    unittest.main()
