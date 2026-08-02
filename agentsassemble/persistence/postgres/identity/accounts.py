"""PostgreSQL persistence for public accounts linked to server-local users."""
from __future__ import annotations

from psycopg import Connection

from agentsassemble.identity.accounts import AccountLinkConflict
from agentsassemble.persistence.postgres.identity.users import user_from_row
from agentsassemble.room.text import clean_room_text


def _clean_link_values(
    *,
    account_id: object,
    provider: object,
    subject_fingerprint: object,
    connected_at: object,
) -> tuple[str, str, str, str]:
    values = (
        clean_room_text(account_id, limit=64),
        clean_room_text(provider, limit=32).lower(),
        clean_room_text(subject_fingerprint, limit=128),
        clean_room_text(connected_at, limit=64),
    )
    if not all(values):
        raise ValueError("External account link is incomplete.")
    return values


def _account_row(connection: Connection, user_id: str) -> dict[str, object] | None:
    row = connection.execute(
        """SELECT accounts.*, links.user_id, identities.provider
           FROM identity_user_accounts links
           JOIN identity_accounts accounts ON accounts.account_id = links.account_id
           JOIN identity_external_accounts identities
             ON identities.account_id = accounts.account_id
           WHERE links.user_id = %s""",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def connect_external_account(
    connection: Connection,
    user_id: str,
    *,
    account_id: object,
    provider: object,
    subject_fingerprint: object,
    display_name: object = "",
    email: object = "",
    avatar_image_url: object = "",
    connected_at: object,
) -> dict[str, object]:
    clean_user_id = clean_room_text(user_id, limit=128)
    clean_account_id, clean_provider, clean_subject, clean_connected_at = _clean_link_values(
        account_id=account_id,
        provider=provider,
        subject_fingerprint=subject_fingerprint,
        connected_at=connected_at,
    )
    for lock_key in (
        f"external:{clean_provider}:{clean_subject}",
        f"account:{clean_account_id}",
        f"account-user:{clean_user_id}",
    ):
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (lock_key,),
        )
    if connection.execute(
        "SELECT 1 FROM identity_users WHERE user_id = %s", (clean_user_id,)
    ).fetchone() is None:
        raise ValueError("Account user was not found.")

    subject_owner = connection.execute(
        """SELECT links.user_id, identities.account_id
           FROM identity_external_accounts identities
           LEFT JOIN identity_user_accounts links ON links.account_id = identities.account_id
           WHERE identities.provider = %s AND identities.subject_fingerprint = %s
           FOR UPDATE OF identities""",
        (clean_provider, clean_subject),
    ).fetchone()
    if subject_owner is not None and (
        str(subject_owner["account_id"]) != clean_account_id
        or str(subject_owner["user_id"] or "") not in {"", clean_user_id}
    ):
        raise AccountLinkConflict("This external account is linked to another user.")

    user_link = connection.execute(
        "SELECT account_id FROM identity_user_accounts WHERE user_id = %s FOR UPDATE",
        (clean_user_id,),
    ).fetchone()
    if user_link is not None and str(user_link["account_id"]) != clean_account_id:
        raise AccountLinkConflict("This user is linked to another public account.")

    account_link = connection.execute(
        "SELECT user_id FROM identity_user_accounts WHERE account_id = %s FOR UPDATE",
        (clean_account_id,),
    ).fetchone()
    if account_link is not None and str(account_link["user_id"]) != clean_user_id:
        raise AccountLinkConflict("This public account is linked to another user.")

    clean_name = clean_room_text(display_name, limit=120)
    clean_email = clean_room_text(email, limit=320)
    clean_avatar = clean_room_text(avatar_image_url, limit=2048)
    connection.execute(
        """INSERT INTO identity_accounts(
               account_id, display_name, email, avatar_image_url, created_at, updated_at
           ) VALUES(%s, %s, %s, %s, %s, %s)
           ON CONFLICT(account_id) DO UPDATE SET
               display_name = CASE WHEN excluded.display_name != ''
                   THEN excluded.display_name ELSE identity_accounts.display_name END,
               email = CASE WHEN excluded.email != ''
                   THEN excluded.email ELSE identity_accounts.email END,
               avatar_image_url = CASE WHEN excluded.avatar_image_url != ''
                   THEN excluded.avatar_image_url ELSE identity_accounts.avatar_image_url END,
               updated_at = excluded.updated_at""",
        (
            clean_account_id,
            clean_name,
            clean_email,
            clean_avatar,
            clean_connected_at,
            clean_connected_at,
        ),
    )
    connection.execute(
        """INSERT INTO identity_external_accounts(
               provider, subject_fingerprint, account_id, connected_at
           ) VALUES(%s, %s, %s, %s)
           ON CONFLICT(provider, subject_fingerprint) DO UPDATE SET
               connected_at = excluded.connected_at""",
        (clean_provider, clean_subject, clean_account_id, clean_connected_at),
    )
    connection.execute(
        """INSERT INTO identity_user_accounts(user_id, account_id, linked_at)
           VALUES(%s, %s, %s)
           ON CONFLICT(user_id) DO UPDATE SET linked_at = excluded.linked_at""",
        (clean_user_id, clean_account_id, clean_connected_at),
    )
    row = _account_row(connection, clean_user_id)
    if row is None:
        raise RuntimeError("External account link was not persisted.")
    return row


