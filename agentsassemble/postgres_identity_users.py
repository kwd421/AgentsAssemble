"""PostgreSQL operations for users, credentials, and operator pairing."""
from __future__ import annotations

from datetime import datetime

from psycopg import Connection

from agentsassemble.identity_store import (
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
    OPERATOR_PAIRING_REDEMPTION_STATUSES,
)
from agentsassemble.meeting_events import clean_lobby_text


def count_users(connection: Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) AS count FROM identity_users").fetchone()["count"])


def user_for_credential(connection: Connection, auth_key: str) -> dict[str, object] | None:
    clean_key = clean_lobby_text(auth_key, limit=128)
    if not clean_key:
        return None
    row = connection.execute(
        """SELECT u.* FROM identity_users u
           JOIN identity_credentials c ON c.user_id = u.user_id
           WHERE c.auth_key = %s""",
        (clean_key,),
    ).fetchone()
    return user_from_row(row) if row else None


def get_user(connection: Connection, user_id: str) -> dict[str, object] | None:
    row = connection.execute(
        "SELECT * FROM identity_users WHERE user_id = %s",
        (str(user_id or ""),),
    ).fetchone()
    return user_from_row(row) if row else None


def user_for_participant(
    connection: Connection,
    participant_id: str,
) -> dict[str, object] | None:
    row = connection.execute(
        "SELECT * FROM identity_users WHERE participant_id = %s",
        (str(participant_id or ""),),
    ).fetchone()
    return user_from_row(row) if row else None


def resolve_credential_user(
    connection: Connection,
    auth_key: str,
    *,
    provider: str,
    user_id: str,
    participant_id: str,
    display_name: str,
    avatar_image_url: str,
    participant_type: str,
    now: str,
) -> dict[str, object] | None:
    clean_key = clean_lobby_text(auth_key, limit=128)
    if not clean_key:
        return None
    clean_provider = clean_lobby_text(provider, limit=32) or "device"
    clean_display_name = clean_lobby_text(display_name, limit=64)
    clean_avatar = clean_lobby_text(avatar_image_url, limit=2048)
    clean_type = clean_lobby_text(participant_type, limit=32).lower()
    connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (clean_key,))
    row = connection.execute(
        """SELECT u.* FROM identity_users u
           JOIN identity_credentials c ON c.user_id = u.user_id
           WHERE c.auth_key = %s""",
        (clean_key,),
    ).fetchone()
    if row is None:
        suffix = clean_key.split(":", 1)[-1]
        new_user_id = str(user_id or "").strip() or f"u-{suffix[:12]}"
        new_participant_id = str(participant_id or "").strip() or f"guest-{suffix[:8]}"
        connection.execute(
            """INSERT INTO identity_users(
                   user_id, participant_id, display_name, avatar_image_url,
                   participant_type, auth_provider, created_at, last_seen_at
               ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                new_user_id,
                new_participant_id,
                clean_display_name,
                clean_avatar,
                clean_type or "human",
                clean_provider,
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO identity_credentials(
                   auth_key, user_id, provider, created_at, last_used_at
               ) VALUES(%s, %s, %s, %s, %s)""",
            (clean_key, new_user_id, clean_provider, now, now),
        )
    else:
        assignments = ["last_seen_at = %s"]
        values: list[object] = [now]
        for column, value in (
            ("display_name", clean_display_name),
            ("avatar_image_url", clean_avatar),
            ("participant_type", clean_type),
        ):
            if value:
                assignments.append(f"{column} = %s")
                values.append(value)
        values.append(row["user_id"])
        connection.execute(
            f"UPDATE identity_users SET {', '.join(assignments)} WHERE user_id = %s",
            tuple(values),
        )
        connection.execute(
            """UPDATE identity_credentials SET last_used_at = %s
               WHERE auth_key = %s""",
            (now, clean_key),
        )
    return user_for_credential(connection, clean_key)


def set_user_operator(connection: Connection, user_id: str, is_operator: bool) -> bool:
    cursor = connection.execute(
        "UPDATE identity_users SET is_operator = %s WHERE user_id = %s",
        (bool(is_operator), str(user_id or "")),
    )
    return cursor.rowcount > 0


