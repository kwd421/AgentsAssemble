from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from agentsassemble.live_cli_control import GeneralRoomController
from agentsassemble.meeting_events import clean_lobby_text


SendJson = Callable[[dict[str, object]], None]


@dataclass
class GeneralRoomSocketConnection:
    connection_id: str
    send_json: SendJson
    last_event_id: str = ""
    closed: bool = False


class GeneralRoomSocketHub:
    """WebSocket protocol core for the local CLI-first #general room.

    JSONL remains the durable source of truth. This hub is only the live
    transport: it pushes appended events, CLI deltas, state changes, and final
    latency to connected browsers.
    """

    def __init__(self, controller: GeneralRoomController) -> None:
        self.controller = controller
        self._lock = threading.RLock()
        self._connections: dict[str, GeneralRoomSocketConnection] = {}
        self.controller.room.add_listener(self._broadcast_event)
        self.controller.scheduler.agent_state_listener = self._broadcast_agent_state
        self.controller.scheduler.latency_listener = self._broadcast_latency

    def connect(self, send_json: SendJson) -> GeneralRoomSocketConnection:
        connection = GeneralRoomSocketConnection(connection_id="ws_" + uuid4().hex[:12], send_json=send_json)
        with self._lock:
            self._connections[connection.connection_id] = connection
        return connection

    def disconnect(self, connection: GeneralRoomSocketConnection) -> None:
        connection.closed = True
        with self._lock:
            self._connections.pop(connection.connection_id, None)

    def handle_message(self, connection: GeneralRoomSocketConnection, message: dict[str, object]) -> None:
        message_type = clean_lobby_text(message.get("type"), limit=64)
        if not message_type:
            self._send_error(connection, "message type is required")
            return
        try:
            if message_type == "hello":
                self._handle_hello(connection, message)
            elif message_type == "user_message":
                self._handle_user_message(message)
            elif message_type == "agent_control":
                self._handle_agent_control(message)
            elif message_type == "dispatch":
                self.controller.dispatch()
            elif message_type == "smoke_start":
                self._handle_smoke_start(message)
            else:
                self._send_error(connection, f"unknown room socket message type: {message_type}")
        except Exception as error:
            self._send_error(connection, str(error))

    def handle_text(self, connection: GeneralRoomSocketConnection, payload: bytes) -> None:
        import json

        try:
            message = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_error(connection, "WebSocket frame is not valid JSON")
            return
        if not isinstance(message, dict):
            self._send_error(connection, "WebSocket message must be a JSON object")
            return
        self.handle_message(connection, message)

    def _handle_hello(self, connection: GeneralRoomSocketConnection, message: dict[str, object]) -> None:
        after_event_id = clean_lobby_text(message.get("after_event_id"), limit=128)
        connection.last_event_id = after_event_id
        self._send(connection, self._snapshot(after_event_id=after_event_id))

    def _handle_user_message(self, message: dict[str, object]) -> None:
        content = clean_lobby_text(message.get("content") or message.get("message"), limit=12000)
        if not content:
            raise ValueError("content is required")
        actor_id = clean_lobby_text(message.get("actor_id"), limit=128) or "human"
        self.controller.post_message(content=content, actor_id=actor_id)

    def _handle_agent_control(self, message: dict[str, object]) -> None:
        agent_id = clean_lobby_text(message.get("agent_id"), limit=128)
        action = clean_lobby_text(message.get("action"), limit=64)
        if not agent_id:
            raise ValueError("agent_id is required")
        if action == "start":
            payload = self.controller.start_agent(agent_id)
        elif action == "stop":
            payload = self.controller.stop_agent(agent_id)
        elif action == "resume":
            payload = self.controller.resume_agent(agent_id)
        elif action == "interrupt":
            payload = self.controller.interrupt_agent(agent_id)
        else:
            raise ValueError(f"unsupported agent control action: {action}")
        self._broadcast({"type": "agent_state", "agent": payload["agent"]})

    def _handle_smoke_start(self, message: dict[str, object]) -> None:
        def run() -> None:
            self._broadcast({"type": "smoke_progress", "run_id": "", "phase": "start", "status": "running"})
            result = self.controller.smoke_payload(message, reporter=self._broadcast)
            self._broadcast(
                {
                    "type": "smoke_progress",
                    "run_id": str(result.get("run_id") or ""),
                    "phase": "complete",
                    "status": str(result.get("status") or "unknown"),
                    "result": result,
                }
            )

        threading.Thread(target=run, daemon=True).start()

    def _snapshot(self, *, after_event_id: str = "") -> dict[str, object]:
        agents = list(self.controller.agents_payload().get("agents", []))
        latency = {
            str(agent.get("agent_id") or ""): dict(agent.get("latency") or {})
            for agent in agents
            if isinstance(agent, dict)
        }
        latency.update(self.controller.scheduler.latency_payload())
        return {
            "type": "snapshot",
            "room_id": self.controller.room.room_id,
            "events": self.controller.room.read_events(after=after_event_id),
            "agents": agents,
            "latency": latency,
        }

    def _broadcast_event(self, event: dict[str, object]) -> None:
        kind = clean_lobby_text(event.get("kind"), limit=64)
        actor_id = clean_lobby_text(event.get("actor_id"), limit=128)
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if kind == "agent_delta":
            self._broadcast(
                {
                    "type": "agent_delta",
                    "agent_id": actor_id,
                    "turn_id": str(metadata.get("source_event_id") or ""),
                    "delta": str(event.get("content") or ""),
                    "event": event,
                }
            )
            return
        if kind == "agent_message":
            self._broadcast({"type": "agent_message", "event": event})
            return
        if kind == "agent_error":
            self._broadcast(
                {
                    "type": "error",
                    "message": str(event.get("content") or ""),
                    "recoverable": True,
                    "event": event,
                }
            )
            return
        self._broadcast({"type": "room_event", "event": event})

    def _broadcast_agent_state(self, agent_id: str, agent: dict[str, object]) -> None:
        if not agent:
            return
        self._broadcast({"type": "agent_state", "agent_id": agent_id, "agent": agent})

    def _broadcast_latency(self, agent_id: str, latency: dict[str, object]) -> None:
        if not latency:
            return
        self._broadcast({"type": "latency", "agent_id": agent_id, **dict(latency)})

    def _send_error(self, connection: GeneralRoomSocketConnection, message: str) -> None:
        self._send(connection, {"type": "error", "message": message, "recoverable": True})

    def _broadcast(self, payload: dict[str, object]) -> None:
        with self._lock:
            connections = list(self._connections.values())
        for connection in connections:
            self._send(connection, payload)

    def _send(self, connection: GeneralRoomSocketConnection, payload: dict[str, object]) -> None:
        if connection.closed:
            return
        try:
            connection.send_json(dict(payload))
        except Exception:
            self.disconnect(connection)
