"""Issue and revoke bounded room sessions without owning invite policy."""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from agentsassemble.room_invite_repository import SessionRepository


def session_token_fingerprint(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


class RoomSessionIssuer:
    """Own the lifecycle of fingerprinted, short-lived room bearer sessions."""

    def __init__(
        self,
        repository: SessionRepository,
        *,
        token_prefix: str,
        ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._token_prefix = str(token_prefix or "").strip()
        self._ttl_seconds = int(ttl_seconds)
        self._now = now or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        if not self._token_prefix:
            raise ValueError("session token prefix is required")

    def issue(self, record: dict[str, object]) -> tuple[str, dict[str, object]]:
        now = self._now()
        token = f"{self._token_prefix}.{self._token_factory()}"
        session = {
            **record,
            "joined_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self._ttl_seconds)).isoformat(),
        }
        self._repository.replace_participant_session(
            session_token_fingerprint(token),
            session,
        )
        return token, session

    def verify(self, token: str) -> dict[str, object] | None:
        if not token or not token.startswith(f"{self._token_prefix}."):
            return None
        fingerprint = session_token_fingerprint(token)
        session = self._repository.session(fingerprint)
        if session is None:
            return None
        try:
            expires_at = datetime.fromisoformat(str(session.get("expires_at") or ""))
        except ValueError:
            self._repository.revoke_session(fingerprint)
            return None
        if expires_at <= self._now():
            self._repository.revoke_session(fingerprint)
            return None
        return session

    def revoke(self, token: str) -> bool:
        return self._repository.revoke_session(session_token_fingerprint(token))

    def revoke_participant(self, room_id: str, participant_id: str) -> int:
        return self._repository.revoke_participant_sessions(room_id, participant_id)

    def revoke_room(self, room_id: str) -> int:
        return self._repository.revoke_room_sessions(room_id)

    def active(self) -> list[dict[str, object]]:
        active: list[dict[str, object]] = []
        now = self._now()
        for fingerprint, session in self._repository.list_sessions():
            try:
                expires_at = datetime.fromisoformat(str(session.get("expires_at") or ""))
            except ValueError:
                self._repository.revoke_session(fingerprint)
                continue
            if expires_at <= now:
                self._repository.revoke_session(fingerprint)
                continue
            active.append(session)
        return active
