"""PostgreSQL operations for identity-owned memberships and room registry."""
from __future__ import annotations

from uuid import uuid4

from psycopg import Connection

from agentsassemble.room.text import clean_room_text


_MEMBERSHIP_FIELDS = (
    "meeting_id",
    "participant_id",
    "display_name",
    "role",
    "participant_type",
    "provider_kind",
    "connection_kind",
    "invite_scope",
    "status",
    "muted",
    "is_host",
    "source",
    "created_at",
    "updated_at",
    "last_seen_at",
)

_MEMBERSHIP_MERGE_FIELDS = (
    "display_name",
    "role",
    "participant_type",
    "provider_kind",
    "connection_kind",
    "invite_scope",
    "status",
    "source",
    "last_seen_at",
)


def count_memberships(connection: Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM identity_memberships"
    ).fetchone()
    return int(row["count"])


def list_memberships(
    connection: Connection,
    meeting_id: str = "",
) -> list[dict[str, object]]:
    room_id = clean_room_text(meeting_id, limit=128)
    if room_id:
        rows = connection.execute(
            """SELECT * FROM identity_memberships
               WHERE meeting_id = %s ORDER BY updated_at DESC""",
            (room_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM identity_memberships ORDER BY updated_at DESC"
        ).fetchall()
    return [membership_from_row(row) for row in rows]


def get_membership(
    connection: Connection,
    meeting_id: str,
    participant_id: str,
) -> dict[str, object] | None:
    row = connection.execute(
        """SELECT * FROM identity_memberships
           WHERE meeting_id = %s AND participant_id = %s""",
        (
            clean_room_text(meeting_id, limit=128),
            clean_room_text(participant_id, limit=128),
        ),
    ).fetchone()
    return membership_from_row(row) if row else None


def upsert_membership(
    connection: Connection,
    record: dict[str, object],
    *,
    now: str,
) -> dict[str, object]:
    member = {key: record.get(key, "") for key in _MEMBERSHIP_FIELDS}
    meeting_id = clean_room_text(member["meeting_id"], limit=128)
    participant_id = clean_room_text(member["participant_id"], limit=128)
    if not participant_id:
        raise ValueError("participant_id is required for a room membership.")

    lock_key = f"membership:{meeting_id}:{participant_id}"
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (lock_key,),
    )
    existing = connection.execute(
        """SELECT * FROM identity_memberships
           WHERE meeting_id = %s AND participant_id = %s""",
        (meeting_id, participant_id),
    ).fetchone()
    if existing is None:
        connection.execute(
            """INSERT INTO identity_memberships(
                   meeting_id, participant_id, display_name, role,
                   participant_type, provider_kind, connection_kind, invite_scope,
                   status, muted, is_host, source, created_at, updated_at, last_seen_at
               ) VALUES(
                   %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
               )""",
            (
                meeting_id,
                participant_id,
                str(member["display_name"] or participant_id),
                str(member["role"] or "agent"),
                str(member["participant_type"] or "unknown"),
                str(member["provider_kind"] or ""),
                str(member["connection_kind"] or ""),
                str(member["invite_scope"] or "read_only"),
                str(member["status"] or ""),
                bool(member["muted"]),
                bool(member["is_host"]),
                str(member["source"] or "manual"),
                str(member["created_at"] or now),
                now,
                str(member["last_seen_at"] or ""),
            ),
        )
    else:
        assignments = ["updated_at = %s"]
        values: list[object] = [now]
        for field in _MEMBERSHIP_MERGE_FIELDS:
            incoming = record.get(field)
            if incoming not in ("", None, [], {}):
                assignments.append(f"{field} = %s")
                values.append(str(incoming))
        if "muted" in record:
            assignments.append("muted = %s")
            values.append(bool(record["muted"]))
        if "is_host" in record:
            assignments.append("is_host = %s")
            values.append(bool(record["is_host"]))
        values.extend((meeting_id, participant_id))
        connection.execute(
            f"""UPDATE identity_memberships SET {', '.join(assignments)}
                WHERE meeting_id = %s AND participant_id = %s""",
            tuple(values),
        )
    refreshed = connection.execute(
        """SELECT * FROM identity_memberships
           WHERE meeting_id = %s AND participant_id = %s""",
        (meeting_id, participant_id),
    ).fetchone()
    return membership_from_row(refreshed)


def remove_membership(
    connection: Connection,
    meeting_id: str,
    participant_id: str,
) -> bool:
    room_id = clean_room_text(meeting_id, limit=128)
    clean_participant_id = clean_room_text(participant_id, limit=128)
    if not clean_participant_id:
        return False
    if room_id:
        cursor = connection.execute(
            """DELETE FROM identity_memberships
               WHERE meeting_id = %s AND participant_id = %s""",
            (room_id, clean_participant_id),
        )
    else:
        cursor = connection.execute(
            "DELETE FROM identity_memberships WHERE participant_id = %s",
            (clean_participant_id,),
        )
    return cursor.rowcount > 0


def set_membership_muted(
    connection: Connection,
    meeting_id: str,
    participant_id: str,
    muted: bool,
    *,
    now: str,
) -> dict[str, object]:
    clean_participant_id = clean_room_text(participant_id, limit=128)
    if not clean_participant_id:
        raise ValueError("participant_id is required to set mute state.")
    existing = get_membership(connection, meeting_id, clean_participant_id)
    if existing is None:
        return upsert_membership(
            connection,
            {
                "meeting_id": meeting_id,
                "participant_id": clean_participant_id,
                "display_name": clean_participant_id,
                "role": "agent",
                "source": "moderation",
                "muted": muted,
            },
            now=now,
        )
    return upsert_membership(
        connection,
        {
            "meeting_id": meeting_id,
            "participant_id": clean_participant_id,
            "muted": muted,
        },
        now=now,
    )


