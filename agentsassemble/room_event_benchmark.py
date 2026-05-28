from __future__ import annotations

import json
import platform
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agentsassemble.live_agent_flow import (
    DEFAULT_FLOW_FAIRNESS_MAX_LEAD,
    DEFAULT_FLOW_FAIRNESS_MIN_GAP,
    DEFAULT_FLOW_FAIRNESS_RECENT_WINDOW,
    DEFAULT_FLOW_FAIRNESS_START_ORDER,
    flow_should_yield_for_fairness,
)
from agentsassemble.meeting_events import (
    append_live_event,
    append_lobby_event_to_file,
    read_live_events,
    read_live_events_after,
    read_lobby_events,
    read_lobby_events_after,
)


BENCHMARK_SCHEMA_VERSION = 1
SCHEDULER_IMBALANCE_MARGIN = 0.5
# A 10k-event O(n) scan should stay comfortably below this on local dev machines;
# this is a regression tripwire, not a product latency SLA.
SCHEDULER_P99_LATENCY_CEILING_MS = 75.0
SCHEDULER_LATENCY_EVENT_COUNT = 10_000
SCHEDULER_LATENCY_CALLS = 60


@dataclass(frozen=True)
class RoomEventBenchmarkOptions:
    output_root: Path | None = None
    events: int = 500
    read_window: int = 80
    warmup_events: int = 20
    agent_count: int = 5
    cleanup: bool = True


def run_room_event_benchmark(options: RoomEventBenchmarkOptions) -> dict[str, object]:
    events = max(1, int(options.events))
    read_window = max(1, int(options.read_window))
    warmup_events = max(0, int(options.warmup_events))
    agent_count = max(1, int(options.agent_count))
    run_root, temporary_root = _benchmark_run_root(options.output_root)
    run_root.mkdir(parents=True, exist_ok=False)
    lobby_path = run_root / "lobby.jsonl"
    meeting_dir = run_root / "meetings" / "benchmark-room"
    all_lobby_ids: list[str] = []
    all_live_ids: list[str] = []
    measured_lobby_appends: list[float] = []
    measured_live_appends: list[float] = []
    cleanup_removed = False
    try:
        for index in range(warmup_events):
            lobby_event = append_lobby_event_to_file(
                lobby_path,
                _lobby_payload(index=index, warmup=True),
                live_agent_endpoint=True,
                allow_flow_metadata=True,
            )
            live_event = append_live_event(meeting_dir, _live_payload(index=index, warmup=True))
            all_lobby_ids.append(str(lobby_event.get("id") or ""))
            all_live_ids.append(str(live_event.get("id") or ""))
        for index in range(events):
            lobby_event, lobby_ms = _timed_ms(
                lambda index=index: append_lobby_event_to_file(
                    lobby_path,
                    _lobby_payload(index=index, warmup=False),
                    live_agent_endpoint=True,
                    allow_flow_metadata=True,
                )
            )
            live_event, live_ms = _timed_ms(lambda index=index: append_live_event(meeting_dir, _live_payload(index=index, warmup=False)))
            all_lobby_ids.append(str(lobby_event.get("id") or ""))
            all_live_ids.append(str(live_event.get("id") or ""))
            measured_lobby_appends.append(lobby_ms)
            measured_live_appends.append(live_ms)

        lobby_read_after = _measure_read_after_lobby(lobby_path, all_lobby_ids, read_window)
        live_read_after = _measure_read_after_live(meeting_dir, all_live_ids, read_window)
        _, lobby_tail_ms = _timed_ms(lambda: read_lobby_events(lobby_path, limit=read_window))
        _, live_tail_ms = _timed_ms(lambda: read_live_events(meeting_dir, limit=read_window))
        fairness = flow_speaking_distribution(_synthetic_flow_events(agent_count=agent_count, turns=events), flow_id="benchmark-flow")
        scheduler_comparison = flow_scheduler_comparison(agent_count=agent_count, turns=events)
        payload: dict[str, object] = {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "benchmark": "room_event_log_v1",
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "implementation": platform.python_implementation(),
            },
            "params": {
                "events": events,
                "read_window": read_window,
                "warmup_events": warmup_events,
                "agent_count": agent_count,
                "cleanup": bool(options.cleanup),
            },
            "paths": {
                "run_root": str(run_root),
                "lobby_log": str(lobby_path),
                "live_log": str(meeting_dir / "live_events.jsonl"),
                "temporary_root": str(temporary_root) if temporary_root else "",
            },
            "metrics": {
                "lobby_append_ms": _latency_stats(measured_lobby_appends),
                "live_append_ms": _latency_stats(measured_live_appends),
                "lobby_read_after_cursor_ms": _latency_stats(lobby_read_after),
                "live_read_after_cursor_ms": _latency_stats(live_read_after),
                "lobby_tail_read_ms": _single_latency(lobby_tail_ms),
                "live_tail_read_ms": _single_latency(live_tail_ms),
                "flow_speaking_distribution": fairness,
                "flow_scheduler_comparison": scheduler_comparison,
            },
            "notes": [
                "This benchmark calls the existing meeting_events append/read functions and does not add fsync.",
                "Tail metrics use read_lobby_events/read_live_events with the configured read_window.",
                "Flow scheduler comparison is synthetic and measures turn distribution, not provider quality.",
                "SSE delivery time, queue wait time, and backpressure counts are out of scope for this slice.",
                "This is an operator regression signal, not an SLA.",
            ],
        }
    finally:
        if options.cleanup:
            shutil.rmtree(run_root, ignore_errors=True)
            cleanup_removed = not run_root.exists()
            if temporary_root is not None:
                shutil.rmtree(temporary_root, ignore_errors=True)
        else:
            cleanup_removed = False
    payload["cleanup_removed"] = cleanup_removed
    return payload


