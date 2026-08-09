"""Server-scoped application service for bounded room access sessions."""
from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from agentsassemble.admission.repository import SessionRepository
from agentsassemble.admission.session_issuer import RoomSessionIssuer
from agentsassemble.room.text import clean_room_text

SERVER_BRIDGE_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


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
        token_key: Callable[[], str] | None = None,
    ) -> None:
        self._token_prefix = str(token_prefix or "").strip()
        self._ttl_seconds = int(ttl_seconds)
        self._now = now or (lambda: datetime.now(UTC))
        self._token_key = token_key
        self._issuer = RoomSessionIssuer(
            repository,
            token_prefix=self._token_prefix,
            ttl_seconds=ttl_seconds,
            now=self._now,
            token_factory=token_factory,
        )

    def issue(self, record: dict[str, object]) -> tuple[str, dict[str, object]]:
        return self._issuer.issue(record)

    def assert_capacity(self, record: dict[str, object]) -> None:
        self._issuer.assert_capacity(record)

    def token_for_request(self, request_key: str) -> str:
        if self._token_key is None:
            raise RuntimeError("idempotent room session key is not configured")
        key = self._token_key().encode("utf-8")
        digest = hmac.new(
            key,
            f"room-session:{request_key}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return f"{self._token_prefix}.{encoded}"

    def ensure_for_request(
        self,
        request_key: str,
        record: dict[str, object],
        *,
        joined_at: str = "",
        expires_at: str = "",
    ) -> tuple[str, dict[str, object]]:
        token = self.token_for_request(request_key)
        existing = self._issuer.verify(token)
        if existing is not None:
            return token, existing
        now = self._now()
        return self._issuer.issue_with_token(
            token,
            record,
            joined_at=joined_at or now.isoformat(),
            expires_at=expires_at
            or (now + timedelta(seconds=self._ttl_seconds)).isoformat(),
        )

    def ensure_server_bridge(
        self,
        request_key: str,
        record: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        """Issue a renewable, room-scoped credential for an owned bridge.

        A WebSocket ticket is deliberately single use and in-memory.  The
        bridge credential is persisted only as a fingerprint and lets the
        bridge obtain a fresh ticket after a rolling server replacement.
        """

        token = self.token_for_request(f"server-bridge:{request_key}")
        now = self._now()
        return self._issuer.issue_with_token(
            token,
            record,
            joined_at=now.isoformat(),
            expires_at=(
                now + timedelta(seconds=SERVER_BRIDGE_SESSION_TTL_SECONDS)
            ).isoformat(),
        )

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
                    "agent_id": clean_room_text(session.get("agent_id"), limit=128),
                    "display_name": clean_room_text(session.get("display_name"), limit=128),
                    "meeting_id": clean_room_text(session.get("meeting_id"), limit=128),
                    "invite_scope": clean_room_text(session.get("invite_scope"), limit=32)
                    or "room",
                    "participant_type": clean_room_text(
                        session.get("participant_type"),
                        limit=32,
                    )
                    or "human",
                    "client_type": clean_room_text(session.get("client_type"), limit=32)
                    or "browser",
                    "provider_kind": clean_room_text(session.get("provider_kind"), limit=64),
                    "joined_at": clean_room_text(session.get("joined_at"), limit=64),
                    "expires_at": clean_room_text(session.get("expires_at"), limit=64),
                }
            )
        return summaries