def claim_local_operator_credential(
    connection: Connection,
    auth_key: str,
    *,
    provider: str,
    display_name: str,
    now: str,
) -> dict[str, object] | None:
    clean_key = clean_lobby_text(auth_key, limit=128)
    if not clean_key:
        return None
    clean_provider = clean_lobby_text(provider, limit=32) or "device"
    clean_display_name = clean_lobby_text(display_name, limit=64)
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (f"operator:{LOCAL_OPERATOR_USER_ID}",),
    )
    conflicting_user = connection.execute(
        "SELECT participant_id FROM identity_users WHERE user_id = %s",
        (LOCAL_OPERATOR_USER_ID,),
    ).fetchone()
    if conflicting_user and conflicting_user["participant_id"] != LOCAL_OPERATOR_PARTICIPANT_ID:
        raise RuntimeError("The canonical operator user id is already assigned to another participant.")
    conflicting_participant = connection.execute(
        "SELECT user_id FROM identity_users WHERE participant_id = %s",
        (LOCAL_OPERATOR_PARTICIPANT_ID,),
    ).fetchone()
    if conflicting_participant and conflicting_participant["user_id"] != LOCAL_OPERATOR_USER_ID:
        raise RuntimeError("The canonical operator participant id is already assigned to another user.")

    credential = connection.execute(
        """SELECT c.user_id, u.display_name FROM identity_credentials c
           JOIN identity_users u ON u.user_id = c.user_id
           WHERE c.auth_key = %s""",
        (clean_key,),
    ).fetchone()
    legacy_rows = connection.execute(
        """SELECT user_id FROM identity_users
           WHERE is_operator = TRUE AND user_id != %s""",
        (LOCAL_OPERATOR_USER_ID,),
    ).fetchall()
    legacy_operator_ids = [str(row["user_id"]) for row in legacy_rows]
    inherited_name = clean_display_name or (
        str(credential["display_name"] or "") if credential else ""
    )
    canonical = connection.execute(
        "SELECT 1 FROM identity_users WHERE user_id = %s",
        (LOCAL_OPERATOR_USER_ID,),
    ).fetchone()
    if canonical is None:
        connection.execute(
            """INSERT INTO identity_users(
                   user_id, participant_id, display_name, avatar_image_url,
                   participant_type, auth_provider, is_operator, created_at,
                   last_seen_at
               ) VALUES(%s, %s, %s, '', 'human', %s, TRUE, %s, %s)""",
            (
                LOCAL_OPERATOR_USER_ID,
                LOCAL_OPERATOR_PARTICIPANT_ID,
                inherited_name,
                clean_provider,
                now,
                now,
            ),
        )
    else:
        if inherited_name:
            connection.execute(
                """UPDATE identity_users SET is_operator = TRUE,
                       last_seen_at = %s, display_name = %s WHERE user_id = %s""",
                (now, inherited_name, LOCAL_OPERATOR_USER_ID),
            )
        else:
            connection.execute(
                """UPDATE identity_users SET is_operator = TRUE,
                       last_seen_at = %s WHERE user_id = %s""",
                (now, LOCAL_OPERATOR_USER_ID),
            )
    connection.execute(
        """UPDATE identity_users SET is_operator = FALSE
           WHERE user_id != %s AND is_operator = TRUE""",
        (LOCAL_OPERATOR_USER_ID,),
    )
    if legacy_operator_ids:
        connection.execute(
            """UPDATE identity_room_registry SET owner_id = %s
               WHERE owner_id = ANY(%s)""",
            (LOCAL_OPERATOR_USER_ID, legacy_operator_ids),
        )
    if credential is None:
        connection.execute(
            """INSERT INTO identity_credentials(
                   auth_key, user_id, provider, created_at, last_used_at
               ) VALUES(%s, %s, %s, %s, %s)""",
            (clean_key, LOCAL_OPERATOR_USER_ID, clean_provider, now, now),
        )
    else:
        connection.execute(
            """UPDATE identity_credentials SET user_id = %s, provider = %s,
                   last_used_at = %s WHERE auth_key = %s""",
            (LOCAL_OPERATOR_USER_ID, clean_provider, now, clean_key),
        )
    return get_user(connection, LOCAL_OPERATOR_USER_ID)


