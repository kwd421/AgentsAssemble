from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from agentsassemble.codex_resident import default_codex_resident_command
from agentsassemble.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.models import ENGAGEMENT_MODES, ProviderConfig, Role
from agentsassemble.remote_bridge_config import (
    remote_bridge_auth_ref_available,
    remote_bridge_auth_ref_value,
    remote_bridge_endpoint_error,
)


SUPPORTED_RESIDENT_CONNECTION_KINDS = ("local_cli", "live_session", "remote_bridge")


@dataclass(frozen=True)
class ResidentAgentConfig:
    server: str
    agent_id: str
    display_name: str
    provider_kind: str
    connection_kind: str
    session_id: str
    endpoint: str
    auth_ref: str
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
        self.last_observed_live_event_id = ""
        self.last_reply_at: datetime | None = None
        self.last_error_at: datetime | None = None
        self.last_error = ""
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
            self._heartbeat_final_offline()
        return replies

    def tick(self) -> int:
        room = self._room()
        engagement_mode = _runtime_engagement_mode(self.config, room)
        if engagement_mode == "moderator_called":
            self._observe_lobby_cursor(_lobby_events(room))
            events = _live_events(room)
            candidate = official_turn_request_candidate(
                events,
                self.config.agent_id,
                self.last_observed_live_event_id,
            )
            if candidate is None:
                self._advance_live_cursor(events)
                self._heartbeat_if_due()
                return 0
            if self._in_cooldown():
                self._heartbeat_if_due()
                return 0
            if self._in_failure_backoff():
                self._heartbeat_if_due()
                return 0

            generated = self._generate_reply(
                candidate,
                official_turn_prompt(self.config, room, candidate),
                cursor_field="last_observed_live_event_id",
            )
            if generated is None:
                return 0
            source_event_id, reply = generated
            response = self.request_json(
                _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/official-turn"),
                method="POST",
                payload={
                    "meeting_id": _official_turn_meeting_id(self.config, room, candidate),
                    "source_event_id": source_event_id,
                    "content": reply,
                    "role_id": str(candidate.get("role_id") or self.config.agent_id),
                    "display_name": str(candidate.get("display_name") or self.config.display_name or self.config.agent_id),
                    "turn_id": str(candidate.get("turn_id") or ""),
                    "turn_index": _optional_int(candidate.get("turn_index")),
                },
            )
            self._record_reply_success(
                response.get("event"),
                cursor_field="last_observed_live_event_id",
                observed_event_id=source_event_id,
            )
            return 1

        events = _lobby_events(room)
        candidate = event_reply_candidate(
            events,
            self.config.agent_id,
            self.config.display_name,
            self.last_observed_event_id,
            max_chain_depth=self.config.max_chain_depth,
            engagement_mode=engagement_mode,
        )
        if candidate is None:
            self._advance_cursor(events)
            self._heartbeat_if_due()
            return 0
        if self._in_cooldown():
            self._heartbeat_if_due()
            return 0
        if self._in_failure_backoff():
            self._heartbeat_if_due()
            return 0

        generated = self._generate_reply(
            candidate,
            delegate_prompt(self.config, room, candidate),
            cursor_field="last_observed_event_id",
        )
        if generated is None:
            return 0
        source_event_id, reply = generated

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
        self._record_reply_success(response.get("event"), cursor_field="last_observed_event_id")
        return 1

    def _generate_reply(
        self,
        candidate: dict[str, object],
        prompt: str,
        *,
        cursor_field: str,
    ) -> tuple[str, str] | None:
        source_event_id = str(candidate.get("id") or "")
        self._set_cursor(cursor_field, source_event_id)
        self._heartbeat("working", **self._cursor_metadata(cursor_field, source_event_id))
        try:
            reply = self._run_command_with_working_heartbeats(
                self.config.command,
                prompt,
                source_event_id=source_event_id,
                cursor_field=cursor_field,
                timeout_seconds=self.config.timeout_seconds,
            ).strip()
            if not reply:
                raise ValueError("Delegate command returned an empty reply.")
        except Exception as error:
            if self.stop_event.is_set():
                return None
            self.last_error = str(error)
            self.last_error_at = self.now_fn()
            self._heartbeat("error", last_error=self.last_error, **self._cursor_metadata(cursor_field, source_event_id))
            return None
        return source_event_id, reply

    def _record_reply_success(
        self,
        event_payload: object,
        *,
        cursor_field: str,
        observed_event_id: str | None = None,
    ) -> None:
        event = event_payload if isinstance(event_payload, dict) else {}
        if observed_event_id:
            self._set_cursor(cursor_field, observed_event_id)
        elif event.get("id"):
            self._set_cursor(cursor_field, str(event["id"]))
        self.last_reply_at = self.now_fn()
        self.last_error_at = None
        self.last_error = ""
        self._heartbeat(
            "online",
            last_reply_at=self.last_reply_at.isoformat(),
            last_error="",
            **self._cursor_metadata(),
        )

    def _register(self) -> None:
        response = self.request_json(
            _server_url(self.config.server, "/api/live-agents"),
            method="POST",
            payload={
                "agent_id": self.config.agent_id,
                "display_name": self.config.display_name,
                "provider_kind": self.config.provider_kind,
                "connection_kind": self.config.connection_kind,
                "session_id": self._current_session_id(),
                "endpoint": self.config.endpoint,
                "meeting_id": self.config.meeting_id,
                "engagement_mode": self.config.engagement_mode,
                "capabilities": ["room_chat", "mentions"],
            },
        )
        self._restore_agent_snapshot(response.get("agent"))

    def _heartbeat(self, status: str, **metadata: object) -> None:
        payload = {"status": status, **metadata}
        session_id = self._current_session_id()
        if session_id:
            payload.setdefault("session_id", session_id)
        self.request_json(
            _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/heartbeat"),
            method="POST",
            payload=payload,
        )
        self.last_heartbeat_at = self.now_fn()

    def _current_session_id(self) -> str:
        runner_session_id = str(getattr(self.command_runner, "session_id", "") or "").strip()
        return runner_session_id or str(self.config.session_id or "").strip()

    def _heartbeat_final_offline(self) -> None:
        try:
            self._heartbeat("offline", **self._cursor_metadata())
        except Exception:
            return

    def _heartbeat_if_due(self) -> None:
        if self.config.heartbeat_interval <= 0:
            return
        if self.last_heartbeat_at is None:
            self._heartbeat("online", **self._cursor_metadata())
            return
        elapsed = (self.now_fn() - self.last_heartbeat_at).total_seconds()
        if elapsed >= self.config.heartbeat_interval:
            if self._in_failure_backoff():
                self._heartbeat("error", last_error=self.last_error, **self._cursor_metadata())
                return
            self._heartbeat("online", **self._cursor_metadata())

    def _room(self) -> dict[str, object]:
        room = self.request_json(_server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/room"))
        self._restore_agent_snapshot(room.get("agent"))
        return room

    def _restore_agent_snapshot(self, agent: object) -> None:
        self._restore_observed_cursor(agent)
        self._restore_command_runner_session_id(agent)

    def _restore_observed_cursor(self, agent: object) -> None:
        if not isinstance(agent, dict):
            return
        agent_id = str(agent.get("agent_id") or "")
        if agent_id != self.config.agent_id:
            return
        cursor = str(agent.get("last_observed_event_id") or "").strip()
        if cursor and not self.last_observed_event_id:
            self.last_observed_event_id = cursor
        live_cursor = str(agent.get("last_observed_live_event_id") or "").strip()
        if live_cursor and not self.last_observed_live_event_id:
            self.last_observed_live_event_id = live_cursor

    def _restore_command_runner_session_id(self, agent: object) -> None:
        if not isinstance(agent, dict):
            return
        agent_id = str(agent.get("agent_id") or "")
        if agent_id != self.config.agent_id:
            return
        if self._current_session_id():
            return
        session_id = str(agent.get("session_id") or "").strip()
        if session_id and hasattr(self.command_runner, "session_id"):
            setattr(self.command_runner, "session_id", session_id)

    def _advance_cursor(self, events: list[dict[str, object]]) -> None:
        latest_id = _latest_event_id(events)
        if latest_id and latest_id != self.last_observed_event_id:
            self.last_observed_event_id = latest_id
            self._heartbeat("online", last_observed_event_id=latest_id)

    def _observe_lobby_cursor(self, events: list[dict[str, object]]) -> None:
        latest_id = _latest_event_id(events)
        if latest_id and latest_id != self.last_observed_event_id:
            self.last_observed_event_id = latest_id

    def _advance_live_cursor(self, events: list[dict[str, object]]) -> None:
        latest_id = _latest_event_id(events)
        if latest_id and latest_id != self.last_observed_live_event_id:
            self.last_observed_live_event_id = latest_id
            self._heartbeat("online", **self._cursor_metadata())

    def _set_cursor(self, cursor_field: str, event_id: str) -> None:
        if cursor_field == "last_observed_live_event_id":
            self.last_observed_live_event_id = event_id
            return
        self.last_observed_event_id = event_id

    def _cursor_metadata(self, cursor_field: str | None = None, event_id: str | None = None) -> dict[str, object]:
        lobby_cursor = self.last_observed_event_id
        live_cursor = self.last_observed_live_event_id
        if cursor_field == "last_observed_event_id" and event_id is not None:
            lobby_cursor = event_id
        if cursor_field == "last_observed_live_event_id" and event_id is not None:
            live_cursor = event_id
        metadata: dict[str, object] = {}
        if lobby_cursor:
            metadata["last_observed_event_id"] = lobby_cursor
        if live_cursor:
            metadata["last_observed_live_event_id"] = live_cursor
        return metadata

    def _in_cooldown(self) -> bool:
        if self.last_reply_at is None or self.config.cooldown <= 0:
            return False
        return (self.now_fn() - self.last_reply_at).total_seconds() < self.config.cooldown

    def _in_failure_backoff(self) -> bool:
        if self.last_error_at is None or self.config.cooldown <= 0:
            return False
        return (self.now_fn() - self.last_error_at).total_seconds() < self.config.cooldown

    def _run_command_with_working_heartbeats(
        self,
        command: list[str],
        prompt: str,
        *,
        source_event_id: str,
        cursor_field: str,
        timeout_seconds: int,
    ) -> str:
        heartbeat_stop = threading.Event()
        heartbeat_thread = self._start_working_heartbeat_loop(source_event_id, heartbeat_stop, cursor_field=cursor_field)
        try:
            return self.command_runner(command, prompt, timeout_seconds=timeout_seconds)
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join()

    def _start_working_heartbeat_loop(
        self,
        source_event_id: str,
        stop_event: threading.Event,
        *,
        cursor_field: str,
    ) -> threading.Thread | None:
        if self.config.heartbeat_interval <= 0:
            return None
        interval = max(0.01, self.config.heartbeat_interval)

        def keep_working_fresh() -> None:
            while not stop_event.wait(interval):
                if self.stop_event.is_set():
                    return
                try:
                    self._heartbeat("working", **self._cursor_metadata(cursor_field, source_event_id))
                except Exception:
                    return

        thread = threading.Thread(
            target=keep_working_fresh,
            daemon=True,
            name=f"AgentsAssembleWorkingHeartbeat-{self.config.agent_id}",
        )
        thread.start()
        return thread


class RemoteBridgeResidentCommandRunner:
    def __init__(
        self,
        config: ResidentAgentConfig,
        *,
        requester: Callable[..., dict[str, object]] | None = None,
    ) -> None:
        self.config = config
        self._validate_config()
        provider = ProviderConfig(
            id=config.agent_id,
            kind="remote_http_bridge",
            display_name=config.display_name or config.agent_id,
            endpoint=config.endpoint,
            auth_ref=config.auth_ref,
            timeout_seconds=config.timeout_seconds,
        )
        self.adapter = RemoteBridgeAdapter(provider, requester=requester)
        self.role = Role(
            id=config.agent_id,
            display_name=config.display_name or config.agent_id,
            lens=_remote_bridge_lens(config),
            research_focus="Live lobby participation",
        )
        self.session = {
            "meeting_id": config.meeting_id,
            "agent_id": config.agent_id,
            "owner_id": "remote_bridge",
            "join_mode": "current_session",
            "session_id": config.session_id,
        }

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command, timeout_seconds
        try:
            response = self.adapter.run_lobby_prompt(self.role, self.session, prompt)
        except Exception as error:
            raise RuntimeError(_sanitized_remote_bridge_error(error, self.config.auth_ref)) from error
        message = str(response.get("message") or "").strip()
        if not message:
            raise ValueError("Remote bridge returned an empty reply.")
        return message

    def _validate_config(self) -> None:
        endpoint_error = remote_bridge_endpoint_error(self.config.endpoint)
        if endpoint_error == "Remote bridge endpoint is required.":
            raise ValueError("Remote bridge resident requires an endpoint.")
        if endpoint_error:
            raise ValueError("Remote bridge resident requires a safe endpoint.")
        if not remote_bridge_auth_ref_available(self.config.auth_ref):
            raise ValueError("Remote bridge resident requires an available auth_ref.")


def event_reply_candidate(
    events: list[dict[str, object]],
    agent_id: str,
    display_name: str,
    last_observed_event_id: str,
    *,
    max_chain_depth: int,
    engagement_mode: str = "always",
) -> dict[str, object] | None:
    for event in _events_after(events, last_observed_event_id):
        if _is_self_event(event, agent_id, display_name):
            continue
        if _chain_depth(event) > max_chain_depth:
            continue
        if not str(event.get("message") or "").strip():
            continue
        if not should_reply_to_event(engagement_mode, event, agent_id, display_name):
            continue
        return event
    return None


def official_turn_request_candidate(
    events: list[dict[str, object]],
    agent_id: str,
    last_observed_event_id: str,
) -> dict[str, object] | None:
    for event in _events_after(events, last_observed_event_id):
        if str(event.get("kind") or "") != "live_agent_turn_request":
            continue
        if str(event.get("actor_id") or "") == agent_id:
            continue
        if str(event.get("target_agent_id") or "") != agent_id:
            continue
        if not str(event.get("content") or "").strip():
            continue
        return event
    return None


def _runtime_engagement_mode(config: ResidentAgentConfig, room: dict[str, object]) -> str:
    agent = room.get("agent")
    if not isinstance(agent, dict):
        return config.engagement_mode
    if str(agent.get("agent_id") or "") != config.agent_id:
        return config.engagement_mode
    mode = str(agent.get("engagement_mode") or "").strip()
    return mode if mode in ENGAGEMENT_MODES else config.engagement_mode


def should_reply_to_event(
    engagement_mode: str,
    event: dict[str, object],
    agent_id: str,
    display_name: str,
) -> bool:
    mode = str(engagement_mode or "mentioned").strip().lower().replace("-", "_")
    if mode == "always":
        return True
    if mode in {"watch", "manual", "moderator_called"}:
        return False
    if mode == "human_only":
        return _is_human_lobby_event(event)
    if mode == "mentioned":
        return _message_mentions_agent(str(event.get("message") or ""), agent_id, display_name)
    return _message_mentions_agent(str(event.get("message") or ""), agent_id, display_name)


def delegate_prompt(config: ResidentAgentConfig, room: dict[str, object], source_event: dict[str, object]) -> str:
    lines = [
        "You are a live AgentsAssemble participant connected through a resident agent bridge.",
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


def official_turn_prompt(config: ResidentAgentConfig, room: dict[str, object], source_event: dict[str, object]) -> str:
    lines = [
        "You are a live AgentsAssemble participant called into the official meeting record.",
        f"Agent id: {config.agent_id}",
        f"Display name: {config.display_name or config.agent_id}",
        "Reply with one concise official meeting turn only.",
        "Do not include lobby chatter, markdown fences, or multiple alternatives.",
        "",
        "Moderator request:",
        f"- {source_event.get('content') or ''}",
        "",
        "Recent official meeting events:",
    ]
    for event in _live_events(room)[-12:]:
        if not _live_event_visible_to_agent(event, config.agent_id, source_event):
            continue
        content = str(event.get("content") or "").strip()
        if content:
            lines.append(f"- {event.get('display_name') or event.get('actor_id') or event.get('kind') or 'participant'}: {content}")
    recent_lobby = [
        str(event.get("message") or "").strip()
        for event in _lobby_events(room)[-6:]
        if str(event.get("message") or "").strip()
    ]
    if recent_lobby:
        lines.append("")
        lines.append("Recent lobby context, for awareness only:")
        for message in recent_lobby:
            lines.append(f"- {message}")
    return "\n".join(lines).strip() + "\n"


def load_group_configs(
    path: Path,
    *,
    max_ticks_override: int | None = None,
    server_override: str | None = None,
) -> list[ResidentAgentConfig]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Live agent group config must be a JSON object.")
    server = str(server_override or data.get("server") or "http://127.0.0.1:8765")
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
    return [
        _config_from_mapping(agent, server=server, defaults=defaults, server_override=server_override)
        for agent in agents
        if isinstance(agent, dict)
    ]


def config_from_args(args: object) -> ResidentAgentConfig:
    provider_kind = str(getattr(args, "provider_kind"))
    connection_kind = str(getattr(args, "connection_kind"))
    command = list(getattr(args, "resident_command", []) or [])
    return ResidentAgentConfig(
        server=str(getattr(args, "server")),
        agent_id=str(getattr(args, "agent_id")),
        display_name=str(getattr(args, "display_name") or getattr(args, "agent_id")),
        provider_kind=provider_kind,
        connection_kind=connection_kind,
        session_id=str(getattr(args, "session_id")),
        endpoint=str(getattr(args, "endpoint")),
        auth_ref=str(getattr(args, "auth_ref", "")),
        meeting_id=str(getattr(args, "meeting_id")),
        engagement_mode=str(getattr(args, "engagement_mode")),
        command=default_codex_resident_command(provider_kind, connection_kind, command),
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
    server_override: str | None = None,
) -> ResidentAgentConfig:
    connection_kind = str(data.get("connection_kind") or "local_cli")
    if connection_kind not in SUPPORTED_RESIDENT_CONNECTION_KINDS:
        raise ValueError(resident_connection_kind_error())
    provider_kind = str(data.get("provider_kind") or "local_cli")
    command = data.get("command")
    endpoint = data.get("endpoint")
    auth_ref = data.get("auth_ref")
    command_parts = [str(part) for part in command] if isinstance(command, list) else []
    command_parts = default_codex_resident_command(provider_kind, connection_kind, command_parts)
    if connection_kind != "remote_bridge" and not command_parts:
        raise ValueError("Each live agent requires a command list.")
    agent_id = str(data.get("agent_id") or "")
    if not agent_id:
        raise ValueError("Each live agent requires agent_id.")
    return ResidentAgentConfig(
        server=str(server_override or data.get("server") or server),
        agent_id=agent_id,
        display_name=str(data.get("display_name") or agent_id),
        provider_kind=provider_kind,
        connection_kind=connection_kind,
        session_id=str(data.get("session_id") or ""),
        endpoint=endpoint if isinstance(endpoint, str) else "",
        auth_ref=auth_ref if isinstance(auth_ref, str) else "",
        meeting_id=str(data.get("meeting_id") or ""),
        engagement_mode=str(data.get("engagement_mode") or "mentioned"),
        command=command_parts,
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


def _live_events(room: dict[str, object]) -> list[dict[str, object]]:
    events = room.get("live_events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _live_event_visible_to_agent(
    event: dict[str, object],
    agent_id: str,
    source_event: dict[str, object],
) -> bool:
    if event.get("id") and event.get("id") == source_event.get("id"):
        return True
    if event.get("official_record") is True:
        return True
    target_agent_id = str(event.get("target_agent_id") or "")
    if target_agent_id:
        return target_agent_id == agent_id
    audience = str(event.get("audience") or "")
    if audience.startswith("agent:"):
        return audience == f"agent:{agent_id}"
    return str(event.get("kind") or "") != "live_agent_turn_request"


def _official_turn_meeting_id(
    config: ResidentAgentConfig,
    room: dict[str, object],
    source_event: dict[str, object],
) -> str:
    for value in (source_event.get("meeting_id"), room.get("meeting_id"), config.meeting_id):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _is_human_lobby_event(event: dict[str, object]) -> bool:
    if str(event.get("actor_id") or ""):
        return False
    side = str(event.get("side") or "")
    return side in {"", "mine", "other"}


def _message_mentions_agent(message: str, agent_id: str, display_name: str) -> bool:
    normalized_message = message.casefold()
    mentions = [agent_id, display_name]
    return any(str(mention or "").casefold() in normalized_message for mention in mentions if str(mention or "").strip())


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


def _remote_bridge_lens(config: ResidentAgentConfig) -> str:
    provider = str(config.provider_kind or "").strip()
    if provider:
        return f"{provider} remote bridge lobby participant"
    return "Remote bridge lobby participant"


def _sanitized_remote_bridge_error(error: Exception, auth_ref: str) -> str:
    text = str(error).strip() or error.__class__.__name__
    secret = _resolved_secret_for_redaction(auth_ref)
    if secret and secret in text:
        return "Remote bridge request failed."
    if _looks_sensitive_error(text):
        return "Remote bridge request failed."
    return text


def _resolved_secret_for_redaction(auth_ref: str) -> str:
    return remote_bridge_auth_ref_value(auth_ref)


def _looks_sensitive_error(text: str) -> bool:
    normalized = text.casefold()
    markers = (
        "authorization",
        "bearer ",
        "secret",
        "token",
        "api-key",
        "apikey",
        "x-api-key",
        "password",
        "http://",
        "https://",
    )
    return any(marker in normalized for marker in markers)


def resident_connection_kind_error() -> str:
    return "Resident groups support local_cli, live_session, and remote_bridge connections."
