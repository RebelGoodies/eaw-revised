#!/usr/bin/env python3
"""Build the pinned patched Linux editor: --source existing-checkout --out output-dir."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path

REVISION = "fad06b57944fbf17833fe3c65dc9ff1d7446c639"
REPOSITORY = "https://github.com/Thom-Ernst/godot.git"
IDENTITY = "4.7.2.stable.eawr.fad06b579"
RUNTIME_NAME = "Godot_v4.7.2-stable_linux.x86_64"
SCONS = ["uv", "tool", "run", "--from", "scons==4.10.1", "scons"]


def capture(command: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    return subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True,
                          text=True, timeout=60).stdout.strip()


def verify_source(source: Path) -> dict[str, str]:
    """Reject a different checkout, revision, local edits or local SCons overrides."""
    if Path(capture(["git", "rev-parse", "--show-toplevel"], cwd=source)).resolve() != source:
        raise ValueError("--source must be the checkout root")
    revision = capture(["git", "rev-parse", "HEAD"], cwd=source)
    if revision != REVISION:
        raise ValueError(f"source revision must be {REVISION}, got {revision}")
    repository = capture(["git", "remote", "get-url", "origin"], cwd=source)
    if repository != REPOSITORY:
        raise ValueError(f"source origin must be {REPOSITORY}, got {repository}")
    index_flags = subprocess.run(["git", "ls-files", "-v", "-z"], cwd=source, check=True,
                                 capture_output=True, timeout=60).stdout
    flagged = {record[:1] for record in index_flags.split(b"\0") if record and
               (record[:1].islower() or record[:1] == b"S")}
    if flagged:
        names = []
        if any(flag.islower() for flag in flagged):
            names.append("assume-unchanged")
        if b"S" in flagged or b"s" in flagged:
            names.append("skip-worktree")
        raise ValueError(f"source checkout has tracked files marked {' and '.join(names)}")
    if capture(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source):
        raise ValueError("source checkout must be clean (including untracked files)")
    if (source / "custom.py").exists():
        raise ValueError("remove custom.py: local SCons overrides are not permitted")
    return {"repository": repository, "revision": revision}


def build(source: Path, out: Path, jobs: int) -> tuple[Path, Path]:
    """Build with bundled dependencies and write a pin only after verification."""
    if os.geteuid() == 0:
        raise ValueError("run runtime builds as an unprivileged user")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("runtime builder requires a Linux x86_64 host")
    if not 1 <= jobs <= 8:
        raise ValueError("--jobs must be between 1 and 8")
    source = source.resolve(strict=True)
    out = out.resolve()
    if out.is_relative_to(source):
        raise ValueError("--out must be outside the source checkout")
    record = verify_source(source)
    runtime = out / RUNTIME_NAME
    pin_path = out / "runtime-pin.json"
    if runtime.exists() or pin_path.exists():
        raise ValueError("runtime output already exists, use a fresh --out directory")
    # Exclude caller compiler flags and SCons settings from this fixed build recipe.
    env = {key: value for key, value in os.environ.items()
           if key in ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "XDG_CACHE_HOME",
                      "UV_CACHE_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR")}
    env.update(BUILD_NAME="eawr", LC_ALL="C", TZ="UTC")
    compiler = {name: capture([name, "--version"], env=env) for name in ("gcc", "g++")}
    static_cpp = capture(["g++", "-print-file-name=libstdc++.a"], env=env)
    if static_cpp == "libstdc++.a" or not Path(static_cpp).is_file():
        raise ValueError(
            "missing static C++ runtime: g++ could not locate libstdc++.a. "
            "Install your distribution's static C++ runtime development package "
            "(Fedora: sudo dnf install libstdc++-static), then rerun this builder."
        )
    command = [*SCONS, "platform=linuxbsd", "target=editor", "arch=x86_64", "production=yes",
               "debug_symbols=no", "lto=none", "use_llvm=no", "use_static_cpp=yes",
               "CC=gcc", "CXX=g++", f"-j{jobs}"]
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "build.log"
    with log_path.open("w", encoding="utf-8") as log:
        log.write(json.dumps({"source": record, "command": shlex.join(command),
                              "environment": {"BUILD_NAME": "eawr", "LC_ALL": "C", "TZ": "UTC"},
                              "compiler": compiler}, indent=2) + "\n")
        log.flush()
        print(f"Building runtime, log: {log_path}", flush=True)
        subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    verify_source(source)
    built = source / "bin/godot.linuxbsd.editor.x86_64"
    with built.open("rb") as stream:
        header = stream.read(20)
    if (len(header) != 20 or header[:6] != b"\x7fELF\x02\x01"
            or struct.unpack("<H", header[18:20])[0] != 62):
        raise ValueError(f"built runtime is not an x86_64 ELF: {built}")
    identity = capture([str(built), "--version"], env=env)
    if identity != IDENTITY:
        raise ValueError(f"runtime identity must be {IDENTITY}, got {identity}")
    shutil.copyfile(built, runtime)
    runtime.chmod(0o755)
    digest = hashlib.sha256()
    with runtime.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    pin = {"schema": "eawr-linux-runtime-pin/1", "identity": identity, "sha256": digest.hexdigest(),
           "source": record,
           "build": {"command": "BUILD_NAME=eawr " + shlex.join(command), "compiler": compiler,
                     "toolchain": compiler["gcc"], "source_record": str(log_path),
                     "lto": "none", "profile": "local Linux production editor, not Windows release parity"}}
    pin_path.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
    return runtime, pin_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="existing clean pinned Godot checkout")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args(argv)
    try:
        for path in build(args.source, args.out, args.jobs):
            print(path)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"build_linux_runtime: {error}\nSee --out/build.log for compiler/dependency failures.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