def create_operator_pairing(
    connection: Connection,
    *,
    pairing_id: str,
    token_fingerprint: str,
    room_id: str,
    target_origin: str,
    created_at: str,
    expires_at: str,
) -> dict[str, object]:
    clean_pairing_id = clean_lobby_text(pairing_id, limit=128)
    clean_fingerprint = clean_lobby_text(token_fingerprint, limit=128)
    clean_room_id = clean_lobby_text(room_id, limit=128)
    clean_origin = clean_lobby_text(target_origin, limit=512)
    if not all((clean_pairing_id, clean_fingerprint, clean_room_id, clean_origin)):
        raise ValueError("pairing id, token fingerprint, room, and target origin are required")
    operator = connection.execute(
        """SELECT 1 FROM identity_users WHERE user_id = %s
           AND participant_id = %s AND is_operator = TRUE""",
        (LOCAL_OPERATOR_USER_ID, LOCAL_OPERATOR_PARTICIPANT_ID),
    ).fetchone()
    if operator is None:
        raise ValueError("canonical operator identity is not claimed")
    connection.execute(
        """UPDATE identity_operator_pairings SET revoked_at = %s
           WHERE user_id = %s AND room_id = %s AND target_origin = %s
           AND used_at = '' AND revoked_at = ''""",
        (created_at, LOCAL_OPERATOR_USER_ID, clean_room_id, clean_origin),
    )
    connection.execute(
        """INSERT INTO identity_operator_pairings(
               pairing_id, token_fingerprint, user_id, room_id, target_origin,
               created_at, expires_at
           ) VALUES(%s, %s, %s, %s, %s, %s, %s)""",
        (
            clean_pairing_id,
            clean_fingerprint,
            LOCAL_OPERATOR_USER_ID,
            clean_room_id,
            clean_origin,
            clean_lobby_text(created_at, limit=64),
            clean_lobby_text(expires_at, limit=64),
        ),
    )
    row = connection.execute(
        "SELECT * FROM identity_operator_pairings WHERE pairing_id = %s",
        (clean_pairing_id,),
    ).fetchone()
    return pairing_from_row(row)


def operator_pairing_for_fingerprint(
    connection: Connection,
    token_fingerprint: str,
) -> dict[str, object] | None:
    clean_fingerprint = clean_lobby_text(token_fingerprint, limit=128)
    if not clean_fingerprint:
        return None
    row = connection.execute(
        """SELECT * FROM identity_operator_pairings
           WHERE token_fingerprint = %s""",
        (clean_fingerprint,),
    ).fetchone()
    return pairing_from_row(row) if row else None


def consume_operator_pairing(
    connection: Connection,
    *,
    token_fingerprint: str,
    target_origin: str,
    auth_key: str,
    used_at: str,
) -> dict[str, object]:
    clean_fingerprint = clean_lobby_text(token_fingerprint, limit=128)
    clean_origin = clean_lobby_text(target_origin, limit=512)
    clean_auth_key = clean_lobby_text(auth_key, limit=128)
    clean_used_at = clean_lobby_text(used_at, limit=64)
    if not all((clean_fingerprint, clean_origin, clean_auth_key, clean_used_at)):
        return {"status": "invalid"}
    row = connection.execute(
        """SELECT * FROM identity_operator_pairings
           WHERE token_fingerprint = %s FOR UPDATE""",
        (clean_fingerprint,),
    ).fetchone()
    if row is None:
        return {"status": "invalid"}
    pairing = pairing_from_row(row)
    if pairing["target_origin"] != clean_origin:
        return {"status": "origin_mismatch"}
    if pairing["revoked_at"]:
        return {"status": "revoked"}
    if pairing["used_at"]:
        if pairing["consumed_auth_key"] != clean_auth_key:
            return {"status": "already_used"}
        user = connection.execute(
            "SELECT * FROM identity_users WHERE user_id = %s",
            (LOCAL_OPERATOR_USER_ID,),
        ).fetchone()
        if user is None:
            return {"status": "invalid"}
        return {
            "status": "resumed",
            "pairing": pairing,
            "user": user_from_row(user),
        }
    try:
        if datetime.fromisoformat(str(pairing["expires_at"])) <= datetime.fromisoformat(clean_used_at):
            return {"status": "expired"}
    except ValueError:
        return {"status": "invalid"}
    cursor = connection.execute(
        """UPDATE identity_operator_pairings
           SET used_at = %s, consumed_auth_key = %s,
               redemption_status = 'claiming', failure_code = ''
           WHERE pairing_id = %s AND used_at = '' AND revoked_at = ''""",
        (clean_used_at, clean_auth_key, pairing["pairing_id"]),
    )
    if cursor.rowcount != 1:
        return {"status": "already_used"}
    user = claim_local_operator_credential(
        connection,
        clean_auth_key,
        provider="device",
        display_name="",
        now=clean_used_at,
    )
    updated = connection.execute(
        "SELECT * FROM identity_operator_pairings WHERE pairing_id = %s",
        (pairing["pairing_id"],),
    ).fetchone()
    return {
        "status": "consumed",
        "pairing": pairing_from_row(updated),
        "user": user,
    }


