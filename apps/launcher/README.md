# EAWR native first-run launcher

## Windows launcher

`EAWR.exe` (target `eawr_launcher`) is the file a player double-clicks. It is a small native GUI program:
no console window, no Python, the C++ runtime linked statically and only system DLLs imported, so a clean PC
needs no redistributable. It never elevates (`asInvoker`), never changes firewall rules and never writes into
the game folder.

### Package layout it expects

```text
EAWR.exe
godot\Godot_v4.7.2-stable_win64.exe     the Godot 4.7.2 GUI runtime (never the console companion)
project\project.godot ...               the viewer project with its extension
```

`--godot <exe>` and `--project <folder>` override the two relative paths (`EAWR_GODOT_EXECUTABLE` too). Without
`godot\...exe` it also looks beside itself and one folder up, and takes any `Godot_v4.7.2-stable*.exe` whose
name does not contain `console`. The packaging side (`tools/release/package_release.py`) puts the files there.

### What it does

1. **Finds Forces of Corruption**, in this order: `--game-root` / `EAWR_EAW_GAME_ROOT` (an explicit folder is
   never replaced by a search and never overwrites the saved choice), the choice saved by an earlier run (revalidated every run), then Steam (HKCU
   `SteamPath` and HKLM `InstallPath`, `libraryfolders.vdf` in the modern and the pre-2021 format,
   `appmanifest_32470.acf` (Gold Pack), any folder under `steamapps\common`), then GOG
   (`HKLM\SOFTWARE\GOG.com\Games` and the Galaxy / installer uninstall records, 32- and 64-bit views). A folder
   counts when `GameData\Data`, `corruption\Data` and `corruption\StarWarsG.exe` exist (names matched ignoring
   case, as `tools/release/demo.py` does); picking `corruption` or `GameData` selects their parent, and GOG-style
   wrapper folders are looked into one level down. One install is used silently. Several, or none, open a picker
   (a task dialog with one command link per install and a Browse button, then the system folder picker).
   `--choose-game`, or Shift held at startup, asks even when a choice is saved.
2. **Starts Godot hidden**: `Godot_v4.7.2-stable_win64.exe --path project -- --eawr-game-root <root>
   --eawr-front-end [--eawr-allow-unknown-build] --eawr-report <report>` from the launcher's folder with
   `CREATE_NO_WINDOW`. Fonts are extracted by the viewer itself, so no `--eawr-font-cache` is passed;
   `--allow-unknown-build` is forwarded as `--eawr-allow-unknown-build`. The project's default is the Separate render thread;
   `--safe-render-thread` adds Godot's `--render-thread safe` right after `--path` (the opt-out; the player zip's `EAWR (Safe render thread).cmd` uses it). Anything after `--` (and unknown
   options) goes to the viewer, `--godot-quit-after <n>` becomes Godot's `--quit-after`.
