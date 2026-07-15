"""Persistence contracts for room invites and short-lived room sessions."""
from __future__ import annotations

import json
import secrets
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentsassemble.meeting_events import clean_lobby_text

ROOM_INVITE_STORE_SCHEMA = "agentsassemble.room_invite_state.v1"


@runtime_checkable
class InviteRepository(Protocol):
    def signing_secret(self) -> str: ...

    def existing_signing_secret(self) -> str: ...

    def save_invite(self, record: dict[str, object]) -> None: ...

    def invite(self, invite_id: str) -> dict[str, object] | None: ...

    def invite_for_join_code(self, join_code_fingerprint: str) -> dict[str, object] | None: ...

    def nonce_was_used(self, nonce_fingerprint: str) -> bool: ...

    def consume(
        self,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
    ) -> str: ...

    def revoke_invite(self, invite_id: str) -> bool: ...

    def revoke_room_invites(self, room_id: str) -> int: ...

    def list_invites(self) -> list[dict[str, object]]: ...


@runtime_checkable
class SessionRepository(Protocol):
    def save_session(self, token_fingerprint: str, record: dict[str, object]) -> None: ...

    def session(self, token_fingerprint: str) -> dict[str, object] | None: ...

    def revoke_session(self, token_fingerprint: str) -> bool: ...

    def revoke_participant_sessions(self, room_id: str, participant_id: str) -> int: ...

    def revoke_room_sessions(self, room_id: str) -> int: ...

    def list_sessions(self) -> list[tuple[str, dict[str, object]]]: ...


@runtime_checkable
class InviteSessionRepository(InviteRepository, SessionRepository, Protocol):
    def reload(self) -> None: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...


