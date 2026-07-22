"""Retained live-agent room observation and reply CLI commands."""
from __future__ import annotations

import argparse
import json
import shlex
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from agentsassemble.legacy.live_agent.runtime.timing import live_agent_poll_sleep_seconds
from agentsassemble.legacy.meeting.support.live_meeting_memory import compact_live_meeting_memory
from agentsassemble.live_agent_runner import (
    official_turn_request_candidate,
    should_reply_to_event,
)


PERSONA_OFFICIAL_TURN_BLOCK_REASON = "persona_context_blocked_official_turn"


@dataclass(frozen=True)
class LegacyRoomInteractionCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]


def run_legacy_room_interaction_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyRoomInteractionCliRuntime,
) -> int | None:
    handlers = {
        "wait-room-event": _run_wait_room_event,
        "read-since": _run_read_since,
        "dm-reply": _run_dm_reply,
        "official-reply": _run_answer_turn,
        "answer-turn": _run_answer_turn,
        "wait-official-turn": _run_wait_turn_request,
        "wait-turn-request": _run_wait_turn_request,
        "wait-next": _run_wait_next,
    }
    handler = handlers.get(str(getattr(args, "live_agent_command", "")))
    return handler(args, runtime) if handler is not None else None


def _run_wait_room_event(args: argparse.Namespace, runtime: LegacyRoomInteractionCliRuntime) -> int:
    deadline = runtime.monotonic() + float(args.timeout)
    last_room: dict[str, object] = {}
    while True:
        agent_id = urllib.parse.quote(args.agent_id, safe="")
        room = runtime.request_json(runtime.server_url(args.server, f"/api/live-agents/{agent_id}/room"))
        last_room = room
        candidate = _wait_room_event_candidate(args, room)
        if candidate is not None:
            payload = _wait_room_event_payload(args, room, candidate)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(_format_wait_room_event(payload))
            return 0
        remaining = deadline - runtime.monotonic()
        if remaining <= 0:
            payload = _wait_room_timeout_payload(args, last_room)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                cursor = payload.get("last_observed_event_id") or "(none)"
                print(f"no new room event after {cursor}")
            return 1
        sleep_interval = live_agent_poll_sleep_seconds(args.poll_interval)
        runtime.sleep(min(sleep_interval, remaining))


