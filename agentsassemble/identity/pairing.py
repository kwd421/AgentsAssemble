"""Explicit one-time pairing between browser origins for the local operator."""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit, urlunsplit

from agentsassemble.admission.transport_security import require_secure_room_transport
from agentsassemble.admission.session_issuer import session_token_fingerprint
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.application.transaction import ApplicationTransactionBoundary
from agentsassemble.identity.repository import (
    IdentityBackend,
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
    device_auth_key,
)
from agentsassemble.admission.lan_invite import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text

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
    require_secure_room_transport(scheme=scheme, hostname=hostname)
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
        sessions: RoomSessionService,
        transaction_boundary: ApplicationTransactionBoundary | None = None,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._identities = identities
        self._rooms = rooms
        self._sessions = sessions
        self._transaction_boundary = transaction_boundary
        self._now = now or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    def create(
        self,
        *,
        room_id: str,
        public_url: str,
        ttl_seconds: int = OPERATOR_PAIRING_MAX_TTL_SECONDS,
    ) -> dict[str, object]:
        clean_room_id = clean_room_text(room_id, limit=128)
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
        status = self._record_status(
            record,
            target_origin=target_origin,
            auth_key=auth_key,
        )
        if status not in {"ready", "resume"}:
            return {"status": "rejected", "reason": status}
        room_id = clean_room_text((record or {}).get("room_id"), limit=128)
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
        if consumed.get("status") not in {"consumed", "resumed"}:
            return {
                "status": "rejected",
                "reason": f"pairing_{consumed.get('status') or 'invalid'}",
            }
        pairing = consumed.get("pairing") if isinstance(consumed.get("pairing"), dict) else {}
        user = consumed.get("user") if isinstance(consumed.get("user"), dict) else {}
        display_name = clean_room_text(user.get("display_name"), limit=128) or "나"
        if pairing.get("redemption_status") == "completed":
            return self._completed_result(pairing, room=room, display_name=display_name)

        pairing_id = clean_room_text(pairing.get("pairing_id"), limit=128)
        if not pairing_id:
            return {"status": "rejected", "reason": "pairing_invalid"}
        try:
            if self._transaction_boundary is None:
                session_token, session = self._complete_redemption(
                    pairing_id=pairing_id,
                    pairing=pairing,
                    room_id=room_id,
                    display_name=display_name,
                    auth_key=auth_key,
                    completed_at=now.isoformat(),
                )
            else:
                with self._transaction_boundary.transaction():
                    session_token, session = self._complete_redemption(
                        pairing_id=pairing_id,
                        pairing=pairing,
                        room_id=room_id,
                        display_name=display_name,
                        auth_key=auth_key,
                        completed_at=now.isoformat(),
                    )
        except Exception as error:
            try:
                self._identities.update_operator_pairing_redemption(
                    pairing_id=pairing_id,
                    auth_key=auth_key,
                    status="failed_retryable",
                    failure_code=type(error).__name__,
                )
            except Exception as persistence_error:
                error.add_note(
                    "Operator pairing failure state could not be persisted: "
                    f"{type(persistence_error).__name__}."
                )
            raise
        return self._session_result(
            session_token,
            session,
            room=room,
            display_name=display_name,
        )

    def _complete_redemption(
        self,
        *,
        pairing_id: str,
        pairing: dict[str, object],
        room_id: str,
        display_name: str,
        auth_key: str,
        completed_at: str,
    ) -> tuple[str, dict[str, object]]:
        self._commit_operator_membership(room_id, display_name=display_name)
        session_token, session = self._sessions.ensure_for_request(
            f"operator-pairing:{pairing_id}",
            self._session_record(room_id, display_name=display_name),
            joined_at=clean_room_text(pairing.get("used_at"), limit=64),
        )
        completed = self._identities.update_operator_pairing_redemption(
            pairing_id=pairing_id,
            auth_key=auth_key,
            status="completed",
            completed_at=completed_at,
            session_fingerprint=session_token_fingerprint(session_token),
        )
        if not completed or completed.get("redemption_status") != "completed":
            raise RuntimeError("operator pairing completion could not be persisted")
        return session_token, session

    def revoke(self, pairing_id: str) -> bool:
        return self._identities.revoke_operator_pairing(
            clean_room_text(pairing_id, limit=128),
            revoked_at=self._now().isoformat(),
        )

    def _record_status(
        self,
        record: dict[str, object] | None,
        *,
        target_origin: str,
        auth_key: str,
    ) -> str:
        if not record:
            return "pairing_invalid"
        if str(record.get("target_origin") or "") != target_origin:
            return "pairing_origin_mismatch"
        if record.get("revoked_at"):
            return "pairing_revoked"
        if str(record.get("user_id") or "") != LOCAL_OPERATOR_USER_ID:
            return "pairing_invalid"
        if record.get("used_at"):
            if str(record.get("consumed_auth_key") or "") == auth_key:
                return "resume"
            return "pairing_already_used"
        try:
            expires_at = datetime.fromisoformat(str(record.get("expires_at") or ""))
        except ValueError:
            return "pairing_invalid"
        if expires_at <= self._now():
            return "pairing_expired"
        return "ready"

    def _commit_operator_membership(self, room_id: str, *, display_name: str) -> None:
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

    @staticmethod
    def _session_record(room_id: str, *, display_name: str) -> dict[str, object]:
        return {
            "agent_id": LOCAL_OPERATOR_PARTICIPANT_ID,
            "display_name": display_name,
            "meeting_id": room_id,
            "invite_scope": "room",
            "participant_type": "human",
            "client_type": "browser",
            "provider_kind": "manual",
            "owner_id": LOCAL_OPERATOR_USER_ID,
            "principal_user_id": LOCAL_OPERATOR_USER_ID,
            "principal_is_operator": True,
            "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
        }

    def _completed_result(
        self,
        pairing: dict[str, object],
        *,
        room: dict[str, object],
        display_name: str,
    ) -> dict[str, object]:
        pairing_id = clean_room_text(pairing.get("pairing_id"), limit=128)
        session_token = self._sessions.token_for_request(f"operator-pairing:{pairing_id}")
        expected_fingerprint = str(pairing.get("session_fingerprint") or "")
        if (
            not expected_fingerprint
            or session_token_fingerprint(session_token) != expected_fingerprint
        ):
            return {"status": "rejected", "reason": "pairing_session_unavailable"}
        session = self._sessions.verify(session_token)
        if session is None:
            return {"status": "rejected", "reason": "pairing_session_unavailable"}
        return self._session_result(
            session_token,
            session,
            room=room,
            display_name=display_name,
        )

    @staticmethod
    def _session_result(
        session_token: str,
        session: dict[str, object],
        *,
        room: dict[str, object],
        display_name: str,
    ) -> dict[str, object]:
        room_id = clean_room_text(session.get("meeting_id"), limit=128)
        return {
            "status": "admitted",
            "session_token": session_token,
            "agent_id": LOCAL_OPERATOR_PARTICIPANT_ID,
            "display_name": display_name,
            "meeting_id": room_id,
            "invite_scope": "room",
            "participant_type": "human",
            "client_type": "browser",
            "provider_kind": "manual",
            "owner_id": LOCAL_OPERATOR_USER_ID,
            "stable_identity": True,
            "operator": True,
            "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
            "expires_at": str(session.get("expires_at") or ""),
            "room_label": clean_room_text(room.get("label"), limit=128) or room_id,
            "room_topic": clean_room_text(room.get("topic"), limit=160),
            "room_created_at": clean_room_text(room.get("created_at"), limit=64),
        }
