from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentsassemble.identity_store import default_identity_db_path
from agentsassemble.persistence.local.room.repository import RoomStore


PLAN_FILENAME = ".legacy-message-migration-plan.json"
MIGRATION_VERSION = 1


@dataclass(frozen=True)
class LegacyMessage:
    source_id: str
    actor_id: str
    display_name: str
    content: str
    created_at: str
    round_id: str = ""
    turn_id: str = ""
    source_kind: str = "message"


@dataclass(frozen=True)
class LegacyRoomImport:
    room_id: str
    source_path: str
    messages: tuple[LegacyMessage, ...]


def find_legacy_message_imports(output_root: Path) -> list[LegacyRoomImport]:
    root = output_root.expanduser().resolve()
    store = RoomStore(root)
    imports: list[LegacyRoomImport] = []
    with closing(sqlite3.connect(store.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        room_rows = connection.execute("SELECT room_id FROM rooms ORDER BY room_id").fetchall()
        for row in room_rows:
            room_id = str(row["room_id"])
            existing_messages = int(
                connection.execute(
                    "SELECT COUNT(*) FROM room_events WHERE room_id = ? AND event_type = 'message_final'",
                    (room_id,),
                ).fetchone()[0]
            )
            if existing_messages:
                continue
            meeting_dir = root / "meetings" / room_id
            messages, source = _read_legacy_messages(meeting_dir, room_id)
            if messages:
                imports.append(LegacyRoomImport(room_id, str(source), tuple(messages)))
    return imports


def migrate_legacy_messages(output_root: Path, *, apply: bool) -> dict[str, object]:
    root = output_root.expanduser().resolve()
    imports = find_legacy_message_imports(root)
    summary = _summary(root, imports)
    plan_path = root / "rooms" / PLAN_FILENAME
    if not apply:
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {**summary, "status": "dry_run", "plan_path": str(plan_path)}

    if not plan_path.exists():
        raise ValueError("Run migrate-legacy-messages --dry-run before --apply.")
    planned = json.loads(plan_path.read_text(encoding="utf-8"))
    if planned.get("fingerprint") != summary["fingerprint"] or planned.get("rooms") != summary["rooms"]:
        raise ValueError("Legacy message sources changed after dry-run; no messages were imported.")

    store = RoomStore(root)
    identity_path = default_identity_db_path(root)
    backup_dir = root / "backups" / datetime.now(UTC).strftime("legacy-message-import-%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=False)
    _backup_database(store.database_path, backup_dir / store.database_path.name)
    if identity_path.exists():
        _backup_database(identity_path, backup_dir / identity_path.name)

    imported = 0
    participants = 0
    with closing(sqlite3.connect(store.database_path, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for room_import in imports:
                imported += _insert_room_import(connection, room_import)
                participants += _upsert_legacy_participants(connection, room_import)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    plan_path.unlink(missing_ok=True)
    return {
        **summary,
        "status": "applied",
        "imported_message_count": imported,
        "participant_count": participants,
        "backup_dir": str(backup_dir),
    }


def _read_legacy_messages(meeting_dir: Path, room_id: str) -> tuple[list[LegacyMessage], Path]:
    live_events = meeting_dir / "live_events.jsonl"
    if live_events.exists():
        messages = _messages_from_live_events(live_events)
        if messages:
            return messages, live_events
    transcript = meeting_dir / "transcript.md"
    if transcript.exists():
        messages = _messages_from_transcript(transcript, room_id)
        if messages:
            return messages, transcript
    return [], transcript


def _messages_from_live_events(path: Path) -> list[LegacyMessage]:
    messages: list[LegacyMessage] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = str(event.get("kind") or "")
        content = str(event.get("content") or "").strip()
        if kind not in {"message", "synthesis"} or not content:
            continue
        is_official = bool(event.get("official_record")) or event.get("channel") == "official"
        is_legacy_official = "official_record" not in event and not event.get("channel")
        if not is_official and not is_legacy_official:
            continue
        actor_id = str(event.get("role_id") or ("moderator" if kind == "synthesis" else "legacy-agent"))
        display_name = str(event.get("display_name") or ("Moderator" if kind == "synthesis" else actor_id))
        source_id = str(event.get("id") or _message_digest(actor_id, content))
        messages.append(
            LegacyMessage(
                source_id=source_id,
                actor_id=actor_id,
                display_name=display_name,
                content=content,
                created_at=str(event.get("created_at") or _path_timestamp(path)),
                round_id=str(event.get("round") or ""),
                turn_id=str(event.get("turn_id") or ""),
                source_kind=kind,
            )
        )
    return messages


def _messages_from_transcript(path: Path, room_id: str) -> list[LegacyMessage]:
    lines = path.read_text(encoding="utf-8").splitlines()
    base_time = _room_id_timestamp(room_id) or _path_datetime(path)
    messages: list[LegacyMessage] = []
    round_id = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        round_match = re.match(r"^##\s+Round\s+(.+?)\s*$", line, flags=re.IGNORECASE)
        if round_match:
            round_id = f"round_{round_match.group(1).strip().lower().replace(' ', '_')}"
            index += 1
            continue
        actor_match = re.match(r"^###\s+(.+?)\s*$", line)
        synthesis = line.strip().lower() == "## moderator synthesis"
        if not actor_match and not synthesis:
            index += 1
            continue
        display_name = actor_match.group(1).strip() if actor_match else "Moderator"
        actor_id = _actor_id(display_name) if actor_match else "moderator"
        source_kind = "message" if actor_match else "synthesis"
        end = index + 1
        while end < len(lines) and not re.match(r"^##{2,3}\s+", lines[end]):
            end += 1
        content = _transcript_section_content(lines[index + 1 : end])
        if content:
            created_at = (base_time + timedelta(milliseconds=len(messages))).isoformat()
            source_id = _message_digest(room_id, round_id, actor_id, str(len(messages)), content)
            messages.append(
                LegacyMessage(
                    source_id=source_id,
                    actor_id=actor_id,
                    display_name=display_name,
                    content=content,
                    created_at=created_at,
                    round_id=round_id,
                    source_kind=source_kind,
                )
            )
        index = end
    return messages


def _transcript_section_content(lines: list[str]) -> str:
    text = "\n".join(lines).strip()
    if not text:
        return ""
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    natural = [
        paragraph
        for paragraph in paragraphs
        if not paragraph.startswith(("Position:", "Stance:", "Change conditions:", "Confidence:"))
        and not all(line.lstrip().startswith("-") for line in paragraph.splitlines())
    ]
    return natural[-1] if natural else paragraphs[-1]


def _insert_room_import(connection: sqlite3.Connection, room_import: LegacyRoomImport) -> int:
    existing_sources = {
        str(json.loads(row[0]).get("legacy_source_event_id") or "")
        for row in connection.execute(
            "SELECT payload_json FROM room_events WHERE room_id = ?", (room_import.room_id,)
        ).fetchall()
    }
    imported = 0
    next_seq = int(
        connection.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM room_events WHERE room_id = ?", (room_import.room_id,)
        ).fetchone()[0]
    )
    for message in room_import.messages:
        if message.source_id in existing_sources:
            continue
        event_id = "legacy-" + _message_digest(room_import.room_id, message.source_id)[:16]
        payload = {
            "v": 1,
            "id": event_id,
            "seq": next_seq,
            "created_at": message.created_at,
            "room_id": room_import.room_id,
            "type": "message_final",
            "actor": {"participant_id": message.actor_id, "participant_type": "agent"},
            "participant_id": message.actor_id,
            "participant_type": "agent",
            "actor_id": message.actor_id,
            "actor_type": "agent",
            "display_name": message.display_name,
            "content": message.content,
            "turn_id": message.turn_id,
            "round": message.round_id,
            "legacy_source_event_id": message.source_id,
            "legacy_source_kind": message.source_kind,
            "legacy_migration_version": MIGRATION_VERSION,
        }
        connection.execute(
            """INSERT INTO room_events(
                   room_id, seq, event_id, event_type, actor_id, turn_id,
                   created_at, visibility, payload_json
               ) VALUES(?, ?, ?, 'message_final', ?, ?, ?, 'visible', ?)""",
            (
                room_import.room_id,
                next_seq,
                event_id,
                message.actor_id,
                message.turn_id,
                message.created_at,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )
        imported += 1
        next_seq += 1
    return imported


def _upsert_legacy_participants(connection: sqlite3.Connection, room_import: LegacyRoomImport) -> int:
    participants = {(message.actor_id, message.display_name) for message in room_import.messages}
    changed = 0
    for actor_id, display_name in sorted(participants):
        existing = connection.execute(
            "SELECT data_json FROM participants WHERE room_id = ? AND participant_id = ?",
            (room_import.room_id, actor_id),
        ).fetchone()
        if existing:
            continue
        now = datetime.now(UTC).isoformat()
        payload = {
            "room_id": room_import.room_id,
            "participant_id": actor_id,
            "display_name": display_name,
            "role": "agent",
            "participant_type": "agent",
            "provider_kind": "legacy_transcript",
            "connection_kind": "legacy_transcript",
            "status": "left",
            "source": "legacy_transcript",
            "created_at": now,
            "updated_at": now,
        }
        connection.execute(
            "INSERT INTO participants(room_id, participant_id, status, data_json) VALUES(?, ?, 'left', ?)",
            (room_import.room_id, actor_id, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )
        changed += 1
    return changed


def _summary(root: Path, imports: list[LegacyRoomImport]) -> dict[str, object]:
    rooms = [
        {"room_id": item.room_id, "message_count": len(item.messages), "source_path": item.source_path}
        for item in imports
    ]
    serialized = json.dumps(
        [
            [item.room_id, item.source_path, [message.__dict__ for message in item.messages]]
            for item in imports
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "version": MIGRATION_VERSION,
        "output_root": str(root),
        "rooms": rooms,
        "room_count": len(imports),
        "message_count": sum(len(item.messages) for item in imports),
        "fingerprint": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def _actor_id(display_name: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", display_name.strip()).strip("-").lower()
    return f"legacy-{normalized or _message_digest(display_name)[:10]}"


def _message_digest(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


def _room_id_timestamp(room_id: str) -> datetime | None:
    match = re.match(r"^(\d{8}T\d{6}Z)", room_id)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _path_datetime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _path_timestamp(path: Path) -> str:
    return _path_datetime(path).isoformat()


def _backup_database(source: Path, target: Path) -> None:
    if not source.exists():
        return
    with closing(sqlite3.connect(source)) as source_db, closing(sqlite3.connect(target)) as target_db:
        source_db.backup(target_db)
