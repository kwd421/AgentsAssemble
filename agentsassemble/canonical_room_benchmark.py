from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.room_context import project_room_context
from agentsassemble.room_database import VISIBLE, open_room_database
from agentsassemble.room_store import RoomStore


CANONICAL_ROOM_BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_CANONICAL_BENCHMARK_EVENTS = 100_000
DEFAULT_CANONICAL_BENCHMARK_AGENTS = 10
DEFAULT_CANONICAL_BENCHMARK_WINDOW = 200
DEFAULT_CANONICAL_BENCHMARK_SAMPLES = 50


@dataclass(frozen=True)
class CanonicalRoomBenchmarkOptions:
    output_root: Path | None = None
    events: int = DEFAULT_CANONICAL_BENCHMARK_EVENTS
    agent_count: int = DEFAULT_CANONICAL_BENCHMARK_AGENTS
    read_window: int = DEFAULT_CANONICAL_BENCHMARK_WINDOW
    samples: int = DEFAULT_CANONICAL_BENCHMARK_SAMPLES
    cleanup: bool = True


def run_canonical_room_benchmark(options: CanonicalRoomBenchmarkOptions) -> dict[str, object]:
    event_count = max(1, int(options.events))
    agent_count = max(1, int(options.agent_count))
    read_window = max(1, min(1000, int(options.read_window)))
    samples = max(1, int(options.samples))
    run_root, temporary_root = _run_root(options.output_root)
    state_root = run_root / "state"
    room_id = "benchmark-room"
    run_root.mkdir(parents=True, exist_ok=False)
    rss_start = _rss_kb(os.getpid())
    cleanup_removed = False
    payload: dict[str, object] = {}
    try:
        store = RoomStore(state_root)
        store.create_room(room_id, label="Canonical Room Benchmark")
        participant_ids = _register_agents(store, room_id, agent_count)
        seed_started = time.perf_counter()
        _bulk_seed_events(store, room_id, event_count, participant_ids)
        seed_elapsed_ms = round((time.perf_counter() - seed_started) * 1000, 3)
        latest_seq = store.latest_event_sequence(room_id)

        append_latencies = _measure(
            samples,
            lambda index: store.append_event(
                room_id,
                "message_final",
                participant_id="benchmark-human",
                participant_type="human",
                actor_id="benchmark-human",
                actor_type="human",
                display_name="Benchmark Human",
                content=f"measured append {index}",
            ),
        )
        latest_seq = store.latest_event_sequence(room_id)
        latest_window_latencies = _measure(
            samples,
            lambda _index: store.read_events(room_id, limit=read_window, newest=True),
        )
        reconnect_cursor = max(0, latest_seq - read_window)
        reconnect_latencies = _measure(
            samples,
            lambda _index: store.read_events(room_id, after_seq=reconnect_cursor, limit=read_window),
        )
        history_cursor = max(2, latest_seq // 2)
        history_latencies = _measure(
            samples,
            lambda _index: store.read_events(room_id, before_seq=history_cursor, limit=read_window, newest=True),
        )
        context_latencies, context_bounds = _measure_agent_contexts(
            store,
            room_id,
            participant_ids,
            latest_seq=latest_seq,
            samples=samples,
        )
        lookup_event_id = f"benchmark-event-{max(1, event_count // 2):09d}"
        event_lookup_latencies = _measure(
            samples,
            lambda _index: store.event_by_id(room_id, lookup_event_id),
        )
        session_lookup_latencies = _measure(
            samples,
            lambda index: store.session(room_id, participant_ids[index % len(participant_ids)]),
        )
        query_plan = _indexed_query_plan(store.database_path, room_id, history_cursor, read_window)
        database_size = _database_size(store.database_path)
        rss_end = _rss_kb(os.getpid())
        measured_count = store.event_count(room_id)
        metrics = {
            "bulk_seed_ms": seed_elapsed_ms,
            "append_ms": _latency_stats(append_latencies),
            "latest_window_ms": _latency_stats(latest_window_latencies),
            "reconnect_after_seq_ms": _latency_stats(reconnect_latencies),
            "history_before_seq_ms": _latency_stats(history_latencies),
            "agent_context_ms": _latency_stats(context_latencies),
            "event_by_id_ms": _latency_stats(event_lookup_latencies),
            "session_lookup_ms": _latency_stats(session_lookup_latencies),
            "database_bytes": database_size,
            "rss_kb_start": rss_start,
            "rss_kb_end": rss_end,
            "rss_kb_delta": rss_end - rss_start if rss_start and rss_end else None,
        }
        acceptance = {
            "event_count_matches": measured_count == event_count + samples + 1,
            "agent_count_matches": len(store.sessions(room_id)) == agent_count,
            "context_is_bounded": context_bounds["max_events"] <= 12
            and context_bounds["max_chars"] <= 4000,
            "latest_window_p95_under_50_ms": metrics["latest_window_ms"]["p95_ms"] <= 50.0,
            "reconnect_p95_under_50_ms": metrics["reconnect_after_seq_ms"]["p95_ms"] <= 50.0,
            "context_p95_under_100_ms": metrics["agent_context_ms"]["p95_ms"] <= 100.0,
            "indexed_query_plan": not query_plan["full_scan_detected"],
        }
        payload = {
            "schema_version": CANONICAL_ROOM_BENCHMARK_SCHEMA_VERSION,
            "benchmark": "canonical_room_sqlite_v1",
            "status": "ok" if all(acceptance.values()) else "error",
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "implementation": platform.python_implementation(),
            },
            "params": {
                "events": event_count,
                "agent_count": agent_count,
                "read_window": read_window,
                "samples": samples,
            },
            "measured_event_count": measured_count,
            "metrics": metrics,
            "context_bounds": context_bounds,
            "query_plan": query_plan,
            "acceptance": acceptance,
            "paths": {
                "run_root": str(run_root),
                "database": str(store.database_path),
            },
            "notes": [
                "Bulk seeding prepares long-room cardinality; append_ms uses the production RoomStore append path.",
                "Every read and provider context sample uses the production indexed RoomStore and room_context APIs.",
                "This benchmark starts no provider process and sends no network request.",
            ],
        }
    finally:
        if options.cleanup:
            shutil.rmtree(run_root, ignore_errors=True)
            cleanup_removed = not run_root.exists()
            if temporary_root is not None:
                shutil.rmtree(temporary_root, ignore_errors=True)
    payload["cleanup_removed"] = cleanup_removed
    return payload