def _wait_room_event_candidate(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    display_name = str(agent.get("display_name") or "").strip()
    for event in _events_after_id(events, cursor):
        if not isinstance(event, dict):
            continue
        if _wait_room_self_event(args.agent_id, display_name, event):
            continue
        if _delegate_chain_depth(event) > int(args.max_chain_depth):
            continue
        if not str(event.get("message") or "").strip():
            continue
        if not str(event.get("id") or "").strip():
            continue
        return event
    return None


def _events_after_id(events: list[object], event_id: str) -> list[object]:
    if not event_id:
        return events
    for index, event in enumerate(events):
        if isinstance(event, dict) and str(event.get("id") or "") == event_id:
            return events[index + 1 :]
    return events


def _run_read_since(args: argparse.Namespace, runtime: LegacyRoomInteractionCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    room = runtime.request_json(runtime.server_url(args.server, f"/api/live-agents/{agent_id}/room"))
    payload = _live_agent_read_since_payload(args, room)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        lobby_cursor = payload.get("last_observed_event_id") or "(start)"
        live_cursor = payload.get("last_observed_live_event_id") or "(start)"
        dm_cursor = payload.get("last_observed_dm_event_id") or "(start)"
        print(
            "read-since "
            f"lobby {len(payload.get('lobby_events') if isinstance(payload.get('lobby_events'), list) else [])} "
            f"after {lobby_cursor}; "
            f"official {len(payload.get('live_events') if isinstance(payload.get('live_events'), list) else [])} "
            f"after {live_cursor}; "
            f"dm {len(payload.get('dm_events') if isinstance(payload.get('dm_events'), list) else [])} "
            f"after {dm_cursor}"
        )
    return 0


def _live_agent_read_since_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    lobby_cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    live_cursor = str(args.after_live_event_id or agent.get("last_observed_live_event_id") or "").strip()
    dm_cursor = str(args.after_dm_event_id or agent.get("last_observed_dm_event_id") or "").strip()
    lobby_events = [event for event in _events_after_id(_room_event_list(room, "lobby_events"), lobby_cursor) if isinstance(event, dict)]
    live_events = [event for event in _events_after_id(_room_event_list(room, "live_events"), live_cursor) if isinstance(event, dict)]
    dm_events = [event for event in _events_after_id(_room_event_list(room, "dm_events"), dm_cursor) if isinstance(event, dict)]
    next_lobby_cursor = _latest_observed_event_id(lobby_events, lobby_cursor)
    next_live_cursor = _latest_observed_event_id(live_events, live_cursor)
    next_dm_cursor = _latest_observed_event_id(dm_events, dm_cursor)
    meeting_id = str(room.get("meeting_id") or agent.get("meeting_id") or "").strip()
    return {
        "status": "ok",
        "agent_id": args.agent_id,
        "meeting_id": meeting_id,
        "last_observed_event_id": lobby_cursor,
        "last_observed_live_event_id": live_cursor,
        "last_observed_dm_event_id": dm_cursor,
        "next_last_observed_event_id": next_lobby_cursor,
        "next_last_observed_live_event_id": next_live_cursor,
        "next_last_observed_dm_event_id": next_dm_cursor,
        "lobby_events": lobby_events,
        "live_events": live_events,
        "dm_events": dm_events,
        "ack_command": _live_agent_read_since_ack_command(args, next_lobby_cursor, next_live_cursor, next_dm_cursor),
        "room": _wait_room_context(room, meeting_id=meeting_id),
    }


def _room_event_list(room: dict[str, object], key: str) -> list[object]:
    events = room.get(key)
    return events if isinstance(events, list) else []


def _live_agent_read_since_ack_command(args: argparse.Namespace, lobby_cursor: str, live_cursor: str, dm_cursor: str) -> list[str]:
    return [
        "python3",
        "-m",
        "agentsassemble.cli",
        "live-agent",
        "heartbeat",
        "--server",
        str(args.server),
        "--agent-id",
        str(args.agent_id),
        "--status",
        "online",
        "--last-error=",
        f"--last-observed-event-id={lobby_cursor}",
        f"--last-observed-live-event-id={live_cursor}",
        f"--last-observed-dm-event-id={dm_cursor}",
        "--json",
    ]


def _latest_observed_event_id(events: object, fallback: str) -> str:
    if not isinstance(events, list):
        return fallback
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if event_id:
            return event_id
    return fallback


def _wait_room_self_event(agent_id: str, display_name: str, event: dict[str, object]) -> bool:
    actor_id = str(event.get("actor_id") or "")
    if actor_id:
        return actor_id == agent_id
    return bool(display_name) and str(event.get("name") or "") == display_name


def _wait_room_event_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    auto_chain_depth = _delegate_chain_depth(event) + 1
    flow_id = str(event.get("flow_id") or "").strip()
    flow_meeting_id = str(event.get("flow_meeting_id") or room.get("meeting_id") or "").strip()
    reply_command = [
        "python3",
        "-m",
        "agentsassemble.cli",
        "live-agent",
        "say",
        "--server",
        str(args.server),
        "--agent-id",
        str(args.agent_id),
        "--source-event-id",
        event_id,
        "--auto-chain-depth",
        str(auto_chain_depth),
    ]
    if flow_id:
        reply_command.extend(["--flow-id", flow_id])
    if flow_meeting_id:
        reply_command.extend(["--flow-meeting-id", flow_meeting_id])
    reply_command.extend(["--", "<reply>"])
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "source_event_id": event_id,
        "auto_chain_depth": auto_chain_depth,
        "event": event,
        "reply_command": reply_command,
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or "")),
    }


def _wait_room_timeout_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    return {
        "status": "timeout",
        "agent_id": args.agent_id,
        "timeout_seconds": float(args.timeout),
        "last_observed_event_id": _latest_observed_event_id(room.get("lobby_events"), cursor),
    }


