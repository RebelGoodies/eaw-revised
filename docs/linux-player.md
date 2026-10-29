# Linux player AppImage

The Linux x64 player contains the patched Godot editor runtime and the preimported viewer project.
It starts through a native launcher and needs no Python installation to play.
Retail game files and fonts are not included.
Point the launcher at your own supported game installation.

## Playing

Make the downloaded image executable and start it from a terminal or your file manager.
The safe render-thread option is passed through to the native launcher.

```sh
chmod +x eaw-revised-vVERSION-linux-x64-player.AppImage
./eaw-revised-vVERSION-linux-x64-player.AppImage
./eaw-revised-vVERSION-linux-x64-player.AppImage --safe-render-thread
```

On systems without FUSE, extract the image and run its AppRun entry point.
Keep the extracted files together, including the hidden project/.godot directory.
Paths containing spaces are supported.

```sh
./eaw-revised-vVERSION-linux-x64-player.AppImage --appimage-extract
./squashfs-root/AppRun --safe-render-thread
```

The host supplies glibc, its ELF loader and graphics drivers.
A working desktop graphics stack is required.
The oldest supported distribution must be validated with the actual release build because an AppImage does not lower its binaries' glibc requirements.
The private mods manager UI is not included and remains blocked on Linux.
The public `EAWR` entry starts `AppRun`; `EAWR (Safe render thread)` starts `AppRun --safe-render-thread`.
The public `EAWR Mods` entry runs the bundled `run.sh` with `EAWR_PLAYER` set to `AppRun`.
The internal `eawr-launcher` entry remains unchanged.
The bundled `run.sh` provides the optional Workshop mod menu without the private manager UI.

## Release inputs

Run packaging on Linux x86_64 with Python 3.10 or newer, binutils `readelf`, and `ldd` installed.
For releases, supply already-built native binaries from the same clean project commit.
Local uncommitted builds use the explicit development workflow below.
The packager never configures or builds the root project and never uses stock Godot export templates.

- A patched editor reporting exactly `4.7.2.stable.eawr.fad06b579`.
- A separately reviewed JSON runtime pin produced or recorded by the runtime builder.
- The native launcher, `eawr_mods_service`, and adjacent `eawr_loadcheck` build outputs.
- `apps/viewer/project/bin/libeawr_viewer.linux.template_release.x86_64.so`.
- A local appimagetool and its independently verified SHA256.
- A local x86_64 type-2 AppImage runtime and its independently verified SHA256.
  Passing this runtime explicitly prevents appimagetool from downloading one.
- Cached pinned third-party license texts used by the existing Windows packager, or network access for that existing hash-verified license cache.

## Interactive mod launcher

Run `./run.sh` from the source checkout, or place the script beside a player AppImage or `eawr-launcher`.
It scans native and Flatpak Steam locations and additional libraries from `libraryfolders.vdf` for installed Workshop mods for app 32470.
It also scans the installed game's `Mods` and `corruption/Mods` folders, including nonnumeric mod folder names.
The game root comes from `--game-root`, then `EAWR_EAW_GAME_ROOT` or `EAW_GAME_ROOT`, otherwise Steam libraries' `steamapps/common` folders containing both `GameData` and `corruption`.
Mod names come from `modinfo.json` when present and appear as `Name (ID or folder)`.
Unnamed mods and names requiring JSON escape decoding show only their ID or folder name once.
Missing mod directories, including an `EAWR_WORKSHOP_DIR` override, are skipped.
If no mods are found, the script launches vanilla.

- Type a displayed number and press Enter to launch that mod.
- Alternatively, move with Up/Down, press Space to select or deselect, then Enter to launch.
- Enter with no selection launches without mods. Moving the highlight alone does not select a mod.
- Press `q` or Ctrl+C to quit.

The script requires Bash and standard system utilities, with no Python, `jq`, or terminal-menu package.
Small terminals use the numbered prompt instead of the arrow-key menu.
It selects one mod at a time and does not automatically add a submod's dependencies.
Pass launcher options normally, or override discovery with environment variables.

```sh
./run.sh
./run.sh --safe-render-thread
./run.sh --game-root "/path/to/Star Wars Empire at War"
EAWR_PLAYER="/path/to/player.AppImage" \
  EAWR_WORKSHOP_DIR="/path/to/steamapps/workshop/content/32470" ./run.sh
```

## Building the inputs

Build from the repository root with a C++ toolchain, CMake, Ninja, Git, pkg-config, and GTK3 development files installed.
On Debian or Ubuntu the GTK3 development package is `libgtk-3-dev`.
Configure the native player targets and build all four executables.

```sh
cmake -S . -B out/build/linux-player -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DEAWR_BUILD_PLAYER=ON
cmake --build out/build/linux-player --target \
  eawr_launcher eawr_mods_start eawr_mods_service eawr_loadcheck --parallel 8
```

