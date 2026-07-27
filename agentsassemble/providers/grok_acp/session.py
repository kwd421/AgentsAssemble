"""Durable Grok ACP provider-session state."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path

from agentsassemble.room.text import clean_room_text


class GrokAcpSessionStore:
    def __init__(self, state_dir: Path, cwd: Path) -> None:
        self.state_dir = state_dir
        self.cwd = cwd

    @property
    def path(self) -> Path:
        return self.state_dir / "agentsassemble-session.json"

    def read(self) -> str:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
            return ""
        if not isinstance(payload, dict) or payload.get("cwd") != str(self.cwd):
            return ""
        return clean_room_text(payload.get("session_id"), limit=128)

    def persist(self, session_id: str) -> None:
        payload = {
            "version": 1,
            "transport": "grok_acp",
            "session_id": session_id,
            "cwd": str(self.cwd),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary_path, self.path)


__all__ = ["GrokAcpSessionStore"]