def upsert_room(
    connection: Connection,
    *,
    room_id: str,
    room_uid: str,
    owner_id: str,
    label: str,
    origin: str,
    now: str,
) -> dict[str, object]:
    clean_room_id = clean_room_text(room_id, limit=128)
    if not clean_room_id:
        raise ValueError("room_id is required.")
    clean_owner_id = clean_room_text(owner_id, limit=128)
    clean_room_uid = clean_room_text(room_uid, limit=64)
    clean_label = clean_room_text(label, limit=128)
    clean_origin = clean_room_text(origin, limit=64)
    stable_room_uid = clean_room_uid or str(uuid4())
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (f"identity-room:{clean_room_id}",),
    )
    existing = connection.execute(
        "SELECT room_uid FROM identity_room_registry WHERE room_id = %s",
        (clean_room_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """INSERT INTO identity_room_registry(
                   room_id, room_uid, owner_id, label, created_at, last_active_at,
                   archived, origin
               ) VALUES(%s, %s, %s, %s, %s, %s, FALSE, %s)""",
            (
                clean_room_id,
                stable_room_uid,
                clean_owner_id,
                clean_label,
                now,
                now,
                clean_origin,
            ),
        )
    else:
        existing_uid = str(existing.get("room_uid") or "")
        if clean_room_uid and existing_uid and clean_room_uid != existing_uid:
            raise ValueError(f"room_uid for {clean_room_id} is immutable.")
        assignments = ["last_active_at = %s"]
        values: list[object] = [now]
        if clean_room_uid and not existing_uid:
            assignments.append("room_uid = %s")
            values.append(clean_room_uid)
        for column, value in (
            ("owner_id", clean_owner_id),
            ("label", clean_label),
            ("origin", clean_origin),
        ):
            if value:
                assignments.append(f"{column} = %s")
                values.append(value)
        values.append(clean_room_id)
        connection.execute(
            f"""UPDATE identity_room_registry SET {', '.join(assignments)}
                WHERE room_id = %s""",
            tuple(values),
        )
    refreshed = connection.execute(
        "SELECT * FROM identity_room_registry WHERE room_id = %s",
        (clean_room_id,),
    ).fetchone()
    return room_from_row(refreshed)


def list_rooms(
    connection: Connection,
    *,
    owner_id: str = "",
    include_archived: bool = False,
) -> list[dict[str, object]]:
    clean_owner_id = clean_room_text(owner_id, limit=128)
    where: list[str] = []
    parameters: list[object] = []
    if clean_owner_id:
        where.append("owner_id = %s")
        parameters.append(clean_owner_id)
    if not include_archived:
        where.append("archived = FALSE")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = connection.execute(
        f"""SELECT * FROM identity_room_registry{clause}
            ORDER BY last_active_at DESC""",
        tuple(parameters),
    ).fetchall()
    return [room_from_row(row) for row in rows]


def get_room(connection: Connection, room_id: str) -> dict[str, object] | None:
    clean_room_id = clean_room_text(room_id, limit=128)
    if not clean_room_id:
        return None
    row = connection.execute(
        "SELECT * FROM identity_room_registry WHERE room_id = %s",
        (clean_room_id,),
    ).fetchone()
    return room_from_row(row) if row else None


def set_room_archived(connection: Connection, room_id: str, archived: bool) -> bool:
    clean_room_id = clean_room_text(room_id, limit=128)
    if not clean_room_id:
        return False
    cursor = connection.execute(
        """UPDATE identity_room_registry SET archived = %s
           WHERE room_id = %s""",
        (bool(archived), clean_room_id),
    )
    return cursor.rowcount > 0


def touch_room(connection: Connection, room_id: str, *, now: str) -> None:
    clean_room_id = clean_room_text(room_id, limit=128)
    if clean_room_id:
        connection.execute(
            """UPDATE identity_room_registry SET last_active_at = %s
               WHERE room_id = %s""",
            (now, clean_room_id),
        )


def delete_room(connection: Connection, room_id: str) -> bool:
    clean_room_id = clean_room_text(room_id, limit=128)
    if not clean_room_id:
        return False
    connection.execute(
        "DELETE FROM identity_room_user_preferences WHERE room_id = %s",
        (clean_room_id,),
    )
    connection.execute(
        "DELETE FROM identity_memberships WHERE meeting_id = %s",
        (clean_room_id,),
    )
    cursor = connection.execute(
        "DELETE FROM identity_room_registry WHERE room_id = %s",
        (clean_room_id,),
    )
    return cursor.rowcount > 0


def membership_from_row(row: dict[str, object]) -> dict[str, object]:
    member = {key: row[key] for key in _MEMBERSHIP_FIELDS}
    member["muted"] = bool(member["muted"])
    member["is_host"] = bool(member["is_host"])
    return member


def room_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "room_id": str(row["room_id"]),
        "owner_id": str(row["owner_id"] or ""),
        "label": str(row["label"] or ""),
        "created_at": str(row["created_at"] or ""),
        "last_active_at": str(row["last_active_at"] or ""),
        "archived": bool(row["archived"]),
        "origin": str(row["origin"] or ""),
    }
