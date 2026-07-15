"""Persistence contracts for room invites and short-lived room sessions."""
from __future__ import annotations

import json
import secrets
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from agentsassemble.meeting_events import clean_lobby_text

ROOM_INVITE_STORE_SCHEMA = "agentsassemble.room_invite_state.v1"


class InviteRepositoryError(RuntimeError):
    """Base error for unavailable or invalid invite/session persistence."""


class InviteRepositoryUnavailable(InviteRepositoryError):
    """The configured repository cannot be read."""


class InviteRepositoryCorrupt(InviteRepositoryError):
    """The configured repository contains invalid or unsupported state."""


class InviteRepositoryWriteFailed(InviteRepositoryError):
    """A repository mutation could not be durably persisted."""


class InviteRepositoryNotConfigured(InviteRepositoryError):
    """Invite/session persistence has not been selected for this process."""


_RepositoryState = tuple[
    str,
    dict[str, dict[str, object]],
    set[str],
    dict[str, dict[str, object]],
]


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

    def replace_participant_session(
        self,
        token_fingerprint: str,
        record: dict[str, object],
    ) -> None: ...

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


class UnconfiguredInviteSessionRepository:
    """Fail-closed placeholder used until application composition selects storage."""

    _MESSAGE = "Invite/session repository is not configured."

    def _raise(self) -> None:
        raise InviteRepositoryNotConfigured(self._MESSAGE)

    def signing_secret(self) -> str:
        self._raise()

    def existing_signing_secret(self) -> str:
        self._raise()

    def save_invite(self, record: dict[str, object]) -> None:
        del record
        self._raise()

    def invite(self, invite_id: str) -> dict[str, object] | None:
        del invite_id
        self._raise()

    def invite_for_join_code(self, join_code_fingerprint: str) -> dict[str, object] | None:
        del join_code_fingerprint
        self._raise()

    def nonce_was_used(self, nonce_fingerprint: str) -> bool:
        del nonce_fingerprint
        self._raise()

    def consume(
        self,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
    ) -> str:
        del invite_id, nonce_fingerprint, reusable, max_uses
        self._raise()

    def revoke_invite(self, invite_id: str) -> bool:
        del invite_id
        self._raise()

    def revoke_room_invites(self, room_id: str) -> int:
        del room_id
        self._raise()

    def list_invites(self) -> list[dict[str, object]]:
        self._raise()

    def save_session(self, token_fingerprint: str, record: dict[str, object]) -> None:
        del token_fingerprint, record
        self._raise()

    def replace_participant_session(
        self,
        token_fingerprint: str,
        record: dict[str, object],
    ) -> None:
        del token_fingerprint, record
        self._raise()

    def session(self, token_fingerprint: str) -> dict[str, object] | None:
        del token_fingerprint
        self._raise()

    def revoke_session(self, token_fingerprint: str) -> bool:
        del token_fingerprint
        self._raise()

    def revoke_participant_sessions(self, room_id: str, participant_id: str) -> int:
        del room_id, participant_id
        self._raise()

    def revoke_room_sessions(self, room_id: str) -> int:
        del room_id
        self._raise()

    def list_sessions(self) -> list[tuple[str, dict[str, object]]]:
        self._raise()

    def reload(self) -> None:
        self._raise()

    def clear(self) -> None:
        self._raise()

    def close(self) -> None:
        return


