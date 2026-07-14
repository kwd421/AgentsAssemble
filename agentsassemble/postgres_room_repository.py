from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.postgres_attention_repository import (
    cancel_attention_job,
    checkpoint_observed_seq,
    claim_attention_job,
    read_attention_job,
    read_attention_lease,
    read_attention_leases,
    read_attention_jobs,
    read_attention_state,
    record_attention_evaluation,
    resolve_attention_lease,
    write_attention_state,
)
from agentsassemble.postgres_room_schema import upgrade_postgres_room_schema
from agentsassemble.postgres_room_mutations import (
    append_event as append_room_event,
    create_room as create_room_record,
    detach_participant_sessions as detach_sessions,
    record_command_result as persist_command_result,
    update_participant,
    update_room_status,
    update_session,
    upsert_participant,
    upsert_session,
)
from agentsassemble.postgres_room_queries import (
    count_events,
    read_active_participants,
    read_command_record,
    read_event_by_id,
    read_event_sequence,
    read_events as query_events,
    read_latest_event_sequence,
    read_oldest_event_sequence,
    read_participant,
    read_participants,
    read_room,
    read_rooms,
    read_session,
    read_sessions,
    room_is_deleted as query_room_is_deleted,
)
from agentsassemble.room_attention import AgentAttentionState, AttentionEvaluation
from agentsassemble.room_repository import RoomTransaction
from agentsassemble.room_repository_records import (
    clean_participant_id,
    clean_room_id,
    clean_session_id,
    participant_status,
    room_status,
    safe_media_filename,
    utc_now,
)


_LOGGER = logging.getLogger(__name__)


