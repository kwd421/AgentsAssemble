from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.identity_store import default_identity_db_path
from agentsassemble.room_store import RoomStore


PLAN_FILENAME = ".prune-empty-plan.json"
HOST_PARTICIPANT_IDS = {"operator-local", "host", "local-host"}


@dataclass(frozen=True)
class EmptyRoomCandidate:
    room_id: str
    event_count: int
    participant_count: int


def find_empty_rooms(output_root: Path) -> list[EmptyRoomCandidate]:
    store = RoomStore(output_root)
    identity_path = default_identity_db_path(output_root)
    identity = sqlite3.connect(identity_path) if identity_path.exists() else None
    try:
        candidates: list[EmptyRoomCandidate] = []
        with sqlite3.connect(store.database_path) as room_db:
            room_db.row_factory = sqlite3.Row
            rows = room_db.execute("SELECT room_id FROM rooms ORDER BY room_id").fetchall()
            for row in rows:
                room_id = str(row["room_id"])
                events = room_db.execute(
                    "SELECT event_type FROM room_events WHERE room_id = ? ORDER BY seq", (room_id,)
                ).fetchall()
                if any(str(event["event_type"]) != "room_created" for event in events):
                    continue
                session_count = int(
                    room_db.execute(
                        "SELECT COUNT(*) FROM agent_sessions WHERE room_id = ?", (room_id,)
                    ).fetchone()[0]
                )
                if session_count:
                    continue
                participants = room_db.execute(
                    "SELECT participant_id, data_json FROM participants WHERE room_id = ?", (room_id,)
                ).fetchall()
                if not _only_local_host(participants):
                    continue
                if identity is not None and not _identity_members_are_local_host(identity, room_id):
                    continue
                if _has_room_artifacts(output_root, room_id):
                    continue
                candidates.append(EmptyRoomCandidate(room_id, len(events), len(participants)))
        return candidates
    finally:
        if identity is not None:
            identity.close()


def prune_empty_rooms(output_root: Path, *, apply: bool) -> dict[str, object]:
    output_root = output_root.expanduser().resolve()
    candidates = find_empty_rooms(output_root)
    payload = {
        "version": 1,
        "output_root": str(output_root),
        "candidate_room_ids": [candidate.room_id for candidate in candidates],
        "fingerprint": _candidate_fingerprint(candidates),
    }
    plan_path = output_root / "rooms" / PLAN_FILENAME
    if not apply:
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {**payload, "status": "dry_run", "plan_path": str(plan_path)}

    if not plan_path.exists():
        raise ValueError("Run prune-empty --dry-run before --apply.")
    planned = json.loads(plan_path.read_text(encoding="utf-8"))
    if (
        planned.get("candidate_room_ids") != payload["candidate_room_ids"]
        or planned.get("fingerprint") != payload["fingerprint"]
    ):
        raise ValueError("Empty-room candidates changed after dry-run; no rooms were deleted.")

    room_db_path = RoomStore(output_root).database_path
    identity_db_path = default_identity_db_path(output_root)
    backup_dir = output_root / "backups" / datetime.now(UTC).strftime("prune-empty-%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=False)
    _backup_database(room_db_path, backup_dir / room_db_path.name)
    if identity_db_path.exists():
        _backup_database(identity_db_path, backup_dir / identity_db_path.name)

    room_ids = list(payload["candidate_room_ids"])
    connection = sqlite3.connect(room_db_path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if identity_db_path.exists():
            connection.execute("ATTACH DATABASE ? AS identity_db", (str(identity_db_path),))
        connection.execute("BEGIN IMMEDIATE")
        try:
            for room_id in room_ids:
                for table in ("command_results", "room_events", "agent_sessions", "participants", "rooms"):
                    connection.execute(f"DELETE FROM {table} WHERE room_id = ?", (room_id,))
                connection.execute(
                    "INSERT OR REPLACE INTO deleted_rooms(room_id, deleted_at, reason) VALUES(?, ?, ?)",
                    (room_id, datetime.now(UTC).isoformat(), "prune_empty"),
                )
                if identity_db_path.exists():
                    connection.execute("DELETE FROM identity_db.memberships WHERE meeting_id = ?", (room_id,))
                    connection.execute("DELETE FROM identity_db.rooms WHERE room_id = ?", (room_id,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()
    plan_path.unlink(missing_ok=True)
    return {
        **payload,
        "status": "applied",
        "deleted_count": len(room_ids),
        "backup_dir": str(backup_dir),
    }


def _only_local_host(rows: list[sqlite3.Row]) -> bool:
    for row in rows:
        participant_id = str(row["participant_id"] or "")
        try:
            payload = json.loads(str(row["data_json"] or "{}"))
        except json.JSONDecodeError:
            return False
        if participant_id not in HOST_PARTICIPANT_IDS and not bool(payload.get("is_host")):
            return False
        if str(payload.get("participant_type") or "human") not in {"human", "operator"}:
            return False
    return True


def _identity_members_are_local_host(connection: sqlite3.Connection, room_id: str) -> bool:
    rows = connection.execute(
        "SELECT participant_id, participant_type, is_host FROM memberships WHERE meeting_id = ?",
        (room_id,),
    ).fetchall()
    return all(
        (str(participant_id) in HOST_PARTICIPANT_IDS or bool(is_host))
        and str(participant_type or "human") in {"human", "operator"}
        for participant_id, participant_type, is_host in rows
    )


def _has_room_artifacts(output_root: Path, room_id: str) -> bool:
    for base in (output_root / "rooms" / room_id, output_root / "meetings" / room_id):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.name not in {
                "room.json", "participants.json", "sessions.json", "events.jsonl", "commands.json"
            }:
                return True
    return False


def _candidate_fingerprint(candidates: list[EmptyRoomCandidate]) -> str:
    serialized = json.dumps(
        [candidate.__dict__ for candidate in candidates], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _backup_database(source: Path, target: Path) -> None:
    if not source.exists():
        return
    with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
        source_db.backup(target_db)
