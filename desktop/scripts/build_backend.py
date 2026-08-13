"""Build the Python room runtime that ships inside the desktop application."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


DESKTOP_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = DESKTOP_ROOT.parent
DIST_ROOT = DESKTOP_ROOT / "backend-dist"
WORK_ROOT = DESKTOP_ROOT / ".pyinstaller"
EXECUTABLE_NAME = "agentsassemble-server.exe" if os.name == "nt" else "agentsassemble-server"


def main() -> int:
    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise SystemExit("frontend/dist is missing; build the React application first")

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        "agentsassemble-server",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(WORK_ROOT / "build"),
        "--specpath",
        str(WORK_ROOT),
        "--paths",
        str(PROJECT_ROOT),
        "--collect-submodules",
        "agentsassemble",
        "--collect-data",
        "agentsassemble",
        "--add-data",
        f"{frontend_dist}{os.pathsep}frontend/dist",
        "--add-data",
        f"{PROJECT_ROOT / 'plugins'}{os.pathsep}plugins",
        str(DESKTOP_ROOT / "server_entry.py"),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    executable = DIST_ROOT / EXECUTABLE_NAME
    if not executable.is_file():
        raise SystemExit(f"desktop runtime was not produced at {executable}")
    target_triple = rust_host_triple()
    sidecar_suffix = ".exe" if os.name == "nt" else ""
    sidecar = (
        DESKTOP_ROOT
        / "src-tauri"
        / "binaries"
        / f"agentsassemble-server-{target_triple}{sidecar_suffix}"
    )
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, sidecar)
    print(sidecar)
    return 0


def rust_host_triple() -> str:
    result = subprocess.run(
        ["rustc", "-vV"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ").strip()
    raise SystemExit("rustc did not report a host target triple")


if __name__ == "__main__":
    raise SystemExit(main())
