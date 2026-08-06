"""One-time cross-device recovery for a server-scoped guest identity."""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime

from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.identity.repository import IdentityBackend, device_auth_key
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text

_INACTIVE_STATUSES = {"kicked", "left", "exported", "removed"}


def generate_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    return "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def normalize_recovery_code(value: object) -> str:
    normalized = "".join(character for character in str(value or "").upper() if character.isalnum())
    return normalized if len(normalized) == 32 else ""


class GuestIdentityRecoveryService:
    def __init__(
        self,
        *,
        identities: IdentityBackend,
        rooms: RoomRepository,
        sessions: RoomSessionService,
    ) -> None:
        self._identities = identities
        self._rooms = rooms
        self._sessions = sessions

    def issue(self, user_id: str) -> str:
        clean_user_id = clean_room_text(user_id, limit=128)
        if not clean_user_id or self._identities.get_user(clean_user_id) is None:
            raise ValueError("Authenticated user was not found.")
        code = generate_recovery_code()
        self._identities.create_recovery_code(
            user_id=clean_user_id,
            token_fingerprint=self._fingerprint(code),
            created_at=_now(),
        )
        return code

    def redeem(
        self,
        *,
        recovery_code: str,
        room_id: str,
        device_token: str,
        client_id: str,
    ) -> dict[str, object]:
        normalized_code = normalize_recovery_code(recovery_code)
        auth_key = device_auth_key(device_token)
        clean_room_id = clean_room_text(room_id, limit=128)
        if not normalized_code or not auth_key or not clean_room_id:
            return {"status": "rejected", "reason": "recovery_invalid"}
        fingerprint = self._fingerprint(normalized_code)
        user = self._identities.recovery_code_user(fingerprint)
        if user is None:
            return {"status": "rejected", "reason": "recovery_invalid"}
        participant_id = str(user.get("participant_id") or "")
        membership = self._identities.get_membership(clean_room_id, participant_id)
        participant = self._rooms.participant(clean_room_id, participant_id)
        if (
            not membership
            or not participant
            or str(membership.get("status") or "").lower() in _INACTIVE_STATUSES
            or str(participant.get("status") or "").lower() in _INACTIVE_STATUSES
        ):
            return {"status": "rejected", "reason": "recovery_membership_inactive"}

        replacement_code = generate_recovery_code()
        consumed_user = self._identities.consume_recovery_code(
            token_fingerprint=fingerprint,
            auth_key=auth_key,
            replacement_fingerprint=self._fingerprint(replacement_code),
            used_at=_now(),
        )
        if consumed_user is None:
            return {"status": "rejected", "reason": "recovery_invalid"}

        room = self._rooms.room(clean_room_id)
        settings = self._rooms.room_settings(clean_room_id)
        session_token, session = self._sessions.issue(
            {
                "agent_id": participant_id,
                "display_name": str(consumed_user.get("display_name") or participant_id),
                "meeting_id": clean_room_id,
                "invite_scope": "room",
                "participant_type": str(consumed_user.get("participant_type") or "human"),
                "client_type": "browser",
                "client_id": clean_room_text(client_id, limit=128),
                "provider_kind": str(membership.get("provider_kind") or "manual"),
                "principal_user_id": str(consumed_user.get("user_id") or ""),
                "principal_is_operator": bool(consumed_user.get("is_operator")),
                "connection_kind": str(membership.get("connection_kind") or "browser"),
            }
        )
        return {
            "status": "recovered",
            "session_token": session_token,
            "agent_id": participant_id,
            "display_name": str(consumed_user.get("display_name") or participant_id),
            "meeting_id": clean_room_id,
            "room_uid": str(room.get("room_uid") or ""),
            "server_id": self._identities.server_id(),
            "invite_scope": "room",
            "participant_type": str(consumed_user.get("participant_type") or "human"),
            "client_type": "browser",
            "client_id": clean_room_text(client_id, limit=128),
            "provider_kind": str(membership.get("provider_kind") or "manual"),
            "connection_kind": str(membership.get("connection_kind") or "browser"),
            "joined_at": str(session.get("joined_at") or ""),
            "expires_at": str(session.get("expires_at") or ""),
            "room_label": str(settings.get("label") or room.get("label") or clean_room_id),
            "room_topic": str(settings.get("topic") or ""),
            "room_created_at": str(room.get("created_at") or ""),
            "recovery_code": replacement_code,
        }

    def _fingerprint(self, code: str) -> str:
        normalized = normalize_recovery_code(code)
        material = f"guest-recovery-v1:{self._identities.server_id()}:{normalized}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "GuestIdentityRecoveryService",
    "generate_recovery_code",
    "normalize_recovery_code",
]
