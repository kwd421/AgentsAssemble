from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from agentsassemble.live_agents import read_live_agents
from agentsassemble.meeting_events import append_lobby_event_to_file, read_lobby_events

MAX_PROBE_TIMEOUT_SECONDS = 60.0
DEFAULT_PROBE_TIMEOUT_SECONDS = 12.0
DEFAULT_PROBE_POLL_INTERVAL = 0.05


def run_live_agent_probe(
    output_root: Path,
    agent_id: str,
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_PROBE_POLL_INTERVAL,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    clean_agent_id = str(agent_id or "").strip()
    if not clean_agent_id:
        raise ValueError("Agent id is required.")
    agents = read_live_agents(output_root)
    agent = next((item for item in agents if item.get("agent_id") == clean_agent_id), None)
    if agent is None:
        raise ValueError(f"Live agent {clean_agent_id} was not found.")
    status = str(agent.get("status") or "offline")
    if status not in {"online", "working"}:
        return {
            "status": "skipped",
            "agent_id": clean_agent_id,
            "agent_status": status,
            "reason": "agent is not currently live",
        }

    display_name = str(agent.get("display_name") or clean_agent_id)
    probe_event = append_lobby_event_to_file(
        output_root / "lobby.jsonl",
        {
            "name": "AgentsAssemble Probe",
            "side": "mine",
            "kind": "message",
            "message": _probe_message(display_name, clean_agent_id),
            "auto_chain_depth": 0,
        },
    )
    source_event_id = str(probe_event.get("id") or "")
    reply = _wait_for_probe_reply(
        output_root,
        clean_agent_id,
        source_event_id,
        timeout_seconds=safe_probe_timeout(timeout_seconds),
        poll_interval=max(0.0, float(poll_interval)),
        sleep_fn=sleep_fn,
    )
    if reply is None:
        return {"status": "timeout", "agent_id": clean_agent_id, "source_event_id": source_event_id}
    return {
        "status": "ok",
        "agent_id": clean_agent_id,
        "source_event_id": source_event_id,
        "reply_event_id": str(reply.get("id") or ""),
        "reply": _safe_reply(reply),
    }


def _wait_for_probe_reply(
    output_root: Path,
    agent_id: str,
    source_event_id: str,
    *,
    timeout_seconds: float,
    poll_interval: float,
    sleep_fn: Callable[[float], None],
) -> dict[str, object] | None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        reply = _matching_probe_reply(
            read_lobby_events(output_root / "lobby.jsonl"),
            agent_id=agent_id,
            source_event_id=source_event_id,
        )
        if reply is not None:
            return reply
        if time.monotonic() >= deadline:
            return None
        sleep_fn(min(poll_interval, max(0.0, deadline - time.monotonic())))


def _matching_probe_reply(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> dict[str, object] | None:
    for event in events:
        if event.get("actor_id") != agent_id:
            continue
        if event.get("source_event_id") != source_event_id:
            continue
        if event.get("live_agent_endpoint") is not True:
            continue
        if not str(event.get("message") or "").strip():
            continue
        return event
    return None


def _safe_reply(event: dict[str, object]) -> dict[str, object]:
    return {
        "id": str(event.get("id") or ""),
        "actor_id": str(event.get("actor_id") or ""),
        "source_event_id": str(event.get("source_event_id") or ""),
        "message": str(event.get("message") or "")[:500],
    }


def _probe_message(display_name: str, agent_id: str) -> str:
    return f"{display_name} ({agent_id}), reply once to this AgentsAssemble probe."


def safe_probe_timeout(value: float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return DEFAULT_PROBE_TIMEOUT_SECONDS
    if timeout < 0:
        return 0.0
    return min(timeout, MAX_PROBE_TIMEOUT_SECONDS)