def _format_wait_room_event(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_id = str(event.get("id") or "room event")
    name = str(event.get("name") or event.get("actor_id") or "participant")
    message = str(event.get("message") or "").strip()
    return f"{event_id} {name}: {message}"


def _run_dm_reply(args: argparse.Namespace, runtime: LegacyRoomInteractionCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/dm-reply"),
        method="POST",
        payload={
            "source_event_id": args.source_event_id,
            "message": " ".join(args.message),
        },
    )
    event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(f"Answered DM {event.get('id') or args.source_event_id}")
    return 0


def _run_answer_turn(args: argparse.Namespace, runtime: LegacyRoomInteractionCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/official-turn"),
        method="POST",
        payload={
            "meeting_id": args.meeting_id,
            "source_event_id": args.source_event_id,
            "content": " ".join(args.message),
        },
    )
    event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(f"Answered official turn {event.get('id') or args.source_event_id}")
    return 0


def _run_wait_turn_request(args: argparse.Namespace, runtime: LegacyRoomInteractionCliRuntime) -> int:
    deadline = runtime.monotonic() + float(args.timeout)
    last_room: dict[str, object] = {}
    while True:
        agent_id = urllib.parse.quote(args.agent_id, safe="")
        room = runtime.request_json(runtime.server_url(args.server, f"/api/live-agents/{agent_id}/room"))
        last_room = room
        candidate = _wait_turn_request_candidate(args, room)
        if candidate is not None:
            payload = (
                _wait_persona_blocked_official_turn_payload(args, room, candidate)
                if _wait_agent_persona_blocks_official_turn(room)
                else _wait_turn_request_payload(args, room, candidate)
            )
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(_format_wait_persona_block(payload) if payload.get("action") == "persona_blocks_official_turn" else _format_wait_turn_request(payload))
            return 0
        remaining = deadline - runtime.monotonic()
        if remaining <= 0:
            payload = _wait_turn_request_timeout_payload(args, last_room)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                cursor = payload.get("last_observed_live_event_id") or "(none)"
                print(f"no new official turn request after {cursor}")
            return 1
        sleep_interval = live_agent_poll_sleep_seconds(args.poll_interval)
        runtime.sleep(min(sleep_interval, remaining))


def _wait_turn_request_candidate(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("live_events") if isinstance(room.get("live_events"), list) else []
    typed_events = [event for event in events if isinstance(event, dict)]
    requested_cursor = getattr(args, "after_live_event_id", None)
    if requested_cursor is None:
        requested_cursor = getattr(args, "after_event_id", "")
    cursor = str(requested_cursor or agent.get("last_observed_live_event_id") or "").strip()
    return official_turn_request_candidate(typed_events, args.agent_id, cursor)


PERSONA_OFFICIAL_TURN_BLOCK_REASON = "persona_context_blocked_official_turn"


def _wait_agent_persona_blocks_official_turn(room: dict[str, object]) -> bool:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    if not agent:
        return False
    mode = str(agent.get("character_mode") or "").strip()
    if mode == "off":
        return False
    has_persona = bool(str(agent.get("persona_card_id") or agent.get("persona_id") or "").strip())
    if not has_persona:
        return False
    return str(agent.get("connection_kind") or "").strip() in {"self_service", "live_session", "terminal_session", "remote_bridge"}


def _wait_turn_request_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    meeting_id = _wait_turn_request_meeting_id(room, event)
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "meeting_id": meeting_id,
        "source_event_id": event_id,
        "event": event,
        "reply_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "official-reply",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--meeting-id",
            meeting_id,
            "--source-event-id",
            event_id,
            "--",
            "<reply>",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or meeting_id)),
    }


def _wait_persona_blocked_official_turn_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    meeting_id = _wait_turn_request_meeting_id(room, event)
    return {
        "status": "event",
        "action": "persona_blocks_official_turn",
        "agent_id": args.agent_id,
        "meeting_id": meeting_id,
        "source_event_id": event_id,
        "reason": PERSONA_OFFICIAL_TURN_BLOCK_REASON,
        "attention": [PERSONA_OFFICIAL_TURN_BLOCK_REASON],
        "event": event,
        "ack_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "heartbeat",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--status",
            "online",
            "--last-error=",
            f"--last-attention={PERSONA_OFFICIAL_TURN_BLOCK_REASON}",
            f"--last-observed-live-event-id={event_id}",
            "--json",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or meeting_id)),
    }


def _format_wait_persona_block(payload: dict[str, object]) -> str:
    event_id = str(payload.get("source_event_id") or "official turn request")
    return f"persona_blocks_official_turn {event_id}: {PERSONA_OFFICIAL_TURN_BLOCK_REASON}"


def _wait_room_context(room: dict[str, object], *, meeting_id: str) -> dict[str, object]:
    context: dict[str, object] = {
        "meeting_id": meeting_id,
        "lobby_event_count": len(room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []),
        "live_event_count": len(room.get("live_events") if isinstance(room.get("live_events"), list) else []),
        "dm_event_count": len(room.get("dm_events") if isinstance(room.get("dm_events"), list) else []),
    }
    shared_memory = _wait_shared_memory(room)
    if shared_memory:
        context["shared_memory"] = shared_memory
    return context


def _wait_shared_memory(room: dict[str, object]) -> dict[str, object]:
    memory = room.get("shared_memory")
    if not isinstance(memory, dict):
        return {}
    return compact_live_meeting_memory(memory)


