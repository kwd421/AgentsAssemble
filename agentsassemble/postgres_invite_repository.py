"""PostgreSQL invite/session repository for multi-instance hosted mode."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from psycopg.rows import dict_row

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.postgres_connection_pool import (
    BoundedPostgresConnectionPool,
    PoolFactory,
    PostgresPoolSettings,
)

_AUTHORITY_ID = "default"


class PostgresInviteSessionRepository:
    def __init__(
        self,
        dsn: str,
        *,
        pool_settings: PostgresPoolSettings | None = None,
        pool_factory: PoolFactory | None = None,
    ) -> None:
        self._pool = BoundedPostgresConnectionPool(
            dsn,
            connection_kwargs={"row_factory": dict_row},
            settings=pool_settings,
            pool_factory=pool_factory,
        )

    def __repr__(self) -> str:
        return "PostgresInviteSessionRepository(configured=True)"

    def signing_secret(self) -> str:
        candidate = secrets.token_urlsafe(32)
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO room_invite_authority(
                       authority_id, signing_secret, created_at
                   ) VALUES(%s, %s, %s)
                   ON CONFLICT(authority_id) DO NOTHING""",
                (_AUTHORITY_ID, candidate, datetime.now(UTC)),
            )
            row = connection.execute(
                """SELECT signing_secret FROM room_invite_authority
                   WHERE authority_id = %s""",
                (_AUTHORITY_ID,),
            ).fetchone()
        return str(row["signing_secret"])

    def existing_signing_secret(self) -> str:
        with self._pool.connection() as connection:
            row = connection.execute(
                """SELECT signing_secret FROM room_invite_authority
                   WHERE authority_id = %s""",
                (_AUTHORITY_ID,),
            ).fetchone()
        return str(row["signing_secret"]) if row else ""

    def save_invite(self, record: dict[str, object]) -> None:
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO room_invites(
                       invite_id, room_id, agent_id, display_name, invite_scope,
                       participant_type, client_type, provider_kind,
                       created_by_user_id, join_code_fingerprint, join_nonce,
                       permission_mode, max_uses, use_count, expires_at,
                       created_at, revoked
                   ) VALUES(
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s
                   )
                   ON CONFLICT(invite_id) DO UPDATE SET
                       room_id = excluded.room_id,
                       agent_id = excluded.agent_id,
                       display_name = excluded.display_name,
                       invite_scope = excluded.invite_scope,
                       participant_type = excluded.participant_type,
                       client_type = excluded.client_type,
                       provider_kind = excluded.provider_kind,
                       created_by_user_id = excluded.created_by_user_id,
                       join_code_fingerprint = excluded.join_code_fingerprint,
                       join_nonce = excluded.join_nonce,
                       permission_mode = excluded.permission_mode,
                       max_uses = excluded.max_uses,
                       use_count = excluded.use_count,
                       expires_at = excluded.expires_at,
                       created_at = excluded.created_at,
                       revoked = excluded.revoked""",
                _invite_parameters(record),
            )

    def invite(self, invite_id: str) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT * FROM room_invites WHERE invite_id = %s",
                (clean_lobby_text(invite_id, limit=128),),
            ).fetchone()
        return _invite_from_row(row) if row else None

    def invite_for_join_code(self, join_code_fingerprint: str) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT * FROM room_invites WHERE join_code_fingerprint = %s",
                (clean_lobby_text(join_code_fingerprint, limit=128),),
            ).fetchone()
        return _invite_from_row(row) if row else None

    def nonce_was_used(self, nonce_fingerprint: str) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM room_invite_used_nonces
                   WHERE nonce_fingerprint = %s""",
                (clean_lobby_text(nonce_fingerprint, limit=128),),
            ).fetchone()
        return row is not None

    def consume(
        self,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
    ) -> str:
        with self._pool.connection() as connection, connection.transaction():
            if reusable:
                row = connection.execute(
                    """SELECT use_count, max_uses FROM room_invites
                       WHERE invite_id = %s FOR UPDATE""",
                    (clean_lobby_text(invite_id, limit=128),),
                ).fetchone()
                if row is None:
                    return ""
                stored_max_uses = int(row["max_uses"])
                effective_max = stored_max_uses if stored_max_uses >= 0 else max(0, int(max_uses))
                if effective_max and int(row["use_count"]) >= effective_max:
                    return "invite_use_limit_reached"
                connection.execute(
                    "UPDATE room_invites SET use_count = use_count + 1 WHERE invite_id = %s",
                    (clean_lobby_text(invite_id, limit=128),),
                )
                return ""

            inserted = connection.execute(
                """INSERT INTO room_invite_used_nonces(nonce_fingerprint, consumed_at)
                   VALUES(%s, %s) ON CONFLICT(nonce_fingerprint) DO NOTHING
                   RETURNING nonce_fingerprint""",
                (
                    clean_lobby_text(nonce_fingerprint, limit=128),
                    datetime.now(UTC),
                ),
            ).fetchone()
            return "" if inserted else "token_already_used"

    def revoke_invite(self, invite_id: str) -> bool:
        with self._pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """UPDATE room_invites SET revoked = TRUE
                   WHERE invite_id = %s RETURNING invite_id""",
                (clean_lobby_text(invite_id, limit=128),),
            ).fetchone()
        return row is not None

    def revoke_room_invites(self, room_id: str) -> int:
        with self._pool.connection() as connection, connection.transaction():
            cursor = connection.execute(
                """UPDATE room_invites SET revoked = TRUE
                   WHERE room_id = %s AND revoked = FALSE""",
                (clean_lobby_text(room_id, limit=128),),
            )
            return int(cursor.rowcount)

    def list_invites(self) -> list[dict[str, object]]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM room_invites ORDER BY created_at, invite_id"
            ).fetchall()
        return [_invite_from_row(row) for row in rows]

    def save_session(self, token_fingerprint: str, record: dict[str, object]) -> None:
        self.replace_participant_session(token_fingerprint, record)

    def replace_participant_session(
        self,
        token_fingerprint: str,
        record: dict[str, object],
    ) -> None:
        parameters = _session_parameters(token_fingerprint, record)
        if not parameters[0] or not parameters[1] or not parameters[2]:
            raise ValueError("session token, room, and participant are required")
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "DELETE FROM room_access_sessions WHERE token_fingerprint = %s",
                (parameters[0],),
            )
            connection.execute(
                """INSERT INTO room_access_sessions(
                       token_fingerprint, room_id, participant_id, display_name,
                       invite_scope, participant_type, client_type, provider_kind,
                       owner_id, connection_kind, joined_at, expires_at
                   ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT(room_id, participant_id) DO UPDATE SET
                       token_fingerprint = excluded.token_fingerprint,
                       display_name = excluded.display_name,
                       invite_scope = excluded.invite_scope,
                       participant_type = excluded.participant_type,
                       client_type = excluded.client_type,
                       provider_kind = excluded.provider_kind,
                       owner_id = excluded.owner_id,
                       connection_kind = excluded.connection_kind,
                       joined_at = excluded.joined_at,
                       expires_at = excluded.expires_at""",
                parameters,
            )

    def session(self, token_fingerprint: str) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """SELECT * FROM room_access_sessions
                   WHERE token_fingerprint = %s""",
                (clean_lobby_text(token_fingerprint, limit=128),),
            ).fetchone()
        return _session_from_row(row) if row else None

    def revoke_session(self, token_fingerprint: str) -> bool:
        with self._pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """DELETE FROM room_access_sessions WHERE token_fingerprint = %s
                   RETURNING token_fingerprint""",
                (clean_lobby_text(token_fingerprint, limit=128),),
            ).fetchone()
        return row is not None

    def revoke_participant_sessions(self, room_id: str, participant_id: str) -> int:
        clauses = ["participant_id = %s"]
        parameters: list[object] = [clean_lobby_text(participant_id, limit=128)]
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if clean_room_id:
            clauses.append("room_id = %s")
            parameters.append(clean_room_id)
        with self._pool.connection() as connection, connection.transaction():
            cursor = connection.execute(
                f"DELETE FROM room_access_sessions WHERE {' AND '.join(clauses)}",
                tuple(parameters),
            )
            return int(cursor.rowcount)

    def revoke_room_sessions(self, room_id: str) -> int:
        with self._pool.connection() as connection, connection.transaction():
            cursor = connection.execute(
                "DELETE FROM room_access_sessions WHERE room_id = %s",
                (clean_lobby_text(room_id, limit=128),),
            )
            return int(cursor.rowcount)

    def list_sessions(self) -> list[tuple[str, dict[str, object]]]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """SELECT * FROM room_access_sessions
                   ORDER BY joined_at, token_fingerprint"""
            ).fetchall()
        return [
            (str(row["token_fingerprint"]), _session_from_row(row))
            for row in rows
        ]

    def reload(self) -> None:
        return

    def clear(self) -> None:
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                """TRUNCATE TABLE room_access_sessions,
                       room_invite_used_nonces, room_invites,
                       room_invite_authority"""
            )

    def close(self) -> None:
        self._pool.close()

    def public_diagnostics(self) -> dict[str, object]:
        return {"backend": "postgresql", "pool": self._pool.public_diagnostics()}


