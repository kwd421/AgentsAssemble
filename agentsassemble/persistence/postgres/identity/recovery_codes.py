"""PostgreSQL persistence for one-time guest identity recovery codes."""
from __future__ import annotations

from psycopg import Connection

from agentsassemble.persistence.postgres.identity.users import user_from_row


def create_recovery_code(
    connection: Connection,
    *,
    user_id: str,
    token_fingerprint: str,
    created_at: str,
) -> None:
    if connection.execute(
        "SELECT 1 FROM identity_users WHERE user_id = %s", (user_id,)
    ).fetchone() is None:
        raise ValueError("Recovery user was not found.")
    connection.execute(
        """UPDATE identity_recovery_codes SET revoked_at = %s
           WHERE user_id = %s AND consumed_at = '' AND revoked_at = ''""",
        (created_at, user_id),
    )
    connection.execute(
        """INSERT INTO identity_recovery_codes(
               token_fingerprint, user_id, created_at
           ) VALUES(%s, %s, %s)""",
        (token_fingerprint, user_id, created_at),
    )


def recovery_code_user(
    connection: Connection,
    token_fingerprint: str,
) -> dict[str, object] | None:
    row = connection.execute(
        """SELECT users.* FROM identity_recovery_codes codes
           JOIN identity_users users ON users.user_id = codes.user_id
           WHERE codes.token_fingerprint = %s
             AND codes.consumed_at = '' AND codes.revoked_at = ''""",
        (token_fingerprint,),
    ).fetchone()
    return user_from_row(row) if row else None


def consume_recovery_code(
    connection: Connection,
    *,
    token_fingerprint: str,
    auth_key: str,
    replacement_fingerprint: str,
    used_at: str,
) -> dict[str, object] | None:
    code = connection.execute(
        """SELECT user_id FROM identity_recovery_codes
           WHERE token_fingerprint = %s AND consumed_at = '' AND revoked_at = ''
           FOR UPDATE""",
        (token_fingerprint,),
    ).fetchone()
    if code is None:
        return None
    user_id = str(code["user_id"])
    credential = connection.execute(
        "SELECT user_id FROM identity_credentials WHERE auth_key = %s FOR UPDATE",
        (auth_key,),
    ).fetchone()
    if credential is not None and str(credential["user_id"]) != user_id:
        raise ValueError("This device credential belongs to another identity.")
    connection.execute(
        """UPDATE identity_recovery_codes
           SET consumed_at = %s, replacement_fingerprint = %s
           WHERE token_fingerprint = %s""",
        (used_at, replacement_fingerprint, token_fingerprint),
    )
    connection.execute(
        """INSERT INTO identity_credentials(
               auth_key, user_id, provider, created_at, last_used_at
           ) VALUES(%s, %s, 'device', %s, %s)
           ON CONFLICT(auth_key) DO UPDATE SET last_used_at = excluded.last_used_at""",
        (auth_key, user_id, used_at, used_at),
    )
    connection.execute(
        """INSERT INTO identity_recovery_codes(
               token_fingerprint, user_id, created_at
           ) VALUES(%s, %s, %s)""",
        (replacement_fingerprint, user_id, used_at),
    )
    return recovery_code_user(connection, replacement_fingerprint)


class PostgresRecoveryCodesMixin:
    def create_recovery_code(
        self,
        *,
        user_id: str,
        token_fingerprint: str,
        created_at: str,
    ) -> None:
        with self._connections.connection() as connection, connection.transaction():
            create_recovery_code(
                connection,
                user_id=user_id,
                token_fingerprint=token_fingerprint,
                created_at=created_at,
            )

    def recovery_code_user(self, token_fingerprint: str) -> dict[str, object] | None:
        with self._connections.connection() as connection:
            return recovery_code_user(connection, token_fingerprint)

    def consume_recovery_code(
        self,
        *,
        token_fingerprint: str,
        auth_key: str,
        replacement_fingerprint: str,
        used_at: str,
    ) -> dict[str, object] | None:
        with self._connections.connection() as connection, connection.transaction():
            return consume_recovery_code(
                connection,
                token_fingerprint=token_fingerprint,
                auth_key=auth_key,
                replacement_fingerprint=replacement_fingerprint,
                used_at=used_at,
            )


__all__ = ["PostgresRecoveryCodesMixin"]
