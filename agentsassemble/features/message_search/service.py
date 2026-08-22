"""SQLite FTS index derived from the complete public room record."""
from __future__ import annotations

import base64
import binascii
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


SEARCH_PAGE_SIZE = 30
_LOCKS_GUARD = threading.Lock()
_INDEX_LOCKS: dict[str, threading.RLock] = {}


def _index_lock(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve())
    with _LOCKS_GUARD:
        return _INDEX_LOCKS.setdefault(key, threading.RLock())


def _compact(value: str) -> str:
    return "".join(value.casefold().split())


def _fts_phrase(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _attachment_filenames(event: dict[str, object]) -> list[str]:
    attachments = event.get("attachments") if isinstance(event.get("attachments"), list) else []
    return [
        filename
        for item in attachments
        if isinstance(item, dict)
        and (filename := clean_room_text(item.get("filename"), limit=256))
    ]


def _search_record(
    room_id: str,
    channel_id: str,
    event: dict[str, object],
) -> dict[str, object] | None:
    event_type = clean_room_text(event.get("type"), limit=64)
    if channel_id == "lobby" and event_type != "message_final":
        return None
    if channel_id != "lobby" and clean_room_text(event.get("kind"), limit=64) != "message":
        return None
    event_id = clean_room_text(event.get("id"), limit=128)
    if not event_id:
        return None
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    author = clean_room_text(
        event.get("display_name")
        or event.get("name")
        or actor.get("participant_id"),
        limit=128,
    ) or "Room"
    content = clean_room_text(event.get("content") or event.get("message"), limit=12_000)
    filenames = _attachment_filenames(event)
    search_text = "\n".join(value for value in (author, content, *filenames) if value)
    if not search_text:
        return None
    return {
        "room_id": room_id,
        "channel_id": channel_id,
        "event_id": event_id,
        "seq": max(0, int(event.get("seq") or 0)),
        "created_at": clean_room_text(event.get("created_at"), limit=128),
        "author": author,
        "content": content,
        "attachment_filenames": filenames,
        "search_text": search_text.casefold(),
        "compact_text": _compact(search_text),
    }


class MessageSearchService:
    """Maintain a rebuildable local index without becoming message authority."""

    def __init__(self, output_root: str | Path) -> None:
        self.root = Path(output_root).expanduser().resolve() / "derived"
        self.path = self.root / "message-search.sqlite3"
        self._lock = _index_lock(self.path)

    def sync_lobby(self, repository: RoomRepository, room_id: str) -> None:
        clean_room = clean_room_text(room_id, limit=128)
        latest_seq = repository.latest_event_sequence(clean_room)
        with self._lock, self._connection() as connection:
            cursor = self._state_cursor(connection, clean_room, "lobby")
            if latest_seq < cursor:
                self._delete_channel(connection, clean_room, "lobby")
                cursor = 0
            events = repository.read_events(
                clean_room,
                after_seq=cursor,
                limit=None,
                include_hidden=False,
            )
            for event in events:
                record = _search_record(clean_room, "lobby", event)
                if record:
                    self._upsert(connection, record)
            self._write_state(connection, clean_room, "lobby", str(latest_seq))

    def sync_custom_channel(self, room_id: str, channel_id: str, path: Path) -> None:
        clean_room = clean_room_text(room_id, limit=128)
        clean_channel = clean_room_text(channel_id, limit=128)
        try:
            stat = path.stat()
            signature = f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            signature = "missing"
        with self._lock, self._connection() as connection:
            if self._state_value(connection, clean_room, clean_channel) == signature:
                return
            self._delete_channel(connection, clean_room, clean_channel)
            for event in self._read_jsonl(path):
                record = _search_record(clean_room, clean_channel, event)
                if record:
                    self._upsert(connection, record)
            self._write_state(connection, clean_room, clean_channel, signature)

    def search(
        self,
        room_id: str,
        *,
        query: str,
        channel_ids: Iterable[str],
        cursor: str = "",
    ) -> dict[str, object]:
        clean_room = clean_room_text(room_id, limit=128)
        clean_query = clean_room_text(query, limit=200).casefold()
        channels = tuple(
            dict.fromkeys(
                clean
                for value in channel_ids
                if (clean := clean_room_text(value, limit=128))
            )
        )
        if not clean_query or not channels:
            return {"results": [], "next_cursor": ""}
        match_queries = [("message_search_phrase", _fts_phrase(clean_query))]
        compact = _compact(clean_query)
        if len(compact) >= 3:
            match_queries.append(("message_search_compact", _fts_phrase(compact)))
        with self._lock, self._connection() as connection:
            match_selects = [
                f"SELECT rowid FROM {table} WHERE {table} MATCH ?"
                for table, _match_query in match_queries
            ]
            clauses = [
                f"id IN ({' UNION '.join(match_selects)})",
                "room_id = ?",
                f"channel_id IN ({','.join('?' for _ in channels)})",
            ]
            parameters: list[object] = [
                *(match_query for _table, match_query in match_queries),
                clean_room,
                *channels,
            ]
            cursor_values = self._decode_cursor(cursor)
            if cursor_values:
                created_at, seq, event_id = cursor_values
                clauses.append(
                    "(created_at < ? OR (created_at = ? AND seq < ?) "
                    "OR (created_at = ? AND seq = ? AND event_id < ?))"
                )
                parameters.extend([created_at, created_at, seq, created_at, seq, event_id])
            parameters.append(SEARCH_PAGE_SIZE + 1)
            rows = connection.execute(
                f"""SELECT event_id, channel_id, seq, created_at, author, content,
                           attachment_filenames_json
                    FROM message_search_records
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC, seq DESC, event_id DESC
                    LIMIT ?""",
                parameters,
            ).fetchall()
        has_more = len(rows) > SEARCH_PAGE_SIZE
        page_rows = rows[:SEARCH_PAGE_SIZE]
        results = [self._project_row(row) for row in page_rows]
        next_cursor = ""
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = self._encode_cursor(
                str(last["created_at"] or ""),
                int(last["seq"] or 0),
                str(last["event_id"] or ""),
            )
        return {"results": results, "next_cursor": next_cursor}

    def _connection(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """PRAGMA journal_mode = WAL;
               CREATE TABLE IF NOT EXISTS message_search_records (
                   id INTEGER PRIMARY KEY,
                   room_id TEXT NOT NULL,
                   channel_id TEXT NOT NULL,
                   event_id TEXT NOT NULL,
                   seq INTEGER NOT NULL,
                   created_at TEXT NOT NULL,
                   author TEXT NOT NULL,
                   content TEXT NOT NULL,
                   attachment_filenames_json TEXT NOT NULL,
                   search_text TEXT NOT NULL,
                   compact_text TEXT NOT NULL,
                   UNIQUE(room_id, channel_id, event_id)
               );
               CREATE INDEX IF NOT EXISTS idx_message_search_page
                   ON message_search_records(room_id, channel_id, created_at DESC, seq DESC, event_id DESC);
               CREATE TABLE IF NOT EXISTS message_search_state (
                   room_id TEXT NOT NULL,
                   channel_id TEXT NOT NULL,
                   source_value TEXT NOT NULL,
                   PRIMARY KEY(room_id, channel_id)
               );
               CREATE VIRTUAL TABLE IF NOT EXISTS message_search_phrase
                   USING fts5(search_text, tokenize='unicode61');
               CREATE VIRTUAL TABLE IF NOT EXISTS message_search_compact
                   USING fts5(compact_text, tokenize='trigram');
               PRAGMA user_version = 1;"""
        )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return connection

    @staticmethod
    def _upsert(connection: sqlite3.Connection, record: dict[str, object]) -> None:
        existing = connection.execute(
            """SELECT id FROM message_search_records
               WHERE room_id = ? AND channel_id = ? AND event_id = ?""",
            (record["room_id"], record["channel_id"], record["event_id"]),
        ).fetchone()
        if existing is not None:
            row_id = int(existing["id"])
            connection.execute("DELETE FROM message_search_phrase WHERE rowid = ?", (row_id,))
            connection.execute("DELETE FROM message_search_compact WHERE rowid = ?", (row_id,))
            connection.execute("DELETE FROM message_search_records WHERE id = ?", (row_id,))
        cursor = connection.execute(
            """INSERT INTO message_search_records(
                   room_id, channel_id, event_id, seq, created_at, author, content,
                   attachment_filenames_json, search_text, compact_text
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["room_id"],
                record["channel_id"],
                record["event_id"],
                record["seq"],
                record["created_at"],
                record["author"],
                record["content"],
                json.dumps(record["attachment_filenames"], ensure_ascii=False),
                record["search_text"],
                record["compact_text"],
            ),
        )
        row_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO message_search_phrase(rowid, search_text) VALUES(?, ?)",
            (row_id, record["search_text"]),
        )
        connection.execute(
            "INSERT INTO message_search_compact(rowid, compact_text) VALUES(?, ?)",
            (row_id, record["compact_text"]),
        )

    @staticmethod
    def _delete_channel(connection: sqlite3.Connection, room_id: str, channel_id: str) -> None:
        rows = connection.execute(
            "SELECT id FROM message_search_records WHERE room_id = ? AND channel_id = ?",
            (room_id, channel_id),
        ).fetchall()
        for row in rows:
            row_id = int(row["id"])
            connection.execute("DELETE FROM message_search_phrase WHERE rowid = ?", (row_id,))
            connection.execute("DELETE FROM message_search_compact WHERE rowid = ?", (row_id,))
        connection.execute(
            "DELETE FROM message_search_records WHERE room_id = ? AND channel_id = ?",
            (room_id, channel_id),
        )

    @staticmethod
    def _state_value(connection: sqlite3.Connection, room_id: str, channel_id: str) -> str:
        row = connection.execute(
            "SELECT source_value FROM message_search_state WHERE room_id = ? AND channel_id = ?",
            (room_id, channel_id),
        ).fetchone()
        return str(row["source_value"] or "") if row else ""

    def _state_cursor(self, connection: sqlite3.Connection, room_id: str, channel_id: str) -> int:
        try:
            return max(0, int(self._state_value(connection, room_id, channel_id) or 0))
        except ValueError:
            return 0

    @staticmethod
    def _write_state(
        connection: sqlite3.Connection,
        room_id: str,
        channel_id: str,
        value: str,
    ) -> None:
        connection.execute(
            """INSERT INTO message_search_state(room_id, channel_id, source_value)
               VALUES(?, ?, ?)
               ON CONFLICT(room_id, channel_id) DO UPDATE SET source_value = excluded.source_value""",
            (room_id, channel_id, value),
        )

    @staticmethod
    def _read_jsonl(path: Path) -> Iterable[dict[str, object]]:
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield payload
        except OSError:
            return

    @staticmethod
    def _project_row(row: sqlite3.Row) -> dict[str, object]:
        try:
            filenames = json.loads(str(row["attachment_filenames_json"] or "[]"))
        except json.JSONDecodeError:
            filenames = []
        return {
            "event_id": str(row["event_id"] or ""),
            "channel_id": str(row["channel_id"] or ""),
            "seq": int(row["seq"] or 0),
            "created_at": str(row["created_at"] or ""),
            "author": str(row["author"] or ""),
            "content": str(row["content"] or ""),
            "attachment_filenames": [str(value) for value in filenames if str(value)],
        }

    @staticmethod
    def _encode_cursor(created_at: str, seq: int, event_id: str) -> str:
        payload = json.dumps([created_at, seq, event_id], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, int, str] | None:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
            if not isinstance(payload, list) or len(payload) != 3:
                return None
            return str(payload[0]), max(0, int(payload[1])), str(payload[2])
        except (
            ValueError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            binascii.Error,
        ):
            return None


__all__ = ["MessageSearchService", "SEARCH_PAGE_SIZE"]
