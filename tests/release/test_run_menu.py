"""Exercise the standalone launcher with a fake player, including terminal input."""

import errno
import json
import os
from pathlib import Path
import select
import shutil
import struct
import subprocess
import tempfile
import time
import unittest

if os.name != "posix":
    raise unittest.SkipTest("The Bash launcher and PTY checks require POSIX")

import fcntl
import pty
import termios


SCRIPT = Path(__file__).resolve().parents[2] / "run.sh"


class RunMenuTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="eawr menu ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.script = self.root / "run.sh"
        shutil.copy2(SCRIPT, self.script)
        self.workshop = self.root / "Workshop mods"
        self.workshop.mkdir()
        for number in range(1, 13):
            mod = self.workshop / str(1000 + number)
            (mod / "Data").mkdir(parents=True)
            (mod / "modinfo.json").write_text(json.dumps({"name": f"Mod {number}"}))
        self.player = self.root / "fake player"
        self.player.write_text(
            '#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "$CAPTURE"\n'
            'printf "%s" "${EAWR_EAW_GAME_ROOT:-}" > "$CAPTURE_GAME_ROOT"\n'
            'exit "${PLAYER_EXIT:-0}"\n'
        )
        self.player.chmod(0o755)
        self.capture = self.root / "arguments"
        self.env = {
            **os.environ,
            "HOME": str(self.root / "home"),
            "XDG_DATA_HOME": str(self.root / "home/.local/share"),
            "EAWR_PLAYER": str(self.player),
            "EAWR_WORKSHOP_DIR": str(self.workshop),
            "CAPTURE": str(self.capture),
            "CAPTURE_GAME_ROOT": str(self.root / "game root environment"),
            "TERM": "xterm",
        }
        self.env.pop("EAWR_EAW_GAME_ROOT", None)
        self.env.pop("EAW_GAME_ROOT", None)

    def arguments(self):
        data = self.capture.read_bytes()
        return [value.decode() for value in data.rstrip(b"\0").split(b"\0")] if data != b"\0" else []

    def pipe(self, text, *args):
        return subprocess.run(
            [str(self.script), *args], input=text, capture_output=True,
            text=True, env=self.env, timeout=5,
        )

    def terminal(self, keys, rows=10, columns=80):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        process = subprocess.Popen(
            [str(self.script)], stdin=slave, stdout=slave, stderr=slave, env=self.env,
        )
        os.close(slave)
        output = b""
        sent = False
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    output += chunk
                    if not sent and (b"Selection:" in output or b"12) Mod 12" in output):
                        os.write(master, keys)
                        sent = True
                elif process.poll() is not None:
                    break
            self.assertTrue(sent, output.decode(errors="replace"))
            code = process.wait(timeout=1)
            return code, output
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)

    def test_numeric_selection_and_forwarded_arguments(self):
        result = self.pipe("12\n", "--safe-render-thread")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments(), [
            "--safe-render-thread", "--", "--eawr-mod-root", str(self.workshop / "1012"),
        ])
        self.assertIn("12) Mod 12", result.stdout)

    def test_existing_separator_is_preserved(self):
        self.assertEqual(self.pipe("01\n", "--", "--eawr-audio", "off").returncode, 0)
        self.assertEqual(self.arguments(), [
            "--", "--eawr-audio", "off", "--eawr-mod-root", str(self.workshop / "1001"),
        ])

    def test_enter_launches_vanilla(self):
        self.assertEqual(self.pipe("\n").returncode, 0)
        self.assertEqual(self.arguments(), [])

    def test_invalid_number_can_be_corrected_without_overflow(self):
        result = self.pipe("18446744073709551617\ninvalid\n2\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments()[-1], str(self.workshop / "1002"))
        self.assertIn("Invalid selection", result.stdout)

    def test_eof_does_not_launch(self):
        self.assertNotEqual(self.pipe("").returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_quit_does_not_launch(self):
        self.assertEqual(self.pipe("q\n").returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_arrow_space_enter_selects_mod(self):
        code, output = self.terminal(b"\x1b[B \n")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.arguments()[-1], str(self.workshop / "1002"))
        self.assertIn(b"1) Mod 1", output)
        self.assertIn(b"\x1b[?25h", output)

    def test_highlight_alone_is_not_selection(self):
        code, output = self.terminal(b"\x1b[B\n")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.arguments(), [])

    def test_application_mode_arrow_keys(self):
        code, output = self.terminal(b"\x1bOB \n")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.arguments()[-1], str(self.workshop / "1002"))

    def test_space_toggles_selection_off(self):
        code, output = self.terminal(b"  \n")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.arguments(), [])

    def test_terminal_numeric_input_and_backspace(self):
        code, output = self.terminal(b"13\x7f2\n")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.arguments()[-1], str(self.workshop / "1012"))

    def test_terminal_scrolls_to_last_mod(self):
        code, output = self.terminal(b"\x1b[B" * 11 + b" \n")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.arguments()[-1], str(self.workshop / "1012"))
        self.assertIn(b"showing 8-12", output)

    def test_small_terminal_uses_numbered_prompt(self):
        code, output = self.terminal(b"2\n", rows=6, columns=18)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.arguments()[-1], str(self.workshop / "1002"))
        self.assertNotIn(b"\x1b[?25l", output)

    def test_escaped_json_title_falls_back_to_workshop_id(self):
        (self.workshop / "1001/modinfo.json").write_text(
            json.dumps({"name": 'Republic "Remake"'})
        )
        result = self.pipe("1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1) 1001\n", result.stdout)
        self.assertNotIn("1001 (1001)", result.stdout)
        self.assertNotIn("Republic", result.stdout)

    def test_named_and_unnamed_labels_in_both_menus(self):
        (self.workshop / "1001/modinfo.json").unlink()
        for interactive in (False, True):
            with self.subTest(interactive=interactive):
                if interactive:
                    code, output = self.terminal(b"q")
                    output = output.decode().replace("\r\n", "\n")
                else:
                    result = self.pipe("q\n")
                    code, output = result.returncode, result.stdout
                self.assertEqual(code, 0, output)
                self.assertIn("1) 1001\n", output)
                self.assertNotIn("1001 (1001)", output)
                self.assertIn("2) Mod 2 (1002)", output)

    def test_explicit_game_root_local_mod_with_spaces(self):
        game = self.root / "Forces of Corruption"
        mod = game / "Mods/My local mod"
        (mod / "Data").mkdir(parents=True)
        self.env["EAWR_WORKSHOP_DIR"] = str(self.root / "missing workshop")
        self.env["EAW_GAME_ROOT"] = str(self.root / "wrong game")
        self.env["EAWR_EAW_GAME_ROOT"] = str(self.root / "existing environment game")
        result = self.pipe("1\n", "--game-root", str(game))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1) My local mod\n", result.stdout)
        self.assertEqual(self.arguments(), [
            "--game-root", str(game), "--", "--eawr-mod-root", str(mod),
        ])
        self.assertEqual(
            Path(self.env["CAPTURE_GAME_ROOT"]).read_text(),
            self.env["EAWR_EAW_GAME_ROOT"],
        )

    def test_game_root_environment_conventions(self):
        game = self.root / "game"
        mod = game / "corruption/Mods/Local mod"
        (mod / "Data").mkdir(parents=True)
        self.env["EAWR_WORKSHOP_DIR"] = str(self.root / "missing")
        for variable in ("EAW_GAME_ROOT", "EAWR_EAW_GAME_ROOT"):
            with self.subTest(variable=variable):
                self.env[variable] = str(game)
                result = self.pipe("1\n")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.arguments()[-1], str(mod))
                self.assertEqual(Path(self.env["CAPTURE_GAME_ROOT"]).read_text(), str(game))
                del self.env[variable]

    def test_supported_game_root_environment_takes_precedence_over_alias(self):
        game = self.root / "game"
        mod = game / "Mods/Local mod"
        (mod / "Data").mkdir(parents=True)
        self.env["EAWR_WORKSHOP_DIR"] = str(self.root / "missing")
        self.env["EAWR_EAW_GAME_ROOT"] = str(game)
        self.env["EAW_GAME_ROOT"] = str(self.root / "wrong game")
        result = self.pipe("1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments()[-1], str(mod))
        self.assertEqual(Path(self.env["CAPTURE_GAME_ROOT"]).read_text(), str(game))

    def test_child_game_roots_find_parent_mods_and_preserve_arguments(self):
        game = self.root / "game"
        mod = game / "Mods/Local mod"
        (mod / "Data").mkdir(parents=True)
        self.env["EAWR_WORKSHOP_DIR"] = str(self.root / "missing")
        for child in ("corruption", "GameData", "CoRrUpTiOn", "gAmEdAtA"):
            (game / child).mkdir()
            for source in ("cli", "EAWR_EAW_GAME_ROOT", "EAW_GAME_ROOT"):
                with self.subTest(child=child, source=source):
                    supplied = str(game / child) + "///"
                    args = ("--game-root", supplied) if source == "cli" else ()
                    if source != "cli":
                        self.env[source] = supplied
                    try:
                        result = self.pipe("1\n", *args)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(self.arguments(), [
                            *args, "--", "--eawr-mod-root", str(mod),
                        ])
                    finally:
                        self.env.pop("EAWR_EAW_GAME_ROOT", None)
                        self.env.pop("EAW_GAME_ROOT", None)

    def test_installed_game_mods_in_additional_steam_library(self):
        steam = Path(self.env["XDG_DATA_HOME"]) / "Steam"
        (steam / "steamapps").mkdir(parents=True)
        library = self.root / "Steam library"
        game = library / "steamapps/common/Star Wars Empire at War"
        (game / "GameData").mkdir(parents=True)
        mod = game / "corruption/Mods/Local mod"
        (mod / "Data").mkdir(parents=True)
        (steam / "steamapps/libraryfolders.vdf").write_text(f'"path" "{library}"')
        # A missing Workshop override must not prevent installed-game discovery.
        self.env["EAWR_WORKSHOP_DIR"] = str(self.root / "missing")
        result = self.pipe("1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments()[-1], str(mod))

    def test_local_numeric_folder_is_distinct_from_workshop_id(self):
        game = self.root / "game"
        mod = game / "Mods/1001"
        (mod / "Data").mkdir(parents=True)
        self.env["EAW_GAME_ROOT"] = str(game)
        result = self.pipe("13\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments()[-1], str(mod))

    def test_workshop_non_numeric_folders_are_ignored_and_aliases_deduplicated(self):
        shutil.copytree(self.workshop / "1001", self.workshop / "Named folder")
        game = self.root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Mods/Alias").symlink_to(self.workshop / "1001", target_is_directory=True)
        self.env["EAW_GAME_ROOT"] = str(game)
        result = self.pipe("q\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Named folder", result.stdout)
        self.assertNotIn("Alias", result.stdout)
        self.assertEqual(result.stdout.count("Mod 1 (1001)"), 1)

    def test_additional_steam_library_and_duplicate_alias(self):
        del self.env["EAWR_WORKSHOP_DIR"]
        steam = Path(self.env["XDG_DATA_HOME"]) / "Steam"
        (steam / "steamapps").mkdir(parents=True)
        library = self.root / "Steam library"
        mods = library / "steamapps/workshop/content/32470"
        mods.parent.mkdir(parents=True)
        mods.symlink_to(self.workshop, target_is_directory=True)
        (steam / "steamapps/libraryfolders.vdf").write_text(
            f'"path" "{library}"\n"path" "{library}"'
        )
        result = self.pipe("1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments()[-1], str(self.workshop / "1001"))
        self.assertEqual(result.stdout.count("Mod 1 (1001)"), 1)

    def test_duplicate_workshop_ids_across_libraries_appear_once(self):
        del self.env["EAWR_WORKSHOP_DIR"]
        steam = Path(self.env["XDG_DATA_HOME"]) / "Steam"
        local_mods = steam / "steamapps/workshop/content/32470"
        shutil.copytree(self.workshop, local_mods)
        library = self.root / "Steam library"
        mods = library / "steamapps/workshop/content/32470"
        mods.parent.mkdir(parents=True)
        mods.symlink_to(self.workshop, target_is_directory=True)
        (steam / "steamapps/libraryfolders.vdf").write_text(f'"path" "{library}"')
        result = self.pipe("1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("Mod 1 (1001)"), 1)
        self.assertEqual(self.arguments()[-1], str(local_mods / "1001"))

    def test_missing_workshop_and_mods_directories_launch_vanilla(self):
        self.env["EAWR_WORKSHOP_DIR"] = str(self.root / "missing")
        self.env["EAW_GAME_ROOT"] = str(self.root / "missing game")
        result = self.pipe("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Launching vanilla", result.stdout)
        self.assertEqual(self.arguments(), [])

    def test_missing_player_is_an_error(self):
        self.env["EAWR_PLAYER"] = str(self.root / "missing player")
        self.assertNotEqual(self.pipe("\n").returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_sibling_appimage_discovery(self):
        del self.env["EAWR_PLAYER"]
        shutil.copy2(self.player, self.root / "eawr-local-player.AppImage")
        self.assertEqual(self.pipe("\n").returncode, 0)
        self.assertEqual(self.arguments(), [])

    def test_release_appimage_discovery(self):
        del self.env["EAWR_PLAYER"]
        release = self.root / "out/release"
        release.mkdir(parents=True)
        shutil.copy2(self.player, release / "eawr-player.AppImage")
        self.assertEqual(self.pipe("\n").returncode, 0)
        self.assertEqual(self.arguments(), [])

    def test_sibling_apprun_precedes_native_launcher(self):
        del self.env["EAWR_PLAYER"]
        marker = self.root / "AppDir environment"
        launcher = self.root / "eawr-launcher"
        launcher.write_text(
            '#!/usr/bin/env bash\n'
            '[[ ${LD_LIBRARY_PATH:-} == "$REQUIRED_LIBRARY_PATH" ]] || exit 42\n'
            'exec "$PLAYER" "$@"\n'
        )
        launcher.chmod(0o755)
        apprun = self.root / "AppRun"
        apprun.write_text(
            '#!/usr/bin/env bash\n'
            'export LD_LIBRARY_PATH="$REQUIRED_LIBRARY_PATH"\n'
            'exec "$APPDIR/eawr-launcher" "$@"\n'
        )
        apprun.chmod(0o755)
        self.env["APPDIR"] = str(self.root)
        self.env["PLAYER"] = str(self.player)
        self.env["REQUIRED_LIBRARY_PATH"] = str(marker)
        self.assertEqual(self.pipe("\n").returncode, 0)
        self.assertEqual(self.arguments(), [])

    def test_explicit_player_override_precedes_sibling_appimage(self):
        shutil.copy2(self.player, self.root / "eawr-local-player.AppImage")
        self.env["PLAYER_EXIT"] = "19"
        self.assertEqual(self.pipe("\n").returncode, 19)

    def test_player_exit_code_is_preserved(self):
        self.env["PLAYER_EXIT"] = "23"
        self.assertEqual(self.pipe("1\n").returncode, 23)


if __name__ == "__main__":
    unittest.main()
