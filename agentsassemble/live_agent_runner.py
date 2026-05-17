from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ResidentAgentConfig:
    server: str
    agent_id: str
    display_name: str
    provider_kind: str
    connection_kind: str
    session_id: str
    endpoint: str
    meeting_id: str
    engagement_mode: str
    command: list[str]
    timeout_seconds: int
    poll_interval: float
    heartbeat_interval: float
    cooldown: float
    max_chain_depth: int
    max_ticks: int = 0


class LiveAgentRunner:
    def __init__(
        self,
        config: ResidentAgentConfig,
        *,
        request_json: Callable[..., dict[str, object]],
        command_runner: Callable[..., str],
        sleep_fn: Callable[[float], None],
        now_fn: Callable[[], datetime] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.request_json = request_json
        self.command_runner = command_runner
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.stop_event = stop_event or threading.Event()
        self.last_observed_event_id = ""
        self.last_reply_at: datetime | None = None
        self.last_heartbeat_at: datetime | None = None

    def run(self) -> int:
        self._register()
        self._heartbeat("online")
        replies = 0
        ticks = 0
        try:
            while not self.stop_event.is_set():
                ticks += 1
                replies += self.tick()
                if self.config.max_ticks and ticks >= self.config.max_ticks:
                    break
                self.sleep_fn(self.config.poll_interval)
        finally:
            self._heartbeat("offline")
        return replies

    def tick(self) -> int:
        room = self._room()
        events = _lobby_events(room)
        candidate = event_reply_candidate(
            events,
            self.config.agent_id,
            self.config.display_name,
            self.last_observed_event_id,
            max_chain_depth=self.config.max_chain_depth,
        )
        if candidate is None:
            self._advance_cursor(events)
            self._heartbeat_if_due()
            return 0
        if self._in_cooldown():
            self._heartbeat_if_due()
            return 0

        source_event_id = str(candidate.get("id") or "")
        self.last_observed_event_id = source_event_id
        self._heartbeat("working", last_observed_event_id=source_event_id)
        try:
            reply = self.command_runner(
                self.config.command,
                delegate_prompt(self.config, room, candidate),
                timeout_seconds=self.config.timeout_seconds,
            ).strip()
            if not reply:
                raise ValueError("Delegate command returned an empty reply.")
        except Exception as error:
            self._heartbeat("error", last_observed_event_id=source_event_id, last_error=str(error))
            return 0

        source_depth = _chain_depth(candidate)
        response = self.request_json(
            _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/lobby"),
            method="POST",
            payload={
                "message": reply,
                "kind": "message",
                "actor_id": self.config.agent_id,
                "source_event_id": source_event_id,
                "auto_chain_depth": source_depth + 1,
            },
        )
        event = response.get("event") if isinstance(response.get("event"), dict) else {}
        if isinstance(event, dict) and event.get("id"):
            self.last_observed_event_id = str(event["id"])
        self.last_reply_at = self.now_fn()
        self._heartbeat(
            "online",
            last_observed_event_id=self.last_observed_event_id,
            last_reply_at=self.last_reply_at.isoformat(),
            last_error="",
        )
        return 1

    def _register(self) -> None:
        self.request_json(
            _server_url(self.config.server, "/api/live-agents"),
            method="POST",
            payload={
                "agent_id": self.config.agent_id,
                "display_name": self.config.display_name,
                "provider_kind": self.config.provider_kind,
                "connection_kind": self.config.connection_kind,
                "session_id": self.config.session_id,
                "endpoint": self.config.endpoint,
                "meeting_id": self.config.meeting_id,
                "engagement_mode": self.config.engagement_mode,
                "capabilities": ["room_chat", "mentions"],
            },
        )

    def _heartbeat(self, status: str, **metadata: object) -> None:
        payload = {"status": status, **metadata}
        self.request_json(
            _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/heartbeat"),
            method="POST",
            payload=payload,
        )
        self.last_heartbeat_at = self.now_fn()

    def _heartbeat_if_due(self) -> None:
        if self.config.heartbeat_interval <= 0:
            return
        if self.last_heartbeat_at is None:
            self._heartbeat("online", last_observed_event_id=self.last_observed_event_id)
            return
        elapsed = (self.now_fn() - self.last_heartbeat_at).total_seconds()
        if elapsed >= self.config.heartbeat_interval:
            self._heartbeat("online", last_observed_event_id=self.last_observed_event_id)

    def _room(self) -> dict[str, object]:
        return self.request_json(_server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/room"))

    def _advance_cursor(self, events: list[dict[str, object]]) -> None:
        latest_id = _latest_event_id(events)
        if latest_id and latest_id != self.last_observed_event_id:
            self.last_observed_event_id = latest_id
            self._heartbeat("online", last_observed_event_id=latest_id)

    def _in_cooldown(self) -> bool:
        if self.last_reply_at is None or self.config.cooldown <= 0:
            return False
        return (self.now_fn() - self.last_reply_at).total_seconds() < self.config.cooldown


def event_reply_candidate(
    events: list[dict[str, object]],
    agent_id: str,
    display_name: str,
    last_observed_event_id: str,
    *,
    max_chain_depth: int,
) -> dict[str, object] | None:
    for event in _events_after(events, last_observed_event_id):
        if _is_self_event(event, agent_id, display_name):
            continue
        if _chain_depth(event) > max_chain_depth:
            continue
        if not str(event.get("message") or "").strip():
            continue
        return event
    return None


def delegate_prompt(config: ResidentAgentConfig, room: dict[str, object], source_event: dict[str, object]) -> str:
    lines = [
        "You are a live AgentsAssemble participant connected through a local CLI bridge.",
        f"Agent id: {config.agent_id}",
        f"Display name: {config.display_name or config.agent_id}",
        "Reply with one concise lobby message only.",
        "",
        "New event to answer:",
        f"- {source_event.get('name') or 'participant'}: {source_event.get('message') or ''}",
        "",
        "Recent lobby events:",
    ]
    for event in _lobby_events(room)[-12:]:
        message = str(event.get("message") or "").strip()
        if message:
            lines.append(f"- {event.get('name') or 'participant'}: {message}")
    return "\n".join(lines).strip() + "\n"


def load_group_configs(path: Path, *, max_ticks_override: int | None = None) -> list[ResidentAgentConfig]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Live agent group config must be a JSON object.")
    server = str(data.get("server") or "http://127.0.0.1:8765")
    defaults = {
        "poll_interval": float(data.get("poll_interval", 2.0)),
        "heartbeat_interval": float(data.get("heartbeat_interval", 30.0)),
        "cooldown": float(data.get("cooldown", 5.0)),
        "max_chain_depth": int(data.get("max_chain_depth", 1)),
        "max_ticks": int(data.get("max_ticks", 0)),
    }
    if max_ticks_override is not None:
        defaults["max_ticks"] = max_ticks_override
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("Live agent group config requires a non-empty agents list.")
    return [_config_from_mapping(agent, server=server, defaults=defaults) for agent in agents if isinstance(agent, dict)]


def config_from_args(args: object) -> ResidentAgentConfig:
    return ResidentAgentConfig(
        server=str(getattr(args, "server")),
        agent_id=str(getattr(args, "agent_id")),
        display_name=str(getattr(args, "display_name") or getattr(args, "agent_id")),
        provider_kind=str(getattr(args, "provider_kind")),
        connection_kind=str(getattr(args, "connection_kind")),
        session_id=str(getattr(args, "session_id")),
        endpoint=str(getattr(args, "endpoint")),
        meeting_id=str(getattr(args, "meeting_id")),
        engagement_mode=str(getattr(args, "engagement_mode")),
        command=list(getattr(args, "resident_command")),
        timeout_seconds=int(getattr(args, "timeout")),
        poll_interval=float(getattr(args, "poll_interval")),
        heartbeat_interval=float(getattr(args, "heartbeat_interval")),
        cooldown=float(getattr(args, "cooldown")),
        max_chain_depth=int(getattr(args, "max_chain_depth")),
        max_ticks=int(getattr(args, "max_ticks")),
    )


def _config_from_mapping(
    data: dict[str, object],
    *,
    server: str,
    defaults: dict[str, int | float],
) -> ResidentAgentConfig:
    command = data.get("command")
    if not isinstance(command, list) or not command:
        raise ValueError("Each live agent requires a command list.")
    agent_id = str(data.get("agent_id") or "")
    if not agent_id:
        raise ValueError("Each live agent requires agent_id.")
    return ResidentAgentConfig(
        server=str(data.get("server") or server),
        agent_id=agent_id,
        display_name=str(data.get("display_name") or agent_id),
        provider_kind=str(data.get("provider_kind") or "local_cli"),
        connection_kind=str(data.get("connection_kind") or "local_cli"),
        session_id=str(data.get("session_id") or ""),
        endpoint=str(data.get("endpoint") or ""),
        meeting_id=str(data.get("meeting_id") or ""),
        engagement_mode=str(data.get("engagement_mode") or "always"),
        command=[str(part) for part in command],
        timeout_seconds=int(data.get("timeout_seconds") or data.get("timeout") or 120),
        poll_interval=float(_value_or_default(data.get("poll_interval"), defaults["poll_interval"])),
        heartbeat_interval=float(_value_or_default(data.get("heartbeat_interval"), defaults["heartbeat_interval"])),
        cooldown=float(data.get("cooldown") if data.get("cooldown") is not None else defaults["cooldown"]),
        max_chain_depth=int(_value_or_default(data.get("max_chain_depth"), defaults["max_chain_depth"])),
        max_ticks=int(data.get("max_ticks") if data.get("max_ticks") is not None else defaults["max_ticks"]),
    )


def _lobby_events(room: dict[str, object]) -> list[dict[str, object]]:
    events = room.get("lobby_events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _events_after(events: list[dict[str, object]], last_observed_event_id: str) -> list[dict[str, object]]:
    if not last_observed_event_id:
        return events
    for index, event in enumerate(events):
        if event.get("id") == last_observed_event_id:
            return events[index + 1 :]
    return []


def _is_self_event(event: dict[str, object], agent_id: str, display_name: str) -> bool:
    actor_id = str(event.get("actor_id") or "")
    if actor_id and actor_id == agent_id:
        return True
    return bool(display_name) and str(event.get("name") or "") == display_name


def _chain_depth(event: dict[str, object]) -> int:
    value = event.get("auto_chain_depth")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _value_or_default(value: object, default: int | float) -> object:
    return default if value is None else value


def _latest_event_id(events: list[dict[str, object]]) -> str:
    for event in reversed(events):
        if event.get("id"):
            return str(event["id"])
    return ""


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _server_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"