def _register_agents(store: RoomStore, room_id: str, agent_count: int) -> list[str]:
    participant_ids: list[str] = []
    for index in range(agent_count):
        participant_id = f"benchmark-agent-{index + 1:02d}"
        participant_ids.append(participant_id)
        store.upsert_participant(
            room_id,
            {
                "participant_id": participant_id,
                "display_name": f"Benchmark Agent {index + 1}",
                "participant_type": "agent",
                "role": "agent",
                "status": "joined",
            },
        )
        store.upsert_session(
            room_id,
            {
                "session_id": participant_id,
                "participant_id": participant_id,
                "display_name": f"Benchmark Agent {index + 1}",
                "status": "attached",
                "runtime_status": "idle",
                "provider_kind": "benchmark_live_session",
                "runtime_kind": "live_cli",
                "connection_kind": "native_cli_bridge",
                "last_provider_sync_seq": 0,
                "last_seen_seq": 0,
            },
        )
    return participant_ids


def _bulk_seed_events(
    store: RoomStore,
    room_id: str,
    event_count: int,
    participant_ids: list[str],
) -> None:
    connection = open_room_database(store.database_path)
    try:
        starting_seq = store.latest_event_sequence(room_id) + 1
        now = datetime.now(UTC).isoformat()
        connection.execute("BEGIN IMMEDIATE")
        try:
            batch: list[tuple[object, ...]] = []
            for index in range(1, event_count + 1):
                sequence = starting_seq + index - 1
                actor_id = participant_ids[index % len(participant_ids)] if index % 3 else "benchmark-human"
                actor_type = "agent" if actor_id != "benchmark-human" else "human"
                event_type = "message_delta" if index % 20 == 0 else "message_final"
                event_id = f"benchmark-event-{index:09d}"
                event = {
                    "v": 1,
                    "id": event_id,
                    "seq": sequence,
                    "created_at": now,
                    "room_id": room_id,
                    "type": event_type,
                    "actor": {"participant_id": actor_id, "participant_type": actor_type},
                    "participant_id": actor_id,
                    "participant_type": actor_type,
                    "actor_id": actor_id,
                    "actor_type": actor_type,
                    "display_name": actor_id,
                    "turn_id": f"benchmark-turn-{index:09d}",
                    "content": f"bounded benchmark message {index}",
                }
                batch.append(
                    (
                        room_id,
                        sequence,
                        event_id,
                        event_type,
                        actor_id,
                        str(event["turn_id"]),
                        now,
                        VISIBLE,
                        json.dumps(event, ensure_ascii=True, separators=(",", ":")),
                    )
                )
                if len(batch) >= 5000:
                    _insert_event_batch(connection, batch)
                    batch.clear()
            if batch:
                _insert_event_batch(connection, batch)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()