class MemoryInviteSessionRepository:
    """Thread-safe repository used when local persistence is not configured."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._invite_secret = ""
        self._sessions: dict[str, dict[str, object]] = {}
        self._used_nonce_fingerprints: set[str] = set()
        self._invites: dict[str, dict[str, object]] = {}

    def signing_secret(self) -> str:
        with self._lock:
            if not self._invite_secret:
                self._invite_secret = secrets.token_urlsafe(32)
                self._persist_locked()
            return self._invite_secret

    def existing_signing_secret(self) -> str:
        with self._lock:
            return self._invite_secret

    def save_invite(self, record: dict[str, object]) -> None:
        invite_id = clean_lobby_text(record.get("invite_id"), limit=128)
        if not invite_id:
            raise ValueError("invite_id is required")
        with self._lock:
            self._invites[invite_id] = deepcopy(record)
            self._persist_locked()

    def invite(self, invite_id: str) -> dict[str, object] | None:
        with self._lock:
            record = self._invites.get(clean_lobby_text(invite_id, limit=128))
            return deepcopy(record) if record is not None else None

    def invite_for_join_code(self, join_code_fingerprint: str) -> dict[str, object] | None:
        clean_fingerprint = clean_lobby_text(join_code_fingerprint, limit=128)
        if not clean_fingerprint:
            return None
        with self._lock:
            for record in self._invites.values():
                if record.get("join_code_fingerprint") == clean_fingerprint:
                    return deepcopy(record)
        return None

    def nonce_was_used(self, nonce_fingerprint: str) -> bool:
        with self._lock:
            return clean_lobby_text(nonce_fingerprint, limit=128) in self._used_nonce_fingerprints

    def consume(
        self,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
    ) -> str:
        """Atomically consume one admission opportunity.

        Returns an empty string on success or the stable rejection reason on
        failure. Single-use signed tokens remain replay-protected even when the
        pending invite record is unavailable.
        """

        clean_invite_id = clean_lobby_text(invite_id, limit=128)
        clean_nonce = clean_lobby_text(nonce_fingerprint, limit=128)
        with self._lock:
            record = self._invites.get(clean_invite_id)
            if reusable:
                current_uses = int(record.get("use_count", 0)) if record else 0
                if max_uses and current_uses >= max_uses:
                    return "invite_use_limit_reached"
                if record is not None:
                    record["use_count"] = current_uses + 1
            else:
                if clean_nonce in self._used_nonce_fingerprints:
                    return "token_already_used"
                self._used_nonce_fingerprints.add(clean_nonce)
            self._persist_locked()
        return ""

    def revoke_invite(self, invite_id: str) -> bool:
        with self._lock:
            record = self._invites.get(clean_lobby_text(invite_id, limit=128))
            if record is None:
                return False
            record["revoked"] = True
            self._persist_locked()
            return True

    def revoke_room_invites(self, room_id: str) -> int:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        with self._lock:
            revoked = 0
            for record in self._invites.values():
                if record.get("meeting_id") == clean_room_id and not record.get("revoked"):
                    record["revoked"] = True
                    revoked += 1
            if revoked:
                self._persist_locked()
            return revoked

    def list_invites(self) -> list[dict[str, object]]:
        with self._lock:
            return [deepcopy(record) for record in self._invites.values()]

    def save_session(self, token_fingerprint: str, record: dict[str, object]) -> None:
        clean_fingerprint = clean_lobby_text(token_fingerprint, limit=128)
        if not clean_fingerprint:
            raise ValueError("session token fingerprint is required")
        with self._lock:
            self._sessions[clean_fingerprint] = deepcopy(record)
            self._persist_locked()

    def session(self, token_fingerprint: str) -> dict[str, object] | None:
        with self._lock:
            record = self._sessions.get(clean_lobby_text(token_fingerprint, limit=128))
            return deepcopy(record) if record is not None else None

    def revoke_session(self, token_fingerprint: str) -> bool:
        with self._lock:
            removed = self._sessions.pop(
                clean_lobby_text(token_fingerprint, limit=128),
                None,
            )
            if removed is not None:
                self._persist_locked()
            return removed is not None

    def revoke_participant_sessions(self, room_id: str, participant_id: str) -> int:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        if not clean_participant_id:
            return 0
        with self._lock:
            doomed = [
                fingerprint
                for fingerprint, record in self._sessions.items()
                if record.get("agent_id") == clean_participant_id
                and (not clean_room_id or record.get("meeting_id") == clean_room_id)
            ]
            for fingerprint in doomed:
                del self._sessions[fingerprint]
            if doomed:
                self._persist_locked()
            return len(doomed)

    def revoke_room_sessions(self, room_id: str) -> int:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        with self._lock:
            doomed = [
                fingerprint
                for fingerprint, record in self._sessions.items()
                if record.get("meeting_id") == clean_room_id
            ]
            for fingerprint in doomed:
                del self._sessions[fingerprint]
            if doomed:
                self._persist_locked()
            return len(doomed)

    def list_sessions(self) -> list[tuple[str, dict[str, object]]]:
        with self._lock:
            return [
                (fingerprint, deepcopy(record))
                for fingerprint, record in self._sessions.items()
            ]

    def reload(self) -> None:
        return

    def clear(self) -> None:
        with self._lock:
            self._invite_secret = ""
            self._sessions.clear()
            self._used_nonce_fingerprints.clear()
            self._invites.clear()
            self._persist_locked()

    def close(self) -> None:
        return

    def _persist_locked(self) -> None:
        return


class JsonInviteSessionRepository(MemoryInviteSessionRepository):
    """Local-first JSON implementation preserving the existing disk schema."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        super().__init__()
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._invite_secret = ""
            self._sessions.clear()
            self._used_nonce_fingerprints.clear()
            self._invites.clear()
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            except (OSError, json.JSONDecodeError):
                return
            if not isinstance(payload, dict):
                return
            self._invite_secret = clean_lobby_text(payload.get("invite_secret"), limit=256)
            now = datetime.now(UTC)
            sessions = payload.get("sessions")
            if isinstance(sessions, dict):
                for raw_fingerprint, raw_record in sessions.items():
                    fingerprint = clean_lobby_text(raw_fingerprint, limit=128)
                    record = _clean_session_record(raw_record)
                    if not fingerprint or not record or _expired(record, now):
                        continue
                    self._sessions[fingerprint] = record
            invites = payload.get("pending_invites")
            if isinstance(invites, dict):
                for raw_invite_id, raw_record in invites.items():
                    invite_id = clean_lobby_text(raw_invite_id, limit=128)
                    record = _clean_invite_record(raw_record, invite_id=invite_id)
                    if invite_id and record:
                        self._invites[invite_id] = record
            used_nonces = payload.get("used_nonce_fingerprints")
            if isinstance(used_nonces, list):
                self._used_nonce_fingerprints.update(
                    clean_lobby_text(item, limit=128)
                    for item in used_nonces
                    if clean_lobby_text(item, limit=128)
                )
            self._persist_locked()

    def _persist_locked(self) -> None:
        state = {
            "schema": ROOM_INVITE_STORE_SCHEMA,
            "invite_secret": self._invite_secret,
            "sessions": dict(sorted(self._sessions.items())),
            "used_nonce_fingerprints": sorted(self._used_nonce_fingerprints),
            "pending_invites": dict(sorted(self._invites.items())),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_name(f"{self.path.name}.tmp")
            temp_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            # Preserve the existing local-mode behavior: memory remains usable
            # when best-effort durability cannot write the local file.
            return


def _expired(record: dict[str, object], now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(record.get("expires_at") or "")) <= now
    except ValueError:
        return True


def _clean_session_record(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    record = {
        "agent_id": clean_lobby_text(source.get("agent_id"), limit=64),
        "display_name": clean_lobby_text(source.get("display_name"), limit=128),
        "meeting_id": clean_lobby_text(source.get("meeting_id"), limit=128),
        "invite_scope": clean_lobby_text(source.get("invite_scope"), limit=32) or "room",
        "participant_type": clean_lobby_text(source.get("participant_type"), limit=32) or "human",
        "client_type": clean_lobby_text(source.get("client_type"), limit=32) or "browser",
        "provider_kind": clean_lobby_text(source.get("provider_kind"), limit=64) or "manual",
        "owner_id": clean_lobby_text(source.get("owner_id"), limit=128),
        "connection_kind": clean_lobby_text(source.get("connection_kind"), limit=64),
        "joined_at": clean_lobby_text(source.get("joined_at"), limit=64),
        "expires_at": clean_lobby_text(source.get("expires_at"), limit=64),
    }
    if not record["agent_id"] or not record["meeting_id"] or not record["expires_at"]:
        return {}
    return record


def _clean_invite_record(value: object, *, invite_id: str) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    record = {
        "invite_id": clean_lobby_text(source.get("invite_id") or invite_id, limit=128),
        "agent_id": clean_lobby_text(source.get("agent_id"), limit=64),
        "display_name": clean_lobby_text(source.get("display_name"), limit=128),
        "meeting_id": clean_lobby_text(source.get("meeting_id"), limit=128),
        "invite_scope": clean_lobby_text(source.get("invite_scope"), limit=32) or "room",
        "participant_type": clean_lobby_text(source.get("participant_type"), limit=32) or "human",
        "client_type": clean_lobby_text(source.get("client_type"), limit=32) or "browser",
        "provider_kind": clean_lobby_text(source.get("provider_kind"), limit=64) or "manual",
        "created_by_user_id": clean_lobby_text(source.get("created_by_user_id"), limit=128),
        "join_code_fingerprint": clean_lobby_text(source.get("join_code_fingerprint"), limit=128),
        "join_nonce": clean_lobby_text(source.get("join_nonce"), limit=128),
        "permission_mode": clean_lobby_text(source.get("permission_mode"), limit=64),
        "max_uses": max(0, int(source.get("max_uses", 1) or 0)),
        "use_count": max(0, int(source.get("use_count", 0) or 0)),
        "expires_at": clean_lobby_text(source.get("expires_at"), limit=64),
        "created_at": clean_lobby_text(source.get("created_at"), limit=64),
        "revoked": bool(source.get("revoked")),
    }
    if not record["invite_id"] or not record["meeting_id"] or not record["expires_at"]:
        return {}
    return record
