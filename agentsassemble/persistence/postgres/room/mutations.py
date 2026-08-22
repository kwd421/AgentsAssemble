from __future__ import annotations

from psycopg import Connection
from psycopg.types.json import Jsonb

from agentsassemble.room.text import clean_room_text
from agentsassemble.persistence.postgres.room.rows import payload_from_row
from agentsassemble.room.global_settings import (
    RoomGlobalSettingsRecord,
    default_room_global_settings,
    merge_room_global_settings,
    validate_room_global_settings,
)
from agentsassemble.room.repository_records import (
    build_room_event,
    build_room_record,
    clean_participant_id,
    clean_session_id,
    merge_participant_record,
    merge_session_record,
    update_participant_record,
    update_session_record,
    utc_now,
)
from agentsassemble.room.event_updates import apply_event_updates
from agentsassemble.room.visibility import VISIBLE


def create_room(
    connection: Connection,
    room_id: str,
    *,
    label: str,
    status: str,
    room_uid: str = "",
) -> tuple[dict[str, object], bool]:
    deleted = connection.execute(
        "SELECT deleted_at FROM deleted_rooms WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    if deleted is not None:
        raise ValueError(f"Room {room_id} was deleted and cannot be recreated implicitly.")
    existing = payload_from_row(
        connection.execute(
            "SELECT data_json FROM rooms WHERE room_id = %s",
            (room_id,),
        ).fetchone()
    )
    room = build_room_record(
        room_id,
        label=label,
        status=status,
        existing=existing,
        room_uid=room_uid,
    )
    connection.execute(
        """INSERT INTO rooms(room_id, room_uid, label, status, archived, updated_at, data_json)
           VALUES(%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT(room_id) DO UPDATE SET
               label = excluded.label,
               status = excluded.status,
               archived = excluded.archived,
               updated_at = excluded.updated_at,
               data_json = excluded.data_json""",
        (
            room_id,
            room["room_uid"],
            room["label"],
            room["status"],
            room["status"] == "archived",
            room["updated_at"],
            Jsonb(room),
        ),
    )
    settings_row = connection.execute(
        "SELECT data_json FROM room_settings WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    if settings_row is None:
        if existing:
            raise ValueError(f"Room settings for {room_id} are missing.")
        settings = default_room_global_settings(label=str(room["label"]))
    else:
        settings = merge_room_global_settings(
            payload_from_row(settings_row),
            {"label": str(room["label"])},
        )
    _write_room_settings(connection, room_id, settings)
    return room, not bool(existing)


def update_room_settings(
    connection: Connection,
    room_id: str,
    updates: dict[str, object],
) -> RoomGlobalSettingsRecord:
    settings_row = connection.execute(
        "SELECT data_json FROM room_settings WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    room_row = connection.execute(
        "SELECT data_json FROM rooms WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    room = payload_from_row(room_row)
    if not room:
        raise ValueError(f"Room {room_id} was not found.")
    if settings_row is None:
        raise ValueError(f"Room settings for {room_id} are missing.")
    current = validate_room_global_settings(payload_from_row(settings_row))
    settings = merge_room_global_settings(current, updates)
    _write_room_settings(connection, room_id, settings)
    if settings["label"] != current["label"]:
        room = {**room, "label": settings["label"], "updated_at": utc_now()}
        connection.execute(
            """UPDATE rooms SET label = %s, updated_at = %s, data_json = %s
               WHERE room_id = %s""",
            (settings["label"], room["updated_at"], Jsonb(room), room_id),
        )
    return settings


def _write_room_settings(
    connection: Connection,
    room_id: str,
    settings: RoomGlobalSettingsRecord,
) -> None:
    canonical = validate_room_global_settings(settings)
    connection.execute(
        """INSERT INTO room_settings(room_id, updated_at, data_json)
           VALUES(%s, %s, %s)
           ON CONFLICT(room_id) DO UPDATE SET
               updated_at = excluded.updated_at,
               data_json = excluded.data_json""",
        (room_id, utc_now(), Jsonb(canonical)),
    )


def upsert_participant(
    connection: Connection,
    room_id: str,
    participant: dict[str, object],
) -> tuple[dict[str, object], bool]:
    participant_id = clean_participant_id(participant.get("participant_id") or participant.get("agent_id"))
    existing = payload_from_row(
        connection.execute(
            """SELECT data_json FROM participants
               WHERE room_id = %s AND participant_id = %s""",
            (room_id, participant_id),
        ).fetchone()
    )
    updated = merge_participant_record(room_id, participant, existing)
    write_participant(connection, updated)
    return updated, not bool(existing)


def update_participant(
    connection: Connection,
    room_id: str,
    participant_id: str,
    updates: dict[str, object],
) -> dict[str, object]:
    clean_id = clean_participant_id(participant_id)
    existing = payload_from_row(
        connection.execute(
            """SELECT data_json FROM participants
               WHERE room_id = %s AND participant_id = %s""",
            (room_id, clean_id),
        ).fetchone()
    )
    updated = update_participant_record(clean_id, existing, updates)
    write_participant(connection, updated)
    return updated


def upsert_session(
    connection: Connection,
    room_id: str,
    session: dict[str, object],
) -> tuple[dict[str, object], bool]:
    session_id = clean_session_id(session.get("session_id"))
    existing = payload_from_row(
        connection.execute(
            """SELECT data_json FROM agent_sessions
               WHERE room_id = %s AND session_id = %s""",
            (room_id, session_id),
        ).fetchone()
    )
    updated = merge_session_record(room_id, session, existing)
    write_session(connection, updated)
    return updated, not bool(existing)


def update_session(
    connection: Connection,
    room_id: str,
    session_id: str,
    updates: dict[str, object],
) -> dict[str, object]:
    clean_id = clean_session_id(session_id)
    existing = payload_from_row(
        connection.execute(
            """SELECT data_json FROM agent_sessions
               WHERE room_id = %s AND session_id = %s""",
            (room_id, clean_id),
        ).fetchone()
    )
    updated = update_session_record(clean_id, existing, updates)
    write_session(connection, updated)
    return updated


def record_command_result(
    connection: Connection,
    room_id: str,
    request_id: str,
    result: dict[str, object],
    *,
    principal_id: str,
    action: str,
    payload_hash: str,
    max_entries: int,
) -> dict[str, object]:
    clean_request = clean_room_text(request_id, limit=128)
    clean_principal = clean_room_text(principal_id, limit=256)
    if not clean_request:
        raise ValueError("request_id is required.")
    existing = connection.execute(
        """SELECT result_json FROM command_results
           WHERE room_id = %s AND principal_id = %s AND request_id = %s""",
        (room_id, clean_principal, clean_request),
    ).fetchone()
    if existing is not None:
        return payload_from_row(existing, column="result_json")
    connection.execute(
        """INSERT INTO command_results(
               room_id, principal_id, request_id, action, payload_hash, created_at, result_json
           ) VALUES(%s, %s, %s, %s, %s, %s, %s)""",
        (
            room_id,
            clean_principal,
            clean_request,
            clean_room_text(action, limit=64),
            clean_room_text(payload_hash, limit=128),
            utc_now(),
            Jsonb(result),
        ),
    )
    connection.execute(
        """DELETE FROM command_results WHERE ctid IN (
               SELECT ctid FROM command_results WHERE room_id = %s
               ORDER BY created_at DESC, ctid DESC OFFSET %s
           )""",
        (room_id, max(1, int(max_entries or 500))),
    )
    return dict(result)


def append_event(
    connection: Connection,
    room_id: str,
    event_type: str,
    payload: dict[str, object],
) -> dict[str, object]:
    row = connection.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM room_events WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    sequence = int(row["seq"] if row else 1)
    event, visibility, participant_id = build_room_event(room_id, event_type, sequence, payload)
    connection.execute(
        """INSERT INTO room_events(
               room_id, seq, event_id, event_type, actor_id, turn_id,
               created_at, visibility, payload_json
           ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            room_id,
            sequence,
            event["id"],
            event["type"],
            participant_id,
            event.get("turn_id") or "",
            event["created_at"],
            visibility,
            Jsonb(event),
        ),
    )
    return event


def update_event_fields(
    connection: Connection,
    room_id: str,
    event_id: str,
    updates: dict[str, object],
) -> dict[str, object]:
    row = connection.execute(
        "SELECT payload_json FROM room_events "
        "WHERE room_id = %s AND event_id = %s AND visibility = %s",
        (room_id, event_id, VISIBLE),
    ).fetchone()
    event = payload_from_row(row, column="payload_json")
    if not event:
        raise ValueError(f"Room event was not found: {event_id}")
    updated = apply_event_updates(event, updates)
    connection.execute(
        "UPDATE room_events SET payload_json = %s WHERE room_id = %s AND event_id = %s",
        (Jsonb(updated), room_id, event_id),
    )
    return updated


def update_room_status(
    connection: Connection,
    room_id: str,
    status: str,
) -> dict[str, object]:
    room = payload_from_row(
        connection.execute(
            "SELECT data_json FROM rooms WHERE room_id = %s",
            (room_id,),
        ).fetchone()
    )
    if not room:
        raise ValueError(f"Room {room_id} was not found.")
    updated = {**room, "status": status, "updated_at": utc_now()}
    connection.execute(
        """UPDATE rooms SET status = %s, archived = %s, updated_at = %s, data_json = %s
           WHERE room_id = %s""",
        (status, status == "archived", updated["updated_at"], Jsonb(updated), room_id),
    )
    return updated


def detach_participant_sessions(
    connection: Connection,
    room_id: str,
    participant_id: str,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """SELECT data_json FROM agent_sessions
           WHERE room_id = %s AND participant_id = %s
           ORDER BY COALESCE(data_json->>'created_at', ''), session_id""",
        (room_id, participant_id),
    ).fetchall()
    detached: list[dict[str, object]] = []
    for row in rows:
        session = payload_from_row(row)
        updated = {**session, "status": "detached", "updated_at": utc_now()}
        write_session(connection, updated)
        detached.append(updated)
    return detached


def write_participant(connection: Connection, participant: dict[str, object]) -> None:
    connection.execute(
        """INSERT INTO participants(room_id, participant_id, status, role, data_json)
           VALUES(%s, %s, %s, %s, %s)
           ON CONFLICT(room_id, participant_id) DO UPDATE SET
               status = excluded.status,
               role = excluded.role,
               data_json = excluded.data_json""",
        (
            participant.get("room_id") or "",
            participant.get("participant_id") or "",
            participant.get("status") or "joined",
            participant.get("role") or "",
            Jsonb(participant),
        ),
    )


def write_session(connection: Connection, session: dict[str, object]) -> None:
    connection.execute(
        """INSERT INTO agent_sessions(
               room_id, session_id, participant_id, status, runtime_status, data_json
           ) VALUES(%s, %s, %s, %s, %s, %s)
           ON CONFLICT(room_id, session_id) DO UPDATE SET
               participant_id = excluded.participant_id,
               status = excluded.status,
               runtime_status = excluded.runtime_status,
               data_json = excluded.data_json""",
        (
            session.get("room_id") or "",
            session.get("session_id") or "",
            session.get("participant_id") or "",
            session.get("status") or "attached",
            session.get("runtime_status") or "",
            Jsonb(session),
        ),
    )
