"""SQLite persistence for one-time guest identity recovery codes."""
from __future__ import annotations

import sqlite3
from contextlib import closing


def ensure_recovery_code_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """CREATE TABLE IF NOT EXISTS identity_recovery_codes (
               token_fingerprint TEXT PRIMARY KEY,
               user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
               created_at TEXT NOT NULL,
               consumed_at TEXT NOT NULL DEFAULT '',
               revoked_at TEXT NOT NULL DEFAULT '',
               replacement_fingerprint TEXT NOT NULL DEFAULT ''
           );
           CREATE INDEX IF NOT EXISTS idx_identity_recovery_user
           ON identity_recovery_codes(user_id, created_at);"""
    )


def create_recovery_code(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    token_fingerprint: str,
    created_at: str,
) -> None:
    if connection.execute(
        "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
    ).fetchone() is None:
        raise ValueError("Recovery user was not found.")
    connection.execute(
        """UPDATE identity_recovery_codes SET revoked_at = ?
           WHERE user_id = ? AND consumed_at = '' AND revoked_at = ''""",
        (created_at, user_id),
    )
    connection.execute(
        """INSERT INTO identity_recovery_codes(
               token_fingerprint, user_id, created_at
           ) VALUES(?, ?, ?)""",
        (token_fingerprint, user_id, created_at),
    )


def recovery_code_user(
    connection: sqlite3.Connection,
    token_fingerprint: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT users.* FROM identity_recovery_codes
           JOIN users ON users.user_id = identity_recovery_codes.user_id
           WHERE token_fingerprint = ? AND consumed_at = '' AND revoked_at = ''""",
        (token_fingerprint,),
    ).fetchone()


def consume_recovery_code(
    connection: sqlite3.Connection,
    *,
    token_fingerprint: str,
    auth_key: str,
    replacement_fingerprint: str,
    used_at: str,
) -> sqlite3.Row | None:
    code = connection.execute(
        """SELECT user_id FROM identity_recovery_codes
           WHERE token_fingerprint = ? AND consumed_at = '' AND revoked_at = ''""",
        (token_fingerprint,),
    ).fetchone()
    if code is None:
        return None
    user_id = str(code["user_id"])
    credential = connection.execute(
        "SELECT user_id FROM credentials WHERE auth_key = ?", (auth_key,)
    ).fetchone()
    if credential is not None and str(credential["user_id"]) != user_id:
        raise ValueError("This device credential belongs to another identity.")
    cursor = connection.execute(
        """UPDATE identity_recovery_codes
           SET consumed_at = ?, replacement_fingerprint = ?
           WHERE token_fingerprint = ? AND consumed_at = '' AND revoked_at = ''""",
        (used_at, replacement_fingerprint, token_fingerprint),
    )
    if cursor.rowcount != 1:
        return None
    connection.execute(
        """INSERT INTO credentials(auth_key, user_id, provider, created_at, last_used_at)
           VALUES(?, ?, 'device', ?, ?)
           ON CONFLICT(auth_key) DO UPDATE SET last_used_at = excluded.last_used_at""",
        (auth_key, user_id, used_at, used_at),
    )
    connection.execute(
        """INSERT INTO identity_recovery_codes(
               token_fingerprint, user_id, created_at
           ) VALUES(?, ?, ?)""",
        (replacement_fingerprint, user_id, used_at),
    )
    return connection.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()


class SqliteRecoveryCodesMixin:
    """Repository facade for recovery-code operations."""

    def create_recovery_code(
        self,
        *,
        user_id: str,
        token_fingerprint: str,
        created_at: str,
    ) -> None:
        with self._write_lock, closing(self._connect()) as connection, connection:
            create_recovery_code(
                connection,
                user_id=user_id,
                token_fingerprint=token_fingerprint,
                created_at=created_at,
            )

    def recovery_code_user(self, token_fingerprint: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = recovery_code_user(connection, token_fingerprint)
        return self._user_dict(row) if row else None

    def consume_recovery_code(
        self,
        *,
        token_fingerprint: str,
        auth_key: str,
        replacement_fingerprint: str,
        used_at: str,
    ) -> dict[str, object] | None:
        with self._write_lock, closing(self._connect()) as connection, connection:
            row = consume_recovery_code(
                connection,
                token_fingerprint=token_fingerprint,
                auth_key=auth_key,
                replacement_fingerprint=replacement_fingerprint,
                used_at=used_at,
            )
        return self._user_dict(row) if row else None


__all__ = [
    "consume_recovery_code",
    "create_recovery_code",
    "ensure_recovery_code_schema",
    "recovery_code_user",
    "SqliteRecoveryCodesMixin",
]