def flow_scheduler_comparison(*, agent_count: int, turns: int) -> dict[str, object]:
    clean_agent_count = max(1, int(agent_count))
    clean_turns = max(1, int(turns))
    participant_ids = [f"bench-agent-{index}" for index in range(clean_agent_count)]
    off_events = _synthetic_unfair_flow_events(agent_count=clean_agent_count, turns=clean_turns)
    on_events = _simulate_fair_scheduler_flow_events(agent_count=clean_agent_count, turns=clean_turns)
    off_distribution = flow_speaking_distribution(off_events, flow_id="benchmark-flow", participant_agent_ids=participant_ids)
    on_distribution = flow_speaking_distribution(on_events, flow_id="benchmark-flow", participant_agent_ids=participant_ids)
    off_normalized = _normalized_imbalance(off_distribution)
    on_normalized = _normalized_imbalance(on_distribution)
    return {
        "definition": "normalized_imbalance=(max_agent_speaking_count-min_agent_speaking_count)/total_speaking_turns",
        "scheduler_off": {**off_distribution, "normalized_imbalance": off_normalized},
        "scheduler_on": {**on_distribution, "normalized_imbalance": on_normalized},
        "normalized_improvement": round(off_normalized - on_normalized, 6),
        "fairness_params": {
            "recent_window": DEFAULT_FLOW_FAIRNESS_RECENT_WINDOW,
            "min_gap": DEFAULT_FLOW_FAIRNESS_MIN_GAP,
            "max_lead": DEFAULT_FLOW_FAIRNESS_MAX_LEAD,
            "start_order": DEFAULT_FLOW_FAIRNESS_START_ORDER,
        },
        "predicate_latency_ms": _measure_fairness_predicate_latency(clean_agent_count),
    }