def _invite_parameters(record: dict[str, object]) -> tuple[object, ...]:
    return (
        clean_lobby_text(record.get("invite_id"), limit=128),
        clean_lobby_text(record.get("meeting_id"), limit=128),
        clean_lobby_text(record.get("agent_id"), limit=64),
        clean_lobby_text(record.get("display_name"), limit=128),
        clean_lobby_text(record.get("invite_scope"), limit=32) or "room",
        clean_lobby_text(record.get("participant_type"), limit=32) or "human",
        clean_lobby_text(record.get("client_type"), limit=32) or "browser",
        clean_lobby_text(record.get("provider_kind"), limit=64) or "manual",
        clean_lobby_text(record.get("created_by_user_id"), limit=128),
        clean_lobby_text(record.get("join_code_fingerprint"), limit=128),
        clean_lobby_text(record.get("join_nonce"), limit=128),
        clean_lobby_text(record.get("permission_mode"), limit=64) or "participant",
        max(0, int(record.get("max_uses", 1) or 0)),
        max(0, int(record.get("use_count", 0) or 0)),
        _as_datetime(record.get("expires_at")),
        _as_datetime(record.get("created_at")),
        bool(record.get("revoked")),
    )


def _session_parameters(
    token_fingerprint: str,
    record: dict[str, object],
) -> tuple[object, ...]:
    return (
        clean_lobby_text(token_fingerprint, limit=128),
        clean_lobby_text(record.get("meeting_id"), limit=128),
        clean_lobby_text(record.get("agent_id"), limit=128),
        clean_lobby_text(record.get("display_name"), limit=128),
        clean_lobby_text(record.get("invite_scope"), limit=32) or "room",
        clean_lobby_text(record.get("participant_type"), limit=32) or "human",
        clean_lobby_text(record.get("client_type"), limit=32) or "browser",
        clean_lobby_text(record.get("provider_kind"), limit=64) or "manual",
        clean_lobby_text(record.get("owner_id"), limit=128),
        clean_lobby_text(record.get("connection_kind"), limit=64),
        _as_datetime(record.get("joined_at")),
        _as_datetime(record.get("expires_at")),
    )


