"""Build signed desktop release and updater artifacts without storing credentials."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess
from urllib.parse import urlsplit

from macos_signing import signing_environment


DESKTOP_ROOT = Path(__file__).resolve().parent.parent
TAURI_ROOT = DESKTOP_ROOT / "src-tauri"
DEFAULT_NOTARY_PROFILE = "seinel-notary"


def required_environment(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"{name} must be supplied through the release environment")
    return value


def newest_dmg() -> Path:
    candidates = list((TAURI_ROOT / "target/release/bundle/dmg").glob("*.dmg"))
    if not candidates:
        raise SystemExit("The macOS release build did not produce a DMG")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main() -> int:
    endpoint = required_environment("AGENTSASSEMBLE_UPDATE_ENDPOINT")
    required_environment("AGENTSASSEMBLE_UPDATE_PUBLIC_KEY")
    required_environment("TAURI_SIGNING_PRIVATE_KEY")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit("AGENTSASSEMBLE_UPDATE_ENDPOINT must be an HTTPS URL")

    environment = signing_environment(required=True)
    subprocess.run(["npm", "run", "backend:build"], cwd=DESKTOP_ROOT, env=environment, check=True)
    subprocess.run(
        [
            "npm",
            "exec",
            "tauri",
            "build",
            "--",
            "--config",
            "src-tauri/tauri.release.conf.json",
        ],
        cwd=DESKTOP_ROOT,
        env=environment,
        check=True,
    )

    if platform.system() == "Darwin":
        dmg = newest_dmg()
        profile = str(
            os.environ.get("AGENTSASSEMBLE_NOTARY_PROFILE") or DEFAULT_NOTARY_PROFILE
        ).strip()
        subprocess.run(
            ["xcrun", "notarytool", "submit", str(dmg), "--keychain-profile", profile, "--wait"],
            check=True,
        )
        subprocess.run(["xcrun", "stapler", "staple", str(dmg)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
