from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from agentsassemble.persistence.local.room.attention import (
    cancel_attention_job,
    checkpoint_observed_seq,
    claim_attention_job,
    read_attention_job,
    read_attention_jobs,
    read_attention_lease,
    read_attention_leases,
    read_attention_state,
    record_attention_evaluation,
    resolve_attention_lease,
    write_attention_state,
)
from agentsassemble.persistence.local.room.database import (
    LEGACY_HIDDEN,
    ROOM_DATABASE_FILENAME,
    VISIBLE,
    initialize_room_database,
    migration_report,
    open_room_database,
)
from agentsassemble.room_attention import AgentAttentionState, AttentionEvaluation
from agentsassemble.room_global_settings import (
    RoomGlobalSettingsRecord,
    default_room_global_settings,
    merge_room_global_settings,
    validate_room_global_settings,
)
from agentsassemble.room_repository import RoomTransaction
from agentsassemble.room_repository_records import (
    ACTIVE_PARTICIPANT_STATUSES,
    build_room_event,
    build_room_record,
    clean_participant_id as _clean_participant_id,
    clean_room_id as _clean_room_id,
    clean_session_id as _clean_session_id,
    merge_participant_record,
    merge_session_record,
    participant_status as _participant_status,
    room_status as _room_status,
    safe_media_filename as _safe_media_filename,
    update_participant_record,
    update_session_record,
    utc_now as _now,
)
from agentsassemble.room.text import clean_room_text

_STORE_REGISTRY_LOCK = threading.RLock()
_STORE_LOCKS: dict[str, threading.RLock] = {}
_EVENT_LISTENERS: dict[str, list[Callable[[dict[str, object]], None]]] = {}
_INITIALIZED_DATABASES: dict[str, tuple[tuple[int, int], dict[str, object]]] = {}
_LOGGER = logging.getLogger(__name__)


def _store_lock(output_root: Path) -> threading.RLock:
    key = str(output_root.expanduser().resolve())
    with _STORE_REGISTRY_LOCK:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


