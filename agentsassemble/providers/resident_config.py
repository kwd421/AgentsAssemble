"""Configuration view shared by legacy resident provider command adapters."""

from __future__ import annotations

from typing import Protocol


class ResidentCommandConfig(Protocol):
    server: str
    agent_id: str
    display_name: str
    session_id: str
    command: list[str]
    model_id: str
    effort: str
    codex_sandbox: str
    permission_option: str
    fast_mode: bool
    stream_thinking: bool
    meeting_id: str
    workspace_path: str


__all__ = ["ResidentCommandConfig"]