def _insert_event_batch(connection, batch: list[tuple[object, ...]]) -> None:
    connection.executemany(
        """INSERT INTO room_events(
               room_id, seq, event_id, event_type, actor_id, turn_id,
               created_at, visibility, payload_json
           ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        batch,
    )


def _measure_agent_contexts(
    store: RoomStore,
    room_id: str,
    participant_ids: list[str],
    *,
    latest_seq: int,
    samples: int,
) -> tuple[list[float], dict[str, int]]:
    durations: list[float] = []
    max_events = 0
    max_chars = 0
    for index in range(samples):
        participant_id = participant_ids[index % len(participant_ids)]
        after_seq = max(0, latest_seq - 1000 - (index % len(participant_ids)) * 17)
        started = time.perf_counter_ns()
        window = project_room_context(
            store,
            room_id=room_id,
            participant_id=participant_id,
            after_seq=after_seq,
        )
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
        max_events = max(max_events, len(window.events))
        max_chars = max(max_chars, len(window.text))
    return durations, {"max_events": max_events, "max_chars": max_chars}


def _measure(samples: int, operation) -> list[float]:
    durations: list[float] = []
    for index in range(samples):
        started = time.perf_counter_ns()
        operation(index)
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    return durations


def _latency_stats(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 3),
        "p50_ms": round(_percentile(ordered, 50), 3),
        "p95_ms": round(_percentile(ordered, 95), 3),
        "p99_ms": round(_percentile(ordered, 99), 3),
        "max_ms": round(ordered[-1], 3),
        "avg_ms": round(sum(ordered) / len(ordered), 3),
    }


def _percentile(ordered: list[float], percentile: int) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0, min(100, percentile)) / 100
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _indexed_query_plan(database_path: Path, room_id: str, before_seq: int, limit: int) -> dict[str, object]:
    connection = open_room_database(database_path)
    try:
        rows = connection.execute(
            """EXPLAIN QUERY PLAN
               SELECT payload_json FROM room_events
               WHERE room_id = ? AND visibility = ? AND seq < ?
               ORDER BY seq DESC LIMIT ?""",
            (room_id, VISIBLE, before_seq, limit),
        ).fetchall()
    finally:
        connection.close()
    details = [str(row["detail"]) for row in rows]
    return {
        "details": details,
        "full_scan_detected": any("SCAN ROOM_EVENTS" in detail.upper() for detail in details),
    }


def _database_size(database_path: Path) -> int:
    return sum(
        path.stat().st_size
        for path in (database_path, Path(f"{database_path}-wal"), Path(f"{database_path}-shm"))
        if path.exists()
    )


def _rss_kb(pid: int) -> int:
    try:
        output = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True, timeout=2.0)
        return int(output.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _run_root(output_root: Path | None) -> tuple[Path, Path | None]:
    if output_root is None:
        temporary_root = Path(tempfile.mkdtemp(prefix="agentsassemble-canonical-room-benchmark-"))
        return temporary_root / "run", temporary_root
    return Path(output_root) / f"canonical-room-benchmark-{uuid4().hex[:8]}", None
