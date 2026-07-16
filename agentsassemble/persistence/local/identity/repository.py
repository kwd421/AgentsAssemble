"""Local SQLite identity, roster, preference, and usage implementation.

One file (identity.db) holds the relational core that scattered JSON files
couldn't keep consistent:

- users        — one row per stable identity (human or AI), operator flag
- credentials  — login keys mapping to users ("device:<sha>" now, "google:<sub>" later)
- memberships  — per-room roster rows with mute/host moderation state;
                 PRIMARY KEY (meeting_id, participant_id) makes ghost
                 duplicates structurally impossible

Invite/session token state has its own repository contract because token use,
replay prevention, and revocation have a different lifetime from identity.
Hosted mode selects a PostgreSQL implementation of both contracts through the
same room repository settings; local mode keeps this SQLite database.

Concurrency: ThreadingHTTPServer calls from many threads; each operation opens
a short-lived connection (cheap for this size) and WAL mode keeps readers and
writers out of each other's way.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.identity.repository import (
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
    OPERATOR_PAIRING_REDEMPTION_STATUSES,
)
from agentsassemble.identity_room_preferences import (
    delete_room_preferences,
    ensure_room_preferences_schema,
    read_room_preferences,
    update_room_preferences,
)
from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.room_user_preferences import (
    RoomUserPreferencesRecord,
)

IDENTITY_DB_FILENAME = "identity.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    avatar_image_url TEXT NOT NULL DEFAULT '',
    participant_type TEXT NOT NULL DEFAULT 'human',
    auth_provider TEXT NOT NULL DEFAULT 'device',
    is_operator INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS credentials (
    auth_key TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'device',
    created_at TEXT NOT NULL DEFAULT '',
    last_used_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_credentials_user ON credentials(user_id);

CREATE TABLE IF NOT EXISTS operator_pairings (
    pairing_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    room_id TEXT NOT NULL,
    target_origin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT NOT NULL DEFAULT '',
    consumed_auth_key TEXT NOT NULL DEFAULT '',
    redemption_status TEXT NOT NULL DEFAULT 'ready',
    completed_at TEXT NOT NULL DEFAULT '',
    session_fingerprint TEXT NOT NULL DEFAULT '',
    failure_code TEXT NOT NULL DEFAULT '',
    revoked_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_operator_pairings_expiry
ON operator_pairings(expires_at);

CREATE TABLE IF NOT EXISTS memberships (
    meeting_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'agent',
    participant_type TEXT NOT NULL DEFAULT 'unknown',
    provider_kind TEXT NOT NULL DEFAULT '',
    connection_kind TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    muted INTEGER NOT NULL DEFAULT 0,
    is_host INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (meeting_id, participant_id)
);

CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    origin TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rooms_owner_active ON rooms(owner_id, last_active_at);
CREATE INDEX IF NOT EXISTS idx_rooms_active ON rooms(last_active_at);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    participant_id TEXT NOT NULL DEFAULT '',
    meeting_id TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    -- "byok" (user's key) / "free" (NVIDIA·OpenRouter) / "subscription" (we pay) / "local"
    cost_owner TEXT NOT NULL DEFAULT '',
    -- 1 when token counts are estimated (provider gave no usage), 0 when authoritative.
    -- (design consensus with Codex: trust provider usage, else estimate + flag)
    estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_meeting ON usage_events(meeting_id, created_at);
"""

_USER_FIELDS = (
    "user_id",
    "participant_id",
    "display_name",
    "avatar_image_url",
    "participant_type",
    "auth_provider",
    "is_operator",
    "created_at",
    "last_seen_at",
)

_MEMBERSHIP_FIELDS = (
    "meeting_id",
    "participant_id",
    "display_name",
    "role",
    "participant_type",
    "provider_kind",
    "connection_kind",
    "status",
    "muted",
    "is_host",
    "source",
    "created_at",
    "updated_at",
    "last_seen_at",
)

