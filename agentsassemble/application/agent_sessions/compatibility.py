"""Compatibility adapters for providers without a verified Agent Session runtime."""

from __future__ import annotations

from typing import Iterable, Protocol

AgentTurnChunk = dict[str, object]


def is_canonical_room_provider_session(session: dict[str, object]) -> bool:
    """Return whether a session belongs to the current room runtime lifecycle."""

    return any(
        str(session.get(field) or "").strip()
        for field in ("runtime_kind", "transport")
    )


def ensure_legacy_agent_session(session: dict[str, object]) -> None:
    """Keep legacy Agent Session controls away from canonical provider sessions."""

    if is_canonical_room_provider_session(session):
        raise ValueError(
            "Canonical room provider sessions must be controlled through room agent commands."
        )


class AgentSessionAdapter(Protocol):
    def start(self, config: dict[str, object]) -> dict[str, object]: ...

    def attach(self, ids: dict[str, object]) -> dict[str, object]: ...

    def send_turn(self, handle: dict[str, object], packet: dict[str, object]) -> Iterable[AgentTurnChunk]: ...

    def compact(self, handle: dict[str, object], policy: dict[str, object]) -> Iterable[AgentTurnChunk]: ...

    def detach(self, handle: dict[str, object]) -> None: ...

    def diagnose(self, handle: dict[str, object]) -> dict[str, object]: ...


class UnsupportedAgentSessionAdapter:
    provider_name = "unsupported"
    reason = "Provider Agent Session adapter is not configured yet."

    def start(self, config: dict[str, object]) -> dict[str, object]:
        return {
            "provider_kind": self.provider_name,
            "status": "unsupported",
            "resumable": False,
            "reason": self.reason,
        }

    def attach(self, ids: dict[str, object]) -> dict[str, object]:
        return {
            "provider_kind": self.provider_name,
            "status": "unsupported",
            "resumable": False,
            "reason": self.reason,
        }

    def send_turn(self, handle: dict[str, object], packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        yield {
            "type": "error",
            "diagnostics": [
                {
                    "setting": f"{self.provider_name}_adapter",
                    "status": "unsupported",
                    "message": self.reason,
                }
            ],
        }

    def compact(self, handle: dict[str, object], policy: dict[str, object]) -> Iterable[AgentTurnChunk]:
        yield from self.send_turn(handle, {})

    def detach(self, handle: dict[str, object]) -> None:
        return None

    def diagnose(self, handle: dict[str, object]) -> dict[str, object]:
        return {
            "provider_kind": self.provider_name,
            "status": "unsupported",
            "resumable": False,
            "reason": self.reason,
        }


class GrokAgentSessionAdapter(UnsupportedAgentSessionAdapter):
    provider_name = "grok"
    reason = "Grok is not wired into Agent Session runtime yet."


class ClaudeAgentSessionAdapter(UnsupportedAgentSessionAdapter):
    provider_name = "claude"
    reason = "Claude Agent Session runtime is Agent SDK only; claude -p is intentionally forbidden."


class AgyAgentSessionAdapter(UnsupportedAgentSessionAdapter):
    provider_name = "agy"
    reason = "AGY is unavailable until protocol verified."
