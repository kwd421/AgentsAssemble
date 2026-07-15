"""Explicit one-time pairing between browser origins for the local operator."""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit, urlunsplit

from agentsassemble.identity_store import (
    IdentityBackend,
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
)
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.multi_host_invites import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room_invite import issue_paired_operator_session
from agentsassemble.room_repository import RoomRepository
from agentsassemble.room_users import device_auth_key

OPERATOR_PAIRING_TOKEN_PREFIX = "aap1_"
OPERATOR_PAIRING_MAX_TTL_SECONDS = 120


def normalize_pairing_origin(value: str) -> str:
    """Return a canonical HTTP(S) origin with no path, query, or fragment."""
    try:
        parsed = urlsplit(str(value or "").strip())
        parsed.port
    except ValueError:
        raise ValueError("pairing target must be a valid HTTP(S) origin") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("pairing target must be a valid HTTP(S) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("pairing target must not contain credentials, query, or fragment")
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((scheme, netloc, "", "", ""))


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


class OperatorPairingService:
    """Create and redeem explicit cross-origin operator credentials.

    Raw pairing tokens exist only in the create response and redeem request.
    The identity backend persists only their SHA-256 fingerprint.
    """

    def __init__(
        self,
        *,
        identities: IdentityBackend,
        rooms: RoomRepository,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._identities = identities
        self._rooms = rooms
        self._now = now or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    def create(
        self,
        *,
        room_id: str,
        public_url: str,
        ttl_seconds: int = OPERATOR_PAIRING_MAX_TTL_SECONDS,
    ) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        room = self._rooms.room(clean_room_id) if clean_room_id else {}
        if not room:
            raise ValueError("room was not found")
        operator = self._identities.get_user(LOCAL_OPERATOR_USER_ID)
        if not operator or not operator.get("is_operator"):
            raise ValueError("canonical operator identity is not claimed")
        target_origin = normalize_pairing_origin(public_url)
        ttl = min(max(int(ttl_seconds), 15), OPERATOR_PAIRING_MAX_TTL_SECONDS)
        now = self._now()
        expires_at = now + timedelta(seconds=ttl)
        raw_token = f"{OPERATOR_PAIRING_TOKEN_PREFIX}{self._token_factory()}"
        pairing_id = f"pair-{secrets.token_hex(8)}"
        record = self._identities.create_operator_pairing(
            pairing_id=pairing_id,
            token_fingerprint=_token_fingerprint(raw_token),
            room_id=clean_room_id,
            target_origin=target_origin,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        return {
            "status": "created",
            "pairing_id": record["pairing_id"],
            "room_id": clean_room_id,
            "target_origin": target_origin,
            "expires_at": record["expires_at"],
            "pairing_url": f"{target_origin}/pair?token={quote(raw_token)}",
        }

    def redeem(
        self,
        *,
        pairing_token: str,
        device_token: str,
        request_origin: str,
    ) -> dict[str, object]:
        clean_token = str(pairing_token or "").strip()
        if not clean_token.startswith(OPERATOR_PAIRING_TOKEN_PREFIX):
            return {"status": "invalid", "reason": "pairing_invalid"}
        auth_key = device_auth_key(device_token)
        if not auth_key:
            return {"status": "invalid", "reason": "device_credential_required"}
        try:
            target_origin = normalize_pairing_origin(request_origin)
        except ValueError:
            return {"status": "invalid", "reason": "origin_invalid"}

        fingerprint = _token_fingerprint(clean_token)
        record = self._identities.operator_pairing_for_fingerprint(fingerprint)
        status = self._record_status(record, target_origin=target_origin)
        if status != "ready":
            return {"status": "rejected", "reason": status}
        room_id = clean_lobby_text((record or {}).get("room_id"), limit=128)
        room = self._rooms.room(room_id) if room_id else {}
        if not room:
            return {"status": "rejected", "reason": "room_unavailable"}

        now = self._now()
        consumed = self._identities.consume_operator_pairing(
            token_fingerprint=fingerprint,
            target_origin=target_origin,
            auth_key=auth_key,
            used_at=now.isoformat(),
        )
        if consumed.get("status") != "consumed":
            return {
                "status": "rejected",
                "reason": f"pairing_{consumed.get('status') or 'invalid'}",
            }
        user = consumed.get("user") if isinstance(consumed.get("user"), dict) else {}
        display_name = clean_lobby_text(user.get("display_name"), limit=128) or "나"
        self._rooms.upsert_participant(
            room_id,
            {
                "participant_id": LOCAL_OPERATOR_PARTICIPANT_ID,
                "display_name": display_name,
                "participant_type": "human",
                "role": "host",
                "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
                "status": "joined",
            },
        )
        self._identities.upsert_membership(
            {
                "meeting_id": room_id,
                "participant_id": LOCAL_OPERATOR_PARTICIPANT_ID,
                "display_name": display_name,
                "participant_type": "human",
                "role": "host",
                "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
                "status": "online",
                "is_host": True,
                "source": "operator_pairing",
            }
        )
        session = issue_paired_operator_session(
            meeting_id=room_id,
            display_name=display_name,
        )
        session.update(
            {
                "room_label": clean_lobby_text(room.get("label"), limit=128) or room_id,
                "room_topic": clean_lobby_text(room.get("topic"), limit=160),
                "room_created_at": clean_lobby_text(room.get("created_at"), limit=64),
            }
        )
        return session

    def revoke(self, pairing_id: str) -> bool:
        return self._identities.revoke_operator_pairing(
            clean_lobby_text(pairing_id, limit=128),
            revoked_at=self._now().isoformat(),
        )

    def _record_status(
        self,
        record: dict[str, object] | None,
        *,
        target_origin: str,
    ) -> str:
        if not record:
            return "pairing_invalid"
        if str(record.get("target_origin") or "") != target_origin:
            return "pairing_origin_mismatch"
        if record.get("revoked_at"):
            return "pairing_revoked"
        if record.get("used_at"):
            return "pairing_already_used"
        try:
            expires_at = datetime.fromisoformat(str(record.get("expires_at") or ""))
        except ValueError:
            return "pairing_invalid"
        if expires_at <= self._now():
            return "pairing_expired"
        if str(record.get("user_id") or "") != LOCAL_OPERATOR_USER_ID:
            return "pairing_invalid"
        return "ready"