def _wait_turn_request_meeting_id(room: dict[str, object], event: dict[str, object]) -> str:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    return str(event.get("meeting_id") or room.get("meeting_id") or agent.get("meeting_id") or "").strip()


def _wait_turn_request_timeout_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    requested_cursor = getattr(args, "after_live_event_id", None)
    if requested_cursor is None:
        requested_cursor = getattr(args, "after_event_id", "")
    cursor = str(requested_cursor or agent.get("last_observed_live_event_id") or "").strip()
    return {
        "status": "timeout",
        "agent_id": args.agent_id,
        "timeout_seconds": float(args.timeout),
        "last_observed_live_event_id": _latest_observed_event_id(room.get("live_events"), cursor),
    }


def _format_wait_turn_request(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_id = str(event.get("id") or "official turn request")
    role_id = str(event.get("role_id") or event.get("target_agent_id") or payload.get("agent_id") or "agent")
    content = str(event.get("content") or "").strip()
    return f"{event_id} {role_id}: {content}"


def _wait_dm_candidate(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("dm_events") if isinstance(room.get("dm_events"), list) else []
    cursor = str(args.after_dm_event_id or agent.get("last_observed_dm_event_id") or "").strip()
    for event in _events_after_id(events, cursor):
        if not isinstance(event, dict):
            continue
        if str(event.get("side") or "") != "mine":
            continue
        if str(event.get("target_agent_id") or "").strip() != str(args.agent_id):
            continue
        if not str(event.get("id") or "").strip():
            continue
        if not str(event.get("message") or "").strip():
            continue
        return event
    return None


def _wait_dm_payload(args: argparse.Namespace, room: dict[str, object], event: dict[str, object]) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "source_event_id": event_id,
        "event": event,
        "reply_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "dm-reply",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--source-event-id",
            event_id,
            "--",
            "<reply>",
        ],
        "ack_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "heartbeat",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--status",
            "online",
            "--last-error=",
            f"--last-observed-dm-event-id={event_id}",
            "--json",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or "")),
    }


def _format_wait_dm(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_id = str(event.get("id") or "dm")
    message = str(event.get("message") or "").strip()
    return f"{event_id}: {message}"


def _run_wait_next(args: argparse.Namespace, runtime: LegacyRoomInteractionCliRuntime) -> int:
    deadline = runtime.monotonic() + float(args.timeout)
    last_room: dict[str, object] = {}
    while True:
        agent_id = urllib.parse.quote(args.agent_id, safe="")
        room = runtime.request_json(runtime.server_url(args.server, f"/api/live-agents/{agent_id}/room"))
        last_room = room
        dm_candidate = _wait_dm_candidate(args, room)
        if dm_candidate is not None:
            payload = _wait_dm_payload(args, room, dm_candidate)
            payload["action"] = "dm"
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"dm {_format_wait_dm(payload)}")
            return 0
        official_candidate = _wait_turn_request_candidate(args, room)
        if official_candidate is not None:
            if _wait_agent_persona_blocks_official_turn(room):
                payload = _wait_persona_blocked_official_turn_payload(args, room, official_candidate)
            else:
                payload = _wait_turn_request_payload(args, room, official_candidate)
                payload["action"] = "official_turn"
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                prefix = payload.get("action")
                if prefix == "persona_blocks_official_turn":
                    print(_format_wait_persona_block(payload))
                else:
                    print(f"official_turn {_format_wait_turn_request(payload)}")
            return 0
        return_packet_candidate = _wait_return_packet_candidate(args, room)
        if return_packet_candidate is not None:
            payload = _wait_return_packet_payload(args, room, return_packet_candidate)
            payload["action"] = "return_packet"
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"return_packet {_format_wait_return_packet(payload)}")
            return 0
        lobby_observation = _wait_next_lobby_observation(args, room)
        if lobby_observation is not None:
            action, lobby_candidate = lobby_observation
            payload = (
                _wait_room_event_payload(args, room, lobby_candidate)
                if action == "lobby"
                else _wait_lobby_observation_payload(args, room, lobby_candidate)
            )
            payload["action"] = action
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"{action} {_format_wait_room_event(payload)}")
            return 0
        remaining = deadline - runtime.monotonic()
        if remaining <= 0:
            payload = _wait_next_timeout_payload(args, last_room)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                lobby_cursor = payload.get("last_observed_event_id") or "(none)"
                live_cursor = payload.get("last_observed_live_event_id") or "(none)"
                dm_cursor = payload.get("last_observed_dm_event_id") or "(none)"
                print(f"no next action after dm {dm_cursor}, lobby {lobby_cursor}, official {live_cursor}")
            return 1
        sleep_interval = live_agent_poll_sleep_seconds(args.poll_interval)
        runtime.sleep(min(sleep_interval, remaining))


