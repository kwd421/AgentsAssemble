from __future__ import annotations

from dataclasses import dataclass
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from agentsassemble.live_agent_runner import official_turn_request_candidate, should_reply_to_event
from agentsassemble.live_meeting_memory import compact_live_meeting_memory


JsonRequester = Callable[..., dict[str, object]]
MCP_PROFILES = ("participant", "archive")
HEARTBEAT_STATUSES = {"online", "working", "offline", "error"}


@dataclass(frozen=True)
class McpServerConfig:
    profile: str
    server: str = "http://127.0.0.1:8765"
    agent_id: str = ""
    display_name: str = ""
    provider_kind: str = "manual"
    connection_kind: str = "manual"
    meeting_id: str = ""
    engagement_mode: str = "mentioned"
    timeout: float = 30.0
    poll_interval: float = 2.0
    max_chain_depth: int = 1


class RoomClient:
    def __init__(self, config: McpServerConfig, *, requester: JsonRequester | None = None) -> None:
        self.config = config
        self.requester = requester or request_json

    def register(self) -> dict[str, object]:
        payload = {
            "agent_id": self.config.agent_id,
            "display_name": self.config.display_name or self.config.agent_id,
            "provider_kind": self.config.provider_kind or "manual",
            "connection_kind": self.config.connection_kind or "manual",
            "meeting_id": self.config.meeting_id,
            "engagement_mode": self.config.engagement_mode or "mentioned",
            "capabilities": ["room_chat", "official_turn"],
        }
        return self._request("/api/live-agents", method="POST", payload=payload)

    def heartbeat(
        self,
        *,
        status: str = "online",
        last_error: str = "",
        last_reply_at: str = "",
        last_observed_event_id: str = "",
        last_observed_live_event_id: str = "",
    ) -> dict[str, object]:
        if status not in HEARTBEAT_STATUSES:
            raise ValueError("status must be online, working, offline, or error.")
        payload: dict[str, object] = {"status": status}
        for key, value in (
            ("last_error", last_error),
            ("last_reply_at", last_reply_at),
            ("last_observed_event_id", last_observed_event_id),
            ("last_observed_live_event_id", last_observed_live_event_id),
        ):
            if value != "":
                payload[key] = value
        return self._request(f"/api/live-agents/{self._quoted_agent_id()}/heartbeat", method="POST", payload=payload)

    def leave(self, *, last_observed_event_id: str = "", last_observed_live_event_id: str = "") -> dict[str, object]:
        payload: dict[str, object] = {}
        if last_observed_event_id:
            payload["last_observed_event_id"] = last_observed_event_id
        if last_observed_live_event_id:
            payload["last_observed_live_event_id"] = last_observed_live_event_id
        return self._request(f"/api/live-agents/{self._quoted_agent_id()}/leave", method="POST", payload=payload)

    def read_room(self) -> dict[str, object]:
        return self._request(f"/api/live-agents/{self._quoted_agent_id()}/room")

    def say(self, *, message: str, source_event_id: str, auto_chain_depth: int = 0) -> dict[str, object]:
        if not source_event_id:
            raise ValueError("source_event_id is required.")
        payload = {
            "message": message,
            "kind": "message",
            "source_event_id": source_event_id,
            "auto_chain_depth": int(auto_chain_depth),
        }
        return self._request(f"/api/live-agents/{self._quoted_agent_id()}/lobby", method="POST", payload=payload)

    def official_reply(self, *, message: str, meeting_id: str, source_event_id: str) -> dict[str, object]:
        if not meeting_id:
            raise ValueError("meeting_id is required.")
        if not source_event_id:
            raise ValueError("source_event_id is required.")
        payload = {"meeting_id": meeting_id, "source_event_id": source_event_id, "content": message}
        return self._request(f"/api/live-agents/{self._quoted_agent_id()}/official-turn", method="POST", payload=payload)

    def read_return_packet(self, *, meeting_id: str, source_event_id: str) -> dict[str, object]:
        if not source_event_id:
            raise ValueError("source_event_id is required.")
        query_values = {"source_event_id": source_event_id}
        if meeting_id:
            query_values["meeting_id"] = meeting_id
        query = urllib.parse.urlencode(query_values)
        return self._request(f"/api/live-agents/{self._quoted_agent_id()}/return-packet?{query}")

    def wait_next(
        self,
        *,
        timeout: float | None = None,
        poll_interval: float | None = None,
        max_chain_depth: int | None = None,
        after_event_id: str = "",
        after_live_event_id: str = "",
    ) -> dict[str, object]:
        timeout_seconds = self.config.timeout if timeout is None else float(timeout)
        poll_seconds = self.config.poll_interval if poll_interval is None else float(poll_interval)
        chain_depth = self.config.max_chain_depth if max_chain_depth is None else int(max_chain_depth)
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        last_room: dict[str, object] = {}
        while True:
            room = self.read_room()
            last_room = room
            official = self._official_candidate(room, after_live_event_id=after_live_event_id)
            if official is not None:
                payload = self._official_turn_payload(room, official)
                payload["action"] = "official_turn"
                return payload
            lobby = self._lobby_candidate(room, after_event_id=after_event_id, max_chain_depth=chain_depth)
            if lobby is not None:
                action, event = lobby
                payload = self._lobby_payload(room, event) if action == "lobby" else self._observe_lobby_payload(room, event)
                payload["action"] = action
                return payload
            if time.monotonic() >= deadline:
                return self._timeout_payload(last_room, timeout_seconds, after_event_id, after_live_event_id)
            time.sleep(min(max(poll_seconds, 0.05), max(0.0, deadline - time.monotonic())))

    def list_meetings(self) -> dict[str, object]:
        return self._request("/api/meetings")

    def read_meeting_payload(self, meeting_id: str = "") -> dict[str, object]:
        resolved = meeting_id or self.config.meeting_id
        if not resolved:
            return self._request("/api/meetings/latest")
        return self._request(f"/api/meetings/{urllib.parse.quote(resolved, safe='')}")

    def read_meeting_summary(self, meeting_id: str = "") -> dict[str, object]:
        payload = self.read_meeting_payload(meeting_id)
        meeting = payload.get("meeting") if isinstance(payload.get("meeting"), dict) else {}
        return {
            "meeting_id": meeting.get("meeting_id", meeting_id or self.config.meeting_id),
            "topic": meeting.get("topic", ""),
            "question": meeting.get("question", ""),
            "live_status": meeting.get("live_status", ""),
            "meeting_mode": meeting.get("meeting_mode", ""),
        }

    def read_artifact(self, name: str, meeting_id: str = "") -> str:
        payload = self.read_meeting_payload(meeting_id)
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
        return str(artifacts.get(name) or "")

    def read_shared_memory(self, meeting_id: str = "") -> dict[str, object]:
        payload = self.read_meeting_payload(meeting_id)
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
        result = {
            "rolling_summary": str(artifacts.get("shared_memory/rolling-summary.md") or ""),
            "open_questions": str(artifacts.get("shared_memory/open-questions.md") or ""),
            "action_items": str(artifacts.get("shared_memory/action-items.md") or ""),
            "index": _parse_json_object(artifacts.get("shared_memory/index.json")),
        }
        return result

    def _official_candidate(self, room: dict[str, object], *, after_live_event_id: str) -> dict[str, object] | None:
        events = room.get("live_events") if isinstance(room.get("live_events"), list) else []
        typed_events = [event for event in events if isinstance(event, dict)]
        agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
        cursor = after_live_event_id or str(agent.get("last_observed_live_event_id") or "")
        return official_turn_request_candidate(typed_events, self.config.agent_id, cursor)

    def _official_turn_payload(self, room: dict[str, object], event: dict[str, object]) -> dict[str, object]:
        event_id = str(event.get("id") or "")
        meeting_id = str(event.get("meeting_id") or room.get("meeting_id") or self.config.meeting_id or "")
        return {
            "status": "event",
            "agent_id": self.config.agent_id,
            "meeting_id": meeting_id,
            "source_event_id": event_id,
            "event": event,
            "room": self._room_context(room, meeting_id=meeting_id),
        }

    def _lobby_candidate(
        self,
        room: dict[str, object],
        *,
        after_event_id: str,
        max_chain_depth: int,
    ) -> tuple[str, dict[str, object]] | None:
        agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
        events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
        cursor = after_event_id or str(agent.get("last_observed_event_id") or "")
        display_name = str(agent.get("display_name") or self.config.display_name or "").strip()
        engagement_mode = str(agent.get("engagement_mode") or self.config.engagement_mode or "always").strip() or "always"
        observed: dict[str, object] | None = None
        for event in _events_after_id(events, cursor):
            if not isinstance(event, dict):
                continue
            if _is_self_event(self.config.agent_id, display_name, event):
                continue
            if not str(event.get("id") or "").strip() or not str(event.get("message") or "").strip():
                continue
            if _chain_depth(event) > max_chain_depth:
                observed = event
                continue
            if should_reply_to_event(engagement_mode, event, self.config.agent_id, display_name):
                return ("lobby", event)
            observed = event
        if observed is not None:
            return ("observe_lobby", observed)
        return None

    def _lobby_payload(self, room: dict[str, object], event: dict[str, object]) -> dict[str, object]:
        event_id = str(event.get("id") or "")
        return {
            "status": "event",
            "agent_id": self.config.agent_id,
            "source_event_id": event_id,
            "auto_chain_depth": _chain_depth(event) + 1,
            "event": event,
            "room": self._room_context(room, meeting_id=str(room.get("meeting_id") or self.config.meeting_id)),
        }

    def _observe_lobby_payload(self, room: dict[str, object], event: dict[str, object]) -> dict[str, object]:
        return {
            "status": "event",
            "agent_id": self.config.agent_id,
            "source_event_id": str(event.get("id") or ""),
            "event": event,
            "room": self._room_context(room, meeting_id=str(room.get("meeting_id") or self.config.meeting_id)),
        }

    def _timeout_payload(
        self,
        room: dict[str, object],
        timeout_seconds: float,
        after_event_id: str,
        after_live_event_id: str,
    ) -> dict[str, object]:
        agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
        lobby_cursor = after_event_id or str(agent.get("last_observed_event_id") or "")
        live_cursor = after_live_event_id or str(agent.get("last_observed_live_event_id") or "")
        return {
            "status": "timeout",
            "agent_id": self.config.agent_id,
            "timeout_seconds": timeout_seconds,
            "last_observed_event_id": _latest_observed_event_id(room.get("lobby_events"), lobby_cursor),
            "last_observed_live_event_id": _latest_observed_event_id(room.get("live_events"), live_cursor),
        }

    def _room_context(self, room: dict[str, object], *, meeting_id: str) -> dict[str, object]:
        context: dict[str, object] = {
            "meeting_id": meeting_id,
            "lobby_event_count": len(room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []),
            "live_event_count": len(room.get("live_events") if isinstance(room.get("live_events"), list) else []),
        }
        memory = room.get("shared_memory")
        if isinstance(memory, dict):
            context["shared_memory"] = compact_live_meeting_memory(memory)
        return context

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, object]:
        return self.requester(
            _server_url(self.config.server, path),
            method=method,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )

    def _quoted_agent_id(self) -> str:
        if not self.config.agent_id:
            raise ValueError("agent_id is required for participant MCP tools.")
        return urllib.parse.quote(self.config.agent_id, safe="")