class MemoryInviteSessionRepository:
    """Thread-safe repository for explicitly selected ephemeral operation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._invite_secret = ""
        self._sessions: dict[str, dict[str, object]] = {}
        self._used_nonce_fingerprints: set[str] = set()
        self._invites: dict[str, dict[str, object]] = {}

    def signing_secret(self) -> str:
        with self._lock:
            if not self._invite_secret:
                with self._persisted_mutation_locked():
                    self._invite_secret = secrets.token_urlsafe(32)
            return self._invite_secret

    def existing_signing_secret(self) -> str:
        with self._lock:
            return self._invite_secret

    def save_invite(self, record: dict[str, object]) -> None:
        invite_id = clean_lobby_text(record.get("invite_id"), limit=128)
        if not invite_id:
            raise ValueError("invite_id is required")
        with self._lock:
            with self._persisted_mutation_locked():
                self._invites[invite_id] = deepcopy(record)

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
                with self._persisted_mutation_locked():
                    if record is not None:
                        record["use_count"] = current_uses + 1
            else:
                if clean_nonce in self._used_nonce_fingerprints:
                    return "token_already_used"
                with self._persisted_mutation_locked():
                    self._used_nonce_fingerprints.add(clean_nonce)
        return ""

    def revoke_invite(self, invite_id: str) -> bool:
        with self._lock:
            record = self._invites.get(clean_lobby_text(invite_id, limit=128))
            if record is None:
                return False
            with self._persisted_mutation_locked():
                record["revoked"] = True
            return True

    def revoke_room_invites(self, room_id: str) -> int:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        with self._lock:
            targets = [
                record
                for record in self._invites.values()
                if record.get("meeting_id") == clean_room_id and not record.get("revoked")
            ]
            if targets:
                with self._persisted_mutation_locked():
                    for record in targets:
                        record["revoked"] = True
            return len(targets)

    def list_invites(self) -> list[dict[str, object]]:
        with self._lock:
            return [deepcopy(record) for record in self._invites.values()]

    def save_session(self, token_fingerprint: str, record: dict[str, object]) -> None:
        self.replace_participant_session(token_fingerprint, record)

    def replace_participant_session(
        self,
        token_fingerprint: str,
        record: dict[str, object],
    ) -> None:
        clean_fingerprint = clean_lobby_text(token_fingerprint, limit=128)
        if not clean_fingerprint:
            raise ValueError("session token fingerprint is required")
        room_id, participant_id = _session_identity(record)
        with self._lock:
            with self._persisted_mutation_locked():
                replaced = [
                    fingerprint
                    for fingerprint, existing in self._sessions.items()
                    if fingerprint != clean_fingerprint
                    and existing.get("meeting_id") == room_id
                    and existing.get("agent_id") == participant_id
                ]
                for fingerprint in replaced:
                    del self._sessions[fingerprint]
                self._sessions[clean_fingerprint] = deepcopy(record)

    def session(self, token_fingerprint: str) -> dict[str, object] | None:
        with self._lock:
            record = self._sessions.get(clean_lobby_text(token_fingerprint, limit=128))
            return deepcopy(record) if record is not None else None

    def revoke_session(self, token_fingerprint: str) -> bool:
        with self._lock:
            clean_fingerprint = clean_lobby_text(token_fingerprint, limit=128)
            if clean_fingerprint not in self._sessions:
                return False
            with self._persisted_mutation_locked():
                del self._sessions[clean_fingerprint]
            return True

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
            if doomed:
                with self._persisted_mutation_locked():
                    for fingerprint in doomed:
                        del self._sessions[fingerprint]
            return len(doomed)

    def revoke_room_sessions(self, room_id: str) -> int:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        with self._lock:
            doomed = [
                fingerprint
                for fingerprint, record in self._sessions.items()
                if record.get("meeting_id") == clean_room_id
            ]
            if doomed:
                with self._persisted_mutation_locked():
                    for fingerprint in doomed:
                        del self._sessions[fingerprint]
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
            with self._persisted_mutation_locked():
                self._invite_secret = ""
                self._sessions.clear()
                self._used_nonce_fingerprints.clear()
                self._invites.clear()

    def close(self) -> None:
        return

    def _persist_locked(self) -> None:
        return

    @contextmanager
    def _persisted_mutation_locked(self) -> Iterator[None]:
        previous = self._state_snapshot_locked()
        try:
            yield
            self._persist_locked()
        except BaseException:
            self._restore_state_locked(previous)
            raise

    def _state_snapshot_locked(self) -> _RepositoryState:
        return (
            self._invite_secret,
            deepcopy(self._sessions),
            set(self._used_nonce_fingerprints),
            deepcopy(self._invites),
        )

    def _restore_state_locked(
        self,
        state: _RepositoryState,
    ) -> None:
        (
            self._invite_secret,
            self._sessions,
            self._used_nonce_fingerprints,
            self._invites,
        ) = state


class JsonInviteSessionRepository(MemoryInviteSessionRepository):
    """Local-first JSON implementation preserving the existing disk schema."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        super().__init__()
        self.reload()

    def reload(self) -> None:
        try:
            raw_state = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            with self._lock:
                self._invite_secret = ""
                self._sessions.clear()
                self._used_nonce_fingerprints.clear()
                self._invites.clear()
            return
        except OSError as error:
            raise InviteRepositoryUnavailable(
                "Invite repository state could not be read."
            ) from error
        try:
            payload = json.loads(raw_state)
        except json.JSONDecodeError as error:
            raise InviteRepositoryCorrupt(
                "Invite repository state contains invalid JSON."
            ) from error
        if not isinstance(payload, dict):
            raise InviteRepositoryCorrupt("Invite repository state must be an object.")
        if payload.get("schema") != ROOM_INVITE_STORE_SCHEMA:
            raise InviteRepositoryCorrupt(
                "Invite repository state uses an unsupported schema."
            )
        sessions = payload.get("sessions")
        invites = payload.get("pending_invites")
        used_nonces = payload.get("used_nonce_fingerprints")
        invite_secret = payload.get("invite_secret")
        if (
            not isinstance(invite_secret, str)
            or not isinstance(sessions, dict)
            or not isinstance(invites, dict)
            or not isinstance(used_nonces, list)
        ):
            raise InviteRepositoryCorrupt(
                "Invite repository state has invalid field types."
            )

        loaded_sessions: dict[str, dict[str, object]] = {}
        loaded_session_identities: set[tuple[str, str]] = set()
        loaded_invites: dict[str, dict[str, object]] = {}
        loaded_nonces: set[str] = set()
        now = datetime.now(UTC)
        for raw_fingerprint, raw_record in sessions.items():
            fingerprint = clean_lobby_text(raw_fingerprint, limit=128)
            record = _clean_session_record(raw_record)
            if not fingerprint or not record:
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid session record."
                )
            expires_at = _parse_datetime(record.get("expires_at"))
            if expires_at is None:
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid session expiry."
                )
            if expires_at > now:
                identity = _session_identity(record)
                if identity in loaded_session_identities:
                    raise InviteRepositoryCorrupt(
                        "Invite repository state contains duplicate participant sessions."
                    )
                loaded_session_identities.add(identity)
                loaded_sessions[fingerprint] = record
        for raw_invite_id, raw_record in invites.items():
            invite_id = clean_lobby_text(raw_invite_id, limit=128)
            try:
                record = _clean_invite_record(raw_record, invite_id=invite_id)
            except (TypeError, ValueError, OverflowError) as error:
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid invite record."
                ) from error
            if not invite_id or not record:
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid invite record."
                )
            if _parse_datetime(record.get("expires_at")) is None:
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid invite expiry."
                )
            loaded_invites[invite_id] = record
        for item in used_nonces:
            if not isinstance(item, str):
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid nonce fingerprint."
                )
            nonce = clean_lobby_text(item, limit=128)
            if not nonce:
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid nonce fingerprint."
                )
            loaded_nonces.add(nonce)

        with self._lock:
            with self._persisted_mutation_locked():
                self._invite_secret = clean_lobby_text(invite_secret, limit=256)
                self._sessions = loaded_sessions
                self._used_nonce_fingerprints = loaded_nonces
                self._invites = loaded_invites

    def _persist_locked(self) -> None:
        state = {
            "schema": ROOM_INVITE_STORE_SCHEMA,
            "invite_secret": self._invite_secret,
            "sessions": dict(sorted(self._sessions.items())),
            "used_nonce_fingerprints": sorted(self._used_nonce_fingerprints),
            "pending_invites": dict(sorted(self._invites.items())),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temp_path.chmod(0o600)
            temp_path.replace(self.path)
        except OSError as error:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise InviteRepositoryWriteFailed(
                "Invite repository state could not be persisted."
            ) from error


def _parse_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _session_identity(record: dict[str, object]) -> tuple[str, str]:
    room_id = clean_lobby_text(record.get("meeting_id"), limit=128)
    participant_id = clean_lobby_text(record.get("agent_id"), limit=128)
    if not room_id or not participant_id:
        raise ValueError("session room and participant are required")
    return room_id, participant_id


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
