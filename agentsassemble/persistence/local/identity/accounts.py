"""SQLite persistence for public accounts linked to server-local users."""
from __future__ import annotations

import sqlite3
from contextlib import closing

from agentsassemble.identity.accounts import AccountLinkConflict
from agentsassemble.room.text import clean_room_text


def ensure_account_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """CREATE TABLE IF NOT EXISTS accounts (
               account_id TEXT PRIMARY KEY,
               display_name TEXT NOT NULL DEFAULT '',
               email TEXT NOT NULL DEFAULT '',
               avatar_image_url TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           );
           CREATE TABLE IF NOT EXISTS external_account_identities (
               provider TEXT NOT NULL,
               subject_fingerprint TEXT NOT NULL,
               account_id TEXT NOT NULL UNIQUE
                   REFERENCES accounts(account_id) ON DELETE CASCADE,
               connected_at TEXT NOT NULL,
               PRIMARY KEY(provider, subject_fingerprint)
           );
           CREATE TABLE IF NOT EXISTS user_accounts (
               user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
               account_id TEXT NOT NULL UNIQUE
                   REFERENCES accounts(account_id) ON DELETE CASCADE,
               linked_at TEXT NOT NULL
           );"""
    )


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


def _account_row(connection: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT accounts.*, user_accounts.user_id,
                  external_account_identities.provider
           FROM user_accounts
           JOIN accounts ON accounts.account_id = user_accounts.account_id
           JOIN external_account_identities
             ON external_account_identities.account_id = accounts.account_id
           WHERE user_accounts.user_id = ?""",
        (user_id,),
    ).fetchone()


def connect_external_account(
    connection: sqlite3.Connection,
    user_id: str,
    *,
    account_id: object,
    provider: object,
    subject_fingerprint: object,
    display_name: object = "",
    email: object = "",
    avatar_image_url: object = "",
    connected_at: object,
) -> sqlite3.Row:
    clean_user_id = clean_room_text(user_id, limit=128)
    clean_account_id, clean_provider, clean_subject, clean_connected_at = _clean_link_values(
        account_id=account_id,
        provider=provider,
        subject_fingerprint=subject_fingerprint,
        connected_at=connected_at,
    )
    if connection.execute(
        "SELECT 1 FROM users WHERE user_id = ?", (clean_user_id,)
    ).fetchone() is None:
        raise ValueError("Account user was not found.")

    subject_owner = connection.execute(
        """SELECT links.user_id, identities.account_id
           FROM external_account_identities identities
           LEFT JOIN user_accounts links ON links.account_id = identities.account_id
           WHERE identities.provider = ? AND identities.subject_fingerprint = ?""",
        (clean_provider, clean_subject),
    ).fetchone()
    if subject_owner is not None and (
        str(subject_owner["account_id"]) != clean_account_id
        or str(subject_owner["user_id"] or "") not in {"", clean_user_id}
    ):
        raise AccountLinkConflict("This external account is linked to another user.")

    user_link = connection.execute(
        "SELECT account_id FROM user_accounts WHERE user_id = ?",
        (clean_user_id,),
    ).fetchone()
    if user_link is not None and str(user_link["account_id"]) != clean_account_id:
        raise AccountLinkConflict("This user is linked to another public account.")

    account_link = connection.execute(
        "SELECT user_id FROM user_accounts WHERE account_id = ?",
        (clean_account_id,),
    ).fetchone()
    if account_link is not None and str(account_link["user_id"]) != clean_user_id:
        raise AccountLinkConflict("This public account is linked to another user.")

    clean_name = clean_room_text(display_name, limit=120)
    clean_email = clean_room_text(email, limit=320)
    clean_avatar = clean_room_text(avatar_image_url, limit=2048)
    connection.execute(
        """INSERT INTO accounts(
               account_id, display_name, email, avatar_image_url, created_at, updated_at
           ) VALUES(?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET
               display_name = CASE WHEN excluded.display_name != ''
                   THEN excluded.display_name ELSE accounts.display_name END,
               email = CASE WHEN excluded.email != ''
                   THEN excluded.email ELSE accounts.email END,
               avatar_image_url = CASE WHEN excluded.avatar_image_url != ''
                   THEN excluded.avatar_image_url ELSE accounts.avatar_image_url END,
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
        """INSERT INTO external_account_identities(
               provider, subject_fingerprint, account_id, connected_at
           ) VALUES(?, ?, ?, ?)
           ON CONFLICT(provider, subject_fingerprint) DO UPDATE SET
               connected_at = excluded.connected_at""",
        (clean_provider, clean_subject, clean_account_id, clean_connected_at),
    )
    connection.execute(
        """INSERT INTO user_accounts(user_id, account_id, linked_at)
           VALUES(?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET linked_at = excluded.linked_at""",
        (clean_user_id, clean_account_id, clean_connected_at),
    )
    row = _account_row(connection, clean_user_id)
    if row is None:
        raise RuntimeError("External account link was not persisted.")
    return row


def external_account_for_user(
    connection: sqlite3.Connection,
    user_id: str,
) -> sqlite3.Row | None:
    return _account_row(connection, clean_room_text(user_id, limit=128))


def user_for_external_account(
    connection: sqlite3.Connection,
    provider: object,
    subject_fingerprint: object,
) -> sqlite3.Row | None:
    clean_provider = clean_room_text(provider, limit=32).lower()
    clean_subject = clean_room_text(subject_fingerprint, limit=128)
    if not clean_provider or not clean_subject:
        return None
    return connection.execute(
        """SELECT users.* FROM external_account_identities identities
           JOIN user_accounts links ON links.account_id = identities.account_id
           JOIN users ON users.user_id = links.user_id
           WHERE identities.provider = ? AND identities.subject_fingerprint = ?""",
        (clean_provider, clean_subject),
    ).fetchone()


def bind_credential_to_user(
    connection: sqlite3.Connection,
    user_id: str,
    *,
    auth_key: object,
    provider: object,
    used_at: object,
) -> sqlite3.Row:
    clean_user_id = clean_room_text(user_id, limit=128)
    clean_auth_key = clean_room_text(auth_key, limit=128)
    clean_provider = clean_room_text(provider, limit=32) or "device"
    clean_used_at = clean_room_text(used_at, limit=64)
    if not clean_user_id or not clean_auth_key or not clean_used_at:
        raise ValueError("Credential binding is incomplete.")
    if connection.execute(
        "SELECT 1 FROM users WHERE user_id = ?", (clean_user_id,)
    ).fetchone() is None:
        raise ValueError("Credential user was not found.")
    owner = connection.execute(
        "SELECT user_id FROM credentials WHERE auth_key = ?", (clean_auth_key,)
    ).fetchone()
    if owner is not None and str(owner["user_id"]) != clean_user_id:
        raise AccountLinkConflict("This device credential belongs to another user.")
    connection.execute(
        """INSERT INTO credentials(auth_key, user_id, provider, created_at, last_used_at)
           VALUES(?, ?, ?, ?, ?)
           ON CONFLICT(auth_key) DO UPDATE SET
               provider = excluded.provider,
               last_used_at = excluded.last_used_at""",
        (clean_auth_key, clean_user_id, clean_provider, clean_used_at, clean_used_at),
    )
    connection.execute(
        "UPDATE users SET last_seen_at = ? WHERE user_id = ?",
        (clean_used_at, clean_user_id),
    )
    row = connection.execute(
        "SELECT * FROM users WHERE user_id = ?", (clean_user_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Credential user disappeared during binding.")
    return row


class SqliteAccountsMixin:
    def external_account_for_user(self, user_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = external_account_for_user(connection, user_id)
        return dict(row) if row else None

    def user_for_external_account(
        self,
        provider: str,
        subject_fingerprint: str,
    ) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = user_for_external_account(connection, provider, subject_fingerprint)
        return self._user_dict(row) if row else None

    def connect_external_account(self, user_id: str, **account: object) -> dict[str, object]:
        with self._write_lock, closing(self._connect()) as connection, connection:
            row = connect_external_account(connection, user_id, **account)
        return dict(row)

    def bind_credential_to_user(self, user_id: str, **credential: object) -> dict[str, object]:
        with self._write_lock, closing(self._connect()) as connection, connection:
            row = bind_credential_to_user(connection, user_id, **credential)
        return self._user_dict(row)


__all__ = ["SqliteAccountsMixin", "ensure_account_schema"]
