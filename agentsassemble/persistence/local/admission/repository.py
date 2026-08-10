"""Local memory and JSON invite/session persistence adapters."""
from __future__ import annotations

import json
import secrets
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from agentsassemble.admission.maintenance import (
    AdmissionWorkflowSelection,
    PurgeReport,
    build_purge_report,
)
from agentsassemble.admission.capacity import (
    effective_invite_use_limit,
    enforce_room_session_capacity,
)
from agentsassemble.admission.repository import (
    InviteRepositoryCorrupt,
    InviteRepositoryUnavailable,
    InviteRepositoryWriteFailed,
)
from agentsassemble.admission.workflow_record import validate_admission_workflow_record
from agentsassemble.room.text import clean_room_text as clean_lobby_text

ROOM_INVITE_STORE_SCHEMA = "agentsassemble.admission.invite_state.v1"
LEGACY_ROOM_INVITE_STORE_SCHEMA = "agentsassemble.room_invite_state.v1"


_RepositoryState = tuple[
    str,
    dict[str, dict[str, object]],
    set[str],
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
]


class MemoryInviteSessionRepository:
    """Thread-safe repository for explicitly selected ephemeral operation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._invite_secret = ""
        self._sessions: dict[str, dict[str, object]] = {}
        self._used_nonce_fingerprints: set[str] = set()
        self._invites: dict[str, dict[str, object]] = {}
        self._admission_workflows: dict[str, dict[str, object]] = {}

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
                if record is None:
                    return "invite_not_found"
                current_uses = int(record.get("use_count", 0))
                if current_uses >= effective_invite_use_limit(max_uses):
                    return "invite_use_limit_reached"
                with self._persisted_mutation_locked():
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
            enforce_room_session_capacity(self._sessions.values(), record)
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

    def revoke_credential_sessions(self, credential_auth_key: str) -> int:
        clean_auth_key = clean_lobby_text(credential_auth_key, limit=128)
        if not clean_auth_key:
            return 0
        with self._lock:
            doomed = [
                fingerprint
                for fingerprint, record in self._sessions.items()
                if record.get("credential_auth_key") == clean_auth_key
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

    def create_admission_workflow(
        self,
        workflow_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        clean_workflow_id = clean_lobby_text(workflow_id, limit=128)
        if not clean_workflow_id:
            raise ValueError("admission workflow_id is required")
        with self._lock:
            existing = self._admission_workflows.get(clean_workflow_id)
            if existing is not None:
                return deepcopy(existing)
            created = validate_admission_workflow_record(
                {**deepcopy(record), "workflow_id": clean_workflow_id},
                workflow_id=clean_workflow_id,
            )
            with self._persisted_mutation_locked():
                self._admission_workflows[clean_workflow_id] = created
            return deepcopy(created)

    def admission_workflow(self, workflow_id: str) -> dict[str, object] | None:
        clean_workflow_id = clean_lobby_text(workflow_id, limit=128)
        with self._lock:
            record = self._admission_workflows.get(clean_workflow_id)
            return deepcopy(record) if record is not None else None

    def update_admission_workflow(
        self,
        workflow_id: str,
        updates: dict[str, object],
    ) -> dict[str, object]:
        clean_workflow_id = clean_lobby_text(workflow_id, limit=128)
        with self._lock:
            existing = self._admission_workflows.get(clean_workflow_id)
            if existing is None:
                raise ValueError("admission workflow was not found")
            updated = validate_admission_workflow_record(
                {**existing, **deepcopy(updates), "workflow_id": clean_workflow_id},
                workflow_id=clean_workflow_id,
            )
            with self._persisted_mutation_locked():
                self._admission_workflows[clean_workflow_id] = updated
            return deepcopy(updated)

    def consume_for_admission(
        self,
        workflow_id: str,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
        updates: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        clean_workflow_id = clean_lobby_text(workflow_id, limit=128)
        clean_invite_id = clean_lobby_text(invite_id, limit=128)
        clean_nonce = clean_lobby_text(nonce_fingerprint, limit=128)
        with self._lock:
            workflow = self._admission_workflows.get(clean_workflow_id)
            if workflow is None:
                raise ValueError("admission workflow was not found")
            if workflow.get("invite_consumed"):
                return "", deepcopy(workflow)
            invite = self._invites.get(clean_invite_id)
            if reusable:
                if invite is None:
                    return "invite_not_found", deepcopy(workflow)
                current_uses = int(invite.get("use_count", 0))
                if current_uses >= effective_invite_use_limit(max_uses):
                    return "invite_use_limit_reached", deepcopy(workflow)
            elif clean_nonce in self._used_nonce_fingerprints:
                return "token_already_used", deepcopy(workflow)

            updated = validate_admission_workflow_record(
                {
                    **workflow,
                    **deepcopy(updates),
                    "workflow_id": clean_workflow_id,
                    "invite_consumed": True,
                },
                workflow_id=clean_workflow_id,
            )
            with self._persisted_mutation_locked():
                if reusable:
                    assert invite is not None
                    invite["use_count"] = int(invite.get("use_count", 0)) + 1
                else:
                    self._used_nonce_fingerprints.add(clean_nonce)
                self._admission_workflows[clean_workflow_id] = updated
            return "", deepcopy(updated)

    def purge_admission_workflows(
        self,
        selection: AdmissionWorkflowSelection,
        *,
        apply: bool,
    ) -> PurgeReport:
        if not isinstance(selection, AdmissionWorkflowSelection):
            raise TypeError("selection must be an AdmissionWorkflowSelection")
        with self._lock:
            selected = [
                deepcopy(record)
                for record in self._admission_workflows.values()
                if selection.matches(record)
            ]
            if apply and selected:
                workflow_ids = {
                    str(record.get("workflow_id") or "")
                    for record in selected
                }
                with self._persisted_mutation_locked():
                    for workflow_id in workflow_ids:
                        self._admission_workflows.pop(workflow_id, None)
            return build_purge_report(
                selection,
                selected,
                applied=apply,
                purged_count=len(selected) if apply else 0,
            )

    def reload(self) -> None:
        return

    def clear(self) -> None:
        with self._lock:
            with self._persisted_mutation_locked():
                self._invite_secret = ""
                self._sessions.clear()
                self._used_nonce_fingerprints.clear()
                self._invites.clear()
                self._admission_workflows.clear()

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
            deepcopy(self._admission_workflows),
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
            self._admission_workflows,
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
                self._admission_workflows.clear()
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
        if payload.get("schema") not in {
            ROOM_INVITE_STORE_SCHEMA,
            LEGACY_ROOM_INVITE_STORE_SCHEMA,
        }:
            raise InviteRepositoryCorrupt(
                "Invite repository state uses an unsupported schema."
            )
        sessions = payload.get("sessions")
        invites = payload.get("pending_invites")
        used_nonces = payload.get("used_nonce_fingerprints")
        admission_workflows = payload.get("admission_workflows", {})
        invite_secret = payload.get("invite_secret")
        if (
            not isinstance(invite_secret, str)
            or not isinstance(sessions, dict)
            or not isinstance(invites, dict)
            or not isinstance(used_nonces, list)
            or not isinstance(admission_workflows, dict)
        ):
            raise InviteRepositoryCorrupt(
                "Invite repository state has invalid field types."
            )

        loaded_sessions: dict[str, dict[str, object]] = {}
        loaded_session_identities: set[tuple[str, str]] = set()
        loaded_invites: dict[str, dict[str, object]] = {}
        loaded_nonces: set[str] = set()
        loaded_workflows: dict[str, dict[str, object]] = {}
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
        for raw_workflow_id, raw_record in admission_workflows.items():
            workflow_id = clean_lobby_text(raw_workflow_id, limit=128)
            try:
                record = validate_admission_workflow_record(
                    raw_record,
                    workflow_id=workflow_id,
                )
            except (TypeError, ValueError, OverflowError) as error:
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid admission workflow."
                ) from error
            if not workflow_id or not record:
                raise InviteRepositoryCorrupt(
                    "Invite repository state contains an invalid admission workflow."
                )
            loaded_workflows[workflow_id] = record

        with self._lock:
            with self._persisted_mutation_locked():
                self._invite_secret = clean_lobby_text(invite_secret, limit=256)
                self._sessions = loaded_sessions
                self._used_nonce_fingerprints = loaded_nonces
                self._invites = loaded_invites
                self._admission_workflows = loaded_workflows

    def _persist_locked(self) -> None:
        state = {
            "schema": ROOM_INVITE_STORE_SCHEMA,
            "invite_secret": self._invite_secret,
            "sessions": dict(sorted(self._sessions.items())),
            "used_nonce_fingerprints": sorted(self._used_nonce_fingerprints),
            "pending_invites": dict(sorted(self._invites.items())),
            "admission_workflows": dict(sorted(self._admission_workflows.items())),
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
        "principal_user_id": clean_lobby_text(
            source.get("principal_user_id"),
            limit=128,
        ),
        "principal_is_operator": bool(source.get("principal_is_operator")),
        "credential_auth_key": clean_lobby_text(
            source.get("credential_auth_key"),
            limit=128,
        ),
        "connection_kind": clean_lobby_text(source.get("connection_kind"), limit=64),
        "client_id": clean_lobby_text(source.get("client_id"), limit=128),
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