def _wait_next_timeout_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    lobby_cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    live_cursor = str(args.after_live_event_id or agent.get("last_observed_live_event_id") or "").strip()
    dm_cursor = str(args.after_dm_event_id or agent.get("last_observed_dm_event_id") or "").strip()
    return {
        "status": "timeout",
        "agent_id": args.agent_id,
        "timeout_seconds": float(args.timeout),
        "last_observed_event_id": _latest_observed_event_id(room.get("lobby_events"), lobby_cursor),
        "last_observed_live_event_id": _latest_observed_event_id(room.get("live_events"), live_cursor),
        "last_observed_dm_event_id": _latest_observed_event_id(room.get("dm_events"), dm_cursor),
    }


def _wait_next_lobby_observation(args: argparse.Namespace, room: dict[str, object]) -> tuple[str, dict[str, object]] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    display_name = str(agent.get("display_name") or "").strip()
    engagement_mode = str(agent.get("engagement_mode") or "always").strip() or "always"
    observed_candidate: dict[str, object] | None = None
    for event in _events_after_id(events, cursor):
        if not isinstance(event, dict):
            continue
        if _wait_room_self_event(args.agent_id, display_name, event):
            continue
        if not str(event.get("id") or "").strip():
            continue
        if not str(event.get("message") or "").strip():
            continue
        if _delegate_chain_depth(event) > int(args.max_chain_depth):
            observed_candidate = event
            continue
        if should_reply_to_event(engagement_mode, event, args.agent_id, display_name):
            return ("lobby", event)
        observed_candidate = event
    if observed_candidate is not None:
        return ("observe_lobby", observed_candidate)
    return None


def _wait_lobby_observation_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    engagement_mode = str(agent.get("engagement_mode") or "always").strip() or "always"
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "source_event_id": event_id,
        "engagement_mode": engagement_mode,
        "event": event,
        "ack_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "heartbeat",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--status",
            "online",
            "--last-error=",
            f"--last-observed-event-id={event_id}",
            "--json",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or "")),
    }


def _wait_return_packet_candidate(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("live_events") if isinstance(room.get("live_events"), list) else []
    cursor = str(args.after_live_event_id or agent.get("last_observed_live_event_id") or "").strip()
    for event in _events_after_id(events, cursor):
        if not isinstance(event, dict):
            continue
        if str(event.get("kind") or "") != "artifact":
            continue
        if str(event.get("artifact_kind") or "") != "return_packet":
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        target_agent_id = str(event.get("target_agent_id") or "").strip()
        audience = str(event.get("audience") or "").strip()
        targeted_to_agent = target_agent_id == args.agent_id or audience == f"agent:{args.agent_id}"
        if not targeted_to_agent:
            continue
        if not str(event.get("artifact_path") or event.get("artifact_json_path") or "").strip():
            continue
        return event
    return None


def _wait_return_packet_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    meeting_id = _wait_turn_request_meeting_id(room, event)
    read_command = [
        "python3",
        "-m",
        "agentsassemble.cli",
        "live-agent",
        "return-packet",
        "--server",
        str(args.server),
        "--agent-id",
        str(args.agent_id),
    ]
    if meeting_id:
        read_command.extend(["--meeting-id", meeting_id])
    read_command.extend(["--source-event-id", event_id, "--json"])
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "meeting_id": meeting_id,
        "source_event_id": event_id,
        "event": event,
        "artifact_path": str(event.get("artifact_path") or ""),
        "artifact_json_path": str(event.get("artifact_json_path") or ""),
        "read_command": read_command,
        "ack_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "heartbeat",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--status",
            "online",
            "--last-error=",
            "--last-observed-live-event-id=" + event_id,
            "--json",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or meeting_id)),
    }


def _format_wait_return_packet(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_id = str(event.get("id") or "return packet")
    artifact_path = str(payload.get("artifact_path") or payload.get("artifact_json_path") or "").strip()
    return f"{event_id} {artifact_path}".strip()


def _delegate_chain_depth(event: dict[str, object]) -> int:
    try:
        return max(0, int(event.get("auto_chain_depth", 0)))
    except (TypeError, ValueError):
        return 0
