"""Immutable provider launch profile and its stable runtime identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from agentsassemble.persona_cards.selection import add_persona_runtime_profile
from agentsassemble.room.text import clean_room_text


@dataclass(frozen=True)
class NativeCliProviderSpec:
    agent_id: str
    display_name: str
    command: tuple[str, ...]
    cwd: str = "."
    provider_kind: str = ""
    model: str = ""
    requested_model_id: str = ""
    model_selection_kind: str = "exact"
    model_observation_policy: str = "unavailable"
    catalog_revision: str = ""
    reasoning_effort: str = ""
    service_tier: str = ""
    variant: str = ""
    execution_harness: str = "builtin"
    permission_mode: str = "meeting_read_only"
    max_output_tokens: int = 0
    provider_endpoint: str = ""
    persona_card_id: str = ""
    persona_card_summary: dict[str, object] = field(default_factory=dict)
    runtime_kind: str = "live_cli"
    transport: str = "pty"
    default_responder: bool = True
    quiet_seconds: float = 4.0
    input_mode: str = "line"
    submit_newline: str = "\r"
    submit_delay_seconds: float = 0.1
    terminal_rows: int = 40
    terminal_columns: int = 120
    startup_quiet_seconds: float = 1.0
    startup_timeout_seconds: float = 20.0
    startup_accept_contains: str = ""
    startup_accept_keys: str = "\r"
    startup_ready_contains: str = ""
    startup_input: str = ""
    turn_timeout_seconds: float = 180.0

    def normalized_provider_kind(self) -> str:
        return clean_room_text(self.provider_kind, limit=64) or f"{self.agent_id}_live_session"

    def runtime_profile_key(self) -> str:
        values = {
            "provider_kind": self.normalized_provider_kind(),
            "command": list(self.command),
            "cwd": str(Path(self.cwd).expanduser().resolve()),
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
            "variant": self.variant,
            "permission_mode": self.permission_mode,
            "max_output_tokens": self.max_output_tokens,
            "runtime_kind": self.runtime_kind,
            "transport": self.transport,
            "quiet_seconds": self.quiet_seconds,
            "input_mode": self.input_mode,
            "submit_newline": self.submit_newline,
            "submit_delay_seconds": self.submit_delay_seconds,
            "terminal_rows": self.terminal_rows,
            "terminal_columns": self.terminal_columns,
            "startup_quiet_seconds": self.startup_quiet_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "startup_accept_contains": self.startup_accept_contains,
            "startup_accept_keys": self.startup_accept_keys,
            "startup_ready_contains": self.startup_ready_contains,
            "startup_input": self.startup_input,
            "turn_timeout_seconds": self.turn_timeout_seconds,
        }
        if self.provider_endpoint:
            values["provider_endpoint"] = self.provider_endpoint
        if self.execution_harness != "builtin":
            values["execution_harness"] = self.execution_harness
        add_persona_runtime_profile(values, self.persona_card_id)
        profile = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(profile.encode("utf-8")).hexdigest()[:20]


__all__ = ["NativeCliProviderSpec"]
