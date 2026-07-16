"""PostgreSQL invite/session repository for multi-instance hosted mode."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agentsassemble.admission.workflow_record import validate_admission_workflow_record
from agentsassemble.persistence.postgres.application_database import (
    PostgresConnectionProvider,
)
from agentsassemble.persistence.postgres.connection_pool import (
    BoundedPostgresConnectionPool,
    PoolFactory,
    PostgresPoolSettings,
)
from agentsassemble.room_admission_workflow_maintenance import (
    AdmissionWorkflowSelection,
    PurgeReport,
    build_purge_report,
)
from agentsassemble.room.text import clean_room_text

_AUTHORITY_ID = "default"


class PostgresInviteSessionRepository:
    def __init__(
        self,
        dsn: str = "",
        *,
        database: PostgresConnectionProvider | None = None,
        pool_settings: PostgresPoolSettings | None = None,
        pool_factory: PoolFactory | None = None,
    ) -> None:
        clean_dsn = str(dsn or "").strip()
        if database is not None and clean_dsn:
            raise ValueError(
                "PostgreSQL invite repository accepts either a database owner or a DSN, not both."
            )
        if database is not None and (pool_settings is not None or pool_factory is not None):
            raise ValueError(
                "PostgreSQL invite repository pool options belong to its database owner."
            )
        if database is None and not clean_dsn:
            raise ValueError("PostgreSQL invite repository requires a database DSN.")
        self._owned_pool: BoundedPostgresConnectionPool | None = None
        if database is None:
            self._owned_pool = BoundedPostgresConnectionPool(
                clean_dsn,
                connection_kwargs={"row_factory": dict_row},
                settings=pool_settings,
                pool_factory=pool_factory,
            )
            self._connections: PostgresConnectionProvider = self._owned_pool
        else:
            self._connections = database

    def __repr__(self) -> str:
        return "PostgresInviteSessionRepository(configured=True)"

    def signing_secret(self) -> str:
        candidate = secrets.token_urlsafe(32)
        with self._connections.connection() as connection, connection.transaction():
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
        with self._connections.connection() as connection:
            row = connection.execute(
                """SELECT signing_secret FROM room_invite_authority
                   WHERE authority_id = %s""",
                (_AUTHORITY_ID,),
            ).fetchone()
        return str(row["signing_secret"]) if row else ""

    def save_invite(self, record: dict[str, object]) -> None:
        with self._connections.connection() as connection, connection.transaction():
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
        with self._connections.connection() as connection:
            row = connection.execute(
                "SELECT * FROM room_invites WHERE invite_id = %s",
                (clean_room_text(invite_id, limit=128),),
            ).fetchone()
        return _invite_from_row(row) if row else None

    def invite_for_join_code(self, join_code_fingerprint: str) -> dict[str, object] | None:
        with self._connections.connection() as connection:
            row = connection.execute(
                "SELECT * FROM room_invites WHERE join_code_fingerprint = %s",
                (clean_room_text(join_code_fingerprint, limit=128),),
            ).fetchone()
        return _invite_from_row(row) if row else None

    def nonce_was_used(self, nonce_fingerprint: str) -> bool:
        with self._connections.connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM room_invite_used_nonces
                   WHERE nonce_fingerprint = %s""",
                (clean_room_text(nonce_fingerprint, limit=128),),
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
        with self._connections.connection() as connection, connection.transaction():
            if reusable:
                row = connection.execute(
                    """SELECT use_count, max_uses FROM room_invites
                       WHERE invite_id = %s FOR UPDATE""",
                    (clean_room_text(invite_id, limit=128),),
                ).fetchone()
                if row is None:
                    return "invite_not_found"
                stored_max_uses = int(row["max_uses"])
                effective_max = stored_max_uses if stored_max_uses >= 0 else max(0, int(max_uses))
                if effective_max and int(row["use_count"]) >= effective_max:
                    return "invite_use_limit_reached"
                connection.execute(
                    "UPDATE room_invites SET use_count = use_count + 1 WHERE invite_id = %s",
                    (clean_room_text(invite_id, limit=128),),
                )
                return ""

            inserted = connection.execute(
                """INSERT INTO room_invite_used_nonces(nonce_fingerprint, consumed_at)
                   VALUES(%s, %s) ON CONFLICT(nonce_fingerprint) DO NOTHING
                   RETURNING nonce_fingerprint""",
                (
                    clean_room_text(nonce_fingerprint, limit=128),
                    datetime.now(UTC),
                ),
            ).fetchone()
            return "" if inserted else "token_already_used"

    def revoke_invite(self, invite_id: str) -> bool:
        with self._connections.connection() as connection, connection.transaction():
            row = connection.execute(
                """UPDATE room_invites SET revoked = TRUE
                   WHERE invite_id = %s RETURNING invite_id""",
                (clean_room_text(invite_id, limit=128),),
            ).fetchone()
        return row is not None

    def revoke_room_invites(self, room_id: str) -> int:
        with self._connections.connection() as connection, connection.transaction():
            cursor = connection.execute(
                """UPDATE room_invites SET revoked = TRUE
                   WHERE room_id = %s AND revoked = FALSE""",
                (clean_room_text(room_id, limit=128),),
            )
            return int(cursor.rowcount)

    def list_invites(self) -> list[dict[str, object]]:
        with self._connections.connection() as connection:
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
        with self._connections.connection() as connection, connection.transaction():
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
        with self._connections.connection() as connection:
            row = connection.execute(
                """SELECT * FROM room_access_sessions
                   WHERE token_fingerprint = %s""",
                (clean_room_text(token_fingerprint, limit=128),),
            ).fetchone()
        return _session_from_row(row) if row else None

    def revoke_session(self, token_fingerprint: str) -> bool:
        with self._connections.connection() as connection, connection.transaction():
            row = connection.execute(
                """DELETE FROM room_access_sessions WHERE token_fingerprint = %s
                   RETURNING token_fingerprint""",
                (clean_room_text(token_fingerprint, limit=128),),
            ).fetchone()
        return row is not None

    def revoke_participant_sessions(self, room_id: str, participant_id: str) -> int:
        clauses = ["participant_id = %s"]
        parameters: list[object] = [clean_room_text(participant_id, limit=128)]
        clean_room_id = clean_room_text(room_id, limit=128)
        if clean_room_id:
            clauses.append("room_id = %s")
            parameters.append(clean_room_id)
        with self._connections.connection() as connection, connection.transaction():
            cursor = connection.execute(
                f"DELETE FROM room_access_sessions WHERE {' AND '.join(clauses)}",
                tuple(parameters),
            )
            return int(cursor.rowcount)

    def revoke_room_sessions(self, room_id: str) -> int:
        with self._connections.connection() as connection, connection.transaction():
            cursor = connection.execute(
                "DELETE FROM room_access_sessions WHERE room_id = %s",
                (clean_room_text(room_id, limit=128),),
            )
            return int(cursor.rowcount)

    def list_sessions(self) -> list[tuple[str, dict[str, object]]]:
        with self._connections.connection() as connection:
            rows = connection.execute(
                """SELECT * FROM room_access_sessions
                   ORDER BY joined_at, token_fingerprint"""
            ).fetchall()
        return [
            (str(row["token_fingerprint"]), _session_from_row(row))
            for row in rows
        ]

    def create_admission_workflow(
        self,
        workflow_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        clean_id = clean_room_text(workflow_id, limit=128)
        if not clean_id:
            raise ValueError("admission workflow_id is required")
        created = validate_admission_workflow_record(
            {**record, "workflow_id": clean_id},
            workflow_id=clean_id,
        )
        with self._connections.connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO room_admission_workflows(
                       workflow_id, room_id, status, record_json, created_at, updated_at
                   ) VALUES(%s, %s, %s, %s, %s, %s)
                   ON CONFLICT(workflow_id) DO NOTHING""",
                _workflow_parameters(created),
            )
            row = connection.execute(
                "SELECT record_json FROM room_admission_workflows WHERE workflow_id = %s",
                (clean_id,),
            ).fetchone()
        return _workflow_from_row(row)

    def admission_workflow(self, workflow_id: str) -> dict[str, object] | None:
        with self._connections.connection() as connection:
            row = connection.execute(
                "SELECT record_json FROM room_admission_workflows WHERE workflow_id = %s",
                (clean_room_text(workflow_id, limit=128),),
            ).fetchone()
        return _workflow_from_row(row) if row else None

    def update_admission_workflow(
        self,
        workflow_id: str,
        updates: dict[str, object],
    ) -> dict[str, object]:
        clean_id = clean_room_text(workflow_id, limit=128)
        with self._connections.connection() as connection, connection.transaction():
            existing = connection.execute(
                """SELECT record_json FROM room_admission_workflows
                   WHERE workflow_id = %s FOR UPDATE""",
                (clean_id,),
            ).fetchone()
            if existing is None:
                raise ValueError("admission workflow was not found")
            updated = validate_admission_workflow_record(
                {**_workflow_from_row(existing), **updates, "workflow_id": clean_id},
                workflow_id=clean_id,
            )
            connection.execute(
                """UPDATE room_admission_workflows
                   SET room_id = %s, status = %s, record_json = %s, updated_at = %s
                   WHERE workflow_id = %s""",
                (
                    clean_room_text(updated.get("room_id"), limit=128),
                    clean_room_text(updated.get("status"), limit=64),
                    Jsonb(updated),
                    _as_datetime(updated.get("updated_at")),
                    clean_id,
                ),
            )
        return updated

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
        clean_workflow_id = clean_room_text(workflow_id, limit=128)
        clean_invite_id = clean_room_text(invite_id, limit=128)
        clean_nonce = clean_room_text(nonce_fingerprint, limit=128)
        with self._connections.connection() as connection, connection.transaction():
            row = connection.execute(
                """SELECT record_json FROM room_admission_workflows
                   WHERE workflow_id = %s FOR UPDATE""",
                (clean_workflow_id,),
            ).fetchone()
            if row is None:
                raise ValueError("admission workflow was not found")
            workflow = _workflow_from_row(row)
            if workflow.get("invite_consumed"):
                return "", workflow

            if reusable:
                invite = connection.execute(
                    """SELECT use_count, max_uses FROM room_invites
                       WHERE invite_id = %s FOR UPDATE""",
                    (clean_invite_id,),
                ).fetchone()
                if invite is None:
                    return "invite_not_found", workflow
                stored_max = int(invite["max_uses"])
                effective_max = stored_max if stored_max >= 0 else max(0, int(max_uses))
                if effective_max and int(invite["use_count"]) >= effective_max:
                    return "invite_use_limit_reached", workflow
                connection.execute(
                    "UPDATE room_invites SET use_count = use_count + 1 WHERE invite_id = %s",
                    (clean_invite_id,),
                )
            else:
                inserted = connection.execute(
                    """INSERT INTO room_invite_used_nonces(nonce_fingerprint, consumed_at)
                       VALUES(%s, %s) ON CONFLICT(nonce_fingerprint) DO NOTHING
                       RETURNING nonce_fingerprint""",
                    (clean_nonce, datetime.now(UTC)),
                ).fetchone()
                if inserted is None:
                    return "token_already_used", workflow

            updated = validate_admission_workflow_record(
                {
                    **workflow,
                    **updates,
                    "workflow_id": clean_workflow_id,
                    "invite_consumed": True,
                },
                workflow_id=clean_workflow_id,
            )
            connection.execute(
                """UPDATE room_admission_workflows
                   SET room_id = %s, status = %s, record_json = %s, updated_at = %s
                   WHERE workflow_id = %s""",
                (
                    clean_room_text(updated.get("room_id"), limit=128),
                    clean_room_text(updated.get("status"), limit=64),
                    Jsonb(updated),
                    _as_datetime(updated.get("updated_at")),
                    clean_workflow_id,
                ),
            )
            return "", updated

    def purge_admission_workflows(
        self,
        selection: AdmissionWorkflowSelection,
        *,
        apply: bool,
    ) -> PurgeReport:
        if not isinstance(selection, AdmissionWorkflowSelection):
            raise TypeError("selection must be an AdmissionWorkflowSelection")
        clauses = ["status = ANY(%s)"]
        parameters: list[object] = [sorted(selection.statuses)]
        if selection.room_id:
            clauses.append("room_id = %s")
            parameters.append(selection.room_id)
        if selection.updated_before is not None:
            clauses.append("updated_at < %s")
            parameters.append(selection.updated_before)
        query = (
            "SELECT record_json FROM room_admission_workflows WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at, workflow_id"
        )

        with self._connections.connection() as connection, connection.transaction():
            rows = connection.execute(
                query + (" FOR UPDATE" if apply else ""),
                tuple(parameters),
            ).fetchall()
            selected = [
                record
                for record in (_workflow_from_row(row) for row in rows)
                if selection.matches(record)
            ]
            purged_count = 0
            if apply and selected:
                workflow_ids = [str(record["workflow_id"]) for record in selected]
                cursor = connection.execute(
                    "DELETE FROM room_admission_workflows WHERE workflow_id = ANY(%s)",
                    (workflow_ids,),
                )
                purged_count = int(cursor.rowcount)
        return build_purge_report(
            selection,
            selected,
            applied=apply,
            purged_count=purged_count,
        )

    def reload(self) -> None:
        return

    def clear(self) -> None:
        with self._connections.connection() as connection, connection.transaction():
            connection.execute(
                """TRUNCATE TABLE room_admission_workflows,
                       room_access_sessions,
                       room_invite_used_nonces, room_invites,
                       room_invite_authority"""
            )

    def close(self) -> None:
        if self._owned_pool is not None:
            self._owned_pool.close()

    def public_diagnostics(self) -> dict[str, object]:
        provider_diagnostics = self._connections.public_diagnostics()
        return {
            "backend": "postgresql",
            "pool": provider_diagnostics.get("pool", provider_diagnostics),
        }


def _invite_parameters(record: dict[str, object]) -> tuple[object, ...]:
    return (
        clean_room_text(record.get("invite_id"), limit=128),
        clean_room_text(record.get("meeting_id"), limit=128),
        clean_room_text(record.get("agent_id"), limit=64),
        clean_room_text(record.get("display_name"), limit=128),
        clean_room_text(record.get("invite_scope"), limit=32) or "room",
        clean_room_text(record.get("participant_type"), limit=32) or "human",
        clean_room_text(record.get("client_type"), limit=32) or "browser",
        clean_room_text(record.get("provider_kind"), limit=64) or "manual",
        clean_room_text(record.get("created_by_user_id"), limit=128),
        clean_room_text(record.get("join_code_fingerprint"), limit=128),
        clean_room_text(record.get("join_nonce"), limit=128),
        clean_room_text(record.get("permission_mode"), limit=64) or "participant",
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
        clean_room_text(token_fingerprint, limit=128),
        clean_room_text(record.get("meeting_id"), limit=128),
        clean_room_text(record.get("agent_id"), limit=128),
        clean_room_text(record.get("display_name"), limit=128),
        clean_room_text(record.get("invite_scope"), limit=32) or "room",
        clean_room_text(record.get("participant_type"), limit=32) or "human",
        clean_room_text(record.get("client_type"), limit=32) or "browser",
        clean_room_text(record.get("provider_kind"), limit=64) or "manual",
        clean_room_text(record.get("owner_id"), limit=128),
        clean_room_text(record.get("connection_kind"), limit=64),
        _as_datetime(record.get("joined_at")),
        _as_datetime(record.get("expires_at")),
    )


def _workflow_parameters(record: dict[str, object]) -> tuple[object, ...]:
    return (
        clean_room_text(record.get("workflow_id"), limit=128),
        clean_room_text(record.get("room_id"), limit=128),
        clean_room_text(record.get("status"), limit=64),
        Jsonb(record),
        _as_datetime(record.get("created_at")),
        _as_datetime(record.get("updated_at")),
    )


def _workflow_from_row(row: dict[str, object]) -> dict[str, object]:
    value = row.get("record_json") if row else {}
    return dict(value) if isinstance(value, dict) else {}


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