# Saved values never blank out an existing non-empty column on merge.
_MEMBERSHIP_MERGE_FIELDS = (
    "display_name",
    "role",
    "participant_type",
    "provider_kind",
    "connection_kind",
    "status",
    "source",
    "last_seen_at",
)

_ROOM_FIELDS = (
    "room_id",
    "owner_id",
    "label",
    "created_at",
    "last_active_at",
    "archived",
    "origin",
)

_OPERATOR_PAIRING_FIELDS = (
    "pairing_id",
    "token_fingerprint",
    "user_id",
    "room_id",
    "target_origin",
    "created_at",
    "expires_at",
    "used_at",
    "consumed_auth_key",
    "redemption_status",
    "completed_at",
    "session_fingerprint",
    "failure_code",
    "revoked_at",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class IdentityStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._write_lock = threading.Lock()
        self._ensure_schema()

    # -- plumbing -----------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock, closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            ensure_room_preferences_schema(connection)
            # Additive column migrations (CREATE TABLE IF NOT EXISTS won't add
            # columns to a pre-existing table). Idempotent: skip if present.
            self._ensure_column(connection, "usage_events", "estimated", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "rooms", "owner_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "rooms", "label", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "rooms", "created_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "rooms", "last_active_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "rooms", "archived", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "rooms", "origin", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                connection,
                "operator_pairings",
                "consumed_auth_key",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "operator_pairings",
                "redemption_status",
                "TEXT NOT NULL DEFAULT 'ready'",
            )
            self._ensure_column(
                connection,
                "operator_pairings",
                "completed_at",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "operator_pairings",
                "session_fingerprint",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "operator_pairings",
                "failure_code",
                "TEXT NOT NULL DEFAULT ''",
            )

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, decl: str) -> None:
        cols = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    @staticmethod
    def _user_dict(row: sqlite3.Row) -> dict[str, object]:
        user = {key: row[key] for key in _USER_FIELDS}
        user["is_operator"] = bool(user["is_operator"])
        return user

    @staticmethod
    def _membership_dict(row: sqlite3.Row) -> dict[str, object]:
        member = {key: row[key] for key in _MEMBERSHIP_FIELDS}
        member["muted"] = bool(member["muted"])
        member["is_host"] = bool(member["is_host"])
        return member

    @staticmethod
    def _room_dict(row: sqlite3.Row) -> dict[str, object]:
        room = {key: row[key] for key in _ROOM_FIELDS}
        room["archived"] = bool(room["archived"])
        return room

    @staticmethod
    def _operator_pairing_dict(row: sqlite3.Row) -> dict[str, object]:
        return {key: row[key] for key in _OPERATOR_PAIRING_FIELDS}

    def count_users(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def count_memberships(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM memberships").fetchone()[0])

    # -- users + credentials --------------------------------------------------
    def user_for_credential(self, auth_key: str) -> dict[str, object] | None:
        clean_key = clean_lobby_text(auth_key, limit=128)
        if not clean_key:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT u.* FROM users u JOIN credentials c ON c.user_id = u.user_id"
                " WHERE c.auth_key = ?",
                (clean_key,),
            ).fetchone()
        return self._user_dict(row) if row else None

    def get_user(self, user_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (str(user_id or ""),)
            ).fetchone()
        return self._user_dict(row) if row else None

    def user_for_participant(self, participant_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE participant_id = ?",
                (str(participant_id or ""),),
            ).fetchone()
        return self._user_dict(row) if row else None

    def resolve_credential_user(
        self,
        auth_key: str,
        *,
        provider: str = "device",
        user_id: str = "",
        participant_id: str = "",
        display_name: str = "",
        avatar_image_url: str = "",
        participant_type: str = "",
    ) -> dict[str, object] | None:
        """Return the stable user for this credential, creating/refreshing it."""
        clean_key = clean_lobby_text(auth_key, limit=128)
        if not clean_key:
            return None
        now = _now()
        clean_display_name = clean_lobby_text(display_name, limit=64)
        clean_avatar = clean_lobby_text(avatar_image_url, limit=2048)
        clean_type = clean_lobby_text(participant_type, limit=32).lower()
        with self._write_lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT u.* FROM users u JOIN credentials c ON c.user_id = u.user_id"
                " WHERE c.auth_key = ?",
                (clean_key,),
            ).fetchone()
            if row is None:
                new_user_id = str(user_id or "").strip() or f"u-{clean_key.split(':', 1)[-1][:12]}"
                new_participant_id = (
                    str(participant_id or "").strip() or f"guest-{clean_key.split(':', 1)[-1][:8]}"
                )
                connection.execute(
                    "INSERT INTO users (user_id, participant_id, display_name, avatar_image_url,"
                    " participant_type, auth_provider, created_at, last_seen_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_user_id,
                        new_participant_id,
                        clean_display_name,
                        clean_avatar,
                        clean_type or "human",
                        provider,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO credentials (auth_key, user_id, provider, created_at, last_used_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (clean_key, new_user_id, provider, now, now),
                )
            else:
                updates = {"last_seen_at": now}
                if clean_display_name:
                    updates["display_name"] = clean_display_name
                if clean_avatar:
                    updates["avatar_image_url"] = clean_avatar
                if clean_type:
                    updates["participant_type"] = clean_type
                assignments = ", ".join(f"{column} = ?" for column in updates)
                connection.execute(
                    f"UPDATE users SET {assignments} WHERE user_id = ?",
                    (*updates.values(), row["user_id"]),
                )
                connection.execute(
                    "UPDATE credentials SET last_used_at = ? WHERE auth_key = ?",
                    (now, clean_key),
                )
            refreshed = connection.execute(
                "SELECT u.* FROM users u JOIN credentials c ON c.user_id = u.user_id"
                " WHERE c.auth_key = ?",
                (clean_key,),
            ).fetchone()
        return self._user_dict(refreshed) if refreshed else None

    def set_user_operator(self, user_id: str, is_operator: bool) -> bool:
        """Mark a user as the room server's operator (host across devices)."""
        with self._write_lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE users SET is_operator = ? WHERE user_id = ?",
                (1 if is_operator else 0, str(user_id or "")),
            )
        return cursor.rowcount > 0

    def claim_local_operator_credential(
        self,
        auth_key: str,
        *,
        provider: str = "device",
        display_name: str = "",
    ) -> dict[str, object] | None:
        """Attach one host-authorized credential to the local operator.

        The host token is verified by the HTTP boundary before this method is
        called. Rebinding is therefore explicit; ordinary invite admission
        must continue to use ``resolve_credential_user`` and can never reach
        this path.

        Historical operator rows are preserved for audit/profile recovery, but
        they lose operator authority. Rooms owned by those legacy user IDs are
        reassigned to the canonical local operator in the same transaction.
        """
        clean_key = clean_lobby_text(auth_key, limit=128)
        if not clean_key:
            return None
        clean_provider = clean_lobby_text(provider, limit=32) or "device"
        clean_display_name = clean_lobby_text(display_name, limit=64)
        now = _now()
        with self._write_lock, closing(self._connect()) as connection, connection:
            refreshed = self._claim_local_operator_credential_in_connection(
                connection,
                auth_key=clean_key,
                provider=clean_provider,
                display_name=clean_display_name,
                now=now,
            )
        return self._user_dict(refreshed) if refreshed else None

    def _claim_local_operator_credential_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        auth_key: str,
        provider: str,
        display_name: str,
        now: str,
    ) -> sqlite3.Row:
        """Attach a credential inside the caller's identity transaction."""
        conflicting_user = connection.execute(
            "SELECT participant_id FROM users WHERE user_id = ?",
            (LOCAL_OPERATOR_USER_ID,),
        ).fetchone()
        if conflicting_user and conflicting_user["participant_id"] != LOCAL_OPERATOR_PARTICIPANT_ID:
            raise RuntimeError("The canonical operator user id is already assigned to another participant.")
        conflicting_participant = connection.execute(
            "SELECT user_id FROM users WHERE participant_id = ?",
            (LOCAL_OPERATOR_PARTICIPANT_ID,),
        ).fetchone()
        if conflicting_participant and conflicting_participant["user_id"] != LOCAL_OPERATOR_USER_ID:
            raise RuntimeError("The canonical operator participant id is already assigned to another user.")

        credential = connection.execute(
            "SELECT c.user_id, u.display_name FROM credentials c"
            " JOIN users u ON u.user_id = c.user_id WHERE c.auth_key = ?",
            (auth_key,),
        ).fetchone()
        legacy_operator_ids = [
            str(row["user_id"])
            for row in connection.execute(
                "SELECT user_id FROM users WHERE is_operator = 1 AND user_id != ?",
                (LOCAL_OPERATOR_USER_ID,),
            ).fetchall()
        ]
        inherited_display_name = display_name or (
            str(credential["display_name"] or "") if credential else ""
        )
        canonical = connection.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (LOCAL_OPERATOR_USER_ID,),
        ).fetchone()
        if canonical is None:
            connection.execute(
                "INSERT INTO users (user_id, participant_id, display_name, avatar_image_url,"
                " participant_type, auth_provider, is_operator, created_at, last_seen_at)"
                " VALUES (?, ?, ?, '', 'human', ?, 1, ?, ?)",
                (
                    LOCAL_OPERATOR_USER_ID,
                    LOCAL_OPERATOR_PARTICIPANT_ID,
                    inherited_display_name,
                    provider,
                    now,
                    now,
                ),
            )
        else:
            updates: dict[str, object] = {"is_operator": 1, "last_seen_at": now}
            if inherited_display_name:
                updates["display_name"] = inherited_display_name
            assignments = ", ".join(f"{column} = ?" for column in updates)
            connection.execute(
                f"UPDATE users SET {assignments} WHERE user_id = ?",
                (*updates.values(), LOCAL_OPERATOR_USER_ID),
            )

        connection.execute(
            "UPDATE users SET is_operator = 0 WHERE user_id != ? AND is_operator = 1",
            (LOCAL_OPERATOR_USER_ID,),
        )
        if legacy_operator_ids:
            placeholders = ", ".join("?" for _ in legacy_operator_ids)
            connection.execute(
                f"UPDATE rooms SET owner_id = ? WHERE owner_id IN ({placeholders})",
                (LOCAL_OPERATOR_USER_ID, *legacy_operator_ids),
            )

        if credential is None:
            connection.execute(
                "INSERT INTO credentials (auth_key, user_id, provider, created_at, last_used_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (auth_key, LOCAL_OPERATOR_USER_ID, provider, now, now),
            )
        else:
            connection.execute(
                "UPDATE credentials SET user_id = ?, provider = ?, last_used_at = ?"
                " WHERE auth_key = ?",
                (LOCAL_OPERATOR_USER_ID, provider, now, auth_key),
            )
        return connection.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (LOCAL_OPERATOR_USER_ID,),
        ).fetchone()

    def create_operator_pairing(
        self,
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
        with self._write_lock, closing(self._connect()) as connection, connection:
            operator = connection.execute(
                "SELECT user_id FROM users WHERE user_id = ? AND participant_id = ? AND is_operator = 1",
                (LOCAL_OPERATOR_USER_ID, LOCAL_OPERATOR_PARTICIPANT_ID),
            ).fetchone()
            if operator is None:
                raise ValueError("canonical operator identity is not claimed")
            connection.execute(
                "UPDATE operator_pairings SET revoked_at = ?"
                " WHERE user_id = ? AND room_id = ? AND target_origin = ?"
                " AND used_at = '' AND revoked_at = ''",
                (created_at, LOCAL_OPERATOR_USER_ID, clean_room_id, clean_origin),
            )
            connection.execute(
                "INSERT INTO operator_pairings"
                " (pairing_id, token_fingerprint, user_id, room_id, target_origin, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
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
                "SELECT * FROM operator_pairings WHERE pairing_id = ?",
                (clean_pairing_id,),
            ).fetchone()
        return self._operator_pairing_dict(row)

    def operator_pairing_for_fingerprint(
        self,
        token_fingerprint: str,
    ) -> dict[str, object] | None:
        clean_fingerprint = clean_lobby_text(token_fingerprint, limit=128)
        if not clean_fingerprint:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM operator_pairings WHERE token_fingerprint = ?",
                (clean_fingerprint,),
            ).fetchone()
        return self._operator_pairing_dict(row) if row else None

    def consume_operator_pairing(
        self,
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
        with self._write_lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM operator_pairings WHERE token_fingerprint = ?",
                (clean_fingerprint,),
            ).fetchone()
            if row is None:
                return {"status": "invalid"}
            pairing = self._operator_pairing_dict(row)
            if pairing["target_origin"] != clean_origin:
                return {"status": "origin_mismatch"}
            if pairing["revoked_at"]:
                return {"status": "revoked"}
            if pairing["used_at"]:
                if pairing["consumed_auth_key"] != clean_auth_key:
                    return {"status": "already_used"}
                user = connection.execute(
                    "SELECT * FROM users WHERE user_id = ?",
                    (LOCAL_OPERATOR_USER_ID,),
                ).fetchone()
                if user is None:
                    return {"status": "invalid"}
                return {
                    "status": "resumed",
                    "pairing": pairing,
                    "user": self._user_dict(user),
                }
            try:
                expires_at = datetime.fromisoformat(str(pairing["expires_at"]))
                used_at_value = datetime.fromisoformat(clean_used_at)
            except ValueError:
                return {"status": "invalid"}
            if expires_at <= used_at_value:
                return {"status": "expired"}
            cursor = connection.execute(
                "UPDATE operator_pairings SET used_at = ?, consumed_auth_key = ?,"
                " redemption_status = 'claiming', failure_code = ''"
                " WHERE pairing_id = ? AND used_at = '' AND revoked_at = ''",
                (clean_used_at, clean_auth_key, pairing["pairing_id"]),
            )
            if cursor.rowcount != 1:
                return {"status": "already_used"}
            user = self._claim_local_operator_credential_in_connection(
                connection,
                auth_key=clean_auth_key,
                provider="device",
                display_name="",
                now=clean_used_at,
            )
            updated = connection.execute(
                "SELECT * FROM operator_pairings WHERE pairing_id = ?",
                (pairing["pairing_id"],),
            ).fetchone()
        return {
            "status": "consumed",
            "pairing": self._operator_pairing_dict(updated),
            "user": self._user_dict(user),
        }

    def update_operator_pairing_redemption(
        self,
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
        with self._write_lock, closing(self._connect()) as connection, connection:
            pairing = connection.execute(
                "SELECT * FROM operator_pairings WHERE pairing_id = ?",
                (clean_pairing_id,),
            ).fetchone()
            if pairing is None or str(pairing["consumed_auth_key"] or "") != clean_auth_key:
                return None
            if pairing["redemption_status"] == "completed" and clean_status != "completed":
                return self._operator_pairing_dict(pairing)
            connection.execute(
                "UPDATE operator_pairings SET redemption_status = ?, completed_at = ?,"
                " session_fingerprint = ?, failure_code = ? WHERE pairing_id = ?"
                " AND consumed_auth_key = ?",
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
                "SELECT * FROM operator_pairings WHERE pairing_id = ?",
                (clean_pairing_id,),
            ).fetchone()
        return self._operator_pairing_dict(updated) if updated else None

    def revoke_operator_pairing(self, pairing_id: str, *, revoked_at: str) -> bool:
        clean_pairing_id = clean_lobby_text(pairing_id, limit=128)
        if not clean_pairing_id:
            return False
        with self._write_lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE operator_pairings SET revoked_at = ?"
                " WHERE pairing_id = ? AND used_at = '' AND revoked_at = ''",
                (clean_lobby_text(revoked_at, limit=64), clean_pairing_id),
            )
        return cursor.rowcount == 1

    def participant_is_operator(self, participant_id: str) -> bool:
        user = self.user_for_participant(participant_id)
        return bool(user and user.get("is_operator"))

    def operator_user_id(self) -> str:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id FROM users WHERE participant_id = ? AND is_operator = 1",
                (LOCAL_OPERATOR_PARTICIPANT_ID,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT user_id FROM users WHERE is_operator = 1"
                    " ORDER BY last_seen_at DESC, created_at DESC LIMIT 1"
                ).fetchone()
        return str(row["user_id"]) if row else ""

    # -- memberships ----------------------------------------------------------
    def list_memberships(self, meeting_id: str = "") -> list[dict[str, object]]:
        room_id = clean_lobby_text(meeting_id, limit=128)
        with closing(self._connect()) as connection:
            if room_id:
                rows = connection.execute(
                    "SELECT * FROM memberships WHERE meeting_id = ? ORDER BY updated_at DESC",
                    (room_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM memberships ORDER BY updated_at DESC"
                ).fetchall()
        return [self._membership_dict(row) for row in rows]

    def get_membership(self, meeting_id: str, participant_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM memberships WHERE meeting_id = ? AND participant_id = ?",
                (clean_lobby_text(meeting_id, limit=128), clean_lobby_text(participant_id, limit=128)),
            ).fetchone()
        return self._membership_dict(row) if row else None

    def upsert_membership(self, record: dict[str, object]) -> dict[str, object]:
        """Insert or merge one roster row (same merge semantics as the old JSON
        store: non-empty incoming values win; blanks never erase saved data)."""
        member = {key: record.get(key, "") for key in _MEMBERSHIP_FIELDS}
        meeting_id = clean_lobby_text(member["meeting_id"], limit=128)
        participant_id = clean_lobby_text(member["participant_id"], limit=128)
        if not participant_id:
            raise ValueError("participant_id is required for a room membership.")
        now = _now()
        with self._write_lock, closing(self._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT * FROM memberships WHERE meeting_id = ? AND participant_id = ?",
                (meeting_id, participant_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO memberships (meeting_id, participant_id, display_name, role,"
                    " participant_type, provider_kind, connection_kind, status, muted, is_host,"
                    " source, created_at, updated_at, last_seen_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        meeting_id,
                        participant_id,
                        str(member["display_name"] or participant_id),
                        str(member["role"] or "agent"),
                        str(member["participant_type"] or "unknown"),
                        str(member["provider_kind"] or ""),
                        str(member["connection_kind"] or ""),
                        str(member["status"] or ""),
                        1 if member["muted"] else 0,
                        1 if member["is_host"] else 0,
                        str(member["source"] or "manual"),
                        str(member["created_at"] or now),
                        now,
                        str(member["last_seen_at"] or ""),
                    ),
                )
            else:
                updates: dict[str, object] = {"updated_at": now}
                for field in _MEMBERSHIP_MERGE_FIELDS:
                    incoming = record.get(field)
                    if incoming not in ("", None, [], {}):
                        updates[field] = str(incoming)
                if "muted" in record:
                    updates["muted"] = 1 if record["muted"] else 0
                if "is_host" in record:
                    updates["is_host"] = 1 if record["is_host"] else 0
                assignments = ", ".join(f"{column} = ?" for column in updates)
                connection.execute(
                    f"UPDATE memberships SET {assignments} WHERE meeting_id = ? AND participant_id = ?",
                    (*updates.values(), meeting_id, participant_id),
                )
            refreshed = connection.execute(
                "SELECT * FROM memberships WHERE meeting_id = ? AND participant_id = ?",
                (meeting_id, participant_id),
            ).fetchone()
        return self._membership_dict(refreshed)

    def remove_membership(self, meeting_id: str, participant_id: str) -> bool:
        room_id = clean_lobby_text(meeting_id, limit=128)
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        if not clean_participant_id:
            return False
        with self._write_lock, closing(self._connect()) as connection, connection:
            if room_id:
                cursor = connection.execute(
                    "DELETE FROM memberships WHERE meeting_id = ? AND participant_id = ?",
                    (room_id, clean_participant_id),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM memberships WHERE participant_id = ?",
                    (clean_participant_id,),
                )
        return cursor.rowcount > 0

    def set_membership_muted(self, meeting_id: str, participant_id: str, muted: bool) -> dict[str, object]:
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        if not clean_participant_id:
            raise ValueError("participant_id is required to set mute state.")
        existing = self.get_membership(meeting_id, clean_participant_id)
        if existing is None:
            # Mute-only placeholder for a participant that exists purely as a
            # live agent/session merged in at read time.
            return self.upsert_membership(
                {
                    "meeting_id": meeting_id,
                    "participant_id": clean_participant_id,
                    "display_name": clean_participant_id,
                    "role": "agent",
                    "source": "moderation",
                    "muted": muted,
                }
            )
        return self.upsert_membership(
            {"meeting_id": meeting_id, "participant_id": clean_participant_id, "muted": muted}
        )

    def membership_muted(self, meeting_id: str, participant_id: str) -> bool:
        member = self.get_membership(meeting_id, participant_id)
        return bool(member and member.get("muted"))

    # -- rooms ---------------------------------------------------------------
    def upsert_room(self, *, room_id: str, owner_id: str = "", label: str = "", origin: str = "") -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        clean_owner_id = clean_lobby_text(owner_id, limit=128)
        clean_label = clean_lobby_text(label, limit=128)
        clean_origin = clean_lobby_text(origin, limit=64)
        now = _now()
        with self._write_lock, closing(self._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT * FROM rooms WHERE room_id = ?",
                (clean_room_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO rooms (room_id, owner_id, label, created_at, last_active_at, archived, origin)"
                    " VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (clean_room_id, clean_owner_id, clean_label, now, now, clean_origin),
                )
            else:
                updates: dict[str, object] = {"last_active_at": now}
                if clean_owner_id:
                    updates["owner_id"] = clean_owner_id
                if clean_label:
                    updates["label"] = clean_label
                if clean_origin:
                    updates["origin"] = clean_origin
                assignments = ", ".join(f"{column} = ?" for column in updates)
                connection.execute(
                    f"UPDATE rooms SET {assignments} WHERE room_id = ?",
                    (*updates.values(), clean_room_id),
                )
            refreshed = connection.execute(
                "SELECT * FROM rooms WHERE room_id = ?",
                (clean_room_id,),
            ).fetchone()
        return self._room_dict(refreshed)

    def list_rooms(self, *, owner_id: str = "", include_archived: bool = False) -> list[dict[str, object]]:
        clean_owner_id = clean_lobby_text(owner_id, limit=128)
        where: list[str] = []
        params: list[object] = []
        if clean_owner_id:
            where.append("owner_id = ?")
            params.append(clean_owner_id)
        if not include_archived:
            where.append("archived = 0")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM rooms{clause} ORDER BY last_active_at DESC",
                params,
            ).fetchall()
        return [self._room_dict(row) for row in rows]

    def get_room(self, room_id: str) -> dict[str, object] | None:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM rooms WHERE room_id = ?",
                (clean_room_id,),
            ).fetchone()
        return self._room_dict(row) if row else None

    def set_room_archived(self, room_id: str, archived: bool) -> bool:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            return False
        with self._write_lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE rooms SET archived = ? WHERE room_id = ?",
                (1 if archived else 0, clean_room_id),
            )
        return cursor.rowcount > 0

    def touch_room(self, room_id: str) -> None:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            return
        with self._write_lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE rooms SET last_active_at = ? WHERE room_id = ?",
                (_now(), clean_room_id),
            )

    def delete_room(self, room_id: str) -> bool:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            return False
        with self._write_lock, closing(self._connect()) as connection, connection:
            delete_room_preferences(connection, clean_room_id)
            connection.execute("DELETE FROM memberships WHERE meeting_id = ?", (clean_room_id,))
            cursor = connection.execute("DELETE FROM rooms WHERE room_id = ?", (clean_room_id,))
        return cursor.rowcount > 0

    # -- user-owned room preferences ----------------------------------------
    def room_preferences(self, user_id: str, room_id: str) -> RoomUserPreferencesRecord:
        with closing(self._connect()) as connection:
            return read_room_preferences(connection, user_id, room_id)

    def update_room_preferences(
        self,
        user_id: str,
        room_id: str,
        updates: dict[str, object],
    ) -> RoomUserPreferencesRecord:
        with self._write_lock, closing(self._connect()) as connection, connection:
            return update_room_preferences(
                connection,
                user_id,
                room_id,
                updates,
                now=_now(),
            )

    # -- usage accounting -----------------------------------------------------
    def record_usage(self, event: dict[str, object]) -> None:
        """Append one model-call usage event. The seed for quotas/billing
        (master plan 먼 미래) — the API provider lane records here on each call.
        Cheap append; never blocks the response path on read aggregation."""

        def _int(value: object) -> int:
            try:
                return max(0, int(value))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return 0

        row = (
            str(event.get("created_at") or _now()),
            clean_lobby_text(event.get("user_id"), limit=128),
            clean_lobby_text(event.get("participant_id"), limit=128),
            clean_lobby_text(event.get("meeting_id"), limit=128),
            clean_lobby_text(event.get("provider"), limit=64),
            clean_lobby_text(event.get("model"), limit=128),
            _int(event.get("input_tokens")),
            _int(event.get("output_tokens")),
            clean_lobby_text(event.get("cost_owner"), limit=32),
            1 if event.get("estimated") else 0,
        )
        with self._write_lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO usage_events (created_at, user_id, participant_id, meeting_id,"
                " provider, model, input_tokens, output_tokens, cost_owner, estimated)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )

    def usage_summary(self, *, user_id: str = "", meeting_id: str = "", since: str = "") -> dict[str, object]:
        """Aggregate usage, optionally filtered by user / meeting / since-time."""
        where, params = [], []
        if user_id:
            where.append("user_id = ?")
            params.append(clean_lobby_text(user_id, limit=128))
        if meeting_id:
            where.append("meeting_id = ?")
            params.append(clean_lobby_text(meeting_id, limit=128))
        if since:
            where.append("created_at >= ?")
            params.append(str(since))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with closing(self._connect()) as connection:
            totals = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
                " COALESCE(SUM(estimated),0)"
                f" FROM usage_events{clause}",
                params,
            ).fetchone()
            by_model = connection.execute(
                "SELECT provider, model, COUNT(*) c,"
                " COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o"
                f" FROM usage_events{clause} GROUP BY provider, model ORDER BY (i + o) DESC",
                params,
            ).fetchall()
        return {
            "events": int(totals[0]),
            "input_tokens": int(totals[1]),
            "output_tokens": int(totals[2]),
            "estimated_events": int(totals[3]),
            "by_model": [
                {
                    "provider": r[0],
                    "model": r[1],
                    "events": int(r[2]),
                    "input_tokens": int(r[3]),
                    "output_tokens": int(r[4]),
                }
                for r in by_model
            ],
        }


# Explicit alias retained for callers that name the storage technology.
SqliteIdentityStore = IdentityStore