class _SQLiteRoomTransaction:
    def __init__(
        self,
        store: RoomStore,
        connection: sqlite3.Connection,
        room_id: str,
        pending_events: list[dict[str, object]],
    ) -> None:
        self._store = store
        self._connection = connection
        self._room_id = room_id
        self._pending_events = pending_events

    @property
    def room_id(self) -> str:
        return self._room_id

    def participant(self, participant_id: str) -> dict[str, object]:
        return self._store._participant(self._connection, self._room_id, participant_id)

    def session(self, session_id: str) -> dict[str, object]:
        return self._store._session(self._connection, self._room_id, session_id)

    def command_record(self, principal_id: str, request_id: str) -> dict[str, object]:
        return self._store._command_record(
            self._connection,
            self._room_id,
            principal_id,
            request_id,
        )

    def room_settings(self) -> RoomGlobalSettingsRecord:
        return self._store._room_settings(self._connection, self._room_id)

    def update_room_settings(self, updates: dict[str, object]) -> RoomGlobalSettingsRecord:
        return self._store._update_room_settings(
            self._connection,
            self._room_id,
            updates,
        )

    def create_room(self, *, label: str = "", status: str = "active") -> tuple[dict[str, object], bool]:
        return self._store._create_room(self._connection, self._room_id, label=label, status=status)

    def upsert_participant(
        self,
        participant: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        return self._store._upsert_participant(self._connection, self._room_id, participant)

    def update_participant_fields(self, participant_id: str, **updates: object) -> dict[str, object]:
        return self._store._update_participant_fields(
            self._connection,
            self._room_id,
            participant_id,
            **updates,
        )

    def upsert_session(self, session: dict[str, object]) -> tuple[dict[str, object], bool]:
        return self._store._upsert_session(self._connection, self._room_id, session)

    def update_session_fields(self, session_id: str, **updates: object) -> dict[str, object]:
        return self._store._update_session_fields(
            self._connection,
            self._room_id,
            session_id,
            **updates,
        )

    def append_event(self, event_type: str, **payload: object) -> dict[str, object]:
        event = self._store._append_event(self._connection, self._room_id, event_type, **payload)
        self._pending_events.append(event)
        return event

    def record_command_result(
        self,
        request_id: str,
        result: dict[str, object],
        *,
        principal_id: str = "",
        action: str = "",
        payload_hash: str = "",
        max_entries: int = 500,
    ) -> dict[str, object]:
        return self._store._record_command_result(
            self._connection,
            self._room_id,
            request_id,
            result,
            principal_id=principal_id,
            action=action,
            payload_hash=payload_hash,
            max_entries=max_entries,
        )

    def advance_attention_state(
        self,
        participant_id: str,
        *,
        observed_seq: int | None = None,
        attention_evaluated_seq: int | None = None,
        provider_sync_seq: int | None = None,
        spoke_seq: int | None = None,
    ) -> AgentAttentionState:
        clean_participant_id = _clean_participant_id(participant_id)
        current = read_attention_state(self._connection, self._room_id, clean_participant_id)
        updated = current.advance(
            observed_seq=observed_seq,
            attention_evaluated_seq=attention_evaluated_seq,
            provider_sync_seq=provider_sync_seq,
            spoke_seq=spoke_seq,
        )
        return write_attention_state(self._connection, updated)

    def checkpoint_observed_seq(
        self,
        participant_id: str,
        observed_seq: int,
    ) -> AgentAttentionState:
        return checkpoint_observed_seq(
            self._connection,
            self._room_id,
            _clean_participant_id(participant_id),
            observed_seq,
        )

    def attention_state(self, participant_id: str) -> AgentAttentionState:
        return read_attention_state(
            self._connection,
            self._room_id,
            _clean_participant_id(participant_id),
        )

    def record_attention_evaluation(
        self,
        evaluation: AttentionEvaluation,
        *,
        mode: str,
        status: str,
    ) -> dict[str, object]:
        if evaluation.room_id != self._room_id:
            raise ValueError("Attention evaluation room does not match transaction room.")
        return record_attention_evaluation(
            self._connection,
            evaluation,
            mode=mode,
            status=status,
        )

    def attention_job(self, job_id: str) -> dict[str, object]:
        return read_attention_job(self._connection, self._room_id, job_id)

    def attention_lease(self, lease_id: str) -> dict[str, object]:
        return read_attention_lease(self._connection, self._room_id, lease_id)

    def claim_attention_job(
        self,
        job_id: str,
        *,
        participant_id: str,
        owner_id: str,
        lease_seconds: float,
    ) -> dict[str, object]:
        return claim_attention_job(
            self._connection,
            self._room_id,
            job_id,
            participant_id=participant_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
        )

    def resolve_attention_lease(self, lease_id: str, *, status: str) -> dict[str, object]:
        return resolve_attention_lease(
            self._connection,
            self._room_id,
            lease_id,
            status=status,
        )

    def cancel_attention_job(self, job_id: str) -> dict[str, object]:
        return cancel_attention_job(self._connection, self._room_id, job_id)


class RoomStore:
    """SQLite source of truth for room, participant, session, and event state."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)
        self.rooms_root = self.output_root / "rooms"
        self.database_path = self.rooms_root / ROOM_DATABASE_FILENAME
        self._lock = _store_lock(self.output_root)
        with self._lock:
            database_key = str(self.database_path.expanduser().resolve())
            cached = _INITIALIZED_DATABASES.get(database_key)
            signature = _database_signature(self.database_path)
            if cached is not None and signature is not None and cached[0] == signature:
                self._migration_report = dict(cached[1])
            else:
                self._migration_report = initialize_room_database(self.rooms_root, self.database_path)
                signature = _database_signature(self.database_path)
                if signature is not None:
                    _INITIALIZED_DATABASES[database_key] = (signature, dict(self._migration_report))

    def close(self) -> None:
        """SQLite connections are operation-scoped, so the repository has no resident handle."""

    def public_diagnostics(self) -> dict[str, object]:
        return {"backend": "sqlite"}

    def create_room(self, room_id: str, *, label: str = "", status: str = "active") -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self.transaction(clean_room_id) as transaction:
            room, created = transaction.create_room(label=label, status=status)
            if created:
                transaction.append_event("room_created", label=room["label"])
        return room

    @contextmanager
    def transaction(self, room_id: str) -> Iterator[RoomTransaction]:
        clean_room_id = _clean_room_id(room_id)
        pending_events: list[dict[str, object]] = []
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            transaction = _SQLiteRoomTransaction(self, connection, clean_room_id, pending_events)
            try:
                yield transaction
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self._publish_events(clean_room_id, pending_events)

    def room_is_deleted(self, room_id: str) -> bool:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM deleted_rooms WHERE room_id = ?", (clean_room_id,)
            ).fetchone() is not None

    def deleted_room_record(self, room_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT room_id, deleted_at, reason, principal_id, request_id,
                          action, payload_hash, cleanup_status, room_name, result_json
                   FROM deleted_rooms WHERE room_id = ?""",
                (clean_room_id,),
            ).fetchone()
        if row is None:
            return {}
        return {
            "room_id": str(row["room_id"] or ""),
            "deleted_at": str(row["deleted_at"] or ""),
            "reason": str(row["reason"] or ""),
            "principal_id": str(row["principal_id"] or ""),
            "request_id": str(row["request_id"] or ""),
            "action": str(row["action"] or ""),
            "payload_hash": str(row["payload_hash"] or ""),
            "cleanup_status": str(row["cleanup_status"] or ""),
            "room_name": str(row["room_name"] or ""),
            "result": _row_payload(row, column="result_json"),
        }

    def attention_state(self, room_id: str, participant_id: str) -> AgentAttentionState:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant_id)
        with self._connection() as connection:
            return read_attention_state(connection, clean_room_id, clean_participant_id)

    def attention_jobs(
        self,
        room_id: str,
        *,
        mode: str = "",
        status: str = "",
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            return read_attention_jobs(
                connection,
                clean_room_id,
                mode=mode,
                status=status,
                after_seq=after_seq,
                limit=limit,
            )

    def attention_job(self, room_id: str, job_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_job_id = clean_room_text(job_id, limit=128)
        with self._connection() as connection:
            return read_attention_job(connection, clean_room_id, clean_job_id)

    def attention_leases(
        self,
        room_id: str,
        *,
        status: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            return read_attention_leases(
                connection,
                clean_room_id,
                status=status,
                limit=limit,
            )

    def attention_lease(self, room_id: str, lease_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_lease_id = clean_room_text(lease_id, limit=128)
        with self._connection() as connection:
            return read_attention_lease(connection, clean_room_id, clean_lease_id)

    def delete_room(
        self,
        room_id: str,
        *,
        reason: str = "",
        tombstone: dict[str, object] | None = None,
        cleanup_status: str = "complete",
        room_name: str = "",
    ) -> bool:
        """Delete canonical room state and retain a tombstone against stale clients."""

        clean_room_id = _clean_room_id(room_id)
        now = _now()
        command = dict(tombstone or {})
        with self._lock, self._write_transaction() as connection:
            existed = connection.execute(
                "SELECT 1 FROM rooms WHERE room_id = ?", (clean_room_id,)
            ).fetchone() is not None
            if not existed and connection.execute(
                "SELECT 1 FROM deleted_rooms WHERE room_id = ?",
                (clean_room_id,),
            ).fetchone() is not None:
                return False
            for table in ("command_results", "room_events", "agent_sessions", "participants", "rooms"):
                connection.execute(f"DELETE FROM {table} WHERE room_id = ?", (clean_room_id,))
            connection.execute(
                """INSERT INTO deleted_rooms(
                       room_id, deleted_at, reason, principal_id, request_id, action,
                       payload_hash, cleanup_status, room_name, result_json
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(room_id) DO UPDATE SET
                       deleted_at = excluded.deleted_at,
                       reason = excluded.reason,
                       principal_id = excluded.principal_id,
                       request_id = excluded.request_id,
                       action = excluded.action,
                       payload_hash = excluded.payload_hash,
                       cleanup_status = excluded.cleanup_status,
                       room_name = excluded.room_name,
                       result_json = excluded.result_json""",
                (
                    clean_room_id,
                    now,
                    clean_room_text(reason, limit=500),
                    clean_room_text(command.get("principal_id"), limit=256),
                    clean_room_text(command.get("request_id"), limit=128),
                    clean_room_text(command.get("action"), limit=64),
                    clean_room_text(command.get("payload_hash"), limit=128),
                    clean_room_text(cleanup_status, limit=32) or "complete",
                    clean_room_text(room_name, limit=128),
                    _json_dumps(command.get("result") if isinstance(command.get("result"), dict) else {}),
                ),
            )
        return existed

    def update_deleted_room_record(
        self,
        room_id: str,
        *,
        result: dict[str, object],
        cleanup_status: str,
    ) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self._lock, self._write_transaction() as connection:
            updated = connection.execute(
                """UPDATE deleted_rooms
                   SET result_json = ?, cleanup_status = ?
                   WHERE room_id = ?""",
                (
                    _json_dumps(result),
                    clean_room_text(cleanup_status, limit=32) or "complete",
                    clean_room_id,
                ),
            ).rowcount
            if not updated:
                raise ValueError(f"Deleted room tombstone {clean_room_id} was not found.")
        return self.deleted_room_record(clean_room_id)

    def room(self, room_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            row = connection.execute("SELECT data_json FROM rooms WHERE room_id = ?", (clean_room_id,)).fetchone()
        return _row_payload(row)

    def room_settings(self, room_id: str) -> RoomGlobalSettingsRecord:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            return self._room_settings(connection, clean_room_id)

    def update_room_settings(
        self,
        room_id: str,
        updates: dict[str, object],
    ) -> RoomGlobalSettingsRecord:
        clean_room_id = _clean_room_id(room_id)
        with self.transaction(clean_room_id) as transaction:
            return transaction.update_room_settings(updates)

    def list_rooms(self, *, include_archived: bool = False) -> list[dict[str, object]]:
        query = "SELECT data_json FROM rooms"
        parameters: tuple[object, ...] = ()
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY updated_at DESC, room_id ASC"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_row_payload(row) for row in rows]

    def participants(self, room_id: str) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT data_json FROM participants WHERE room_id = ? ORDER BY rowid",
                (clean_room_id,),
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def participant(self, room_id: str, participant_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            return self._participant(connection, clean_room_id, participant_id)

    def active_participants(self, room_id: str) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        placeholders = ",".join("?" for _ in ACTIVE_PARTICIPANT_STATUSES)
        parameters: tuple[object, ...] = (clean_room_id, *sorted(ACTIVE_PARTICIPANT_STATUSES))
        with self._connection() as connection:
            rows = connection.execute(
                f"""SELECT data_json FROM participants
                    WHERE room_id = ? AND status IN ({placeholders}) ORDER BY rowid""",
                parameters,
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def upsert_participant(self, room_id: str, participant: dict[str, object]) -> tuple[dict[str, object], bool]:
        clean_room_id = _clean_room_id(room_id)
        with self.transaction(clean_room_id) as transaction:
            return transaction.upsert_participant(participant)

    def set_participant_status(
        self,
        room_id: str,
        participant_id: str,
        status: str,
        *,
        reason: str = "",
    ) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant_id)
        clean_status = _participant_status(status)
        updated = self.update_participant_fields(clean_room_id, clean_participant_id, status=clean_status)
        event_type = {
            "left": "participant_left",
            "kicked": "participant_kicked",
            "exported": "participant_exported",
            "detached": "session_detached",
        }.get(clean_status)
        if event_type:
            self.append_event(
                clean_room_id,
                event_type,
                participant_id=clean_participant_id,
                reason=clean_room_text(reason, limit=500),
            )
        if clean_status in {"left", "kicked", "exported", "detached"}:
            self.detach_participant_sessions(clean_room_id, clean_participant_id)
        return updated

    def sessions(self, room_id: str) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT data_json FROM agent_sessions WHERE room_id = ? ORDER BY rowid",
                (clean_room_id,),
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def session(self, room_id: str, session_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            return self._session(connection, clean_room_id, session_id)

    def upsert_session(self, room_id: str, session: dict[str, object]) -> tuple[dict[str, object], bool]:
        clean_room_id = _clean_room_id(room_id)
        with self.transaction(clean_room_id) as transaction:
            return transaction.upsert_session(session)

    def update_session_fields(self, room_id: str, session_id: str, **updates: object) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self.transaction(clean_room_id) as transaction:
            return transaction.update_session_fields(session_id, **updates)

    def update_participant_fields(self, room_id: str, participant_id: str, **updates: object) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self.transaction(clean_room_id) as transaction:
            return transaction.update_participant_fields(participant_id, **updates)

    def command_record(self, room_id: str, principal_id: str, request_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_principal_id = clean_room_text(principal_id, limit=256)
        clean_request_id = clean_room_text(request_id, limit=128)
        if not clean_request_id:
            return {}
        with self._connection() as connection:
            return self._command_record(
                connection,
                clean_room_id,
                clean_principal_id,
                clean_request_id,
            )

    def command_result(self, room_id: str, request_id: str, *, principal_id: str = "") -> dict[str, object]:
        return dict(self.command_record(room_id, principal_id, request_id).get("result") or {})

    def record_command_result(
        self,
        room_id: str,
        request_id: str,
        result: dict[str, object],
        *,
        principal_id: str = "",
        action: str = "",
        payload_hash: str = "",
        max_entries: int = 500,
    ) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self.transaction(clean_room_id) as transaction:
            return transaction.record_command_result(
                request_id,
                result,
                principal_id=principal_id,
                action=action,
                payload_hash=payload_hash,
                max_entries=max_entries,
            )

    def detach_participant_sessions(self, room_id: str, participant_id: str) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant_id)
        detached: list[dict[str, object]] = []
        with self._lock, self._write_transaction() as connection:
            rows = connection.execute(
                "SELECT data_json FROM agent_sessions WHERE room_id = ? AND participant_id = ? ORDER BY rowid",
                (clean_room_id, clean_participant_id),
            ).fetchall()
            for row in rows:
                session = _row_payload(row)
                updated = {**session, "status": "detached", "updated_at": _now()}
                self._write_session(connection, updated)
                detached.append(updated)
        return detached

    def append_event(self, room_id: str, event_type: str, **payload: object) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        with self.transaction(clean_room_id) as transaction:
            return transaction.append_event(event_type, **payload)

    def read_events(
        self,
        room_id: str,
        *,
        after: str = "",
        after_seq: int = 0,
        before_seq: int = 0,
        limit: int | None = None,
        newest: bool = False,
        include_hidden: bool = False,
        event_types: Iterable[str] | None = None,
        exclude_actor_id: str = "",
    ) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        clauses = ["room_id = ?"]
        parameters: list[object] = [clean_room_id]
        if not include_hidden:
            clauses.append("visibility = ?")
            parameters.append(VISIBLE)
        clean_event_types = tuple(
            event_type
            for event_type in (clean_room_text(value, limit=64) for value in (event_types or ()))
            if event_type
        )
        if clean_event_types:
            placeholders = ",".join("?" for _ in clean_event_types)
            clauses.append(f"event_type IN ({placeholders})")
            parameters.extend(clean_event_types)
        clean_excluded_actor = clean_room_text(exclude_actor_id, limit=128)
        if clean_excluded_actor:
            clauses.append("actor_id != ?")
            parameters.append(clean_excluded_actor)
        effective_after_seq = max(0, int(after_seq or 0))
        with self._connection() as connection:
            if not effective_after_seq and after:
                row = connection.execute(
                    "SELECT seq FROM room_events WHERE room_id = ? AND event_id = ?",
                    (clean_room_id, str(after)),
                ).fetchone()
                if row is not None:
                    effective_after_seq = int(row["seq"])
            if effective_after_seq:
                clauses.append("seq > ?")
                parameters.append(effective_after_seq)
            if before_seq:
                clauses.append("seq < ?")
                parameters.append(max(0, int(before_seq)))
            order = "DESC" if newest else "ASC"
            query = f"SELECT payload_json FROM room_events WHERE {' AND '.join(clauses)} ORDER BY seq {order}"
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(max(1, int(limit)))
            rows = connection.execute(query, tuple(parameters)).fetchall()
        events = [_row_payload(row, column="payload_json") for row in rows]
        if newest:
            events.reverse()
        return events

    def event_count(
        self,
        room_id: str,
        *,
        include_hidden: bool = False,
        after_seq: int = 0,
        before_seq: int = 0,
        event_types: Iterable[str] | None = None,
        exclude_actor_id: str = "",
    ) -> int:
        clean_room_id = _clean_room_id(room_id)
        clauses = ["room_id = ?"]
        parameters: list[object] = [clean_room_id]
        if not include_hidden:
            clauses.append("visibility = ?")
            parameters.append(VISIBLE)
        if after_seq:
            clauses.append("seq > ?")
            parameters.append(max(0, int(after_seq)))
        if before_seq:
            clauses.append("seq < ?")
            parameters.append(max(0, int(before_seq)))
        clean_event_types = tuple(
            event_type
            for event_type in (clean_room_text(value, limit=64) for value in (event_types or ()))
            if event_type
        )
        if clean_event_types:
            placeholders = ",".join("?" for _ in clean_event_types)
            clauses.append(f"event_type IN ({placeholders})")
            parameters.extend(clean_event_types)
        clean_excluded_actor = clean_room_text(exclude_actor_id, limit=128)
        if clean_excluded_actor:
            clauses.append("actor_id != ?")
            parameters.append(clean_excluded_actor)
        query = f"SELECT COUNT(*) FROM room_events WHERE {' AND '.join(clauses)}"
        with self._connection() as connection:
            return int(connection.execute(query, tuple(parameters)).fetchone()[0])

    def event_by_id(self, room_id: str, event_id: str, *, include_hidden: bool = False) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_event_id = clean_room_text(event_id, limit=128)
        if not clean_event_id:
            return {}
        query = "SELECT payload_json FROM room_events WHERE room_id = ? AND event_id = ?"
        parameters: tuple[object, ...] = (clean_room_id, clean_event_id)
        if not include_hidden:
            query += " AND visibility = ?"
            parameters = (clean_room_id, clean_event_id, VISIBLE)
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return _row_payload(row, column="payload_json")

    def event_sequence(self, room_id: str, event_id: str) -> int:
        clean_room_id = _clean_room_id(room_id)
        clean_event_id = clean_room_text(event_id, limit=128)
        if not clean_event_id:
            return 0
        with self._connection() as connection:
            row = connection.execute(
                "SELECT seq FROM room_events WHERE room_id = ? AND event_id = ?",
                (clean_room_id, clean_event_id),
            ).fetchone()
        return int(row["seq"]) if row is not None else 0

    def latest_event_sequence(self, room_id: str) -> int:
        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM room_events WHERE room_id = ?",
                (clean_room_id,),
            ).fetchone()
        return int(row[0])

    def oldest_event_sequence(self, room_id: str, *, include_hidden: bool = False) -> int:
        clean_room_id = _clean_room_id(room_id)
        query = "SELECT COALESCE(MIN(seq), 0) FROM room_events WHERE room_id = ?"
        parameters: tuple[object, ...] = (clean_room_id,)
        if not include_hidden:
            query += " AND visibility = ?"
            parameters = (clean_room_id, VISIBLE)
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return int(row[0])

    def add_event_listener(
        self,
        room_id: str,
        listener: Callable[[dict[str, object]], None],
    ) -> Callable[[], None]:
        listener_key = self._listener_key(_clean_room_id(room_id))
        with _STORE_REGISTRY_LOCK:
            _EVENT_LISTENERS.setdefault(listener_key, []).append(listener)

        def remove() -> None:
            with _STORE_REGISTRY_LOCK:
                listeners = _EVENT_LISTENERS.get(listener_key, [])
                if listener in listeners:
                    listeners.remove(listener)
                if not listeners:
                    _EVENT_LISTENERS.pop(listener_key, None)

        return remove

    def canonicalize_events(self, room_id: str) -> dict[str, object]:
        """Return the one-time migration report kept for compatibility callers."""

        clean_room_id = _clean_room_id(room_id)
        with self._connection() as connection:
            report = migration_report(connection)
        rooms = report.get("rooms") if isinstance(report.get("rooms"), dict) else {}
        room_report = rooms.get(clean_room_id) if isinstance(rooms.get(clean_room_id), dict) else {}
        return {
            "room_id": clean_room_id,
            "migrated": bool(report.get("migrated")),
            "event_count": int(room_report.get("event_count") or self.event_count(clean_room_id, include_hidden=True)),
            "hidden_event_count": int(room_report.get("hidden_event_count") or 0),
            "backup_path": str(report.get("backup_path") or ""),
            "database_path": str(self.database_path),
        }

    def attach_media(
        self,
        room_id: str,
        *,
        filename: str,
        content_type: str,
        size: int = 0,
        supported: bool,
        data: bytes = b"",
    ) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        media_id = uuid4().hex[:12]
        safe_filename = _safe_media_filename(filename) or media_id
        media_dir = self._media_dir(clean_room_id) / media_id
        media_path = media_dir / safe_filename
        media_dir.mkdir(parents=True, exist_ok=True)
        if data:
            media_path.write_bytes(data)
            size = len(data)
        media = {
            "id": media_id,
            "filename": safe_filename,
            "content_type": clean_room_text(content_type, limit=128) or "application/octet-stream",
            "size": max(0, int(size or 0)),
            "path": str(media_path),
            "supported": bool(supported),
        }
        self.append_event(
            clean_room_id,
            "media_attached" if supported else "unsupported_media",
            media=media,
        )
        return media

    def set_room_status(self, room_id: str, status: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_status = _room_status(status)
        with self._lock, self._write_transaction() as connection:
            row = connection.execute("SELECT data_json FROM rooms WHERE room_id = ?", (clean_room_id,)).fetchone()
            room = _row_payload(row)
            if not room:
                raise ValueError(f"Room {clean_room_id} was not found.")
            room = {**room, "status": clean_status, "updated_at": _now()}
            connection.execute(
                """UPDATE rooms SET status = ?, archived = ?, updated_at = ?, data_json = ?
                   WHERE room_id = ?""",
                (
                    clean_status,
                    1 if clean_status == "archived" else 0,
                    str(room["updated_at"]),
                    _json_dumps(room),
                    clean_room_id,
                ),
            )
        if clean_status == "archived":
            self.append_event(clean_room_id, "room_archived")
        elif clean_status == "closed":
            self.append_event(clean_room_id, "room_closed")
        return room

    def export_participant(self, room_id: str, participant_id: str, *, reason: str = "") -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant_id)
        participant = self.set_participant_status(clean_room_id, clean_participant_id, "exported", reason=reason)
        handoff_dir = self._room_dir(clean_room_id) / "handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        packet_path = handoff_dir / f"{clean_participant_id}.md"
        packet = (
            f"# Agent Session Handoff\n\n"
            f"- Room: {clean_room_id}\n"
            f"- Participant: {clean_participant_id}\n"
            f"- Status: exported\n"
            f"- Reason: {clean_room_text(reason, limit=500)}\n"
        )
        packet_path.write_text(packet, encoding="utf-8")
        return {"participant": participant, "handoff_packet_path": str(packet_path)}

    def room_payload(self, room_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        return {
            "room": self.room(clean_room_id),
            "participants": self.participants(clean_room_id),
            "sessions": self.sessions(clean_room_id),
            "events": self.read_events(clean_room_id),
        }

    def _create_room(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        *,
        label: str,
        status: str,
    ) -> tuple[dict[str, object], bool]:
        deleted = connection.execute(
            "SELECT deleted_at FROM deleted_rooms WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        if deleted is not None:
            raise ValueError(f"Room {room_id} was deleted and cannot be recreated implicitly.")
        row = connection.execute("SELECT data_json FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
        existing = _row_payload(row)
        room = build_room_record(room_id, label=label, status=status, existing=existing)
        connection.execute(
            """INSERT INTO rooms(room_id, label, status, archived, updated_at, data_json)
               VALUES(?, ?, ?, ?, ?, ?)
               ON CONFLICT(room_id) DO UPDATE SET
                   label = excluded.label,
                   status = excluded.status,
                   archived = excluded.archived,
                   updated_at = excluded.updated_at,
                   data_json = excluded.data_json""",
            (
                room_id,
                str(room["label"]),
                str(room["status"]),
                1 if room["status"] == "archived" else 0,
                str(room["updated_at"]),
                _json_dumps(room),
            ),
        )
        current_settings_row = connection.execute(
            "SELECT data_json FROM room_settings WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        if current_settings_row is None:
            if existing:
                raise ValueError(f"Room settings for {room_id} are missing.")
            settings = default_room_global_settings(label=str(room["label"]))
        else:
            settings = merge_room_global_settings(
                _row_payload(current_settings_row),
                {"label": str(room["label"])},
            )
        self._write_room_settings(connection, room_id, settings)
        return room, not bool(existing)

    def _room_settings(
        self,
        connection: sqlite3.Connection,
        room_id: str,
    ) -> RoomGlobalSettingsRecord:
        row = connection.execute(
            "SELECT data_json FROM room_settings WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        if row is not None:
            return validate_room_global_settings(_row_payload(row))
        room = connection.execute(
            "SELECT label FROM rooms WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        if room is None:
            raise ValueError(f"Room {room_id} was not found.")
        raise ValueError(f"Room settings for {room_id} are missing.")

    def _update_room_settings(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        updates: dict[str, object],
    ) -> RoomGlobalSettingsRecord:
        current = self._room_settings(connection, room_id)
        settings = merge_room_global_settings(current, updates)
        self._write_room_settings(connection, room_id, settings)
        if settings["label"] != current["label"]:
            room_row = connection.execute(
                "SELECT data_json FROM rooms WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            room = _row_payload(room_row)
            if not room:
                raise ValueError(f"Room {room_id} was not found.")
            room = {**room, "label": settings["label"], "updated_at": _now()}
            connection.execute(
                """UPDATE rooms SET label = ?, updated_at = ?, data_json = ?
                   WHERE room_id = ?""",
                (
                    settings["label"],
                    room["updated_at"],
                    _json_dumps(room),
                    room_id,
                ),
            )
        return settings

    def _write_room_settings(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        settings: RoomGlobalSettingsRecord,
    ) -> None:
        canonical = validate_room_global_settings(settings)
        connection.execute(
            """INSERT INTO room_settings(room_id, updated_at, data_json)
               VALUES(?, ?, ?)
               ON CONFLICT(room_id) DO UPDATE SET
                   updated_at = excluded.updated_at,
                   data_json = excluded.data_json""",
            (room_id, _now(), _json_dumps(canonical)),
        )

    def _upsert_participant(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        participant: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        participant_id = _clean_participant_id(participant.get("participant_id") or participant.get("agent_id"))
        row = connection.execute(
            "SELECT data_json FROM participants WHERE room_id = ? AND participant_id = ?",
            (room_id, participant_id),
        ).fetchone()
        existing = _row_payload(row)
        updated = merge_participant_record(room_id, participant, existing)
        self._write_participant(connection, updated)
        return updated, not bool(existing)

    def _update_participant_fields(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        participant_id: str,
        **updates: object,
    ) -> dict[str, object]:
        clean_participant_id = _clean_participant_id(participant_id)
        row = connection.execute(
            "SELECT data_json FROM participants WHERE room_id = ? AND participant_id = ?",
            (room_id, clean_participant_id),
        ).fetchone()
        participant = _row_payload(row)
        updated = update_participant_record(clean_participant_id, participant, dict(updates))
        self._write_participant(connection, updated)
        return updated

    def _upsert_session(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        session: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        session_id = _clean_session_id(session.get("session_id"))
        row = connection.execute(
            "SELECT data_json FROM agent_sessions WHERE room_id = ? AND session_id = ?",
            (room_id, session_id),
        ).fetchone()
        existing = _row_payload(row)
        updated = merge_session_record(room_id, session, existing)
        self._write_session(connection, updated)
        return updated, not bool(existing)

    def _update_session_fields(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        session_id: str,
        **updates: object,
    ) -> dict[str, object]:
        clean_session_id = _clean_session_id(session_id)
        row = connection.execute(
            "SELECT data_json FROM agent_sessions WHERE room_id = ? AND session_id = ?",
            (room_id, clean_session_id),
        ).fetchone()
        session = _row_payload(row)
        updated = update_session_record(clean_session_id, session, dict(updates))
        self._write_session(connection, updated)
        return updated

    def _record_command_result(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        request_id: str,
        result: dict[str, object],
        *,
        principal_id: str,
        action: str,
        payload_hash: str,
        max_entries: int,
    ) -> dict[str, object]:
        clean_request_id = clean_room_text(request_id, limit=128)
        clean_principal_id = clean_room_text(principal_id, limit=256)
        if not clean_request_id:
            raise ValueError("request_id is required.")
        row = connection.execute(
            """SELECT result_json FROM command_results
               WHERE room_id = ? AND principal_id = ? AND request_id = ?""",
            (room_id, clean_principal_id, clean_request_id),
        ).fetchone()
        if row is not None:
            return _row_payload(row, column="result_json")
        connection.execute(
            """INSERT INTO command_results(
                   room_id, principal_id, request_id, action, payload_hash, created_at, result_json
               ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (
                room_id,
                clean_principal_id,
                clean_request_id,
                clean_room_text(action, limit=64),
                clean_room_text(payload_hash, limit=128),
                _now(),
                _json_dumps(result),
            ),
        )
        keep = max(1, int(max_entries or 500))
        connection.execute(
            """DELETE FROM command_results WHERE rowid IN (
                   SELECT rowid FROM command_results WHERE room_id = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?
               )""",
            (room_id, keep),
        )
        return dict(result)

    def _command_record(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        principal_id: str,
        request_id: str,
    ) -> dict[str, object]:
        clean_principal_id = clean_room_text(principal_id, limit=256)
        clean_request_id = clean_room_text(request_id, limit=128)
        if not clean_request_id:
            return {}
        row = connection.execute(
            """SELECT action, payload_hash, result_json FROM command_results
               WHERE room_id = ? AND principal_id = ? AND request_id = ?""",
            (room_id, clean_principal_id, clean_request_id),
        ).fetchone()
        if row is None:
            return {}
        return {
            "action": str(row["action"] or ""),
            "payload_hash": str(row["payload_hash"] or ""),
            "result": _row_payload(row, column="result_json"),
        }

    def _participant(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        participant_id: str,
    ) -> dict[str, object]:
        clean_participant_id = _clean_participant_id(participant_id)
        row = connection.execute(
            "SELECT data_json FROM participants WHERE room_id = ? AND participant_id = ?",
            (room_id, clean_participant_id),
        ).fetchone()
        return _row_payload(row)

    def _session(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        session_id: str,
    ) -> dict[str, object]:
        clean_session_id = _clean_session_id(session_id)
        row = connection.execute(
            "SELECT data_json FROM agent_sessions WHERE room_id = ? AND session_id = ?",
            (room_id, clean_session_id),
        ).fetchone()
        return _row_payload(row)

    def _append_event(
        self,
        connection: sqlite3.Connection,
        room_id: str,
        event_type: str,
        **payload: object,
    ) -> dict[str, object]:
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM room_events WHERE room_id = ?",
                (room_id,),
            ).fetchone()[0]
        )
        event, visibility, participant_id = build_room_event(room_id, event_type, sequence, dict(payload))
        connection.execute(
            """INSERT INTO room_events(
                   room_id, seq, event_id, event_type, actor_id, turn_id,
                   created_at, visibility, payload_json
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                room_id,
                sequence,
                str(event["id"]),
                str(event["type"]),
                participant_id,
                str(event.get("turn_id") or ""),
                str(event["created_at"]),
                visibility,
                _json_dumps(event),
            ),
        )
        return event

    def _publish_events(self, room_id: str, events: list[dict[str, object]]) -> None:
        if not events:
            return
        with _STORE_REGISTRY_LOCK:
            listeners = list(_EVENT_LISTENERS.get(self._listener_key(room_id), []))
        for event in events:
            for listener in listeners:
                try:
                    listener(dict(event))
                except Exception:
                    _LOGGER.exception(
                        "Room event listener failed after commit",
                        extra={"room_id": room_id, "event_id": str(event.get("id") or "")},
                    )

    def _write_participant(self, connection: sqlite3.Connection, participant: dict[str, object]) -> None:
        connection.execute(
            """INSERT INTO participants(room_id, participant_id, status, role, data_json)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(room_id, participant_id) DO UPDATE SET
                   status = excluded.status,
                   role = excluded.role,
                   data_json = excluded.data_json""",
            (
                str(participant.get("room_id") or ""),
                str(participant.get("participant_id") or ""),
                str(participant.get("status") or "joined"),
                str(participant.get("role") or ""),
                _json_dumps(participant),
            ),
        )

    def _write_session(self, connection: sqlite3.Connection, session: dict[str, object]) -> None:
        connection.execute(
            """INSERT INTO agent_sessions(
                   room_id, session_id, participant_id, status, runtime_status, data_json
               ) VALUES(?, ?, ?, ?, ?, ?)
               ON CONFLICT(room_id, session_id) DO UPDATE SET
                   participant_id = excluded.participant_id,
                   status = excluded.status,
                   runtime_status = excluded.runtime_status,
                   data_json = excluded.data_json""",
            (
                str(session.get("room_id") or ""),
                str(session.get("session_id") or ""),
                str(session.get("participant_id") or ""),
                str(session.get("status") or "attached"),
                str(session.get("runtime_status") or ""),
                _json_dumps(session),
            ),
        )

    def _listener_key(self, room_id: str) -> str:
        return f"{self.database_path.expanduser().resolve()}::{room_id}"

    def _room_dir(self, room_id: str) -> Path:
        return self.rooms_root / room_id

    def _media_dir(self, room_id: str) -> Path:
        return self._room_dir(room_id) / "media"

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = open_room_database(self.database_path)
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def _row_payload(row: sqlite3.Row | None, *, column: str = "data_json") -> dict[str, object]:
    if row is None:
        return {}
    try:
        payload = json.loads(str(row[column]))
    except (json.JSONDecodeError, ValueError, IndexError, KeyError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _database_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_dev, stat.st_ino