def external_account_for_user(
    connection: Connection,
    user_id: str,
) -> dict[str, object] | None:
    return _account_row(connection, clean_room_text(user_id, limit=128))


def disconnect_external_account(
    connection: Connection,
    user_id: str,
) -> bool:
    clean_user_id = clean_room_text(user_id, limit=128)
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (f"account-user:{clean_user_id}",),
    )
    link = connection.execute(
        "SELECT account_id FROM identity_user_accounts WHERE user_id = %s FOR UPDATE",
        (clean_user_id,),
    ).fetchone()
    if link is None:
        return False
    connection.execute(
        "DELETE FROM identity_accounts WHERE account_id = %s",
        (str(link["account_id"]),),
    )
    return True


def user_for_external_account(
    connection: Connection,
    provider: object,
    subject_fingerprint: object,
) -> dict[str, object] | None:
    clean_provider = clean_room_text(provider, limit=32).lower()
    clean_subject = clean_room_text(subject_fingerprint, limit=128)
    if not clean_provider or not clean_subject:
        return None
    row = connection.execute(
        """SELECT users.* FROM identity_external_accounts identities
           JOIN identity_user_accounts links ON links.account_id = identities.account_id
           JOIN identity_users users ON users.user_id = links.user_id
           WHERE identities.provider = %s AND identities.subject_fingerprint = %s""",
        (clean_provider, clean_subject),
    ).fetchone()
    return user_from_row(row) if row else None


def bind_credential_to_user(
    connection: Connection,
    user_id: str,
    *,
    auth_key: object,
    provider: object,
    used_at: object,
) -> dict[str, object]:
    clean_user_id = clean_room_text(user_id, limit=128)
    clean_auth_key = clean_room_text(auth_key, limit=128)
    clean_provider = clean_room_text(provider, limit=32) or "device"
    clean_used_at = clean_room_text(used_at, limit=64)
    if not clean_user_id or not clean_auth_key or not clean_used_at:
        raise ValueError("Credential binding is incomplete.")
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))", (clean_auth_key,)
    )
    if connection.execute(
        "SELECT 1 FROM identity_users WHERE user_id = %s", (clean_user_id,)
    ).fetchone() is None:
        raise ValueError("Credential user was not found.")
    owner = connection.execute(
        "SELECT user_id FROM identity_credentials WHERE auth_key = %s FOR UPDATE",
        (clean_auth_key,),
    ).fetchone()
    if owner is not None and str(owner["user_id"]) != clean_user_id:
        raise AccountLinkConflict("This device credential belongs to another user.")
    connection.execute(
        """INSERT INTO identity_credentials(
               auth_key, user_id, provider, created_at, last_used_at
           ) VALUES(%s, %s, %s, %s, %s)
           ON CONFLICT(auth_key) DO UPDATE SET
               provider = excluded.provider,
               last_used_at = excluded.last_used_at""",
        (clean_auth_key, clean_user_id, clean_provider, clean_used_at, clean_used_at),
    )
    connection.execute(
        "UPDATE identity_users SET last_seen_at = %s WHERE user_id = %s",
        (clean_used_at, clean_user_id),
    )
    row = connection.execute(
        "SELECT * FROM identity_users WHERE user_id = %s", (clean_user_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Credential user disappeared during binding.")
    return user_from_row(row)


class PostgresAccountsMixin:
    def external_account_for_user(self, user_id: str) -> dict[str, object] | None:
        with self._connections.connection() as connection:
            return external_account_for_user(connection, user_id)

    def user_for_external_account(
        self,
        provider: str,
        subject_fingerprint: str,
    ) -> dict[str, object] | None:
        with self._connections.connection() as connection:
            return user_for_external_account(connection, provider, subject_fingerprint)

    def connect_external_account(self, user_id: str, **account: object) -> dict[str, object]:
        with self._connections.connection() as connection, connection.transaction():
            return connect_external_account(connection, user_id, **account)

    def disconnect_external_account(self, user_id: str) -> bool:
        with self._connections.connection() as connection, connection.transaction():
            return disconnect_external_account(connection, user_id)

    def bind_credential_to_user(self, user_id: str, **credential: object) -> dict[str, object]:
        with self._connections.connection() as connection, connection.transaction():
            return bind_credential_to_user(connection, user_id, **credential)


__all__ = ["PostgresAccountsMixin"]