def update_operator_pairing_redemption(
    connection: Connection,
    *,
    pairing_id: str,
    auth_key: str,
    status: str,
    completed_at: str = "",
    session_fingerprint: str = "",
    failure_code: str = "",
) -> dict[str, object] | None:
    clean_pairing_id = clean_lobby_text(pairing_id, limit=128)
    clean_auth_key = clean_lobby_text(auth_key, limit=128)
    clean_status = clean_lobby_text(status, limit=32)
    if (
        not clean_pairing_id
        or not clean_auth_key
        or clean_status not in OPERATOR_PAIRING_REDEMPTION_STATUSES
    ):
        raise ValueError("valid pairing id, credential, and redemption status are required")
    pairing = connection.execute(
        "SELECT * FROM identity_operator_pairings WHERE pairing_id = %s FOR UPDATE",
        (clean_pairing_id,),
    ).fetchone()
    if pairing is None or str(pairing["consumed_auth_key"] or "") != clean_auth_key:
        return None
    if pairing["redemption_status"] == "completed" and clean_status != "completed":
        return pairing_from_row(pairing)
    connection.execute(
        """UPDATE identity_operator_pairings
           SET redemption_status = %s, completed_at = %s,
               session_fingerprint = %s, failure_code = %s
           WHERE pairing_id = %s AND consumed_auth_key = %s""",
        (
            clean_status,
            clean_lobby_text(completed_at, limit=64),
            clean_lobby_text(session_fingerprint, limit=128),
            clean_lobby_text(failure_code, limit=128),
            clean_pairing_id,
            clean_auth_key,
        ),
    )
    updated = connection.execute(
        "SELECT * FROM identity_operator_pairings WHERE pairing_id = %s",
        (clean_pairing_id,),
    ).fetchone()
    return pairing_from_row(updated) if updated else None


def revoke_operator_pairing(
    connection: Connection,
    pairing_id: str,
    *,
    revoked_at: str,
) -> bool:
    clean_pairing_id = clean_lobby_text(pairing_id, limit=128)
    if not clean_pairing_id:
        return False
    cursor = connection.execute(
        """UPDATE identity_operator_pairings SET revoked_at = %s
           WHERE pairing_id = %s AND used_at = '' AND revoked_at = ''""",
        (clean_lobby_text(revoked_at, limit=64), clean_pairing_id),
    )
    return cursor.rowcount == 1


def operator_user_id(connection: Connection) -> str:
    row = connection.execute(
        """SELECT user_id FROM identity_users
           WHERE participant_id = %s AND is_operator = TRUE""",
        (LOCAL_OPERATOR_PARTICIPANT_ID,),
    ).fetchone()
    if row is None:
        row = connection.execute(
            """SELECT user_id FROM identity_users WHERE is_operator = TRUE
               ORDER BY last_seen_at DESC, created_at DESC LIMIT 1"""
        ).fetchone()
    return str(row["user_id"]) if row else ""


def user_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "user_id": str(row["user_id"]),
        "participant_id": str(row["participant_id"]),
        "display_name": str(row["display_name"] or ""),
        "avatar_image_url": str(row["avatar_image_url"] or ""),
        "participant_type": str(row["participant_type"] or "human"),
        "auth_provider": str(row["auth_provider"] or "device"),
        "is_operator": bool(row["is_operator"]),
        "created_at": str(row["created_at"] or ""),
        "last_seen_at": str(row["last_seen_at"] or ""),
    }


def pairing_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "pairing_id": str(row["pairing_id"]),
        "token_fingerprint": str(row["token_fingerprint"]),
        "user_id": str(row["user_id"]),
        "room_id": str(row["room_id"]),
        "target_origin": str(row["target_origin"]),
        "created_at": str(row["created_at"]),
        "expires_at": str(row["expires_at"]),
        "used_at": str(row["used_at"] or ""),
        "consumed_auth_key": str(row["consumed_auth_key"] or ""),
        "redemption_status": str(row["redemption_status"] or "ready"),
        "completed_at": str(row["completed_at"] or ""),
        "session_fingerprint": str(row["session_fingerprint"] or ""),
        "failure_code": str(row["failure_code"] or ""),
        "revoked_at": str(row["revoked_at"] or ""),
    }
