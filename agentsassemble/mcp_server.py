from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from agentsassemble.live_agent_runner import official_turn_request_candidate, should_reply_to_event
from agentsassemble.live_agent_timing import DEFAULT_LIVE_AGENT_POLL_INTERVAL, live_agent_poll_sleep_seconds
from agentsassemble.meeting_events import clean_lobby_text


MCP_PROFILES = ("participant", "archive")
HEARTBEAT_STATUSES = {"online", "working", "offline", "error"}


@dataclass
class McpParticipantContext:
    agent_id: str = ""
    meeting_id: str = ""
    display_name: str = ""
    provider_kind: str = "manual"
    connection_kind: str = "manual"
    engagement_mode: str = "mentioned"
    last_observed_event_id: str = ""
    last_observed_live_event_id: str = ""
    last_observed_dm_event_id: str = ""


RequestJson = Callable[..., dict[str, object]]


class McpRoomClient:
    def __init__(
        self,
        server: str,
        *,
        request_json: RequestJson | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.server = str(server or "http://127.0.0.1:8765").rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._request_json = request_json or self._urllib_request_json

    def get(self, path: str, *, timeout_seconds: float | None = None) -> dict[str, object]:
        return self.request_json("GET", path, timeout_seconds=timeout_seconds)

    def post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        return self.request_json("POST", path, payload=payload, timeout_seconds=timeout_seconds)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        return self._request_json(
            method=method,
            path=path,
            payload=payload,
            timeout_seconds=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
        )

    def _urllib_request_json(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, object]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        request = urllib.request.Request(f"{self.server}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                loaded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                message = _http_error_message(error)
            finally:
                error.close()
            raise ValueError(message) from error
        return loaded if isinstance(loaded, dict) else {}


def build_tool_registry(
    profile: str,
    client: McpRoomClient,
    context: McpParticipantContext | None = None,
) -> dict[str, Callable[..., dict[str, object]]]:
    normalized_profile = str(profile or "").strip()
    if normalized_profile == "participant":
        return _participant_tools(client, context or McpParticipantContext())
    if normalized_profile == "archive":
        return _archive_tools(client)
    raise ValueError(f"Unsupported MCP profile: {profile}")


def create_mcp_server(
    *,
    profile: str,
    server: str,
    agent_id: str = "",
    meeting_id: str = "",
    display_name: str = "",
    provider_kind: str = "manual",
    connection_kind: str = "manual",
    engagement_mode: str = "mentioned",
):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise RuntimeError("MCP Python SDK is required; install dependency mcp>=1,<2.") from error

    client = McpRoomClient(server)
    context = McpParticipantContext(
        agent_id=agent_id,
        meeting_id=meeting_id,
        display_name=display_name,
        provider_kind=provider_kind,
        connection_kind=connection_kind,
        engagement_mode=engagement_mode,
    )
    tools = build_tool_registry(profile, client, context)
    mcp = FastMCP(f"AgentsAssemble {profile}", json_response=True)
    for tool in tools.values():
        mcp.tool()(tool)
    return mcp


def serve_mcp(
    *,
    profile: str,
    server: str,
    agent_id: str = "",
    meeting_id: str = "",
    display_name: str = "",
    provider_kind: str = "manual",
    connection_kind: str = "manual",
    engagement_mode: str = "mentioned",
) -> None:
    mcp = create_mcp_server(
        profile=profile,
        server=server,
        agent_id=agent_id,
        meeting_id=meeting_id,
        display_name=display_name,
        provider_kind=provider_kind,
        connection_kind=connection_kind,
        engagement_mode=engagement_mode,
    )
    mcp.run(transport="stdio")


def _participant_tools(client: McpRoomClient, context: McpParticipantContext) -> dict[str, Callable[..., dict[str, object]]]:
    context.agent_id = _required_text(context.agent_id, "agent_id")

    def register() -> dict[str, object]:
        context.display_name = _clean_text(context.display_name, limit=128) or context.agent_id
        context.provider_kind = _clean_text(context.provider_kind, limit=64) or "manual"
        context.connection_kind = _clean_text(context.connection_kind, limit=64) or "manual"
        context.engagement_mode = _clean_text(context.engagement_mode, limit=64) or "mentioned"
        payload = {
            "agent_id": context.agent_id,
            "display_name": context.display_name,
            "provider_kind": context.provider_kind,
            "connection_kind": context.connection_kind,
            "meeting_id": _clean_meeting_id(context.meeting_id) if context.meeting_id else "",
            "engagement_mode": context.engagement_mode,
            "capabilities": ["room_chat", "mentions", "official_turns", "return_packets", "direct_dm"],
        }
        return client.post("/api/live-agents", payload)

    def heartbeat(
        status: str = "online",
        last_error: str = "",
        last_reply_at: str = "",
        last_observed_event_id: str = "",
        last_observed_live_event_id: str = "",
        last_observed_dm_event_id: str = "",
    ) -> dict[str, object]:
        clean_status = _clean_text(status, limit=32) or "online"
        if clean_status not in HEARTBEAT_STATUSES:
            raise ValueError("heartbeat status must be online, working, offline, or error.")
        payload: dict[str, object] = {"status": clean_status}
        _add_optional(payload, "last_error", last_error, limit=500)
        _add_optional(payload, "last_reply_at", last_reply_at, limit=128)
        _add_optional(payload, "last_observed_event_id", last_observed_event_id, limit=128)
        _add_optional(payload, "last_observed_live_event_id", last_observed_live_event_id, limit=128)
        _add_optional(payload, "last_observed_dm_event_id", last_observed_dm_event_id, limit=128)
        if payload.get("last_observed_event_id"):
            context.last_observed_event_id = str(payload["last_observed_event_id"])
        if payload.get("last_observed_live_event_id"):
            context.last_observed_live_event_id = str(payload["last_observed_live_event_id"])
        if payload.get("last_observed_dm_event_id"):
            context.last_observed_dm_event_id = str(payload["last_observed_dm_event_id"])
        return client.post(f"/api/live-agents/{_quote(context.agent_id)}/heartbeat", payload)

    def read_room() -> dict[str, object]:
        return client.get(f"/api/live-agents/{_quote(context.agent_id)}/room")

    def read_since(after_event_id: str = "", after_live_event_id: str = "", after_dm_event_id: str = "") -> dict[str, object]:
        room = read_room()
        return _read_since_payload(
            context,
            room,
            after_event_id=after_event_id,
            after_live_event_id=after_live_event_id,
            after_dm_event_id=after_dm_event_id,
        )

    def wait_next(
        timeout_seconds: float = 30.0,
        poll_interval: float = DEFAULT_LIVE_AGENT_POLL_INTERVAL,
        max_chain_depth: int = 1,
    ) -> dict[str, object]:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        last_room: dict[str, object] = {}
        while True:
            room = read_room()
            last_room = room
            dm = _next_dm(context, room)
            if dm is not None:
                context.last_observed_dm_event_id = str(dm.get("id") or "")
                return _dm_payload(context, room, dm)
            official = _next_official_turn(context, room)
            if official is not None:
                context.last_observed_live_event_id = str(official.get("id") or "")
                return _official_turn_payload(context, room, official)
            packet = _next_return_packet(context, room)
            if packet is not None:
                context.last_observed_live_event_id = str(packet.get("id") or "")
                return _return_packet_payload(context, room, packet)
            lobby_action = _next_lobby_action(context, room, max_chain_depth=max_chain_depth)
            if lobby_action is not None:
                action, event = lobby_action
                context.last_observed_event_id = str(event.get("id") or "")
                return _lobby_payload(context, room, event, action=action)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _wait_timeout_payload(context, last_room, timeout_seconds=float(timeout_seconds))
            time.sleep(min(live_agent_poll_sleep_seconds(poll_interval), remaining))

    def say(message: str, source_event_id: str, auto_chain_depth: int = 1) -> dict[str, object]:
        source_id = _required_text(source_event_id, "source_event_id")
        payload = {
            "message": _required_text(message, "message"),
            "kind": "message",
            "source_event_id": source_id,
            "auto_chain_depth": max(0, int(auto_chain_depth)),
        }
        context.last_observed_event_id = source_id
        return client.post(f"/api/live-agents/{_quote(context.agent_id)}/lobby", payload)

    def official_reply(message: str, meeting_id: str, source_event_id: str) -> dict[str, object]:
        clean_meeting_id = _clean_meeting_id(meeting_id or context.meeting_id)
        source_id = _required_text(source_event_id, "source_event_id")
        payload = {
            "meeting_id": clean_meeting_id,
            "source_event_id": source_id,
            "content": _required_text(message, "message"),
        }
        context.last_observed_live_event_id = source_id
        return client.post(f"/api/live-agents/{_quote(context.agent_id)}/official-turn", payload)

    def dm_reply(message: str, source_event_id: str) -> dict[str, object]:
        source_id = _required_text(source_event_id, "source_event_id")
        payload = {
            "source_event_id": source_id,
            "message": _required_text(message, "message"),
        }
        context.last_observed_dm_event_id = source_id
        return client.post(f"/api/live-agents/{_quote(context.agent_id)}/dm-reply", payload)

    def read_return_packet(source_event_id: str, meeting_id: str = "") -> dict[str, object]:
        source_id = _required_text(source_event_id, "source_event_id")
        query_values = {}
        clean_meeting_id = _clean_meeting_id(meeting_id or context.meeting_id) if meeting_id or context.meeting_id else ""
        if clean_meeting_id:
            query_values["meeting_id"] = clean_meeting_id
        query_values["source_event_id"] = source_id
        query = urllib.parse.urlencode(query_values)
        return client.get(f"/api/live-agents/{_quote(context.agent_id)}/return-packet?{query}")

    def leave() -> dict[str, object]:
        payload = {
            "status": "offline",
            "last_error": "",
            "last_observed_event_id": context.last_observed_event_id,
            "last_observed_live_event_id": context.last_observed_live_event_id,
            "last_observed_dm_event_id": context.last_observed_dm_event_id,
        }
        return client.post(f"/api/live-agents/{_quote(context.agent_id)}/leave", payload)

    return {
        "register": register,
        "heartbeat": heartbeat,
        "wait_next": wait_next,
        "read_since": read_since,
        "say": say,
        "dm_reply": dm_reply,
        "official_reply": official_reply,
        "read_room": read_room,
        "read_return_packet": read_return_packet,
        "leave": leave,
    }


def _archive_tools(client: McpRoomClient) -> dict[str, Callable[..., dict[str, object]]]:
    def list_meetings() -> dict[str, object]:
        payload = client.get("/api/meetings")
        meetings = payload.get("meetings") if isinstance(payload.get("meetings"), list) else []
        return {"meetings": [_archive_meeting_item(meeting) for meeting in meetings if isinstance(meeting, dict)]}

    def read_transcript(meeting_id: str) -> dict[str, object]:
        return _fixed_artifact(client, meeting_id, "transcript.md")

    def read_decision(meeting_id: str) -> dict[str, object]:
        return _fixed_artifact(client, meeting_id, "decision.md")

    def read_shared_memory(meeting_id: str) -> dict[str, object]:
        payload = _meeting_payload(client, meeting_id)
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
        shared = {str(key): value for key, value in artifacts.items() if str(key).startswith("shared_memory/")}
        meeting = payload.get("meeting") if isinstance(payload.get("meeting"), dict) else {}
        return {"meeting_id": _clean_meeting_id(meeting_id), "shared_memory": meeting.get("shared_memory") or {}, "artifacts": shared}

    def read_meeting_summary(meeting_id: str) -> dict[str, object]:
        payload = _meeting_payload(client, meeting_id)
        meeting = payload.get("meeting") if isinstance(payload.get("meeting"), dict) else {}
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
        return {
            "meeting_id": _clean_meeting_id(meeting_id),
            "summary": _archive_meeting_item(meeting),
            "artifact_paths": sorted(str(path) for path in artifacts),
        }

    return {
        "read_transcript": read_transcript,
        "read_decision": read_decision,
        "read_shared_memory": read_shared_memory,
        "list_meetings": list_meetings,
        "read_meeting_summary": read_meeting_summary,
    }


def _next_official_turn(context: McpParticipantContext, room: dict[str, object]) -> dict[str, object] | None:
    events = [event for event in room.get("live_events", []) if isinstance(event, dict)]
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    cursor = context.last_observed_live_event_id or str(agent.get("last_observed_live_event_id") or "")
    return official_turn_request_candidate(events, context.agent_id, cursor)


def _next_dm(context: McpParticipantContext, room: dict[str, object]) -> dict[str, object] | None:
    events = [event for event in room.get("dm_events", []) if isinstance(event, dict)]
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    cursor = context.last_observed_dm_event_id or str(agent.get("last_observed_dm_event_id") or "")
    for event in _events_after(events, cursor):
        if str(event.get("side") or "") != "mine":
            continue
        if str(event.get("target_agent_id") or "").strip() != context.agent_id:
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        if not str(event.get("message") or "").strip():
            continue
        return event
    return None


def _next_return_packet(context: McpParticipantContext, room: dict[str, object]) -> dict[str, object] | None:
    events = [event for event in room.get("live_events", []) if isinstance(event, dict)]
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    cursor = context.last_observed_live_event_id or str(agent.get("last_observed_live_event_id") or "")
    for event in _events_after(events, cursor):
        if str(event.get("kind") or "") != "artifact":
            continue
        if str(event.get("artifact_kind") or "") != "return_packet":
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        audience = str(event.get("audience") or "").strip()
        target_agent_id = str(event.get("target_agent_id") or "").strip()
        if target_agent_id == context.agent_id or audience == f"agent:{context.agent_id}":
            return event
    return None


def _next_lobby_action(
    context: McpParticipantContext,
    room: dict[str, object],
    *,
    max_chain_depth: int,
) -> tuple[str, dict[str, object]] | None:
    events = [event for event in room.get("lobby_events", []) if isinstance(event, dict)]
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    cursor = context.last_observed_event_id or str(agent.get("last_observed_event_id") or "")
    display_name = context.display_name or str(agent.get("display_name") or "")
    engagement_mode = str(agent.get("engagement_mode") or context.engagement_mode or "always")
    observed: dict[str, object] | None = None
    for event in _events_after(events, cursor):
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        if _is_self_event(event, context.agent_id, display_name):
            continue
        if not str(event.get("message") or "").strip():
            continue
        if _chain_depth(event) > int(max_chain_depth):
            observed = event
            continue
        if should_reply_to_event(engagement_mode, event, context.agent_id, display_name):
            return ("lobby", event)
        observed = event
    if observed is not None:
        return ("observe_lobby", observed)
    return None


def _official_turn_payload(
    context: McpParticipantContext,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    meeting_id = str(event.get("meeting_id") or room.get("meeting_id") or context.meeting_id or "").strip()
    return {
        "status": "event",
        "action": "official_turn",
        "agent_id": context.agent_id,
        "meeting_id": meeting_id,
        "source_event_id": str(event.get("id") or ""),
        "event": event,
        "room": _room_context(room, meeting_id=meeting_id),
    }


def _return_packet_payload(
    context: McpParticipantContext,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    meeting_id = str(event.get("meeting_id") or room.get("meeting_id") or context.meeting_id or "").strip()
    return {
        "status": "event",
        "action": "return_packet",
        "agent_id": context.agent_id,
        "meeting_id": meeting_id,
        "source_event_id": str(event.get("id") or ""),
        "event": event,
        "artifact_path": str(event.get("artifact_path") or ""),
        "artifact_json_path": str(event.get("artifact_json_path") or ""),
        "room": _room_context(room, meeting_id=meeting_id),
    }


def _dm_payload(
    context: McpParticipantContext,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    return {
        "status": "event",
        "action": "dm",
        "agent_id": context.agent_id,
        "source_event_id": event_id,
        "event": event,
        "room": _room_context(room, meeting_id=str(room.get("meeting_id") or context.meeting_id or "")),
    }


def _lobby_payload(
    context: McpParticipantContext,
    room: dict[str, object],
    event: dict[str, object],
    *,
    action: str,
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    return {
        "status": "event",
        "action": action,
        "agent_id": context.agent_id,
        "source_event_id": event_id,
        "auto_chain_depth": _chain_depth(event) + 1,
        "event": event,
        "room": _room_context(room, meeting_id=str(room.get("meeting_id") or context.meeting_id or "")),
    }


def _wait_timeout_payload(
    context: McpParticipantContext,
    room: dict[str, object],
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    context.last_observed_event_id = _latest_event_id(room.get("lobby_events"), context.last_observed_event_id)
    context.last_observed_live_event_id = _latest_event_id(room.get("live_events"), context.last_observed_live_event_id)
    context.last_observed_dm_event_id = _latest_event_id(room.get("dm_events"), context.last_observed_dm_event_id)
    return {
        "status": "timeout",
        "agent_id": context.agent_id,
        "timeout_seconds": timeout_seconds,
        "last_observed_event_id": context.last_observed_event_id,
        "last_observed_live_event_id": context.last_observed_live_event_id,
        "last_observed_dm_event_id": context.last_observed_dm_event_id,
    }


def _room_context(room: dict[str, object], *, meeting_id: str) -> dict[str, object]:
    context: dict[str, object] = {
        "meeting_id": meeting_id,
        "lobby_event_count": len(room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []),
        "live_event_count": len(room.get("live_events") if isinstance(room.get("live_events"), list) else []),
        "dm_event_count": len(room.get("dm_events") if isinstance(room.get("dm_events"), list) else []),
    }
    if isinstance(room.get("shared_memory"), dict):
        context["shared_memory"] = room["shared_memory"]
    return context


def _read_since_payload(
    context: McpParticipantContext,
    room: dict[str, object],
    *,
    after_event_id: str = "",
    after_live_event_id: str = "",
    after_dm_event_id: str = "",
) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    lobby_cursor = (
        _clean_text(after_event_id, limit=128)
        or str(agent.get("last_observed_event_id") or "").strip()
        or context.last_observed_event_id
    )
    live_cursor = (
        _clean_text(after_live_event_id, limit=128)
        or str(agent.get("last_observed_live_event_id") or "").strip()
        or context.last_observed_live_event_id
    )
    dm_cursor = (
        _clean_text(after_dm_event_id, limit=128)
        or str(agent.get("last_observed_dm_event_id") or "").strip()
        or context.last_observed_dm_event_id
    )
    lobby_events = [event for event in room.get("lobby_events", []) if isinstance(event, dict)]
    live_events = [event for event in room.get("live_events", []) if isinstance(event, dict)]
    dm_events = [event for event in room.get("dm_events", []) if isinstance(event, dict)]
    lobby_diff = _events_after(lobby_events, lobby_cursor)
    live_diff = _events_after(live_events, live_cursor)
    dm_diff = _events_after(dm_events, dm_cursor)
    meeting_id = str(room.get("meeting_id") or context.meeting_id or agent.get("meeting_id") or "").strip()
    return {
        "status": "ok",
        "agent_id": context.agent_id,
        "meeting_id": meeting_id,
        "last_observed_event_id": lobby_cursor,
        "last_observed_live_event_id": live_cursor,
        "last_observed_dm_event_id": dm_cursor,
        "next_last_observed_event_id": _latest_event_id(lobby_diff, lobby_cursor),
        "next_last_observed_live_event_id": _latest_event_id(live_diff, live_cursor),
        "next_last_observed_dm_event_id": _latest_event_id(dm_diff, dm_cursor),
        "lobby_events": lobby_diff,
        "live_events": live_diff,
        "dm_events": dm_diff,
        "room": _room_context(room, meeting_id=meeting_id),
    }


def _meeting_payload(client: McpRoomClient, meeting_id: str) -> dict[str, object]:
    return client.get(f"/api/meetings/{_quote(_clean_meeting_id(meeting_id))}")


def _fixed_artifact(client: McpRoomClient, meeting_id: str, path: str) -> dict[str, object]:
    payload = _meeting_payload(client, meeting_id)
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    return {"meeting_id": _clean_meeting_id(meeting_id), "path": path, "text": str(artifacts.get(path) or "")}


def _archive_meeting_item(meeting: dict[str, object]) -> dict[str, object]:
    safe_keys = ("meeting_id", "topic", "question", "created_at", "live_status")
    item: dict[str, object] = {}
    for key in safe_keys:
        value = _clean_text(meeting.get(key), limit=256)
        if value:
            item[key] = value
    return item


def _clean_meeting_id(meeting_id: object) -> str:
    clean_meeting_id = _required_text(meeting_id, "meeting_id", limit=128)
    if clean_meeting_id in {".", ".."}:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.")
    if "/" in clean_meeting_id or "\\" in clean_meeting_id or Path(clean_meeting_id).name != clean_meeting_id:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.")
    return clean_meeting_id


def _events_after(events: list[dict[str, object]], event_id: str) -> list[dict[str, object]]:
    if not event_id:
        return events
    for index, event in enumerate(events):
        if str(event.get("id") or "") == event_id:
            return events[index + 1 :]
    return events


def _latest_event_id(events: object, fallback: str) -> str:
    if not isinstance(events, list):
        return fallback
    for event in reversed(events):
        if isinstance(event, dict) and str(event.get("id") or "").strip():
            return str(event.get("id"))
    return fallback


def _is_self_event(event: dict[str, object], agent_id: str, display_name: str) -> bool:
    actor_id = str(event.get("actor_id") or "")
    if actor_id:
        return actor_id == agent_id
    return bool(display_name) and str(event.get("name") or "") == display_name


def _chain_depth(event: dict[str, object]) -> int:
    value = event.get("auto_chain_depth")
    return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _add_optional(payload: dict[str, object], key: str, value: object, *, limit: int) -> None:
    text = _clean_text(value, limit=limit)
    if text:
        payload[key] = text


def _required_text(value: object, field_name: str, *, limit: int = 256) -> str:
    text = _clean_text(value, limit=limit)
    if not text:
        raise ValueError(f"{field_name} is required.")
    return text


def _clean_text(value: object, *, limit: int) -> str:
    return clean_lobby_text(value, limit=limit).strip()


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _http_error_message(error: urllib.error.HTTPError) -> str:
    body = error.read().decode("utf-8", errors="replace")
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])
        return body.strip()
    return f"HTTP {error.code}"
