"""Resolve a stable macOS code-signing identity for local and release builds."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import subprocess


_DEVELOPER_ID_PATTERN = re.compile(
    r'^\s*\d+\)\s+([0-9A-F]{40})\s+"Developer ID Application: [^"]+"',
    re.MULTILINE,
)


def signing_environment(*, required: bool) -> dict[str, str]:
    environment = dict(os.environ)
    if platform.system() != "Darwin":
        return environment
    if str(environment.get("APPLE_SIGNING_IDENTITY") or "").strip():
        return environment
    identity = installed_developer_id_identity()
    if identity:
        environment["APPLE_SIGNING_IDENTITY"] = identity
        return environment
    if required:
        raise SystemExit(
            "No Developer ID Application identity is available in the keychain"
        )
    print(
        "warning: no Developer ID Application identity found; "
        "the macOS build will use an unstable ad-hoc identity"
    )
    return environment


def installed_developer_id_identity() -> str:
    login_keychain = Path.home() / "Library/Keychains/login.keychain-db"
    searches = ([str(login_keychain)] if login_keychain.is_file() else []) + [None]
    for keychain in searches:
        command = ["security", "find-identity", "-v", "-p", "codesigning"]
        if keychain:
            command.append(keychain)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            continue
        matches = _DEVELOPER_ID_PATTERN.findall(result.stdout)
        if matches:
            return matches[0]
    return ""


__all__ = ["installed_developer_id_identity", "signing_environment"]