class _PostgresRoomTransaction:
    def __init__(
        self,
        connection: Connection,
        room_id: str,
        pending_events: list[dict[str, object]],
    ) -> None:
        self._connection = connection
        self._room_id = room_id
        self._pending_events = pending_events

    @property
    def room_id(self) -> str:
        return self._room_id

    def participant(self, participant_id: str) -> dict[str, object]:
        return read_participant(self._connection, self._room_id, clean_participant_id(participant_id))

    def session(self, session_id: str) -> dict[str, object]:
        return read_session(self._connection, self._room_id, clean_session_id(session_id))

    def command_record(self, principal_id: str, request_id: str) -> dict[str, object]:
        return read_command_record(
            self._connection,
            self._room_id,
            clean_lobby_text(principal_id, limit=256),
            clean_lobby_text(request_id, limit=128),
        )

    def create_room(self, *, label: str = "", status: str = "active") -> tuple[dict[str, object], bool]:
        return create_room_record(self._connection, self._room_id, label=label, status=status)

    def upsert_participant(self, participant: dict[str, object]) -> tuple[dict[str, object], bool]:
        return upsert_participant(self._connection, self._room_id, participant)

    def update_participant_fields(self, participant_id: str, **updates: object) -> dict[str, object]:
        return update_participant(self._connection, self._room_id, participant_id, dict(updates))

    def upsert_session(self, session: dict[str, object]) -> tuple[dict[str, object], bool]:
        return upsert_session(self._connection, self._room_id, session)

    def update_session_fields(self, session_id: str, **updates: object) -> dict[str, object]:
        return update_session(self._connection, self._room_id, session_id, dict(updates))

    def append_event(self, event_type: str, **payload: object) -> dict[str, object]:
        event = append_room_event(self._connection, self._room_id, event_type, dict(payload))
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
        return persist_command_result(
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
        clean_id = clean_participant_id(participant_id)
        current = read_attention_state(self._connection, self._room_id, clean_id)
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
            clean_participant_id(participant_id),
            observed_seq,
        )

    def attention_state(self, participant_id: str) -> AgentAttentionState:
        return read_attention_state(
            self._connection,
            self._room_id,
            clean_participant_id(participant_id),
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


class PostgresRoomRepository:
    """Canonical room repository backed by PostgreSQL and explicit psycopg SQL."""

    def __init__(
        self,
        dsn: str,
        *,
        output_root: Path | None = None,
        migrate: bool = False,
    ) -> None:
        clean_dsn = str(dsn or "").strip()
        if not clean_dsn:
            raise ValueError("PostgreSQL room repository requires a database DSN.")
        self._dsn = clean_dsn
        self.output_root = Path(output_root) if output_root is not None else None
        self._listener_lock = threading.RLock()
        self._listeners: dict[str, list[Callable[[dict[str, object]], None]]] = {}
        if migrate:
            upgrade_postgres_room_schema(clean_dsn)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(configured=True)"

    def create_room(self, room_id: str, *, label: str = "", status: str = "active") -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        with self.transaction(clean_id) as transaction:
            room, created = transaction.create_room(label=label, status=status)
            if created:
                transaction.append_event("room_created", label=room["label"])
        return room

    @contextmanager
    def transaction(self, room_id: str) -> Iterator[RoomTransaction]:
        clean_id = clean_room_id(room_id)
        pending_events: list[dict[str, object]] = []
        with self._connection() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (clean_id,),
                )
                yield _PostgresRoomTransaction(connection, clean_id, pending_events)
        self._publish_events(clean_id, pending_events)

    def room_is_deleted(self, room_id: str) -> bool:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return query_room_is_deleted(connection, clean_id)

    def deleted_room_record(self, room_id: str) -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT room_id, deleted_at, reason, principal_id, request_id,
                          action, payload_hash, cleanup_status, room_name, result_json
                   FROM deleted_rooms WHERE room_id = %s""",
                (clean_id,),
            ).fetchone()
        if row is None:
            return {}
        return {
            "room_id": str(row["room_id"] or ""),
            "deleted_at": (
                row["deleted_at"].isoformat()
                if hasattr(row["deleted_at"], "isoformat")
                else str(row["deleted_at"] or "")
            ),
            "reason": str(row["reason"] or ""),
            "principal_id": str(row["principal_id"] or ""),
            "request_id": str(row["request_id"] or ""),
            "action": str(row["action"] or ""),
            "payload_hash": str(row["payload_hash"] or ""),
            "cleanup_status": str(row["cleanup_status"] or ""),
            "room_name": str(row["room_name"] or ""),
            "result": dict(row["result_json"]) if isinstance(row["result_json"], dict) else {},
        }

    def delete_room(
        self,
        room_id: str,
        *,
        reason: str = "",
        tombstone: dict[str, object] | None = None,
        cleanup_status: str = "complete",
        room_name: str = "",
    ) -> bool:
        clean_id = clean_room_id(room_id)
        command = dict(tombstone or {})
        with self._connection() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (clean_id,),
                )
                existed = connection.execute(
                    "SELECT 1 FROM rooms WHERE room_id = %s",
                    (clean_id,),
                ).fetchone() is not None
                if not existed and connection.execute(
                    "SELECT 1 FROM deleted_rooms WHERE room_id = %s",
                    (clean_id,),
                ).fetchone() is not None:
                    return False
                connection.execute("DELETE FROM rooms WHERE room_id = %s", (clean_id,))
                connection.execute(
                    """INSERT INTO deleted_rooms(
                           room_id, deleted_at, reason, principal_id, request_id, action,
                           payload_hash, cleanup_status, room_name, result_json
                       ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        clean_id,
                        utc_now(),
                        clean_lobby_text(reason, limit=500),
                        clean_lobby_text(command.get("principal_id"), limit=256),
                        clean_lobby_text(command.get("request_id"), limit=128),
                        clean_lobby_text(command.get("action"), limit=64),
                        clean_lobby_text(command.get("payload_hash"), limit=128),
                        clean_lobby_text(cleanup_status, limit=32) or "complete",
                        clean_lobby_text(room_name, limit=128),
                        Jsonb(command.get("result") if isinstance(command.get("result"), dict) else {}),
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
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            with connection.transaction():
                updated = connection.execute(
                    """UPDATE deleted_rooms
                       SET result_json = %s, cleanup_status = %s
                       WHERE room_id = %s""",
                    (
                        Jsonb(result),
                        clean_lobby_text(cleanup_status, limit=32) or "complete",
                        clean_id,
                    ),
                ).rowcount
                if not updated:
                    raise ValueError(f"Deleted room tombstone {clean_id} was not found.")
        return self.deleted_room_record(clean_id)

    def room(self, room_id: str) -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return read_room(connection, clean_id)

    def list_rooms(self, *, include_archived: bool = False) -> list[dict[str, object]]:
        with self._connection() as connection:
            return read_rooms(connection, include_archived=include_archived)

    def participants(self, room_id: str) -> list[dict[str, object]]:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return read_participants(connection, clean_id)

    def participant(self, room_id: str, participant_id: str) -> dict[str, object]:
        clean_room = clean_room_id(room_id)
        clean_participant = clean_participant_id(participant_id)
        with self._connection() as connection:
            return read_participant(connection, clean_room, clean_participant)

    def active_participants(self, room_id: str) -> list[dict[str, object]]:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return read_active_participants(connection, clean_id)

    def upsert_participant(
        self,
        room_id: str,
        participant: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        clean_id = clean_room_id(room_id)
        with self.transaction(clean_id) as transaction:
            return transaction.upsert_participant(participant)

    def update_participant_fields(
        self,
        room_id: str,
        participant_id: str,
        **updates: object,
    ) -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        with self.transaction(clean_id) as transaction:
            return transaction.update_participant_fields(participant_id, **updates)

    def set_participant_status(
        self,
        room_id: str,
        participant_id: str,
        status: str,
        *,
        reason: str = "",
    ) -> dict[str, object]:
        clean_room = clean_room_id(room_id)
        clean_participant = clean_participant_id(participant_id)
        clean_status = participant_status(status)
        with self.transaction(clean_room) as transaction:
            concrete = _require_postgres_transaction(transaction)
            updated = transaction.update_participant_fields(clean_participant, status=clean_status)
            event_type = {
                "left": "participant_left",
                "kicked": "participant_kicked",
                "exported": "participant_exported",
                "detached": "session_detached",
            }.get(clean_status)
            if event_type:
                transaction.append_event(
                    event_type,
                    participant_id=clean_participant,
                    reason=clean_lobby_text(reason, limit=500),
                )
            if clean_status in {"left", "kicked", "exported", "detached"}:
                detach_sessions(
                    concrete._connection,
                    clean_room,
                    clean_participant,
                )
        return updated

    def sessions(self, room_id: str) -> list[dict[str, object]]:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return read_sessions(connection, clean_id)

    def session(self, room_id: str, session_id: str) -> dict[str, object]:
        clean_room = clean_room_id(room_id)
        clean_session = clean_session_id(session_id)
        with self._connection() as connection:
            return read_session(connection, clean_room, clean_session)

    def upsert_session(
        self,
        room_id: str,
        session: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        clean_id = clean_room_id(room_id)
        with self.transaction(clean_id) as transaction:
            return transaction.upsert_session(session)

    def update_session_fields(
        self,
        room_id: str,
        session_id: str,
        **updates: object,
    ) -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        with self.transaction(clean_id) as transaction:
            return transaction.update_session_fields(session_id, **updates)

    def detach_participant_sessions(self, room_id: str, participant_id: str) -> list[dict[str, object]]:
        clean_room = clean_room_id(room_id)
        clean_participant = clean_participant_id(participant_id)
        with self._connection() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (clean_room,),
                )
                return detach_sessions(connection, clean_room, clean_participant)

    def command_record(self, room_id: str, principal_id: str, request_id: str) -> dict[str, object]:
        clean_room = clean_room_id(room_id)
        clean_principal = clean_lobby_text(principal_id, limit=256)
        clean_request = clean_lobby_text(request_id, limit=128)
        if not clean_request:
            return {}
        with self._connection() as connection:
            return read_command_record(connection, clean_room, clean_principal, clean_request)

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
        clean_id = clean_room_id(room_id)
        with self.transaction(clean_id) as transaction:
            return transaction.record_command_result(
                request_id,
                result,
                principal_id=principal_id,
                action=action,
                payload_hash=payload_hash,
                max_entries=max_entries,
            )

    def append_event(self, room_id: str, event_type: str, **payload: object) -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        with self.transaction(clean_id) as transaction:
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
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return query_events(
                connection,
                clean_id,
                after=after,
                after_seq=after_seq,
                before_seq=before_seq,
                limit=limit,
                newest=newest,
                include_hidden=include_hidden,
                event_types=event_types,
                exclude_actor_id=exclude_actor_id,
            )

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
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return count_events(
                connection,
                clean_id,
                include_hidden=include_hidden,
                after_seq=after_seq,
                before_seq=before_seq,
                event_types=event_types,
                exclude_actor_id=exclude_actor_id,
            )

    def event_by_id(self, room_id: str, event_id: str, *, include_hidden: bool = False) -> dict[str, object]:
        clean_room = clean_room_id(room_id)
        clean_event = clean_lobby_text(event_id, limit=128)
        if not clean_event:
            return {}
        with self._connection() as connection:
            return read_event_by_id(
                connection,
                clean_room,
                clean_event,
                include_hidden=include_hidden,
            )

    def event_sequence(self, room_id: str, event_id: str) -> int:
        clean_room = clean_room_id(room_id)
        clean_event = clean_lobby_text(event_id, limit=128)
        if not clean_event:
            return 0
        with self._connection() as connection:
            return read_event_sequence(connection, clean_room, clean_event)

    def latest_event_sequence(self, room_id: str) -> int:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return read_latest_event_sequence(connection, clean_id)

    def oldest_event_sequence(self, room_id: str, *, include_hidden: bool = False) -> int:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return read_oldest_event_sequence(
                connection,
                clean_id,
                include_hidden=include_hidden,
            )

    def add_event_listener(
        self,
        room_id: str,
        listener: Callable[[dict[str, object]], None],
    ) -> Callable[[], None]:
        clean_id = clean_room_id(room_id)
        with self._listener_lock:
            self._listeners.setdefault(clean_id, []).append(listener)

        def remove() -> None:
            with self._listener_lock:
                listeners = self._listeners.get(clean_id, [])
                if listener in listeners:
                    listeners.remove(listener)
                if not listeners:
                    self._listeners.pop(clean_id, None)

        return remove

    def set_room_status(self, room_id: str, status: str) -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        clean_status = room_status(status)
        with self.transaction(clean_id) as transaction:
            concrete = _require_postgres_transaction(transaction)
            room = update_room_status(concrete._connection, clean_id, clean_status)
            if clean_status == "archived":
                transaction.append_event("room_archived")
            elif clean_status == "closed":
                transaction.append_event("room_closed")
        return room

    def attention_state(self, room_id: str, participant_id: str) -> AgentAttentionState:
        clean_room = clean_room_id(room_id)
        clean_participant = clean_participant_id(participant_id)
        with self._connection() as connection:
            return read_attention_state(connection, clean_room, clean_participant)

    def attention_jobs(
        self,
        room_id: str,
        *,
        mode: str = "",
        status: str = "",
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        clean_id = clean_room_id(room_id)
        with self._connection() as connection:
            return read_attention_jobs(
                connection,
                clean_id,
                mode=mode,
                status=status,
                after_seq=after_seq,
                limit=limit,
            )

    def attention_job(self, room_id: str, job_id: str) -> dict[str, object]:
        clean_room = clean_room_id(room_id)
        clean_job = clean_lobby_text(job_id, limit=128)
        with self._connection() as connection:
            return read_attention_job(connection, clean_room, clean_job)

    def attention_leases(
        self,
        room_id: str,
        *,
        status: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        clean_room = clean_room_id(room_id)
        with self._connection() as connection:
            return read_attention_leases(
                connection,
                clean_room,
                status=status,
                limit=limit,
            )

    def attention_lease(self, room_id: str, lease_id: str) -> dict[str, object]:
        clean_room = clean_room_id(room_id)
        clean_lease = clean_lobby_text(lease_id, limit=128)
        with self._connection() as connection:
            return read_attention_lease(connection, clean_room, clean_lease)

    def room_payload(self, room_id: str) -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        return {
            "room": self.room(clean_id),
            "participants": self.participants(clean_id),
            "sessions": self.sessions(clean_id),
            "events": self.read_events(clean_id),
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
        root = self._require_output_root()
        clean_id = clean_room_id(room_id)
        media_id = uuid4().hex[:12]
        clean_filename = safe_media_filename(filename) or media_id
        media_dir = root / "rooms" / clean_id / "media" / media_id
        media_path = media_dir / clean_filename
        media_dir.mkdir(parents=True, exist_ok=True)
        if data:
            media_path.write_bytes(data)
            size = len(data)
        media = {
            "id": media_id,
            "filename": clean_filename,
            "content_type": clean_lobby_text(content_type, limit=128) or "application/octet-stream",
            "size": max(0, int(size or 0)),
            "path": str(media_path),
            "supported": bool(supported),
        }
        self.append_event(
            clean_id,
            "media_attached" if supported else "unsupported_media",
            media=media,
        )
        return media

    def export_participant(self, room_id: str, participant_id: str, *, reason: str = "") -> dict[str, object]:
        root = self._require_output_root()
        clean_room = clean_room_id(room_id)
        clean_participant = clean_participant_id(participant_id)
        participant = self.set_participant_status(
            clean_room,
            clean_participant,
            "exported",
            reason=reason,
        )
        handoff_dir = root / "rooms" / clean_room / "handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        packet_path = handoff_dir / f"{clean_participant}.md"
        packet_path.write_text(
            "# Agent Session Handoff\n\n"
            f"- Room: {clean_room}\n"
            f"- Participant: {clean_participant}\n"
            "- Status: exported\n"
            f"- Reason: {clean_lobby_text(reason, limit=500)}\n",
            encoding="utf-8",
        )
        return {"participant": participant, "handoff_packet_path": str(packet_path)}

    def canonicalize_events(self, room_id: str) -> dict[str, object]:
        clean_id = clean_room_id(room_id)
        return {
            "room_id": clean_id,
            "migrated": False,
            "event_count": self.event_count(clean_id, include_hidden=True),
            "hidden_event_count": self.event_count(
                clean_id,
                include_hidden=True,
            ) - self.event_count(clean_id),
            "backup_path": "",
            "database_path": "",
            "backend": "postgresql",
        }

    def _publish_events(self, room_id: str, events: list[dict[str, object]]) -> None:
        if not events:
            return
        with self._listener_lock:
            listeners = list(self._listeners.get(room_id, []))
        for event in events:
            for listener in listeners:
                try:
                    listener(dict(event))
                except Exception:
                    _LOGGER.exception(
                        "Room event listener failed after PostgreSQL commit",
                        extra={"room_id": room_id, "event_id": str(event.get("id") or "")},
                    )

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        connection = psycopg.connect(self._dsn, row_factory=dict_row)
        try:
            yield connection
        finally:
            connection.close()

    def _require_output_root(self) -> Path:
        if self.output_root is None:
            raise RuntimeError("This PostgreSQL room repository has no configured media output root.")
        return self.output_root


def _require_postgres_transaction(transaction: RoomTransaction) -> _PostgresRoomTransaction:
    if not isinstance(transaction, _PostgresRoomTransaction):
        raise TypeError("PostgreSQL repository received an incompatible transaction.")
    return transaction
