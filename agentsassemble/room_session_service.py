"""Server-scoped application service for bounded room access sessions."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_invite_repository import SessionRepository
from agentsassemble.room_session_issuer import RoomSessionIssuer


class RoomSessionService:
    """Expose session lifecycle operations without leaking repository details."""

    def __init__(
        self,
        repository: SessionRepository,
        *,
        token_prefix: str,
        ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._issuer = RoomSessionIssuer(
            repository,
            token_prefix=token_prefix,
            ttl_seconds=ttl_seconds,
            now=now,
            token_factory=token_factory,
        )

    def issue(self, record: dict[str, object]) -> tuple[str, dict[str, object]]:
        return self._issuer.issue(record)

    def verify(self, token: str) -> dict[str, object] | None:
        return self._issuer.verify(token)

    def revoke(self, token: str) -> bool:
        return self._issuer.revoke(token)

    def revoke_participant(self, room_id: str, participant_id: str) -> int:
        return self._issuer.revoke_participant(room_id, participant_id)

    def revoke_room(self, room_id: str) -> int:
        return self._issuer.revoke_room(room_id)

    def active_summary(self) -> list[dict[str, object]]:
        """Return active session diagnostics without bearer or owner material."""
        summaries: list[dict[str, object]] = []
        for session in self._issuer.active():
            summaries.append(
                {
                    "agent_id": clean_lobby_text(session.get("agent_id"), limit=128),
                    "display_name": clean_lobby_text(session.get("display_name"), limit=128),
                    "meeting_id": clean_lobby_text(session.get("meeting_id"), limit=128),
                    "invite_scope": clean_lobby_text(session.get("invite_scope"), limit=32)
                    or "room",
                    "participant_type": clean_lobby_text(
                        session.get("participant_type"),
                        limit=32,
                    )
                    or "human",
                    "client_type": clean_lobby_text(session.get("client_type"), limit=32)
                    or "browser",
                    "provider_kind": clean_lobby_text(session.get("provider_kind"), limit=64),
                    "joined_at": clean_lobby_text(session.get("joined_at"), limit=64),
                    "expires_at": clean_lobby_text(session.get("expires_at"), limit=64),
                }
            )
        return summaries
