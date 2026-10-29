#!/usr/bin/env python3
"""Stage the patched runtime and preimported player or Debug viewer as a Linux x64 AppImage."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_release as release
import build_linux_runtime as linux_runtime

GODOT_IDENTITY = linux_runtime.IDENTITY
GODOT_NAME = "godot/" + linux_runtime.RUNTIME_NAME
EXTENSION = release.PLATFORMS["linux-x64"]["extension"]
# These belong to the host ABI or graphics stack, never to the AppImage.
HOST_LIBRARY = re.compile(
    r"^(?:ld-linux-x86-64\.so\.2|lib(?:c|m|mvec|dl|pthread|rt|resolv|util|anl|BrokenLocale|thread_db|"
    r"nss_[A-Za-z0-9_-]+|memusage|pcprofile|SegFault|c_malloc_debug)\.so(?:\.[0-9]+)*|"
    r"lib(?:GL|GLX|GLdispatch|OpenGL|EGL|GLESv[12]|vulkan|drm|drm_amdgpu|gbm)\.so\.[0-9]+|"
    r"lib(?:GLX_(?:nvidia|mesa)|EGL_mesa|gallium[A-Za-z0-9_-]*|nvidia-[A-Za-z0-9_-]+|"
    r"vulkan_[A-Za-z0-9_-]+)\.so(?:\.[0-9.]+)?|[A-Za-z0-9_-]+_dri\.so)$"
)
APP_RUN = '''#!/bin/sh
set -eu
# AppDir may be mounted or extracted under a path containing spaces.
APPDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
export APPDIR
export LD_LIBRARY_PATH="$APPDIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$APPDIR/eawr-launcher" "$@"
'''
PLAYER_START = '''#!/bin/sh
set -eu
APPDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$APPDIR/AppRun" "$@"
'''
SAFE_START = PLAYER_START.replace('"$@"', '--safe-render-thread "$@"')
MODS_START = '''#!/bin/sh
set -eu
APPDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
export EAWR_PLAYER="$APPDIR/AppRun"
exec "$APPDIR/run.sh" "$@"
'''
DEBUG_GUIDE = '''# EaW Revised: Linux Debug build

This package uses the compiler's Debug configuration for the viewer and headless tools.
Use the player package for normal play and performance measurements.

## Starting the viewer

Run `./EAWR`, or `./EAWR (Safe render thread)` for the safe rendering mode.
Run `./EAWR Mods` from a terminal for the optional installed Workshop mod menu.
See README.md for runtime requirements and user data locations.

## Debugging

The viewer extension in `project/bin/` and the programs in `tools/` retain their embedded DWARF symbols.
Keep the package intact and use GDB or LLDB to obtain function names, source lines, and a backtrace.
The pinned Godot runtime and launcher helpers are shared with the player package, not rebuilt as Debug.
Viewer debug menu entries and internal consistency checks are enabled by the Debug build.

From the package directory, use the bundled library path when running a tool directly:

```sh
LD_LIBRARY_PATH="$PWD/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ./tools/sim_headless --help
```

Include the package version, runtime.json, relevant logs, and the backtrace when reporting a crash.
Match the build kind of other players because Debug is slower and may fall behind in busy matches.
'''
DESKTOP = '''[Desktop Entry]
Type=Application
Name=EaW Revised
Exec=AppRun %U
Icon=eawr
Categories=Game;StrategyGame;
Terminal=false
'''
ICON = '''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<rect width="256" height="256" rx="36" fill="#152238"/>
<path d="M48 64h160v28H80v22h108v28H80v22h128v28H48z" fill="#d7b46a"/>
</svg>
'''


def run(command: list[str], **kwargs) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=180, **kwargs)
    return (result.stdout + result.stderr).strip()


def check_hash(path: Path, expected: str) -> str:
    observed = release.sha256_file(path)
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or observed != expected:
        raise ValueError(f"SHA256 mismatch for {path}: observed {observed}, expected {expected}")
    return observed


def verify_runtime(godot: Path, pin_path: Path, library_roots: tuple[Path, ...] | list[Path] = ()) -> dict:
    """The pin is a build provenance record, supplied separately from the binary."""
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    if not isinstance(pin, dict) or pin.get("schema") != "eawr-linux-runtime-pin/1" or pin.get("identity") != GODOT_IDENTITY:
        raise ValueError(f"runtime pin must identify {GODOT_IDENTITY}")
    source = pin.get("source", {})
    if not isinstance(source, dict) or source.get("revision") != linux_runtime.REVISION:
        raise ValueError(f"runtime pin source revision must be {linux_runtime.REVISION}")
    if source.get("repository") != linux_runtime.REPOSITORY:
        raise ValueError(f"runtime pin source repository must be {linux_runtime.REPOSITORY}")
    if (not isinstance(pin.get("build"), dict)
            or not isinstance(pin["build"].get("command"), str) or not pin["build"]["command"].strip()):
        raise ValueError("runtime pin needs build provenance with a nonempty command")
    check_hash(godot, pin.get("sha256", ""))
    if not is_elf(godot):
        raise ValueError("runtime must be a Linux x86_64 ELF")
    env = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join(str(path.resolve(strict=True)) for path in library_roots))
    if run([str(godot), "--version"], env=env) != GODOT_IDENTITY:
        raise ValueError(f"runtime must report exactly {GODOT_IDENTITY}")
    return pin


def is_elf(path: Path) -> bool:
    with path.open("rb") as stream:
        header = stream.read(20)
    if not header.startswith(b"\x7fELF"):
        return False
    if len(header) != 20 or header[4:6] != b"\x02\x01" or struct.unpack("<H", header[18:20])[0] != 62:
        raise ValueError(f"not a Linux x86_64 ELF: {path}")
    return True


def copy_executable(source: Path, target: Path) -> None:
    if not is_elf(source):
        raise ValueError(f"native ELF required: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o755)


def validate_elf_search_paths(binary: Path, folder: Path, dynamic: str) -> None:
    for needed in re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic):
        if "/" in needed or needed in ("", ".", ".."):
            raise ValueError(f"non-relocatable ELF dependency in {binary}: {needed}")
    # RPATH could bypass the audited closure after relocation.
    for value in re.findall(r"\((?:RPATH|RUNPATH)\).*?\[(.*?)\]", dynamic):
        for entry in value.split(":"):
            if not entry or not (entry == "$ORIGIN" or entry.startswith("$ORIGIN/")):
                raise ValueError(f"non-relocatable ELF search path in {binary}: {value}")
            resolved = (binary.parent / entry.removeprefix("$ORIGIN").lstrip("/")).resolve()
            if not resolved.is_relative_to(folder.resolve()):
                raise ValueError(f"ELF search path escapes AppDir: {binary}: {value}")


def ldd_dependencies(binary: Path, output: str):
    for line in output.splitlines():
        if "not found" in line:
            raise ValueError(f"missing shared library for {binary.name}: {line.strip()}")
        match = re.match(r"\s*(\S+)\s+=>\s+(/.*?)\s+\(0x[0-9a-f]+\)", line)
        if match:
            name, location = match.groups()
        else:
            loader = re.match(r"\s*(/\S+)\s+\(0x[0-9a-f]+\)", line)
            if not loader:
                if line.strip().startswith("linux-vdso.so."):
                    continue
                raise ValueError(f"unrecognized ldd output for {binary}: {line}")
            location = loader[1]
            name = Path(location).name
            if not HOST_LIBRARY.fullmatch(name):
                raise ValueError(f"absolute ELF dependency in {binary}: {location}")
        if name != Path(name).name or name in (".", ".."):
            raise ValueError(f"non-relocatable ELF dependency in {binary}: {name}")
        yield name, Path(location).resolve(strict=True)


def bundle_dependency(name: str, source: Path, folder: Path, roots: list[Path]) -> dict:
    if HOST_LIBRARY.fullmatch(name):
        if source.is_relative_to(folder.resolve()) or any(source.is_relative_to(root) for root in roots):
            raise ValueError(f"host ABI/graphics library must not be bundled: {name}")
        return {"name": name, "host": str(source), "sha256": release.sha256_file(source)}
    target = folder / "lib" / name
    if source.is_relative_to(folder.resolve()):
        target = source
    elif any(source.is_relative_to(root) for root in roots):
        if target.exists() and release.sha256_file(target) != release.sha256_file(source):
            raise ValueError(f"conflicting shared library: {name}")
        if not target.exists():
            copy_executable(source, target)
    else:
        raise ValueError(f"{name} resolved outside --library-root: {source}")
    return {"name": name, "bundled": target.relative_to(folder).as_posix(),
            "sha256": release.sha256_file(source)}


def audit_libraries(folder: Path, roots: list[Path]) -> list[dict]:
    """Resolve every ELF's full ldd closure. Non-host libraries must come from explicit roots.

    ldd runs only on trusted, locally built release inputs. Never use this on an untrusted download.
    """
    lib = folder / "lib"
    lib.mkdir(exist_ok=True)
    roots = [path.resolve(strict=True) for path in roots]
    env = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join(map(str, [lib, *roots])))
    audited: set[Path] = set()
    records = []
    while True:
        pending = [path for path in sorted(folder.rglob("*"))
                   if path.is_file() and path not in audited and is_elf(path)]
        if not pending:
            return records
        for binary in pending:
            audited.add(binary)
            if HOST_LIBRARY.fullmatch(binary.name):
                raise ValueError(f"host ABI/graphics library in payload: {binary.name}")
            dynamic = run(["readelf", "-d", str(binary)])
            validate_elf_search_paths(binary, folder, dynamic)
            if "(NEEDED)" not in dynamic:
                records.append({"elf": binary.relative_to(folder).as_posix(), "dependencies": []})
                continue
            output = run(["ldd", str(binary)], env=env)
            dependencies = [bundle_dependency(name, source, folder, roots)
                            for name, source in ldd_dependencies(binary, output)]
            records.append({"elf": binary.relative_to(folder).as_posix(), "dependencies": dependencies})


def validate_payload(folder: Path) -> None:
    release.reject_payload_leftovers(folder)
    for path in folder.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink in payload: {path}")
        if path.is_file() and (path.suffix.lower() in release.FORBIDDEN_PLAYER_SUFFIXES
                               or path.name.casefold() == "starwarsg.exe"):
            raise ValueError(f"game or generated file in payload: {path}")


def verify_player_identity(expected: str, binaries: list[tuple[Path, bool]], development: bool) -> bool:
    """Return observed dirty state, allowing local builds only with explicit development mode."""
    if not development:
        release.verify_identity(expected, binaries)
        return False
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{40}", expected.strip().lower()):
        raise ValueError("--expect-identity must be a full 40-character commit hash")
    expected = expected.strip().lower()
    required_dirty = set()
    build_dirty = False
    for binary, required in binaries:
        found = release.embedded_identities(binary)
        if required and not found:
            raise ValueError(f"{binary.name} embeds no build identity (expected commit {expected})")
        for commit, dirty in found:
            if commit != expected:
                raise ValueError(f"{binary.name} embeds build identity {commit}, not the packaged commit {expected}")
            build_dirty |= dirty
            if required:
                required_dirty.add(dirty)
    if len(required_dirty) > 1:
        raise ValueError("required extension and service build identities disagree on dirty state: rebuild both")
    return build_dirty


def debug_inputs(args, extension: Path) -> list[Path]:
    """Require explicit single-config Debug builds and embedded Linux source symbols."""
    if not args.debug:
        return []
    for option in ("build_dir", "viewer_build_dir"):
        directory = getattr(args, option)
        if directory is None:
            raise ValueError(f"--debug requires --{option.replace('_', '-')}")
        cache = (directory / "CMakeCache.txt").read_text(encoding="utf-8")
        if not re.search(r"^CMAKE_BUILD_TYPE:STRING=Debug$", cache, re.MULTILINE):
            raise ValueError(f"--{option.replace('_', '-')} must be configured with CMAKE_BUILD_TYPE=Debug")
    tools = [release.find_tool(args.build_dir, name, "", "Debug") for name in release.TOOLS]
    for binary in (extension, *tools):
        if not is_elf(binary):
            raise ValueError(f"Debug binary must be a Linux x86_64 ELF: {binary}")
        sections = run(["readelf", "--section-headers", "--wide", str(binary)])
        if not all(re.search(rf"\s{section}\s", sections) for section in (r"\.debug_info", r"\.debug_line")):
            raise ValueError(f"Debug binary needs embedded DWARF .debug_info and .debug_line (do not strip): {binary}")
    return tools


def copy_viewer_project(project: Path) -> None:
    for source in sorted(release.VIEWER_PROJECT.rglob("*")):
        relative = source.relative_to(release.VIEWER_PROJECT)
        if not source.is_file() or ".godot" in relative.parts or source.suffix.lower() == ".pdb":
            continue
        if relative.parts[0] == "bin" and relative != Path("bin") / EXTENSION:
            continue
        if source.is_symlink():
            raise ValueError(f"symlink in project: {source}")
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if not is_elf(project / "bin" / EXTENSION):
        raise ValueError("viewer extension must be a Linux x86_64 ELF")
    (project / "bin" / EXTENSION).chmod(0o755)


def stage_desktop_files(args, folder: Path) -> None:
    for name in release.DOCS:
        shutil.copyfile(release.ROOT / name, folder / name)
    shutil.copyfile(release.ROOT / "docs/linux-player.md", folder / "README.md")
    release.export_package.bundle_licenses(folder)
    if any((folder / "lib").iterdir()):
        if args.library_notices is None:
            raise ValueError("bundled libraries require --library-notices (licenses and source offers)")
        shutil.copyfile(args.library_notices, folder / "LINUX-LIBRARY-NOTICES.txt")
    shutil.copyfile(release.ROOT / "run.sh", folder / "run.sh")
    (folder / "run.sh").chmod(0o755)
    for name, script in (("AppRun", APP_RUN), ("EAWR", PLAYER_START),
                         ("EAWR (Safe render thread)", SAFE_START), ("EAWR Mods", MODS_START)):
        (folder / name).write_text(script, encoding="utf-8")
        (folder / name).chmod(0o755)
    if args.debug:
        (folder / "DEBUG-BUILD.md").write_text(DEBUG_GUIDE, encoding="utf-8")
    (folder / "eawr.desktop").write_text(DESKTOP, encoding="utf-8")
    (folder / "eawr.svg").write_text(ICON, encoding="utf-8")
    # Supply a regular icon file so appimagetool does not create an unhashed symlink.
    (folder / ".DirIcon").write_text(ICON, encoding="utf-8")


def write_runtime_manifest(args, folder: Path, runtime_pin: dict, build_dirty: bool, tools: list[Path]) -> None:
    audit = audit_libraries(folder, [])
    validate_payload(folder)
    manifest = {"schema": "eawr-linux-player-runtime/1", "platform": "linux-x64",
                "variant": "debug" if args.debug else "player",
                "godot": runtime_pin, "build_commit": args.expect_identity,
                "development": args.development, "build_dirty": build_dirty,
                "appimagetool": args.tool_record, "elf_audit": audit,
                "dlopen_policy": "Host graphics drivers and optional system backends are not bundled."
                                  " ldd audits DT_NEEDED, not arbitrary dlopen."}
    if args.debug:
        manifest.update(configuration="Debug", symbols="embedded DWARF",
                        tools_sha256={tool.name: release.sha256_file(folder / "tools" / tool.name) for tool in tools})
    (folder / "runtime.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (folder / "SHA256SUMS").write_text(payload_sums(folder), encoding="utf-8")


def stage(args, folder: Path, importer=None) -> None:
    if args.with_mods_manager:
        raise ValueError("--with-mods-manager is blocked until the private manager is validated on Linux")
    runtime_pin = verify_runtime(args.godot, args.runtime_pin, args.library_root)
    extension = release.VIEWER_PROJECT / "bin" / EXTENSION
    tools = debug_inputs(args, extension)
    binaries = [(extension, True), (args.launcher, False), (args.mods_service, True), (args.loadcheck, False)]
    binaries.extend((tool, False) for tool in tools)
    build_dirty = verify_player_identity(args.expect_identity, binaries, args.development)
    for source, name in ((args.godot, GODOT_NAME), (args.launcher, "eawr-launcher"),
                         (args.mods_service, "eawr_mods_service"), (args.loadcheck, "eawr_loadcheck")):
        copy_executable(source, folder / name)
    for tool in tools:
        copy_executable(tool, folder / "tools" / tool.name)
    project = folder / "project"
    copy_viewer_project(project)
    check_hash(folder / GODOT_NAME, runtime_pin["sha256"])
    # Stage the importer's library closure before running the bundled editor.
    audit_libraries(folder, args.library_root)
    if importer is None:
        importer = import_project
    release.preimport_project(folder / GODOT_NAME, project, importer)
    stage_desktop_files(args, folder)
    write_runtime_manifest(args, folder, runtime_pin, build_dirty, tools)


def payload_sums(folder: Path) -> str:
    return "".join(f"{release.sha256_file(path)}  {path.relative_to(folder).as_posix()}\n"
                   for path in sorted(folder.rglob("*")) if path.is_file() and path != folder / "SHA256SUMS")


def import_project(godot: Path, project: Path) -> str:
    result = subprocess.run([str(godot), "--headless", "--path", str(project), "--import"],
                            env=dict(os.environ, LD_LIBRARY_PATH=str(project.parent / "lib")),
                            check=False, capture_output=True, text=True,
                            timeout=release.IMPORT_TIMEOUT_SECONDS)
    return f"exit {result.returncode}: {(result.stdout + result.stderr)[-400:]}"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("godot", "runtime-pin", "launcher", "mods-service", "loadcheck", "appimagetool", "appimage-runtime"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--appimagetool-sha256", required=True)
    parser.add_argument("--appimage-runtime-sha256", required=True, help="local type-2 runtime, disables runtime downloads")
    parser.add_argument("--expect-identity", required=True, help="full project commit embedded in binaries")
    parser.add_argument("--development", action="store_true",
                         help="package matching local dirty builds as explicitly named development artifacts")
    parser.add_argument("--debug", action="store_true", help="package the Debug viewer and headless tools with embedded DWARF")
    parser.add_argument("--build-dir", type=Path, help="root tools build configured with CMAKE_BUILD_TYPE=Debug")
    parser.add_argument("--viewer-build-dir", type=Path, help="viewer build configured with CMAKE_BUILD_TYPE=Debug")
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", type=Path, default=release.ROOT / "out/release")
    parser.add_argument("--library-root", type=Path, action="append", default=[], help="repeat for flat library directories")
    parser.add_argument("--library-notices", type=Path)
    parser.add_argument("--with-mods-manager", action="store_true")
    return parser.parse_args(argv)


def validate_packaging_inputs(args) -> dict:
    if args.with_mods_manager:
        raise ValueError("--with-mods-manager is blocked until Linux validation")
    if args.debug and (args.build_dir is None or args.viewer_build_dir is None):
        raise ValueError("--debug requires --build-dir and --viewer-build-dir configured as Debug")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.version):
        raise ValueError("--version must be a filename-safe version")
    args.out = args.out.resolve()
    for name in ("godot", "runtime_pin", "launcher", "mods_service", "loadcheck", "appimagetool", "appimage_runtime"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    tool_hash = check_hash(args.appimagetool, args.appimagetool_sha256)
    appimage_runtime_hash = check_hash(args.appimage_runtime, args.appimage_runtime_sha256)
    if not is_elf(args.appimage_runtime):
        raise ValueError("--appimage-runtime must be an x86_64 ELF")
    tool_env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1", ARCH="x86_64")
    tool_version = run([str(args.appimagetool), "--version"], env=tool_env)
    if "appimagetool" not in tool_version.lower():
        raise ValueError("packaging tool does not identify as appimagetool")
    args.tool_record = {"sha256": tool_hash, "version": tool_version,
                        "appimage_runtime_sha256": appimage_runtime_hash}
    return tool_env


def build_verified_image(args, staged: Path, image: Path, tool_env: dict) -> None:
    run([str(args.appimagetool), "--no-appstream", "--runtime-file", str(args.appimage_runtime),
         str(staged), str(image)], env=tool_env)
    validate_payload(staged)
    if (staged / "SHA256SUMS").read_text(encoding="utf-8") != payload_sums(staged):
        raise ValueError("appimagetool changed the audited AppDir payload")
    if not is_elf(image):
        raise ValueError("appimagetool produced no x86_64 AppImage ELF")
    with image.open("rb") as stream:
        stream.seek(8)
        if stream.read(3) != b"AI\x02":
            raise ValueError("appimagetool output is not a type-2 AppImage")
    image.chmod(0o755)


def publish_outputs(staged: Path, built_image: Path, staged_sums: Path, appdir: Path) -> None:
    image = appdir.with_suffix(".AppImage")
    sums = image.with_suffix(".AppImage.sha256")
    published = []
    try:
        appdir.mkdir()  # Atomically claim publication before replacing our empty directory.
        published.append(appdir)
        staged.replace(appdir)
        for source, target in ((built_image, image), (staged_sums, sums)):
            os.link(source, target)
            published.append(target)
    except OSError:
        for path in reversed(published):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError:
                pass  # Preserve the original publication error if cleanup also fails.
        raise


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        tool_env = validate_packaging_inputs(args)
        args.out.mkdir(parents=True, exist_ok=True)
        stem = f"eaw-revised-{args.version}-linux-x64-{'debug' if args.debug else 'player'}"
        if args.development:
            stem += "-development"
        appdir = args.out / (stem + ".AppDir")
        image = args.out / (stem + ".AppImage")
        sums = image.with_suffix(".AppImage.sha256")
        if any(path.exists() or path.is_symlink() for path in (appdir, image, sums)):
            raise ValueError("output already exists, use a clean output directory or a new version")
        with tempfile.TemporaryDirectory(prefix=".linux-player-", dir=args.out) as temporary:
            staged = Path(temporary) / appdir.name
            staged.mkdir()
            stage(args, staged)
            built_image = Path(temporary) / image.name
            build_verified_image(args, staged, built_image, tool_env)
            staged_sums = Path(temporary) / sums.name
            staged_sums.write_text(f"{release.sha256_file(built_image)}  {image.name}\n", encoding="utf-8")
            publish_outputs(staged, built_image, staged_sums, appdir)
        for path in (appdir, image, sums):
            print(path)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"package_linux_player: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