3. **Explains failures**: a missing game, a bad folder, a missing runtime or project, a runtime that will not
   start, or Godot exiting with an error within a minute of starting opens a message box with the reason (the
   viewer's own text from its `--eawr-report` file when it wrote one) and the log paths. `--no-dialogs` sends the
   same text to stderr and never opens a window.

Exit codes: 0 played, 2 error, 3 cancelled in the picker, anything else is Godot's own code.

## Linux launcher

The Linux launcher uses GTK 3.24 or newer for the installation chooser, folder picker and error dialogs.
It starts the bundled runtime with `execv` arguments and waits for it to exit, without a shell or runtime Python.
Paths in the release layout are relative to the launcher executable, including when an AppImage mounts it elsewhere.

### Package layout it expects

```text
eawr-launcher                              target eawr_launcher, ordinary Play
eawr-mods                                  target eawr_mods_start, optional manager entry point
godot/Godot_v4.7.2-stable_linux.x86_64       executable Godot runtime
project/project.godot                       viewer project and Linux extension
launcher/project.godot                      optional manager project
eawr_mods_service                           optional manager service, exact root-level lookup
```

As on Windows, `eawr-launcher` starts the viewer and `eawr-mods` starts the manager (`eawr-launcher --mods`).
The manager project receives the absolute service path through `--eawr-mods-service`.
The manager project and service are optional, so ordinary Play works without them.
Choosing Mods without the manager package shows an explanation and leaves Play available.

### What it does

1. **Finds Forces of Corruption**: checks `~/.steam/steam`, `~/.steam/root`, `~/.local/share/Steam`,
   `$XDG_DATA_HOME/Steam` and the Flatpak `.local/share/Steam` and `data/Steam` locations under
   `~/.var/app/com.valvesoftware.Steam`. Shared Steam utilities expand `libraryfolders.vdf`, read app
    manifests and scan library folders. One valid installation is used automatically. Several or no
    installations, or `--choose-game`, open the chooser with manual folder selection. It also searches GOG
    installs under the absolute `WINEPREFIX` (or `~/.wine`) in `drive_c/GOG Games`,
    `Program Files/GOG Galaxy/Games`, and `Program Files (x86)/GOG Galaxy/Games`. Heroic and Lutris installs
    can be selected manually or passed with `--game-root`. Without a display,
   errors go to stderr and a choice-requiring discovery exits with cancellation and a `--game-root` hint.
2. **Starts the viewer**: in GTK desktop mode, holding Shift at startup asks for a game installation even when
   one is already saved. Headless mode has no key-state detection; use `--choose-game` or `--game-root`.
   The launcher passes `--render-thread safe` or `--render-thread separate` through to override
   the saved Performance setting. `--safe-render-thread` remains the Safe alias. Settings, launcher state
   and font provisioning use the existing Godot XDG layout under
   `$XDG_DATA_HOME/godot/app_userdata/EAWR Viewer`, falling back to
   `~/.local/share/godot/app_userdata/EAWR Viewer`. The viewer provisions fonts itself. `--user-data`
   overrides launcher state and logs, while viewer settings retain their existing location.
3. **Explains failures**: uses GTK dialogs for errors and folder selection. In headless use, errors go to
   stderr. The launcher waits for the runtime to exit.

The portable `discover_installs(const RegistryReader&)` interface has no GTK dependency.
Linux service callers can use `discover_installs()` without a registry reader or launcher platform objects.

## Files

Godot's user-data folder for the viewer, `%APPDATA%\Godot\app_userdata\EAWR Viewer` (the folder the viewer's
`user://` uses; its name is the project's `config/name`, checked by `launcher_smoke`). `--user-data <folder>`
overrides it for tests. The launcher keeps its own files in the `launcher\` subfolder:

| File | Content |
|---|---|
| `launcher\launcher.json` | the saved game folder |
| `launcher\launcher.log` | what the launcher found and did (trimmed past 1 MiB) |
| `launcher\front-end-report.json` | the viewer's report of the last start (deleted before each start) |

The viewer's own `logs\godot.log` is named in the error dialogs.

## Layout of the code

`launcher_flow` holds the steps behind `Ui`, `RegistryReader` and `ProcessRunner` interfaces, so the tests run
them with fakes; `install_discovery` (VDF/registry search and validation), `launch_plan` (command line and
quoting) and `launcher_state` (saved choice) are portable standard C++; `win_*.cpp` are the registry, process,
dialog and entry-point layers. Linux uses GTK for its dialogs and entry points. The JSON and KeyValues parsers,
the UTF-8 and case-insensitive path helpers and the
`libraryfolders.vdf` reader live in `src/mods/steam` (shared with the mod core); the launcher compiles those
sources itself with its static C runtime instead of linking `eawr_mods`, and `src/text_formats.hpp` and
`src/paths.hpp` keep the launcher's names for them.

## Tests

- `launcher_core_contracts` (`launcher_tests`): discovery against VDF and registry fixtures, Unicode
  and space paths, validation, the saved choice, the Godot command line round-trip and the whole flow with a
  fake UI and process runner. The parser cases run in `mods_contracts` (`tests/mods`).
- `launcher_smoke`: the built exe against `launcher_fake_godot` (records its argv and whether it has a console
  window): front-end arguments, hidden start, saved choice, readable errors; and checks on the exe itself (GUI
  subsystem, imports system DLLs only, `asInvoker` manifest).
- `launcher_ui_smoke`: the real dialogs, clicked on a private desktop (`tools/rig/private_desktop.py`).
- Linux builds also run `launcher_core_contracts` with UTF-8 argument, process failure and native/Flatpak
  discovery fixtures. `launcher_linux_smoke` relocates a fixture kit and exercises both native entry points
  against a fake runtime. Win32-only smoke helpers remain Windows-only.

Fixtures are empty folders and made-up bytes; no retail data.

The Performance **Render thread (restart)** setting persists in Godot's `user://eawr-settings.cfg` and applies at the next launcher start. For the bundled project this is `%APPDATA%\Godot\app_userdata\EAWR Viewer\eawr-settings.cfg` on Windows, or `$XDG_DATA_HOME/godot/app_userdata/EAWR Viewer/eawr-settings.cfg` on Linux (defaulting to `~/.local/share` for `$XDG_DATA_HOME`). The launcher reads that same file even when `--user-data` puts its own state and logs elsewhere. `--render-thread safe|separate` (or `--safe-render-thread`) overrides the saved choice. With no saved choice or flag, the project default applies. An explicit `--eawr-settings-file` forwarded to the viewer selects the same settings file for startup. Relative overrides resolve from the launcher folder, matching Godot's working directory; an empty final override restores the default user:// file. Direct engine launches use their explicit engine flag or project default; they do not read this startup preference themselves. Shipped Safe start files and the owner comparison kit deliberately select a mode explicitly.
For manager-selected profiles, render-thread precedence is explicit CLI mode > profile mode > saved Performance setting > project default. Both explicit Safe and explicit Separate override a profile. A plain EAWR start remains CLI > saved Performance setting > project default.

With a bundled manager, `EAWR.exe --mods` starts EAWR Mods and passes
`--eawr-launch-loop <user-data>/launcher/next-action.json`. A successful child exit
may request `{"action":"launch-profile","profile":"<id>"}` or
`{"action":"open-manager"}`. The launcher consumes the request before starting
the next process and clears stale requests before every start. Missing or invalid
requests end the launcher normally; nonzero child exits retain their exit code.
If an old request cannot be deleted, startup stops with a readable error, including
on a plain EAWR start, so a stale request can never trigger a later launch.
More than 16 immediate restart requests stop with an error; a game session lasting
at least 60 seconds resets that guard.

Profile launches use `mods/profiles/<id>.json` under the launcher user-data folder,
pass `--eawr-mod-profile` to the game and put the profile's `--render-thread safe|separate`
before Godot's `--` separator. An explicit `--safe-render-thread` overrides the
profile setting. The launcher remembers `last_profile` in
`launcher/launcher.json`; a plain EAWR start keeps its existing front-end behaviour.
The engine remains responsible for loading and validating the full profile.

`--user-data` is not yet supported together with `--mods`: the manager currently
starts the profile service with its default user-data folder. Use the default
folder for manager launches until the manager forwards this option to the service.

Manager-equipped kits include `EAWR Mods.exe`, a native GUI program with a static C runtime.
Double-click it to start `EAWR.exe --mods` from the stub's own folder without a console.
Run `"EAWR Mods.exe" --create-shortcut` to create an **EAWR Mods** desktop shortcut pointing
at the stub. It checks for an incomplete kit and asks before replacing an existing shortcut,
with **No** selected by default. The start executable keeps working when the kit moves;
recreate the desktop shortcut after moving the kit. No script host is required.
