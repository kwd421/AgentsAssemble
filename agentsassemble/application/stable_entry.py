"""Permanent public entrypoint over the rotating quick tunnel.

A Cloudflare Worker (infra/room-redirector) 302-redirects a fixed
*.workers.dev URL to the current tunnel URL stored in KV. Whenever the tunnel
comes up with a fresh hostname, the server announces it to KV here, so one
stable link — including /join?token=... invites — survives restarts.

Best-effort by design: announcing requires npx + a wrangler OAuth login on
this machine; when either is missing the room still works on the raw tunnel
URL, just without the stable alias.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

_REDIRECTOR_DIR = Path(__file__).resolve().parents[2] / "infra" / "room-redirector"
_CONFIG_PATH = _REDIRECTOR_DIR / "stable-entry.json"
_ANNOUNCE_TIMEOUT_SECONDS = 90
_ANNOUNCE_ATTEMPTS = 3
_ANNOUNCE_RETRY_DELAY_SECONDS = 15


def stable_entry_config() -> dict[str, str] | None:
    try:
        payload = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = str(payload.get("url") or "").strip().rstrip("/")
    namespace_id = str(payload.get("namespace_id") or "").strip()
    if not url.startswith("https://") or not namespace_id:
        return None
    return {
        "url": url,
        "namespace_id": namespace_id,
        "kv_key": str(payload.get("kv_key") or "target").strip() or "target",
    }


def stable_entry_url() -> str:
    config = stable_entry_config()
    return config["url"] if config else ""


def announce_stable_entry(public_url: str) -> None:
    """Point the stable worker at the current tunnel URL (async, best-effort)."""
    config = stable_entry_config()
    clean_url = str(public_url or "").strip().rstrip("/")
    if not config or not clean_url.startswith("https://"):
        return

    def push() -> None:
        # npx/wrangler can hiccup right after boot (network, cold npx cache),
        # which would leave the permanent URL pointing at a dead tunnel — so
        # retry a few times before giving up.
        for attempt in range(1, _ANNOUNCE_ATTEMPTS + 1):
            try:
                completed = subprocess.run(
                    [
                        "npx", "wrangler", "kv", "key", "put",
                        config["kv_key"], clean_url,
                        f"--namespace-id={config['namespace_id']}",
                        "--remote",
                    ],
                    cwd=_REDIRECTOR_DIR,
                    capture_output=True,
                    text=True,
                    timeout=_ANNOUNCE_TIMEOUT_SECONDS,
                    check=False,
                )
                if completed.returncode == 0:
                    print(f"Stable entry updated: {config['url']} -> {clean_url}")
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass
            if attempt < _ANNOUNCE_ATTEMPTS:
                time.sleep(_ANNOUNCE_RETRY_DELAY_SECONDS)
        print("Stable entry update failed (room stays reachable on the tunnel URL).")

    threading.Thread(target=push, daemon=True, name="AgentsAssembleStableEntryAnnounce").start()