def flow_speaking_distribution(
    events: list[dict[str, object]],
    *,
    flow_id: str,
    participant_agent_ids: list[str] | None = None,
) -> dict[str, object]:
    counts: dict[str, int] = {
        str(agent_id): 0
        for agent_id in (participant_agent_ids or [])
        if str(agent_id).strip()
    }
    for event in events:
        if str(event.get("flow_id") or "") != flow_id:
            continue
        action = str(event.get("flow_action") or "")
        if action == "wait" or not action:
            continue
        actor_id = str(event.get("actor_id") or "").strip()
        if not actor_id:
            continue
        counts[actor_id] = counts.get(actor_id, 0) + 1
    values = list(counts.values())
    total = sum(values)
    max_count = max(values) if values else 0
    min_count = min(values) if values else 0
    imbalance_ratio = float(max_count / max(min_count, 1)) if values else 0.0
    return {
        "definition": "imbalance_ratio=max_agent_speaking_count/max(min_agent_speaking_count,1)",
        "counts": dict(sorted(counts.items())),
        "total_speaking_turns": total,
        "agent_count": len(counts),
        "max_count": max_count,
        "min_count": min_count,
        "spread": max_count - min_count,
        "imbalance_ratio": imbalance_ratio,
    }


def _normalized_imbalance(distribution: dict[str, object]) -> float:
    total = int(distribution.get("total_speaking_turns") or 0)
    if total <= 0:
        return 0.0
    spread = int(distribution.get("spread") or 0)
    return round(spread / total, 6)


def _benchmark_run_root(output_root: Path | None) -> tuple[Path, Path | None]:
    if output_root is None:
        temporary_root = Path(tempfile.mkdtemp(prefix="agentsassemble-room-benchmark-"))
        return temporary_root / "run", temporary_root
    return Path(output_root) / f"room-event-benchmark-{uuid4().hex[:8]}", None


def _timed_ms(callback):
    started = time.perf_counter_ns()
    result = callback()
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return result, elapsed_ms


def _measure_read_after_lobby(path: Path, event_ids: list[str], read_window: int) -> list[float]:
    samples = _cursor_samples(event_ids, read_window)
    durations: list[float] = []
    for event_id in samples:
        _, elapsed_ms = _timed_ms(lambda event_id=event_id: read_lobby_events_after(path, event_id, limit=read_window))
        durations.append(elapsed_ms)
    return durations


def _measure_read_after_live(meeting_dir: Path, event_ids: list[str], read_window: int) -> list[float]:
    samples = _cursor_samples(event_ids, read_window)
    durations: list[float] = []
    for event_id in samples:
        _, elapsed_ms = _timed_ms(lambda event_id=event_id: read_live_events_after(meeting_dir, event_id, limit=read_window))
        durations.append(elapsed_ms)
    return durations


def _cursor_samples(event_ids: list[str], read_window: int) -> list[str]:
    candidates = [event_id for event_id in event_ids[-read_window:] if event_id]
    if not candidates:
        return [""]
    sample_count = min(len(candidates), 100)
    if sample_count == len(candidates):
        return candidates
    step = max(1, len(candidates) // sample_count)
    return candidates[::step][:sample_count]


def _latency_stats(values: list[float]) -> dict[str, object]:
    if not values:
        return {"count": 0, "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    sorted_values = sorted(max(0.0, float(value)) for value in values)
    return {
        "count": len(sorted_values),
        "avg_ms": round(sum(sorted_values) / len(sorted_values), 6),
        "p50_ms": round(_percentile_nearest_rank(sorted_values, 0.50), 6),
        "p95_ms": round(_percentile_nearest_rank(sorted_values, 0.95), 6),
        "p99_ms": round(_percentile_nearest_rank(sorted_values, 0.99), 6),
        "max_ms": round(sorted_values[-1], 6),
    }


def _single_latency(value: float) -> dict[str, object]:
    latency = round(max(0.0, float(value)), 6)
    return {"count": 1, "avg_ms": latency, "p50_ms": latency, "p95_ms": latency, "p99_ms": latency, "max_ms": latency}


def _percentile_nearest_rank(sorted_values: list[float], percentile: float) -> float:
    index = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * percentile + 0.999999) - 1))
    return sorted_values[index]