def _invite_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "invite_id": str(row["invite_id"]),
        "agent_id": str(row["agent_id"] or ""),
        "display_name": str(row["display_name"] or ""),
        "meeting_id": str(row["room_id"]),
        "invite_scope": str(row["invite_scope"] or "room"),
        "participant_type": str(row["participant_type"] or "human"),
        "client_type": str(row["client_type"] or "browser"),
        "provider_kind": str(row["provider_kind"] or "manual"),
        "created_by_user_id": str(row["created_by_user_id"] or ""),
        "join_code_fingerprint": str(row["join_code_fingerprint"] or ""),
        "join_nonce": str(row["join_nonce"] or ""),
        "permission_mode": str(row["permission_mode"] or "participant"),
        "max_uses": int(row["max_uses"]),
        "use_count": int(row["use_count"]),
        "expires_at": _isoformat(row["expires_at"]),
        "created_at": _isoformat(row["created_at"]),
        "revoked": bool(row["revoked"]),
    }


def _session_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "agent_id": str(row["participant_id"]),
        "display_name": str(row["display_name"] or ""),
        "meeting_id": str(row["room_id"]),
        "invite_scope": str(row["invite_scope"] or "room"),
        "participant_type": str(row["participant_type"] or "human"),
        "client_type": str(row["client_type"] or "browser"),
        "provider_kind": str(row["provider_kind"] or "manual"),
        "owner_id": str(row["owner_id"] or ""),
        "connection_kind": str(row["connection_kind"] or ""),
        "joined_at": _isoformat(row["joined_at"]),
        "expires_at": _isoformat(row["expires_at"]),
    }


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value or ""))


def _isoformat(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value or "")