Clone the patched Godot fork at its exact pinned revision, then run the runtime builder as an unprivileged user.
The builder uses GCC/G++, Python, and `uv` to run its pinned SCons version.
Install the Linux editor development dependencies required by Godot before building.
The builder writes the editor, `runtime-pin.json`, and `build.log` under `out/godot-runtime`.

```sh
git clone https://github.com/Thom-Ernst/godot.git out/godot-source
git -C out/godot-source checkout --detach fad06b57944fbf17833fe3c65dc9ff1d7446c639
python3 tools/release/build_linux_runtime.py \
  --source out/godot-source --out out/godot-runtime --jobs 8
```

Build the viewer separately using the pinned godot-cpp checkout.
The binding revision below matches `apps/viewer/dependencies.json`.

```sh
git clone https://github.com/godotengine/godot-cpp.git out/godot-cpp
git -C out/godot-cpp checkout --detach 507ed9d840c01a3c5b2a39af8bb4000bfac30bf5
cmake -S apps/viewer -B out/build/linux-viewer -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DEAWR_GODOT_CPP_ROOT="$PWD/out/godot-cpp"
cmake --build out/build/linux-viewer --target eawr_viewer --parallel 8
```

Set the packaging input paths from these build outputs.
The loadcheck target writes its executable beside the mods service.

```sh
GODOT_EDITOR="$PWD/out/godot-runtime/Godot_v4.7.2-stable_linux.x86_64"
RUNTIME_PIN_JSON="$PWD/out/godot-runtime/runtime-pin.json"
NATIVE_LAUNCHER="$PWD/out/build/linux-player/apps/launcher/eawr-launcher"
NATIVE_BIN="$PWD/out/build/linux-player/apps/mods_service"
```

### Runtime pin

The runtime pin has the following shape.
The packager requires the exact canonical source repository and full revision used by the runtime builder.
Replace the descriptive values with the approved build's actual binary hash and recorded build details.
Do not generate a checksum from an unknown binary and treat that alone as provenance.
Review the source and build record separately.

```json
{
  "schema": "eawr-linux-runtime-pin/1",
  "identity": "4.7.2.stable.eawr.fad06b579",
  "sha256": "<64 lowercase hex digits from the approved runtime build>",
  "source": {
    "repository": "https://github.com/Thom-Ernst/godot.git",
    "revision": "fad06b57944fbf17833fe3c65dc9ff1d7446c639"
  },
  "build": {
    "command": "<actual editor build command>",
    "toolchain": "<compiler and version>",
    "source_record": "<build log or provenance artifact reference>"
  }
}
```

## Packaging

Run the following from the repository root after setting the paths and approved hashes.
`PROJECT_COMMIT` is the full project commit embedded in the extension and mods service.
Any embedded identities in the launcher and loadcheck must also match and be clean.
The output version must contain only letters, digits, periods, underscores and hyphens.

```sh
python3 tools/release/package_linux_player.py \
  --godot "$GODOT_EDITOR" \
  --runtime-pin "$RUNTIME_PIN_JSON" \
  --launcher "$NATIVE_LAUNCHER" \
  --mods-service "$NATIVE_BIN/eawr_mods_service" \
  --loadcheck "$NATIVE_BIN/eawr_loadcheck" \
  --expect-identity "$PROJECT_COMMIT" \
  --appimagetool "$APPIMAGETOOL" \
  --appimagetool-sha256 "$APPIMAGETOOL_SHA256" \
  --appimage-runtime "$APPIMAGE_RUNTIME" \
  --appimage-runtime-sha256 "$APPIMAGE_RUNTIME_SHA256" \
  --library-root "$RELEASE_LIBS" \
  --library-notices "$RELEASE_LIBRARY_NOTICES" \
  --version vVERSION --out out/release
```

Use one `--library-root` per flat directory of approved runtime libraries.
Omit library roots and notices only if no extra libraries are needed.
Copy resolved library files into those directories, rather than symlinks escaping to unrelated locations.
The packager copies needed non-host libraries and their transitive dependencies into `lib/`.
Supply their full license texts and required source offers in `--library-notices`.
It refuses dependencies found elsewhere, missing libraries, non-x86_64 ELFs, and non-relocatable RPATH or RUNPATH entries.
Build inputs must be trusted because version probing, importing and `ldd` execute them.

Every ELF is audited again after import against the bundled closure and host libraries.
The audit covers `DT_NEEDED` dependencies, including transitive dependencies.
Libraries loaded by arbitrary `dlopen`, optional audio or window-system backends, and graphics-driver plugins still require target-system smoke testing.
Do not place glibc, loaders or graphics drivers in a library root.

### Local development artifacts

Use `--development` to package and verify locally built, uncommitted implementation changes without creating a source commit.
The default release workflow still requires clean binaries.
Development packaging requires the full expected project commit, identities in both the viewer extension and mods service, and matching commits in every observed binary identity.
The extension and service must agree on their dirty flag, so rebuild both after changing the working tree.
The launcher and loadcheck may omit identities, as in release mode.
Runtime provenance, dependency closure, licenses, preimport, and payload checks still apply.

