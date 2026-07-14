"""SQLite-backed identity + roster storage (the DB phase of the rebuild).

One file (identity.db) holds the relational core that scattered JSON files
couldn't keep consistent:

- users        — one row per stable identity (human or AI), operator flag
- credentials  — login keys mapping to users ("device:<sha>" now, "google:<sub>" later)
- memberships  — per-room roster rows with mute/host moderation state;
                 PRIMARY KEY (meeting_id, participant_id) makes ghost
                 duplicates structurally impossible

Invite/session token state intentionally stays in room_invite.py's JSON store:
its on-disk format is a tested security contract (no raw tokens at rest) and
sessions are TTL-bound runtime state, not identity.

Concurrency: ThreadingHTTPServer calls from many threads; each operation opens
a short-lived connection (cheap for this size) and WAL mode keeps readers and
writers out of each other's way.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentsassemble.identity_room_preferences import (
    delete_room_preferences,
    ensure_room_preferences_schema,
    read_room_preferences,
    update_room_preferences,
)
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_user_preferences import (
    RoomUserPreferencesRecord,
)

IDENTITY_DB_FILENAME = "identity.db"


@runtime_checkable
class IdentityBackend(Protocol):
    """The storage contract for identity (users/credentials/memberships).

    SqliteIdentityStore implements this for local-first mode. The web-deploy
    mode (master plan 기둥1) will add a Postgres/Supabase implementation of the
    SAME contract and register it via register_identity_backend — consumers
    depend on this Protocol, never on a concrete backend, so swapping the
    backend is a config change, not a code change across the app.

    Any new backend must implement every method below with the same semantics:
    dict shapes match SqliteIdentityStore's _user_dict / _membership_dict, and
    upsert merges (non-empty incoming wins, blanks never erase saved values).
    """

    def count_users(self) -> int: ...
    def count_memberships(self) -> int: ...
    def user_for_credential(self, auth_key: str) -> dict[str, object] | None: ...
    def get_user(self, user_id: str) -> dict[str, object] | None: ...
    def user_for_participant(self, participant_id: str) -> dict[str, object] | None: ...
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
    ) -> dict[str, object] | None: ...
    def set_user_operator(self, user_id: str, is_operator: bool) -> bool: ...
    def participant_is_operator(self, participant_id: str) -> bool: ...
    def operator_user_id(self) -> str: ...
    def list_memberships(self, meeting_id: str = "") -> list[dict[str, object]]: ...
    def get_membership(self, meeting_id: str, participant_id: str) -> dict[str, object] | None: ...
    def upsert_membership(self, record: dict[str, object]) -> dict[str, object]: ...
    def remove_membership(self, meeting_id: str, participant_id: str) -> bool: ...
    def set_membership_muted(self, meeting_id: str, participant_id: str, muted: bool) -> dict[str, object]: ...
    def membership_muted(self, meeting_id: str, participant_id: str) -> bool: ...
    def upsert_room(self, *, room_id: str, owner_id: str = "", label: str = "", origin: str = "") -> dict[str, object]: ...
    def list_rooms(self, *, owner_id: str = "", include_archived: bool = False) -> list[dict[str, object]]: ...
    def get_room(self, room_id: str) -> dict[str, object] | None: ...
    def set_room_archived(self, room_id: str, archived: bool) -> bool: ...
    def touch_room(self, room_id: str) -> None: ...
    def delete_room(self, room_id: str) -> bool: ...
    def room_preferences(self, user_id: str, room_id: str) -> RoomUserPreferencesRecord: ...
    def update_room_preferences(
        self,
        user_id: str,
        room_id: str,
        updates: dict[str, object],
    ) -> RoomUserPreferencesRecord: ...
    def record_usage(self, event: dict[str, object]) -> None: ...
    def usage_summary(self, *, user_id: str = "", meeting_id: str = "", since: str = "") -> dict[str, object]: ...


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

    def participant_is_operator(self, participant_id: str) -> bool:
        user = self.user_for_participant(participant_id)
        return bool(user and user.get("is_operator"))

    def operator_user_id(self) -> str:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id FROM users WHERE is_operator = 1 ORDER BY last_seen_at DESC, created_at DESC LIMIT 1"
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


# The local-first backend. The name `IdentityStore` is kept as an alias so
# existing imports/tests keep working; new code should think in terms of the
# IdentityBackend contract and the make_identity_backend selection point.
SqliteIdentityStore = IdentityStore


# -- backend selection (the single swap point) ---------------------------------
# A future Postgres/Supabase backend registers itself here; everything else in
# the app goes through make_identity_backend and depends only on IdentityBackend.
_BACKEND_FACTORIES: dict[str, Callable[..., IdentityBackend]] = {}


def register_identity_backend(kind: str, factory: Callable[..., IdentityBackend]) -> None:
    """Register a backend implementation (e.g. 'postgres') for web-deploy mode."""
    _BACKEND_FACTORIES[str(kind).strip().lower()] = factory


def make_identity_backend(kind: str = "sqlite", **config: object) -> IdentityBackend:
    """Construct the configured identity backend. Local mode = 'sqlite' (db_path);
    web mode will pass kind='postgres' with its own config once registered."""
    clean_kind = str(kind or "sqlite").strip().lower()
    factory = _BACKEND_FACTORIES.get(clean_kind)
    if factory is None:
        available = ", ".join(sorted(_BACKEND_FACTORIES)) or "(none)"
        raise NotImplementedError(
            f"identity backend {clean_kind!r} is not registered "
            f"(available: {available}). Implement IdentityBackend and call "
            f"register_identity_backend({clean_kind!r}, factory)."
        )
    return factory(**config)


# -- sqlite store registry (one instance per db file) ---------------------------
_registry_lock = threading.Lock()
_stores: dict[str, IdentityBackend] = {}


def default_identity_db_path(output_root: Path) -> Path:
    return Path(output_root) / IDENTITY_DB_FILENAME


def identity_store_at(db_path: Path) -> IdentityBackend:
    key = str(Path(db_path).resolve())
    with _registry_lock:
        store = _stores.get(key)
        if store is None:
            store = make_identity_backend("sqlite", db_path=Path(db_path))
            _stores[key] = store
        return store


_migrated_member_roots: set[str] = set()


def identity_store_for_output_root(output_root: Path) -> IdentityBackend:
    """Local-first entry: sqlite store for a server data root; imports legacy
    room_members.json once while empty. (Web mode uses a different entry once a
    Postgres backend is registered.)

    The legacy import runs at most ONCE per root — not on every call — so a busy
    room (many concurrent WS connections each resolving the store) doesn't
    re-count + re-attempt the migration on every request."""
    store = identity_store_at(default_identity_db_path(output_root))
    key = str(Path(output_root).resolve())
    if key not in _migrated_member_roots:
        with _registry_lock:
            if key not in _migrated_member_roots:
                if store.count_memberships() == 0:
                    migrate_legacy_members_json(store, Path(output_root) / "room_members.json")
                _migrated_member_roots.add(key)
    return store


def reset_identity_store_registry() -> None:
    """Testing only: drop cached store instances (files stay on disk)."""
    with _registry_lock:
        _stores.clear()
        _migrated_member_roots.clear()


# Register the built-in sqlite backend (db_path kwarg).
register_identity_backend("sqlite", lambda db_path: SqliteIdentityStore(Path(db_path)))


# -- one-time migrations from the JSON era --------------------------------------
def migrate_legacy_members_json(store: IdentityBackend, members_json_path: Path) -> int:
    """Import room_members.json roster rows; returns how many were imported."""
    try:
        payload = json.loads(members_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, list):
        return 0
    imported = 0
    for member in members:
        if not isinstance(member, dict) or not member.get("participant_id"):
            continue
        try:
            store.upsert_membership(member)
            imported += 1
        except ValueError:
            continue
    return imported


def migrate_legacy_users_json(store: IdentityBackend, users_json_path: Path) -> int:
    """Import users.json identities (device credentials); returns import count."""
    try:
        payload = json.loads(users_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    users = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users, dict):
        return 0
    imported = 0
    for auth_key, record in users.items():
        if not isinstance(record, dict) or not record.get("user_id"):
            continue
        resolved = store.resolve_credential_user(
            str(auth_key),
            provider=str(record.get("auth_provider") or "device"),
            user_id=str(record.get("user_id") or ""),
            participant_id=str(record.get("participant_id") or ""),
            display_name=str(record.get("display_name") or ""),
            avatar_image_url=str(record.get("avatar_image_url") or ""),
            participant_type=str(record.get("participant_type") or ""),
        )
        if resolved:
            imported += 1
    return imported
