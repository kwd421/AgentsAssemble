"""Build or run the desktop app with a stable macOS signing identity."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from macos_signing import signing_environment


DESKTOP_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("build", "dev"))
    args = parser.parse_args()
    environment = signing_environment(required=False)
    subprocess.run(
        ["npm", "run", "backend:build"],
        cwd=DESKTOP_ROOT,
        env=environment,
        check=True,
    )
    subprocess.run(
        ["npm", "exec", "tauri", args.mode],
        cwd=DESKTOP_ROOT,
        env=environment,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