Use the same approved inputs as the release command and add `--development`.
For a build of the current working tree, set `PROJECT_COMMIT` to its full HEAD commit.

```sh
PROJECT_COMMIT="$(git rev-parse HEAD)"
python3 tools/release/package_linux_player.py \
  --development \
  --godot "$GODOT_EDITOR" \
  --runtime-pin "$RUNTIME_PIN_JSON" \
  --launcher "$NATIVE_LAUNCHER" \
  --mods-service "$NATIVE_BIN/eawr_mods_service" \
  --loadcheck "$NATIVE_BIN/eawr_loadcheck" \
  --expect-identity "$PROJECT_COMMIT" \
  --appimagetool "$APPIMAGETOOL" \
  --appimagetool-sha256 "$APPIMAGETOOL_SHA256" \
  --appimage-runtime "$APPIMAGE_RUNTIME" \
  --appimage-runtime-sha256 "$APPIMAGE_RUNTIME_SHA256" \
  --library-root "$RELEASE_LIBS" \
  --library-notices "$RELEASE_LIBRARY_NOTICES" \
  --version local --out out/local-player
```

This produces `eaw-revised-local-linux-x64-player-development.AppDir`, `.AppImage`, and `.AppImage.sha256` under `out/local-player`.
The manifest records `development: true` and the actual observed `build_dirty` boolean.
A development artifact built from clean binaries records `build_dirty: false` and retains its development filename suffix.

## Output and verification

### Debug artifacts

Add `--debug --build-dir out/build/rootDebug --viewer-build-dir out/build/viewerDebug` to the packaging command.
Both directories must have `CMAKE_BUILD_TYPE=Debug` caches.
Build the Debug inputs separately so later Release builds cannot replace their packaged extension.

```sh
cmake -S . -B out/build/rootDebug -G Ninja \
  -DCMAKE_BUILD_TYPE=Debug -DEAWR_BUILD_PLAYER=ON
cmake --build out/build/rootDebug --parallel 8
cmake -S apps/viewer -B out/build/viewerDebug -G Ninja \
  -DCMAKE_BUILD_TYPE=Debug -DEAWR_GODOT_CPP_ROOT="$PWD/out/godot-cpp"
cmake --build out/build/viewerDebug --target eawr_viewer --parallel 8
NATIVE_LAUNCHER="$PWD/out/build/rootDebug/apps/launcher/eawr-launcher"
NATIVE_BIN="$PWD/out/build/rootDebug/apps/mods_service"
```

The viewer build preserves its extension under `artifacts/Debug/` inside its build directory.
Debug packaging uses that snapshot instead of the shared `project/bin` copy.
Rebuild older viewer builds with `--clean-first` if their snapshot is missing.
The extension and every headless tool retain embedded DWARF and are rejected if `.debug_info` or `.debug_line` is missing.
The generated package includes `DEBUG-BUILD.md`; Linux Debug packages do not use PDB files.
`--development` is a separate dirty-build qualifier and does not select the build configuration.

Debug outputs use `eaw-revised-vVERSION-linux-x64-debug.AppDir`, `eaw-revised-vVERSION-linux-x64-debug.AppImage`, and the `.AppImage.sha256` sidecar.

Packaging produces the AppDir, an executable type-2 AppImage, and an AppImage SHA256 sidecar.
Existing AppDir or AppImage outputs are refused.
The AppDir contains this layout.

```text
AppRun
EAWR
EAWR (Safe render thread)
EAWR Mods
run.sh
eawr-launcher
eawr_mods_service
eawr_loadcheck
godot/Godot_v4.7.2-stable_linux.x86_64
project/                         # includes preimported .godot and the Linux extension
lib/                            # approved shared-library closure
eawr.desktop
eawr.svg
.DirIcon
licenses/
LICENSE
THIRD_PARTY_NOTICES.md
README.md
runtime.json
SHA256SUMS
```

`runtime.json` records the supplied runtime provenance, project commit, development mode, observed dirty state, verified appimagetool version/hash, AppImage runtime hash, and each ELF's resolved dependencies.
`SHA256SUMS` hashes every other staged file, including notices and imported metadata.
The AppImage sidecar hashes the final image.
The build machine's host dependency paths and hashes are audit evidence, not a promise that all distributions provide the same ABI.

```sh
cd out/release
sha256sum -c eaw-revised-vVERSION-linux-x64-player.AppImage.sha256
cd eaw-revised-vVERSION-linux-x64-player.AppDir
sha256sum -c SHA256SUMS
```

Run offline packaging contracts from the repository root.
They exercise mock importer and image-tool behavior, runtime verification, executable modes, dependency failures, payload rejection, and relocation with arguments containing spaces.

```sh
python3 -m unittest discover -s tests/release -p 'test_*.py'
```

Before publishing, test the actual image on the minimum supported distribution, both mounted and extracted, with the project files read-only.
Check normal startup, safe render-thread startup, game-path selection, font extraction, mod-service startup, and a representative scene.