def _lobby_payload(*, index: int, warmup: bool) -> dict[str, object]:
    actor_number = index % 5
    return {
        "name": f"Bench Agent {actor_number}",
        "side": "other-agent",
        "kind": "message",
        "message": f"{'warmup' if warmup else 'bench'} lobby event {index} " + json.dumps({"index": index}, sort_keys=True),
        "actor_id": f"bench-agent-{actor_number}",
        "flow_id": "benchmark-flow",
        "flow_meeting_id": "benchmark-room",
        "flow_action": "speak",
    }


def _live_payload(*, index: int, warmup: bool) -> dict[str, object]:
    actor_number = index % 5
    return {
        "kind": "message",
        "meeting_id": "benchmark-room",
        "actor_id": f"bench-agent-{actor_number}",
        "display_name": f"Bench Agent {actor_number}",
        "content": f"{'warmup' if warmup else 'bench'} live event {index}",
    }


def _synthetic_flow_events(*, agent_count: int, turns: int) -> list[dict[str, object]]:
    return [
        {
            "flow_id": "benchmark-flow",
            "flow_action": "speak",
            "actor_id": f"bench-agent-{index % agent_count}",
        }
        for index in range(max(1, turns))
    ]


def _synthetic_unfair_flow_events(*, agent_count: int, turns: int) -> list[dict[str, object]]:
    del agent_count
    return [
        {
            "flow_id": "benchmark-flow",
            "flow_action": "speak",
            "actor_id": "bench-agent-0",
        }
        for _ in range(max(1, turns))
    ]


def _simulate_fair_scheduler_flow_events(*, agent_count: int, turns: int) -> list[dict[str, object]]:
    participant_ids = [f"bench-agent-{index}" for index in range(max(1, agent_count))]
    events: list[dict[str, object]] = []
    for _ in range(max(1, turns)):
        speaker = participant_ids[0]
        for candidate in participant_ids:
            if not flow_should_yield_for_fairness(
                events,
                flow_id="benchmark-flow",
                agent_id=candidate,
                participant_agent_ids=participant_ids,
                max_lead=DEFAULT_FLOW_FAIRNESS_MAX_LEAD,
                recent_window=DEFAULT_FLOW_FAIRNESS_RECENT_WINDOW,
                min_gap=DEFAULT_FLOW_FAIRNESS_MIN_GAP,
                start_order=DEFAULT_FLOW_FAIRNESS_START_ORDER,
            ):
                speaker = candidate
                break
        events.append({"flow_id": "benchmark-flow", "flow_action": "speak", "actor_id": speaker})
    return events


def _measure_fairness_predicate_latency(agent_count: int) -> dict[str, object]:
    participant_ids = [f"bench-agent-{index}" for index in range(max(1, agent_count))]
    events = _synthetic_flow_events(agent_count=len(participant_ids), turns=SCHEDULER_LATENCY_EVENT_COUNT)
    durations: list[float] = []
    for index in range(SCHEDULER_LATENCY_CALLS):
        agent_id = participant_ids[index % len(participant_ids)]
        _, elapsed_ms = _timed_ms(
            lambda agent_id=agent_id: flow_should_yield_for_fairness(
                events,
                flow_id="benchmark-flow",
                agent_id=agent_id,
                participant_agent_ids=participant_ids,
                max_lead=DEFAULT_FLOW_FAIRNESS_MAX_LEAD,
                recent_window=DEFAULT_FLOW_FAIRNESS_RECENT_WINDOW,
                min_gap=DEFAULT_FLOW_FAIRNESS_MIN_GAP,
                start_order=DEFAULT_FLOW_FAIRNESS_START_ORDER,
            )
        )
        durations.append(elapsed_ms)
    stats = _latency_stats(durations)
    stats["events_scanned"] = SCHEDULER_LATENCY_EVENT_COUNT
    stats["calls"] = SCHEDULER_LATENCY_CALLS
    return stats
