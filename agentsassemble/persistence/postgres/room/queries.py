from __future__ import annotations

from collections.abc import Iterable

from psycopg import Connection

from agentsassemble.room.text import clean_room_text
from agentsassemble.persistence.postgres.room.rows import payload_from_row
from agentsassemble.room.global_settings import (
    RoomGlobalSettingsRecord,
    validate_room_global_settings,
)
from agentsassemble.room.repository_records import ACTIVE_PARTICIPANT_STATUSES
from agentsassemble.room.visibility import VISIBLE


def room_is_deleted(connection: Connection, room_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM deleted_rooms WHERE room_id = %s",
        (room_id,),
    ).fetchone() is not None


def read_room(connection: Connection, room_id: str) -> dict[str, object]:
    row = connection.execute(
        "SELECT data_json FROM rooms WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    return payload_from_row(row)


def read_room_settings(connection: Connection, room_id: str) -> RoomGlobalSettingsRecord:
    row = connection.execute(
        "SELECT data_json FROM room_settings WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    if row is not None:
        return validate_room_global_settings(payload_from_row(row))
    room = connection.execute(
        "SELECT label FROM rooms WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    if room is None:
        raise ValueError(f"Room {room_id} was not found.")
    raise ValueError(f"Room settings for {room_id} are missing.")


def read_rooms(connection: Connection, *, include_archived: bool) -> list[dict[str, object]]:
    query = "SELECT data_json FROM rooms"
    if not include_archived:
        query += " WHERE archived = FALSE"
    query += " ORDER BY updated_at DESC, room_id ASC"
    return [payload_from_row(row) for row in connection.execute(query).fetchall()]


def read_participants(connection: Connection, room_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """SELECT data_json FROM participants WHERE room_id = %s
           ORDER BY COALESCE(data_json->>'created_at', ''), participant_id""",
        (room_id,),
    ).fetchall()
    return [payload_from_row(row) for row in rows]


def read_participant(
    connection: Connection,
    room_id: str,
    participant_id: str,
) -> dict[str, object]:
    row = connection.execute(
        """SELECT data_json FROM participants
           WHERE room_id = %s AND participant_id = %s""",
        (room_id, participant_id),
    ).fetchone()
    return payload_from_row(row)


def read_active_participants(connection: Connection, room_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """SELECT data_json FROM participants
           WHERE room_id = %s AND status = ANY(%s)
           ORDER BY COALESCE(data_json->>'created_at', ''), participant_id""",
        (room_id, list(sorted(ACTIVE_PARTICIPANT_STATUSES))),
    ).fetchall()
    return [payload_from_row(row) for row in rows]


def read_sessions(connection: Connection, room_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """SELECT data_json FROM agent_sessions WHERE room_id = %s
           ORDER BY COALESCE(data_json->>'created_at', ''), session_id""",
        (room_id,),
    ).fetchall()
    return [payload_from_row(row) for row in rows]


def read_session(
    connection: Connection,
    room_id: str,
    session_id: str,
) -> dict[str, object]:
    row = connection.execute(
        """SELECT data_json FROM agent_sessions
           WHERE room_id = %s AND session_id = %s""",
        (room_id, session_id),
    ).fetchone()
    return payload_from_row(row)


def read_command_record(
    connection: Connection,
    room_id: str,
    principal_id: str,
    request_id: str,
) -> dict[str, object]:
    row = connection.execute(
        """SELECT action, payload_hash, result_json FROM command_results
           WHERE room_id = %s AND principal_id = %s AND request_id = %s""",
        (room_id, principal_id, request_id),
    ).fetchone()
    if row is None:
        return {}
    return {
        "action": str(row.get("action") or ""),
        "payload_hash": str(row.get("payload_hash") or ""),
        "result": payload_from_row(row, column="result_json"),
    }


def read_events(
    connection: Connection,
    room_id: str,
    *,
    after: str,
    after_seq: int,
    before_seq: int,
    limit: int | None,
    newest: bool,
    include_hidden: bool,
    event_types: Iterable[str] | None,
    exclude_actor_id: str,
) -> list[dict[str, object]]:
    clauses, parameters = _event_filter(
        room_id,
        include_hidden=include_hidden,
        after_seq=after_seq,
        before_seq=before_seq,
        event_types=event_types,
        exclude_actor_id=exclude_actor_id,
    )
    if not after_seq and after:
        row = connection.execute(
            "SELECT seq FROM room_events WHERE room_id = %s AND event_id = %s",
            (room_id, str(after)),
        ).fetchone()
        if row is not None:
            clauses.append("seq > %s")
            parameters.append(int(row["seq"]))
    order = "DESC" if newest else "ASC"
    query = f"SELECT payload_json FROM room_events WHERE {' AND '.join(clauses)} ORDER BY seq {order}"
    if limit is not None:
        query += " LIMIT %s"
        parameters.append(max(1, int(limit)))
    events = [
        payload_from_row(row, column="payload_json")
        for row in connection.execute(query, tuple(parameters)).fetchall()
    ]
    if newest:
        events.reverse()
    return events


def count_events(
    connection: Connection,
    room_id: str,
    *,
    include_hidden: bool,
    after_seq: int,
    before_seq: int,
    event_types: Iterable[str] | None,
    exclude_actor_id: str,
) -> int:
    clauses, parameters = _event_filter(
        room_id,
        include_hidden=include_hidden,
        after_seq=after_seq,
        before_seq=before_seq,
        event_types=event_types,
        exclude_actor_id=exclude_actor_id,
    )
    row = connection.execute(
        f"SELECT COUNT(*) AS count FROM room_events WHERE {' AND '.join(clauses)}",
        tuple(parameters),
    ).fetchone()
    return int(row["count"] if row else 0)


def read_event_by_id(
    connection: Connection,
    room_id: str,
    event_id: str,
    *,
    include_hidden: bool,
) -> dict[str, object]:
    query = "SELECT payload_json FROM room_events WHERE room_id = %s AND event_id = %s"
    parameters: tuple[object, ...] = (room_id, event_id)
    if not include_hidden:
        query += " AND visibility = %s"
        parameters = (room_id, event_id, VISIBLE)
    row = connection.execute(query, parameters).fetchone()
    return payload_from_row(row, column="payload_json")


_VOTE_BALLOT_EVENTS_QUERY = """SELECT payload_json FROM room_events
                               WHERE room_id = %s
                                 AND visibility = %s
                                 AND event_type = 'message_final'
                                 AND payload_json->>'message_kind' = 'vote_cast'
                                 AND payload_json->>'vote_id' = %s
                                 AND seq > %s
                               ORDER BY seq"""


def read_vote_events(
    connection: Connection,
    room_id: str,
    vote_id: str,
) -> list[dict[str, object]]:
    poll = read_event_by_id(
        connection,
        room_id,
        vote_id,
        include_hidden=False,
    )
    if (
        not poll
        or str(poll.get("type") or "") != "message_final"
        or str(poll.get("message_kind") or "") != "vote"
        or poll.get("message_deleted") is True
    ):
        return []
    rows = connection.execute(
        _VOTE_BALLOT_EVENTS_QUERY,
        (
            room_id,
            VISIBLE,
            vote_id,
            int(poll.get("seq") or 0),
        ),
    ).fetchall()
    ballots = [
        payload
        for row in rows
        if (payload := payload_from_row(row, column="payload_json")).get("message_deleted")
        is not True
    ]
    return [poll, *ballots]


def read_event_sequence(connection: Connection, room_id: str, event_id: str) -> int:
    row = connection.execute(
        "SELECT seq FROM room_events WHERE room_id = %s AND event_id = %s",
        (room_id, event_id),
    ).fetchone()
    return int(row["seq"]) if row is not None else 0


def read_latest_event_sequence(connection: Connection, room_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM room_events WHERE room_id = %s",
        (room_id,),
    ).fetchone()
    return int(row["seq"] if row else 0)


def read_oldest_event_sequence(
    connection: Connection,
    room_id: str,
    *,
    include_hidden: bool,
) -> int:
    query = "SELECT COALESCE(MIN(seq), 0) AS seq FROM room_events WHERE room_id = %s"
    parameters: tuple[object, ...] = (room_id,)
    if not include_hidden:
        query += " AND visibility = %s"
        parameters = (room_id, VISIBLE)
    row = connection.execute(query, parameters).fetchone()
    return int(row["seq"] if row else 0)


def _event_filter(
    room_id: str,
    *,
    include_hidden: bool,
    after_seq: int,
    before_seq: int,
    event_types: Iterable[str] | None,
    exclude_actor_id: str,
) -> tuple[list[str], list[object]]:
    clauses = ["room_id = %s"]
    parameters: list[object] = [room_id]
    if not include_hidden:
        clauses.append("visibility = %s")
        parameters.append(VISIBLE)
    if after_seq:
        clauses.append("seq > %s")
        parameters.append(max(0, int(after_seq)))
    if before_seq:
        clauses.append("seq < %s")
        parameters.append(max(0, int(before_seq)))
    clean_types = [
        event_type
        for event_type in (clean_room_text(value, limit=64) for value in (event_types or ()))
        if event_type
    ]
    if clean_types:
        clauses.append("event_type = ANY(%s)")
        parameters.append(clean_types)
    clean_excluded_actor = clean_room_text(exclude_actor_id, limit=128)
    if clean_excluded_actor:
        clauses.append("actor_id != %s")
        parameters.append(clean_excluded_actor)
    return clauses, parameters