def build_mcp_server(config: McpServerConfig, *, requester: JsonRequester | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise RuntimeError("The MCP Python SDK is required. Install agentsassemble with the mcp dependency.") from error

    _validate_config(config)
    client = RoomClient(config, requester=requester)
    server = FastMCP(f"AgentsAssemble {config.profile}")
    if config.profile == "participant":
        _register_participant_tools(server, client)
    elif config.profile == "archive":
        _register_archive_tools(server, client)
    else:
        raise ValueError(f"Unsupported MCP profile: {config.profile}")
    return server


def run_mcp_server(config: McpServerConfig) -> None:
    server = build_mcp_server(config)
    server.run(transport="stdio")


def _register_participant_tools(server, client: RoomClient) -> None:
    @server.tool(description="Register this MCP participant in the AgentsAssemble room.")
    def register() -> dict[str, object]:
        return client.register()

    @server.tool(description="Update this participant's room heartbeat and cursors.")
    def heartbeat(
        status: str = "online",
        last_error: str = "",
        last_reply_at: str = "",
        last_observed_event_id: str = "",
        last_observed_live_event_id: str = "",
    ) -> dict[str, object]:
        return client.heartbeat(
            status=status,
            last_error=last_error,
            last_reply_at=last_reply_at,
            last_observed_event_id=last_observed_event_id,
            last_observed_live_event_id=last_observed_live_event_id,
        )

    @server.tool(description="Wait for the next lobby, official-turn, return-packet, or observation action.")
    def wait_next(
        timeout: float | None = None,
        poll_interval: float | None = None,
        max_chain_depth: int | None = None,
        after_event_id: str = "",
        after_live_event_id: str = "",
    ) -> dict[str, object]:
        return client.wait_next(
            timeout=timeout,
            poll_interval=poll_interval,
            max_chain_depth=max_chain_depth,
            after_event_id=after_event_id,
            after_live_event_id=after_live_event_id,
        )

    @server.tool(description="Post one lobby reply as this participant.")
    def say(message: str, source_event_id: str, auto_chain_depth: int = 0) -> dict[str, object]:
        return client.say(message=message, source_event_id=source_event_id, auto_chain_depth=auto_chain_depth)

    @server.tool(description="Post one official turn reply as this participant.")
    def official_reply(message: str, meeting_id: str, source_event_id: str) -> dict[str, object]:
        return client.official_reply(message=message, meeting_id=meeting_id, source_event_id=source_event_id)

    @server.tool(description="Read the current room snapshot visible to this participant.")
    def read_room() -> dict[str, object]:
        return client.read_room()

    @server.tool(description="Read this participant's targeted return packet.")
    def read_return_packet(source_event_id: str, meeting_id: str = "") -> dict[str, object]:
        return client.read_return_packet(meeting_id=meeting_id, source_event_id=source_event_id)

    @server.tool(description="Mark this participant offline before intentional exit.")
    def leave(last_observed_event_id: str = "", last_observed_live_event_id: str = "") -> dict[str, object]:
        return client.leave(
            last_observed_event_id=last_observed_event_id,
            last_observed_live_event_id=last_observed_live_event_id,
        )


def _register_archive_tools(server, client: RoomClient) -> None:
    @server.tool(description="List available AgentsAssemble meetings.")
    def list_meetings() -> dict[str, object]:
        return client.list_meetings()

    @server.tool(description="Read a compact meeting summary.")
    def read_meeting_summary(meeting_id: str = "") -> dict[str, object]:
        return client.read_meeting_summary(meeting_id)

    @server.tool(description="Read transcript.md for a meeting.")
    def read_transcript(meeting_id: str = "") -> str:
        return client.read_artifact("transcript.md", meeting_id)

    @server.tool(description="Read decision.md for a meeting.")
    def read_decision(meeting_id: str = "") -> str:
        return client.read_artifact("decision.md", meeting_id)

    @server.tool(description="Read official-only shared meeting memory artifacts.")
    def read_shared_memory(meeting_id: str = "") -> dict[str, object]:
        return client.read_shared_memory(meeting_id)


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            loaded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace")
        finally:
            error.close()
        raise ValueError(_http_error_message(body)) from error
    return loaded if isinstance(loaded, dict) else {}


def _validate_config(config: McpServerConfig) -> None:
    if config.profile not in MCP_PROFILES:
        raise ValueError(f"profile must be one of: {', '.join(MCP_PROFILES)}")
    if config.profile == "participant" and not config.agent_id:
        raise ValueError("participant MCP profile requires agent_id.")


def _server_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"


def _http_error_message(body: str) -> str:
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])
        return body.strip()
    return "HTTP request failed."


def _events_after_id(events: list[object], event_id: str) -> list[object]:
    if not event_id:
        return events
    for index, event in enumerate(events):
        if isinstance(event, dict) and str(event.get("id") or "") == event_id:
            return events[index + 1 :]
    return events


def _is_self_event(agent_id: str, display_name: str, event: dict[str, object]) -> bool:
    actor_id = str(event.get("actor_id") or "").strip()
    if actor_id and actor_id == agent_id:
        return True
    name = str(event.get("name") or "").strip()
    return bool(display_name and name == display_name)


def _chain_depth(event: dict[str, object]) -> int:
    value = event.get("auto_chain_depth")
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _latest_observed_event_id(events: object, cursor: str) -> str:
    if not isinstance(events, list):
        return cursor
    for event in reversed(events):
        if isinstance(event, dict) and str(event.get("id") or "").strip():
            return str(event.get("id") or "")
    return cursor


def _parse_json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
