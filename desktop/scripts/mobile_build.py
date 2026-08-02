"""Initialize or build the keyless Tauri mobile clients with the Rustup toolchain."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


DESKTOP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ANDROID_SDK = Path.home() / "Library" / "Android" / "sdk"
DEFAULT_MAC_JAVA_HOME = Path("/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home")
APPLE_BUILD_ROOT = DESKTOP_ROOT / "src-tauri" / "gen" / "apple" / "build"


def rustup_binary(name: str) -> Path:
    rustup = shutil.which("rustup")
    if not rustup:
        raise SystemExit("rustup is required for mobile Rust targets")
    result = subprocess.run(
        [rustup, "which", name],
        check=True,
        capture_output=True,
        text=True,
    )
    binary = Path(result.stdout.strip())
    if not binary.is_file():
        raise SystemExit(f"rustup did not provide {name}: {binary}")
    return binary


def mobile_environment(platform: str) -> dict[str, str]:
    environment = dict(os.environ)
    rustc = rustup_binary("rustc")
    cargo = rustup_binary("cargo")
    environment["RUSTC"] = str(rustc)
    environment["CARGO"] = str(cargo)
    environment["PATH"] = os.pathsep.join(
        [str(rustc.parent), environment.get("PATH", "")]
    )
    if platform == "android":
        android_home = Path(environment.get("ANDROID_HOME") or DEFAULT_ANDROID_SDK)
        if not android_home.is_dir():
            raise SystemExit(f"Android SDK is missing: {android_home}")
        environment["ANDROID_HOME"] = str(android_home)
        ndk_home = environment.get("NDK_HOME")
        if not ndk_home:
            candidates = sorted(
                (path for path in (android_home / "ndk").glob("*") if path.is_dir()),
                reverse=True,
            )
            if not candidates:
                raise SystemExit("Android NDK is missing below ANDROID_HOME/ndk")
            ndk_home = str(candidates[0])
        environment["NDK_HOME"] = ndk_home
        if not environment.get("JAVA_HOME") and DEFAULT_MAC_JAVA_HOME.is_dir():
            environment["JAVA_HOME"] = str(DEFAULT_MAC_JAVA_HOME)
    return environment


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"ios", "android"} or sys.argv[2] not in {
        "init",
        "build",
    }:
        raise SystemExit("usage: mobile_build.py {ios|android} {init|build}")
    platform, action = sys.argv[1:]
    if platform == "ios" and action == "build":
        # Tauri's simulator bundler copies through an xcarchive and does not
        # replace a pre-existing app directory reliably. Both paths are ignored
        # generated output, so clear only those products before rebuilding.
        shutil.rmtree(
            APPLE_BUILD_ROOT / "agentsassemble-desktop_iOS.xcarchive",
            ignore_errors=True,
        )
        shutil.rmtree(
            APPLE_BUILD_ROOT / "arm64-sim" / "AgentsAssemble.app",
            ignore_errors=True,
        )
    command = ["npm", "exec", "tauri", platform, action, "--"]
    if action == "build":
        command.append("--debug")
        if platform == "ios":
            command.extend(["--target", "aarch64-sim"])
        else:
            command.extend(["--target", "aarch64", "--apk"])
    subprocess.run(
        command,
        cwd=DESKTOP_ROOT,
        env=mobile_environment(platform),
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
