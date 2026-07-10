from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
import socket
import threading
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from agentsassemble.agent_sessions import build_room_turn_packet
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_invite import revoke_sessions_for_participant
from agentsassemble.room_members import is_room_member_muted, remove_room_member, set_room_member_muted
from agentsassemble.room_store import RoomStore
from agentsassemble.voice_presence import leave_all_voice

ROOM_EVENT_STREAM = "room_events"
ROOM_SNAPSHOT_EVENT_LIMIT = 200
ROOM_HISTORY_MAX_LIMIT = 200
ROOM_COMMAND_ACTIONS = {
    "room.history",
    "message.send",
    "agent.create",
    "agent.start",
    "agent.stop",
    "agent.resume",
    "agent.interrupt",
    "participant.kick",
    "participant.mute",
    "bridge.ready",
    "bridge.health",
    "turn.state",
    "message.delta",
    "message.final",
    "turn.failed",
}


@dataclass(frozen=True)
class NativeCliProviderSpec:
    agent_id: str
    display_name: str
    command: tuple[str, ...]
    cwd: str = "."
    provider_kind: str = ""
    model: str = ""
    default_responder: bool = True
    quiet_seconds: float = 4.0
    input_mode: str = "line"
    submit_newline: str = "\r"
    submit_delay_seconds: float = 0.1
    terminal_rows: int = 40
    terminal_columns: int = 120
    startup_quiet_seconds: float = 1.0
    startup_timeout_seconds: float = 20.0
    startup_accept_contains: str = ""
    startup_accept_keys: str = "\r"
    turn_timeout_seconds: float = 180.0

    def normalized_provider_kind(self) -> str:
        return clean_lobby_text(self.provider_kind, limit=64) or f"{self.agent_id}_live_session"

    def runtime_profile_key(self) -> str:
        profile = json.dumps(
            {
                "provider_kind": self.normalized_provider_kind(),
                "command": list(self.command),
                "cwd": str(Path(self.cwd).expanduser().resolve()),
                "model": self.model,
                "quiet_seconds": self.quiet_seconds,
                "input_mode": self.input_mode,
                "submit_newline": self.submit_newline,
                "submit_delay_seconds": self.submit_delay_seconds,
                "terminal_rows": self.terminal_rows,
                "terminal_columns": self.terminal_columns,
                "startup_quiet_seconds": self.startup_quiet_seconds,
                "startup_timeout_seconds": self.startup_timeout_seconds,
                "startup_accept_contains": self.startup_accept_contains,
                "startup_accept_keys": self.startup_accept_keys,
                "turn_timeout_seconds": self.turn_timeout_seconds,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(profile.encode("utf-8")).hexdigest()[:20]


def default_native_cli_provider_specs(*, workspace: str | Path = ".") -> list[NativeCliProviderSpec]:
    cwd = str(Path(workspace).expanduser().resolve())
    return [
        NativeCliProviderSpec(
            agent_id="codex",
            display_name="Codex Spark",
            command=(
                "codex",
                "--no-alt-screen",
                "--ask-for-approval",
                "never",
                "--sandbox",
                "read-only",
                "--model",
                "gpt-5.3-codex-spark",
            ),
            cwd=cwd,
            provider_kind="codex_live_session",
            model="gpt-5.3-codex-spark",
            input_mode="bracketed_paste",
            startup_accept_contains="Do you trust",
        ),
        NativeCliProviderSpec(
            agent_id="antigravity",
            display_name="Antigravity CLI",
            command=("agy", "--sandbox"),
            cwd=cwd,
            provider_kind="antigravity_live_session",
            input_mode="bracketed_paste",
            startup_accept_contains="Do you trust",
        ),
        NativeCliProviderSpec(
            agent_id="grok",
            display_name="Grok CLI",
            command=("grok", "--no-alt-screen", "--permission-mode", "plan"),
            cwd=cwd,
            provider_kind="grok_live_session",
        ),
        NativeCliProviderSpec(
            agent_id="claude",
            display_name="Claude Haiku",
            command=(
                "claude",
                "--model",
                "haiku",
                "--permission-mode",
                "plan",
                "--tools",
                "--safe-mode",
            ),
            cwd=cwd,
            provider_kind="claude_code",
            model="haiku",
            input_mode="bracketed_paste",
            startup_accept_contains="Do you trust",
        ),
    ]


def validate_native_cli_provider_spec(spec: NativeCliProviderSpec) -> None:
    """Reject provider command shapes that cannot represent a resident session."""

    executable = Path(spec.command[0]).name.casefold() if spec.command else ""
    is_claude = executable == "claude" or spec.normalized_provider_kind() == "claude_code"
    if not is_claude:
        return
    forbidden = [part for part in spec.command[1:] if part in {"-p", "--print"} or part.startswith("--print=")]
    if forbidden:
        raise ValueError("Claude Code Agent Sessions require interactive mode; print mode is forbidden.")


def native_cli_provider_spec_from_payload(payload: dict[str, object]) -> NativeCliProviderSpec:
    provider = clean_lobby_text(payload.get("provider_id") or payload.get("provider_kind") or payload.get("provider"), limit=64)
    aliases = {
        "codex": "codex_live_session",
        "claude": "claude_code",
        "antigravity": "antigravity_live_session",
        "agy": "antigravity_live_session",
        "grok": "grok_live_session",
    }
    provider_kind = aliases.get(provider, provider)
    display_name = clean_lobby_text(payload.get("display_name"), limit=64) or provider or "Agent"
    explicit_agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("participant_id"), limit=128)
    agent_id = explicit_agent_id or _slug_agent_id(f"{provider or 'agent'}-{display_name}")
    workspace = clean_lobby_text(payload.get("workspace") or payload.get("workspace_path") or payload.get("cwd"), limit=500)
    cwd = str(Path(workspace or ".").expanduser().resolve())
    model = clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128)
    if provider_kind == "codex_live_session":
        model = model or "gpt-5.3-codex-spark"
        spec = NativeCliProviderSpec(
            agent_id=agent_id,
            display_name=display_name,
            command=(
                "codex",
                "--no-alt-screen",
                "--ask-for-approval",
                "never",
                "--sandbox",
                "read-only",
                "--model",
                model,
            ),
            cwd=cwd,
            provider_kind=provider_kind,
            model=model,
            input_mode="bracketed_paste",
            startup_accept_contains="Do you trust",
        )
    elif provider_kind == "claude_code":
        model = model or "haiku"
        spec = NativeCliProviderSpec(
            agent_id=agent_id,
            display_name=display_name,
            command=("claude", "--model", model, "--permission-mode", "plan", "--tools", "--safe-mode"),
            cwd=cwd,
            provider_kind=provider_kind,
            model=model,
            input_mode="bracketed_paste",
            startup_accept_contains="Do you trust",
        )
    elif provider_kind == "antigravity_live_session":
        spec = NativeCliProviderSpec(
            agent_id=agent_id,
            display_name=display_name,
            command=("agy", "--sandbox"),
            cwd=cwd,
            provider_kind=provider_kind,
            model=model,
            input_mode="bracketed_paste",
            startup_accept_contains="Do you trust",
        )
    elif provider_kind == "grok_live_session":
        spec = NativeCliProviderSpec(
            agent_id=agent_id,
            display_name=display_name,
            command=("grok", "--no-alt-screen", "--permission-mode", "plan"),
            cwd=cwd,
            provider_kind=provider_kind,
            model=model,
        )
    else:
        raise RoomCommandRejected(f"Provider {provider or provider_kind or 'unknown'} is not available as a native CLI Agent Session.", code="unsupported_provider")
    validate_native_cli_provider_spec(spec)
    return spec


class AgentBridgeManager(Protocol):
    def start(
        self,
        room_id: str,
        session: dict[str, object],
        spec: NativeCliProviderSpec,
        *,
        server_url: str = "",
        ticket_issuer: Callable[[dict[str, object]], str] | None = None,
    ) -> dict[str, object]: ...

    def stop(
        self,
        room_id: str,
        session_id: str,
        *,
        timeout_seconds: float = 2.0,
        provider_pid: int | None = None,
    ) -> dict[str, object]: ...

    def close(self) -> None: ...


class RoomCommandRejected(ValueError):
    def __init__(self, message: str, *, code: str = "rejected") -> None:
        super().__init__(message)
        self.code = code


class RoomSocketChannel:
    """Bounded per-connection outbound queue with a selectable wakeup fd."""

    def __init__(self, identity: dict[str, object], *, max_messages: int = 1000) -> None:
        self.connection_id = f"conn-{uuid4().hex[:12]}"
        self.identity = dict(identity)
        self.room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        self.max_messages = max(10, int(max_messages or 1000))
        self._queue: deque[dict[str, object]] = deque()
        self._subscriptions: set[str] = set()
        self._lock = threading.RLock()
        self._read_socket, self._write_socket = socket.socketpair()
        self._read_socket.setblocking(False)
        self._write_socket.setblocking(False)
        self.closed = False

    def subscribe(self, streams: set[str]) -> None:
        with self._lock:
            self._subscriptions = set(streams)

    def subscribed(self, stream: str) -> bool:
        with self._lock:
            return stream in self._subscriptions

    def send(self, message: dict[str, object]) -> bool:
        with self._lock:
            if self.closed:
                return False
            was_empty = not self._queue
            if len(self._queue) >= self.max_messages:
                self._drop_for_backpressure()
            self._queue.append(dict(message))
            if was_empty:
                try:
                    self._write_socket.send(b"\x01")
                except (BlockingIOError, OSError):
                    pass
            return True

    def drain(self) -> list[dict[str, object]]:
        with self._lock:
            if self.closed:
                return []
            while True:
                try:
                    if not self._read_socket.recv(4096):
                        break
                except BlockingIOError:
                    break
                except OSError:
                    break
            messages = list(self._queue)
            self._queue.clear()
            return messages

    def fileno(self) -> int:
        return self._read_socket.fileno()

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            self._queue.clear()
            for sock in (self._read_socket, self._write_socket):
                try:
                    sock.close()
                except OSError:
                    pass

    def _drop_for_backpressure(self) -> None:
        for index, message in enumerate(self._queue):
            events = message.get("events") if isinstance(message.get("events"), list) else []
            if any(isinstance(event, dict) and event.get("type") == "message_delta" for event in events):
                del self._queue[index]
                return
        if self._queue:
            self._queue.popleft()


class RoomEventBroker:
    """Non-blocking fanout for canonical room events and targeted bridge turns."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._channels: dict[str, RoomSocketChannel] = {}

    def connect(self, identity: dict[str, object]) -> RoomSocketChannel:
        channel = RoomSocketChannel(identity)
        with self._lock:
            self._channels[channel.connection_id] = channel
        return channel

    def disconnect(self, channel: RoomSocketChannel) -> None:
        with self._lock:
            self._channels.pop(channel.connection_id, None)
        channel.close()

    def broadcast_event(self, event: dict[str, object]) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        message = {"op": "event", "stream": ROOM_EVENT_STREAM, "events": [dict(event)]}
        with self._lock:
            channels = list(self._channels.values())
        for channel in channels:
            if channel.room_id == room_id and channel.subscribed(ROOM_EVENT_STREAM):
                channel.send(message)

    def direct_to_bridge(self, room_id: str, participant_id: str, message: dict[str, object]) -> bool:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        delivered = False
        with self._lock:
            channels = list(self._channels.values())
        for channel in channels:
            identity = channel.identity
            if (
                channel.room_id == clean_room_id
                and clean_lobby_text(identity.get("agent_id"), limit=128) == clean_participant_id
                and identity.get("client_type") == "agent_bridge"
            ):
                delivered = channel.send(message) or delivered
        return delivered

    def has_bridge(self, room_id: str, participant_id: str) -> bool:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        with self._lock:
            return any(
                not channel.closed
                and channel.room_id == clean_room_id
                and channel.identity.get("client_type") == "agent_bridge"
                and clean_lobby_text(channel.identity.get("agent_id"), limit=128) == clean_participant_id
                for channel in self._channels.values()
            )

    def close(self) -> None:
        with self._lock:
            channels = list(self._channels.values())
            self._channels.clear()
        for channel in channels:
            channel.close()


class RoomRealtimeController:
    """Canonical room command, event, session, and provider-turn coordinator."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        providers: list[NativeCliProviderSpec] | None = None,
        bridge_manager: AgentBridgeManager | None = None,
        broker: RoomEventBroker | None = None,
        default_room_id: str = "general",
        max_agent_relay_depth: int = 2,
    ) -> None:
        self.output_root = Path(output_root)
        self.store = RoomStore(self.output_root)
        self.broker = broker or RoomEventBroker()
        self.bridge_manager = bridge_manager
        self.default_room_id = clean_lobby_text(default_room_id, limit=128) or "general"
        self.max_agent_relay_depth = max(0, int(max_agent_relay_depth))
        default_providers = {
            clean_lobby_text(spec.agent_id, limit=128): spec
            for spec in list(providers or [])
            if clean_lobby_text(spec.agent_id, limit=128)
        }
        self._providers_by_room: dict[str, dict[str, NativeCliProviderSpec]] = {
            self.default_room_id: default_providers,
        }
        self._lock = threading.RLock()
        self._event_listener_removers: dict[str, Callable[[], None]] = {}
        self._closed = False
        self.ensure_room(self.default_room_id)
        for spec in default_providers.values():
            self._ensure_provider_session(self.default_room_id, spec)

    def register_provider(self, room_id: str, spec: NativeCliProviderSpec) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        validate_native_cli_provider_spec(spec)
        self.ensure_room(clean_room_id)
        with self._lock:
            previous_session = self.store.session(clean_room_id, spec.agent_id)
            previous_participant = self.store.participant(clean_room_id, spec.agent_id)
            providers = self._providers_by_room.setdefault(clean_room_id, {})
            providers[clean_lobby_text(spec.agent_id, limit=128)] = spec
            self._ensure_provider_session(clean_room_id, spec)
            if previous_participant.get("status") == "kicked":
                self.store.update_participant_fields(clean_room_id, spec.agent_id, status="detached")
            current = self.store.session(clean_room_id, spec.agent_id)
            if not previous_session or previous_participant.get("status") == "kicked":
                self.store.append_event(
                    clean_room_id,
                    "participant_joined",
                    participant_id=spec.agent_id,
                    session_id=spec.agent_id,
                )
                self.store.append_event(
                    clean_room_id,
                    "agent_session_created",
                    participant_id=spec.agent_id,
                    session_id=spec.agent_id,
                    provider_kind=spec.normalized_provider_kind(),
                )
            self._publish_session_state(clean_room_id, current)
        return self._public_session(current)

    def ensure_room(self, room_id: str) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        with self._lock:
            room = self.store.create_room(clean_room_id, label="#general" if clean_room_id == "general" else clean_room_id)
            if clean_room_id not in self._event_listener_removers:
                self._event_listener_removers[clean_room_id] = self.store.add_event_listener(
                    clean_room_id,
                    self._on_event_appended,
                )
            return room

    def connect(self, identity: dict[str, object]) -> RoomSocketChannel:
        room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        self.ensure_room(room_id)
        if identity.get("client_type") != "agent_bridge":
            participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
            if participant_id:
                self.store.upsert_participant(
                    room_id,
                    {
                        "participant_id": participant_id,
                        "display_name": clean_lobby_text(identity.get("display_name"), limit=64) or participant_id,
                        "participant_type": "human",
                        "role": "host" if identity.get("operator") else "member",
                        "status": "joined",
                    },
                )
        return self.broker.connect(identity)

    def disconnect(self, channel: RoomSocketChannel) -> None:
        identity = channel.identity
        self.broker.disconnect(channel)
        if identity.get("client_type") != "agent_bridge":
            return
        room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        session_id = clean_lobby_text(identity.get("session_id") or identity.get("agent_id"), limit=128)
        session = self.store.session(room_id, session_id)
        if not session or session.get("runtime_status") == "stopped":
            return
        self.store.update_session_fields(
            room_id,
            session_id,
            status="unavailable",
            runtime_status="disconnected",
            pid=None,
            last_error="Agent bridge disconnected.",
        )
        participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
        if participant_id and self.store.participant(room_id, participant_id):
            self.store.update_participant_fields(room_id, participant_id, status="detached")
        self.store.append_event(
            room_id,
            "session_detached",
            participant_id=participant_id,
            session_id=session_id,
            reason="agent bridge disconnected",
        )
        self._publish_session_state(room_id, self.store.session(room_id, session_id))

    def snapshot(self, identity: dict[str, object], *, after_seq: int = 0) -> dict[str, object]:
        room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        self.ensure_room(room_id)
        latest_seq = self.store.latest_event_sequence(room_id)
        requested_after_seq = max(0, int(after_seq or 0))
        bridge = identity.get("client_type") == "agent_bridge"
        resume_gap = False
        if bridge:
            events: list[dict[str, object]] = []
            snapshot_mode = "bridge"
        elif requested_after_seq:
            resume_gap = latest_seq - requested_after_seq > ROOM_SNAPSHOT_EVENT_LIMIT
            if resume_gap:
                events = self.store.read_events(room_id, limit=ROOM_SNAPSHOT_EVENT_LIMIT, newest=True)
                snapshot_mode = "gap"
            else:
                events = self.store.read_events(
                    room_id,
                    after_seq=requested_after_seq,
                    limit=ROOM_SNAPSHOT_EVENT_LIMIT,
                )
                snapshot_mode = "resume"
        else:
            events = self.store.read_events(room_id, limit=ROOM_SNAPSHOT_EVENT_LIMIT, newest=True)
            snapshot_mode = "initial"
        oldest_seq = int(events[0].get("seq") or 0) if events else 0
        has_more_before = bool(
            not bridge
            and oldest_seq
            and self.store.oldest_event_sequence(room_id) < oldest_seq
        )
        sessions = [self._public_session(session) for session in self.store.sessions(room_id)]
        active_turns = [
            {
                "turn_id": session.get("active_turn_id"),
                "participant_id": session.get("participant_id"),
                "phase": session.get("turn_phase") or session.get("runtime_status"),
            }
            for session in sessions
            if session.get("active_turn_id")
        ]
        return {
            "op": "snapshot",
            "stream": ROOM_EVENT_STREAM,
            "room": self.store.room(room_id),
            "participants": self.store.participants(room_id),
            "agent_sessions": sessions,
            "active_turns": active_turns,
            "events": events,
            "oldest_seq": oldest_seq,
            "last_seq": latest_seq,
            "has_more_before": has_more_before,
            "resume_gap": resume_gap,
            "snapshot_mode": snapshot_mode,
            "capabilities": self.capabilities(identity),
        }

    def history_page(self, room_id: str, *, before_seq: int, limit: int = ROOM_HISTORY_MAX_LIMIT) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        self.ensure_room(clean_room_id)
        clean_before_seq = max(0, int(before_seq or 0))
        clean_limit = min(ROOM_HISTORY_MAX_LIMIT, max(1, int(limit or ROOM_HISTORY_MAX_LIMIT)))
        events = self.store.read_events(
            clean_room_id,
            before_seq=clean_before_seq,
            limit=clean_limit,
            newest=True,
        )
        oldest_seq = int(events[0].get("seq") or 0) if events else 0
        return {
            "events": events,
            "oldest_seq": oldest_seq,
            "has_more_before": bool(
                oldest_seq and self.store.oldest_event_sequence(clean_room_id) < oldest_seq
            ),
            "last_seq": self.store.latest_event_sequence(clean_room_id),
        }

    def capabilities(self, identity: dict[str, object]) -> dict[str, bool]:
        operator = bool(identity.get("operator"))
        bridge = identity.get("client_type") == "agent_bridge"
        read_write = str(identity.get("invite_scope") or "read_write") != "read_only"
        return {
            "room.history": not bridge,
            "message.send": read_write and not bridge,
            "room.manage": operator,
            "participant.kick": operator,
            "participant.mute": operator,
            "agent.control": operator,
            "bridge.report": bridge,
        }

    def handle_command(
        self,
        identity: dict[str, object],
        message: dict[str, object],
        *,
        server_url: str = "",
        ticket_issuer: Callable[[dict[str, object]], str] | None = None,
    ) -> dict[str, object]:
        room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        request_id = clean_lobby_text(message.get("request_id"), limit=128)
        action = clean_lobby_text(message.get("action"), limit=64)
        payload = dict(message.get("payload")) if isinstance(message.get("payload"), dict) else {}
        if not request_id:
            raise RoomCommandRejected("request_id is required.", code="bad_request")
        if action not in ROOM_COMMAND_ACTIONS:
            raise RoomCommandRejected(f"Unsupported room command: {action}", code="unknown_action")
        self.ensure_room(room_id)
        if action == "room.history":
            if identity.get("client_type") == "agent_bridge":
                raise RoomCommandRejected("Agent Bridges receive assigned context, not browser history pages.", code="permission_denied")
            result = self.history_page(
                room_id,
                before_seq=_safe_bounded_int(payload.get("before_seq"), default=0, minimum=0),
                limit=_safe_bounded_int(
                    payload.get("limit"),
                    default=ROOM_HISTORY_MAX_LIMIT,
                    minimum=1,
                    maximum=ROOM_HISTORY_MAX_LIMIT,
                ),
            )
            return {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "action": action,
                "result": result,
                "deduplicated": False,
            }
        with self._lock:
            prior = self.store.command_result(room_id, request_id)
            if prior:
                return {**prior, "deduplicated": True}
            result = self._execute_action(
                identity,
                room_id,
                action,
                payload,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
            ack = {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "action": action,
                "result": result,
                "deduplicated": False,
            }
            return self.store.record_command_result(room_id, request_id, ack)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            removers = list(self._event_listener_removers.values())
            self._event_listener_removers.clear()
        for remove in removers:
            remove()
        if self.bridge_manager is not None:
            for room_id, providers in list(self._providers_by_room.items()):
                for agent_id in list(providers):
                    session = self.store.session(room_id, agent_id)
                    if session and session.get("runtime_status") not in {"stopped", "available"}:
                        try:
                            self._stop_agent(room_id, agent_id)
                        except Exception:
                            continue
            self.bridge_manager.close()
        self.broker.close()

    def bridge_process_exited(
        self,
        room_id: str,
        session_id: str,
        returncode: int,
        stderr_tail: str = "",
    ) -> None:
        """Preserve crash evidence and leave backlog ready for explicit resume."""
        with self._lock:
            session = self.store.session(room_id, session_id)
            if not session or session.get("runtime_status") == "stopped":
                return
            pending = _dedupe_text_list(
                [*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])]
            )
            message = f"Agent Bridge exited with return code {returncode}."
            self.store.update_session_fields(
                room_id,
                session_id,
                status="error",
                runtime_status="error",
                pid=None,
                bridge_pid=None,
                active_turn_id="",
                turn_phase="",
                inflight_event_ids=[],
                pending_event_ids=pending,
                recovery_required=True,
                last_error=message,
                stderr_tail=clean_lobby_text(stderr_tail, limit=16000),
            )
            participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
            if participant_id and self.store.participant(room_id, participant_id):
                self.store.update_participant_fields(room_id, participant_id, status="detached")
            self.store.append_event(
                room_id,
                "error",
                participant_id=participant_id,
                session_id=session_id,
                content=message,
                error_code="bridge_process_exited",
                stderr_tail=clean_lobby_text(stderr_tail, limit=16000),
                recovery_required=True,
            )
            self._publish_session_state(room_id, self.store.session(room_id, session_id))

    def _execute_action(
        self,
        identity: dict[str, object],
        room_id: str,
        action: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        if action == "message.send":
            self._require_capability(identity, "message.send")
            return self._send_message(identity, room_id, payload)
        if action == "agent.create":
            self._require_capability(identity, "agent.control")
            return self._create_agent(
                room_id,
                payload,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        if action in {"agent.start", "agent.resume", "agent.stop", "agent.interrupt"}:
            self._require_capability(identity, "agent.control")
            agent_id = self._payload_agent_id(payload)
            if action == "agent.start":
                return self._start_agent(room_id, agent_id, server_url=server_url, ticket_issuer=ticket_issuer)
            if action == "agent.resume":
                return self._resume_agent(room_id, agent_id, server_url=server_url, ticket_issuer=ticket_issuer)
            if action == "agent.stop":
                return self._stop_agent(room_id, agent_id)
            return self._interrupt_agent(room_id, agent_id)
        if action == "participant.kick":
            self._require_capability(identity, "participant.kick")
            return self._kick_participant(room_id, self._payload_agent_id(payload))
        if action == "participant.mute":
            self._require_capability(identity, "participant.mute")
            return self._mute_participant(room_id, self._payload_agent_id(payload), bool(payload.get("muted", True)))
        self._require_bridge(identity)
        if action == "bridge.ready":
            return self._bridge_ready(identity, room_id, payload)
        if action == "bridge.health":
            return self._bridge_health(identity, room_id, payload)
        if action == "turn.state":
            return self._turn_state(identity, room_id, payload)
        if action == "message.delta":
            return self._message_delta(identity, room_id, payload)
        if action == "message.final":
            return self._message_final(identity, room_id, payload)
        if action == "turn.failed":
            return self._turn_failed(identity, room_id, payload)
        raise RoomCommandRejected(f"Unsupported room command: {action}", code="unknown_action")

    def _send_message(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        content = clean_lobby_text(payload.get("content") or payload.get("message"), limit=12000)
        kind = clean_lobby_text(payload.get("kind"), limit=64) or "message"
        if kind not in {"vote", "vote_cast"} and not content:
            raise RoomCommandRejected("Message content is required.", code="empty")
        participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        participant = self.store.participant(room_id, participant_id)
        if participant.get("status") in {"kicked", "left"}:
            raise RoomCommandRejected("This participant is no longer in the room.", code="session_revoked")
        if participant.get("muted") or is_room_member_muted(self.output_root, room_id, participant_id):
            raise RoomCommandRejected("You are muted by the room host.", code="muted")
        event = self.store.append_event(
            room_id,
            "message_final",
            participant_id=participant_id,
            participant_type="human",
            actor_id=participant_id,
            actor_type="human",
            display_name=clean_lobby_text(identity.get("display_name"), limit=64) or participant_id,
            content=content,
            message_kind=kind,
            attachments=payload.get("attachments") if isinstance(payload.get("attachments"), list) else [],
            vote_id=payload.get("vote_id"),
            vote_question=payload.get("vote_question"),
            vote_options=payload.get("vote_options"),
            vote_choice=payload.get("vote_choice"),
            target_agent_id=payload.get("target_agent_id"),
            relay_depth=0,
        )
        return {"event": event, "event_seq": event["seq"]}

    def _start_agent(
        self,
        room_id: str,
        agent_id: str,
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        spec = self._provider(room_id, agent_id)
        session = self.store.session(room_id, agent_id)
        participant = self.store.participant(room_id, agent_id)
        if participant.get("status") == "kicked":
            raise RoomCommandRejected("This agent was removed from the room. Add it again before starting it.", code="participant_kicked")
        if not session:
            self._ensure_provider_session(room_id, spec)
            session = self.store.session(room_id, agent_id)
        if session.get("runtime_status") in {"starting", "idle", "busy"}:
            return {"agent_session": self._public_session(session), "runtime_reused": True}
        self.store.update_session_fields(
            room_id,
            agent_id,
            status="available",
            enabled=True,
            runtime_status="starting",
            last_error="",
        )
        if self.bridge_manager is None:
            self.store.update_session_fields(
                room_id,
                agent_id,
                status="unavailable",
                enabled=False,
                runtime_status="error",
                last_error="Agent bridge manager is unavailable.",
            )
            raise RoomCommandRejected("Agent bridge manager is unavailable.", code="runtime_unavailable")
        try:
            launch = self.bridge_manager.start(
                room_id,
                self.store.session(room_id, agent_id),
                spec,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        except Exception as error:
            self.store.update_session_fields(
                room_id,
                agent_id,
                status="unavailable",
                enabled=False,
                runtime_status="error",
                last_error=str(error),
            )
            self.store.append_event(
                room_id,
                "error",
                participant_id=agent_id,
                session_id=agent_id,
                content=str(error),
                error_code="runtime_start_failed",
            )
            raise RoomCommandRejected(str(error), code="runtime_start_failed") from error
        updated = self.store.update_session_fields(
            room_id,
            agent_id,
            bridge_pid=launch.get("bridge_pid"),
            resolved_executable=launch.get("resolved_executable") or "",
        )
        self._publish_session_state(room_id, updated)
        return {"agent_session": self._public_session(updated), "launch": dict(launch), "runtime_reused": False}

    def _create_agent(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        spec = native_cli_provider_spec_from_payload(payload)
        session = self.register_provider(room_id, spec)
        result: dict[str, object] = {
            "status": "created",
            "agent_session": session,
            "participant": self.store.participant(room_id, spec.agent_id),
        }
        if bool(payload.get("start") or payload.get("start_now")):
            result["start"] = self._start_agent(
                room_id,
                spec.agent_id,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        return result

    def _resume_agent(
        self,
        room_id: str,
        agent_id: str,
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if session and session.get("runtime_status") not in {"stopped", "available"}:
            self._stop_agent(room_id, agent_id, disable=False)
        return self._start_agent(room_id, agent_id, server_url=server_url, ticket_issuer=ticket_issuer)

    def _stop_agent(self, room_id: str, agent_id: str, *, disable: bool = True) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        self.broker.direct_to_bridge(room_id, agent_id, {"op": "agent.control", "action": "stop"})
        stopped = {"stopped": True, "alive": False}
        if self.bridge_manager is not None:
            stopped = self.bridge_manager.stop(
                room_id,
                agent_id,
                provider_pid=_safe_int_or_none(session.get("pid")),
            )
        pending = _dedupe_text_list([*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])])
        updated = self.store.update_session_fields(
            room_id,
            agent_id,
            status="detached",
            enabled=False if disable else bool(session.get("enabled")),
            runtime_status="stopped",
            pid=None,
            bridge_pid=None,
            active_turn_id="",
            turn_phase="",
            inflight_event_ids=[],
            pending_event_ids=pending,
            last_error="",
        )
        participant = self.store.participant(room_id, agent_id)
        if participant:
            self.store.update_participant_fields(room_id, agent_id, status="detached")
        self.store.append_event(
            room_id,
            "session_detached",
            participant_id=agent_id,
            session_id=agent_id,
            reason="operator stop",
        )
        self._publish_session_state(room_id, updated)
        return {"agent_session": self._public_session(updated), "process": stopped}

    def _interrupt_agent(self, room_id: str, agent_id: str) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        if not self.broker.direct_to_bridge(room_id, agent_id, {"op": "agent.control", "action": "interrupt"}):
            raise RoomCommandRejected("Agent bridge is not connected.", code="runtime_unavailable")
        return {"agent_session": self._public_session(session), "interrupt_sent": True}

    def _kick_participant(self, room_id: str, participant_id: str) -> dict[str, object]:
        if participant_id == "operator-local":
            raise RoomCommandRejected("The room host cannot be removed.", code="permission_denied")
        participant = self.store.participant(room_id, participant_id)
        if not participant:
            participant, _created = self.store.upsert_participant(
                room_id,
                {
                    "participant_id": participant_id,
                    "display_name": participant_id,
                    "participant_type": "human",
                    "role": "member",
                    "status": "joined",
                },
            )
        if participant.get("role") == "agent":
            self._stop_agent(room_id, participant_id)
            self._providers_by_room.get(room_id, {}).pop(participant_id, None)
        revoked_sessions = revoke_sessions_for_participant(room_id, participant_id)
        removed_member = remove_room_member(self.output_root, room_id, participant_id)
        leave_all_voice(room_id, participant_id)
        updated = self.store.update_participant_fields(room_id, participant_id, status="kicked")
        self.store.append_event(room_id, "participant_kicked", participant_id=participant_id)
        return {
            "participant": updated,
            "revoked_sessions": revoked_sessions,
            "removed_member": bool(removed_member),
        }

    def _mute_participant(self, room_id: str, participant_id: str, muted: bool) -> dict[str, object]:
        participant = self.store.participant(room_id, participant_id)
        if not participant:
            participant, _created = self.store.upsert_participant(
                room_id,
                {
                    "participant_id": participant_id,
                    "display_name": participant_id,
                    "participant_type": "human",
                    "role": "member",
                    "status": "joined",
                },
            )
        member = set_room_member_muted(self.output_root, meeting_id=room_id, participant_id=participant_id, muted=muted)
        updated = self.store.update_participant_fields(room_id, participant_id, muted=muted)
        session = self.store.session(room_id, participant_id)
        if participant.get("role") == "agent" and session:
            if muted and session.get("runtime_status") == "busy":
                self.broker.direct_to_bridge(room_id, participant_id, {"op": "agent.control", "action": "interrupt"})
            elif not muted:
                self._assign_pending(room_id, participant_id)
        self.store.append_event(
            room_id,
            "participant_muted",
            participant_id=participant_id,
            muted=muted,
        )
        return {"participant": updated, "member": member}

    def _bridge_ready(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._bridge_session(identity, room_id)
        previous_participant = self.store.participant(room_id, agent_id)
        self.store.update_participant_fields(room_id, agent_id, status="joined")
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="attached",
            enabled=True,
            runtime_status="idle",
            pid=_safe_int_or_none(payload.get("pid")),
            pty=bool(payload.get("pty", True)),
            transport=clean_lobby_text(payload.get("transport"), limit=64) or "pty",
            is_one_shot=bool(payload.get("is_one_shot", False)),
            started_at=clean_lobby_text(payload.get("started_at"), limit=128) or _now(),
            last_error="",
        )
        if previous_participant.get("status") != "joined":
            self.store.append_event(room_id, "participant_joined", participant_id=agent_id, session_id=session["session_id"])
        self.store.append_event(room_id, "session_attached", participant_id=agent_id, session_id=session["session_id"])
        self._assign_pending(room_id, agent_id)
        current = self.store.session(room_id, str(session["session_id"]))
        self._publish_session_state(room_id, current)
        return {"agent_session": self._public_session(current)}

    def _bridge_health(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        _agent_id, session = self._bridge_session(identity, room_id)
        fields = {
            key: payload[key]
            for key in ("pid", "running", "resolved_executable", "started_at", "last_error", "returncode")
            if key in payload
        }
        updated = self.store.update_session_fields(room_id, str(session["session_id"]), **fields)
        self._publish_session_state(room_id, updated)
        return {"agent_session": self._public_session(updated)}

    def _turn_state(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        phase = clean_lobby_text(payload.get("phase"), limit=32) or "thinking"
        latency = _merged_latency(session.get("latency"), payload.get("latency"))
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            turn_phase=phase,
            latency=latency,
        )
        event = self.store.append_event(
            room_id,
            "turn_state",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            phase=phase,
            latency=latency,
        )
        self._publish_session_state(room_id, updated)
        return {"event": event, "agent_session": self._public_session(updated)}

    def _message_delta(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        content = clean_lobby_text(payload.get("content"), limit=12000)
        if not content:
            raise RoomCommandRejected("Delta content is required.", code="empty")
        if session.get("turn_phase") != "streaming":
            self.store.update_session_fields(room_id, str(session["session_id"]), turn_phase="streaming")
            self.store.append_event(
                room_id,
                "turn_state",
                participant_id=agent_id,
                session_id=session["session_id"],
                turn_id=session["active_turn_id"],
                phase="streaming",
            )
        event = self.store.append_event(
            room_id,
            "message_delta",
            participant_id=agent_id,
            participant_type="agent",
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            content=content,
        )
        return {"event": event, "event_seq": event["seq"]}

    def _message_final(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        content = clean_lobby_text(payload.get("content"), limit=12000)
        if not content:
            raise RoomCommandRejected("Final message content is required.", code="empty")
        active_turn_id = str(session["active_turn_id"])
        input_up_to_event_id = clean_lobby_text(session.get("input_up_to_event_id"), limit=128)
        relay_depth = int(session.get("active_relay_depth") or 0)
        event = self.store.append_event(
            room_id,
            "message_final",
            participant_id=agent_id,
            participant_type="agent",
            actor_id=agent_id,
            actor_type="agent",
            display_name=session.get("display_name") or agent_id,
            session_id=session["session_id"],
            turn_id=active_turn_id,
            content=content,
            source_event_id=session.get("active_source_event_id"),
            relay_depth=relay_depth,
            message_source=payload.get("message_source"),
        )
        latency = _merged_latency(session.get("latency"), payload.get("latency"))
        finished = self.store.append_event(
            room_id,
            "turn_finished",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=active_turn_id,
            status="completed",
            latency=latency,
        )
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="attached",
            runtime_status="idle",
            turn_phase="",
            active_turn_id="",
            active_source_event_id="",
            active_relay_depth=0,
            input_up_to_event_id="",
            inflight_event_ids=[],
            last_provider_sync_event_id=input_up_to_event_id or session.get("last_provider_sync_event_id") or "",
            last_seen_event_id=input_up_to_event_id or session.get("last_seen_event_id") or "",
            last_spoke_event_id=event["id"],
            bootstrap_done=True,
            recovery_required=False,
            turn_count=int(session.get("turn_count") or 0) + 1,
            latency=latency,
            last_error="",
        )
        self._assign_pending(room_id, agent_id)
        current = self.store.session(room_id, str(session["session_id"]))
        self._publish_session_state(room_id, current)
        return {"event": event, "turn_finished": finished, "agent_session": self._public_session(current)}

    def _turn_failed(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        interrupted = clean_lobby_text(payload.get("status"), limit=32) == "interrupted"
        content = clean_lobby_text(payload.get("message") or payload.get("content"), limit=4000) or "Provider turn failed."
        error = self.store.append_event(
            room_id,
            "error",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            content=content,
            error_code="interrupted" if interrupted else "provider_turn_failed",
            diagnostics=payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {},
        )
        self.store.append_event(
            room_id,
            "turn_finished",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            status="interrupted" if interrupted else "error",
        )
        pending = _dedupe_text_list([*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])])
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="attached" if interrupted else "error",
            runtime_status="idle" if interrupted else "error",
            turn_phase="",
            active_turn_id="",
            active_source_event_id="",
            active_relay_depth=0,
            input_up_to_event_id="",
            inflight_event_ids=[],
            pending_event_ids=pending,
            recovery_required=not interrupted,
            last_error=content,
        )
        self._publish_session_state(room_id, updated)
        return {"event": error, "agent_session": self._public_session(updated)}

    def _on_event_appended(self, event: dict[str, object]) -> None:
        self.broker.broadcast_event(event)
        if event.get("type") != "message_final":
            return
        with self._lock:
            self._route_message_event(event)

    def _route_message_event(self, event: dict[str, object]) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
        actor_id = clean_lobby_text(actor.get("participant_id") or event.get("participant_id"), limit=128)
        actor_type = clean_lobby_text(actor.get("participant_type") or event.get("participant_type"), limit=32)
        content = clean_lobby_text(event.get("content"), limit=12000)
        relay_depth = int(event.get("relay_depth") or 0)
        if actor_type == "agent" and relay_depth >= self.max_agent_relay_depth:
            return
        providers = self._room_providers(room_id)
        mentioned = _mentioned_agents(content, providers)
        target_agent_id = clean_lobby_text(event.get("target_agent_id"), limit=128)
        if target_agent_id in providers:
            mentioned.add(target_agent_id)
        if "@all" in content.lower():
            targets = set(providers)
        elif mentioned:
            targets = mentioned
        elif actor_type != "agent":
            targets = {agent_id for agent_id, spec in providers.items() if spec.default_responder}
        else:
            targets = set()
        targets.discard(actor_id)
        for agent_id in sorted(targets):
            participant = self.store.participant(room_id, agent_id)
            if participant.get("status") == "kicked" or participant.get("muted"):
                continue
            self._queue_event(room_id, agent_id, event, relay_depth=relay_depth + (1 if actor_type == "agent" else 0))

    def _queue_event(self, room_id: str, agent_id: str, event: dict[str, object], *, relay_depth: int) -> None:
        session = self.store.session(room_id, agent_id)
        if not session:
            return
        event_id = clean_lobby_text(event.get("id"), limit=128)
        pending = _dedupe_text_list([*list(session.get("pending_event_ids") or []), event_id])
        session = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            pending_event_ids=pending,
            pending_relay_depth=max(int(session.get("pending_relay_depth") or 0), relay_depth),
        )
        if (
            session.get("enabled")
            and session.get("runtime_status") == "idle"
            and self.broker.has_bridge(room_id, agent_id)
        ):
            self._assign_pending(room_id, agent_id)

    def _assign_pending(self, room_id: str, agent_id: str) -> bool:
        session = self.store.session(room_id, agent_id)
        participant = self.store.participant(room_id, agent_id)
        pending = _dedupe_text_list(list(session.get("pending_event_ids") or [])) if session else []
        if (
            not session
            or participant.get("status") == "kicked"
            or bool(participant.get("muted"))
            or not pending
            or not session.get("enabled")
            or session.get("runtime_status") != "idle"
            or not self.broker.has_bridge(room_id, agent_id)
        ):
            return False
        turn_id = f"turn-{uuid4().hex[:12]}"
        packet = build_room_turn_packet(
            self.output_root,
            room_id=room_id,
            participant_id=agent_id,
            session_id=str(session["session_id"]),
            instruction="Reply naturally to the new room messages. Return only the text that should appear in the room.",
        )
        input_up_to_event_id = clean_lobby_text(packet.get("last_provider_sync_event_id_after"), limit=128) or pending[-1]
        source_event = _event_by_id(self.store.read_events(room_id), pending[-1])
        dispatched_at = _now()
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            runtime_status="busy",
            turn_phase="thinking",
            active_turn_id=turn_id,
            active_source_event_id=pending[-1],
            active_relay_depth=int(session.get("pending_relay_depth") or 0),
            input_up_to_event_id=input_up_to_event_id,
            inflight_event_ids=pending,
            pending_event_ids=[],
            pending_relay_depth=0,
            latency={
                "queued_at": source_event.get("created_at") or dispatched_at,
                "dispatch_started_at": dispatched_at,
            },
        )
        self._publish_session_state(room_id, updated)
        self.store.append_event(
            room_id,
            "turn_started",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=turn_id,
            source_event_id=pending[-1],
            provider_visible_chars=packet.get("provider_visible_chars"),
            provider_visible_event_count=packet.get("provider_visible_event_count"),
        )
        self.store.append_event(
            room_id,
            "turn_state",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=turn_id,
            phase="thinking",
        )
        assignment = {
            "op": "turn.assign",
            "room_id": room_id,
            "participant_id": agent_id,
            "session_id": session["session_id"],
            "turn_id": turn_id,
            "source_event_id": pending[-1],
            "input_up_to_event_id": input_up_to_event_id,
            "provider_input": packet.get("provider_input") or "",
            "provider_visible_chars": packet.get("provider_visible_chars") or 0,
            "timeout_seconds": self._provider(room_id, agent_id).turn_timeout_seconds,
        }
        if self.broker.direct_to_bridge(room_id, agent_id, assignment):
            return True
        self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="unavailable",
            runtime_status="disconnected",
            active_turn_id="",
            turn_phase="",
            inflight_event_ids=[],
            pending_event_ids=pending,
            last_error="Agent bridge disconnected before turn assignment.",
        )
        return False

    def _publish_session_state(self, room_id: str, session: dict[str, object]) -> dict[str, object]:
        if not session:
            return {}
        return self.store.append_event(
            room_id,
            "agent_session_state",
            participant_id=session.get("participant_id"),
            session_id=session.get("session_id"),
            runtime_status=session.get("runtime_status"),
            agent_session=self._public_session(session),
        )

    def _ensure_provider_session(self, room_id: str, spec: NativeCliProviderSpec) -> None:
        agent_id = clean_lobby_text(spec.agent_id, limit=128)
        participant = self.store.participant(room_id, agent_id)
        if not participant:
            self.store.upsert_participant(
                room_id,
                {
                    "participant_id": agent_id,
                    "display_name": spec.display_name,
                    "role": "agent",
                    "participant_type": "agent",
                    "owner_id": "operator-local",
                    "created_by": "operator-local",
                    "provider_kind": spec.normalized_provider_kind(),
                    "connection_kind": "native_cli_bridge",
                    "status": "detached",
                },
            )
        session = self.store.session(room_id, agent_id)
        if session:
            self.store.update_participant_fields(
                room_id,
                agent_id,
                display_name=spec.display_name,
                provider_kind=spec.normalized_provider_kind(),
                connection_kind="native_cli_bridge",
            )
            self.store.update_session_fields(
                room_id,
                agent_id,
                display_name=spec.display_name,
                provider_kind=spec.normalized_provider_kind(),
                runtime_kind="live_cli",
                connection_kind="native_cli_bridge",
                command_configured=list(spec.command),
                workspace=str(Path(spec.cwd).expanduser().resolve()),
                model=spec.model,
                runtime_profile_key=spec.runtime_profile_key(),
                pty=True,
                transport="pty",
                is_one_shot=False,
            )
            return
        latest_public = _latest_public_event_id(self.store.read_events(room_id))
        self.store.upsert_session(
            room_id,
            {
                "session_id": agent_id,
                "participant_id": agent_id,
                "display_name": spec.display_name,
                "status": "available",
                "provider_kind": spec.normalized_provider_kind(),
                "runtime_kind": "live_cli",
                "connection_kind": "native_cli_bridge",
                "command_configured": list(spec.command),
                "workspace": str(Path(spec.cwd).expanduser().resolve()),
                "model": spec.model,
                "runtime_profile_key": spec.runtime_profile_key(),
                "enabled": False,
                "runtime_status": "stopped",
                "pending_event_ids": [],
                "inflight_event_ids": [],
                "turn_count": 0,
                "last_provider_sync_event_id": latest_public,
                "last_seen_event_id": latest_public,
                "pty": True,
                "transport": "pty",
                "is_one_shot": False,
            },
        )

    def _room_providers(self, room_id: str) -> dict[str, NativeCliProviderSpec]:
        with self._lock:
            return dict(self._providers_by_room.get(clean_lobby_text(room_id, limit=128), {}))

    def _provider(self, room_id: str, agent_id: str) -> NativeCliProviderSpec:
        spec = self._room_providers(room_id).get(clean_lobby_text(agent_id, limit=128))
        if spec is None:
            raise RoomCommandRejected(f"Unknown configured agent: {agent_id}", code="not_found")
        return spec

    def _payload_agent_id(self, payload: dict[str, object]) -> str:
        agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("participant_id") or payload.get("session_id"), limit=128)
        if not agent_id:
            raise RoomCommandRejected("agent_id is required.", code="bad_request")
        return agent_id

    def _bridge_session(self, identity: dict[str, object], room_id: str) -> tuple[str, dict[str, object]]:
        agent_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        session_id = clean_lobby_text(identity.get("session_id") or agent_id, limit=128)
        session = self.store.session(room_id, session_id)
        if not session or session.get("participant_id") != agent_id:
            raise RoomCommandRejected("Agent bridge session does not match its ticket identity.", code="permission_denied")
        return agent_id, session

    def _active_bridge_turn(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        agent_id, session = self._bridge_session(identity, room_id)
        turn_id = clean_lobby_text(payload.get("turn_id"), limit=128)
        if not turn_id or turn_id != session.get("active_turn_id"):
            raise RoomCommandRejected("Turn does not match the active assignment.", code="turn_conflict")
        return agent_id, session

    def _require_capability(self, identity: dict[str, object], capability: str) -> None:
        if not self.capabilities(identity).get(capability):
            raise RoomCommandRejected(f"{capability} permission is required.", code="permission_denied")

    @staticmethod
    def _require_bridge(identity: dict[str, object]) -> None:
        if identity.get("client_type") != "agent_bridge":
            raise RoomCommandRejected("This command is reserved for an Agent Bridge.", code="permission_denied")

    @staticmethod
    def _public_session(session: dict[str, object]) -> dict[str, object]:
        hidden = {"env", "token", "ticket", "credentials"}
        return {key: value for key, value in session.items() if key not in hidden}


def _mentioned_agents(content: str, providers: dict[str, NativeCliProviderSpec]) -> set[str]:
    lowered = content.lower()
    mentioned = set()
    for agent_id in providers:
        if re.search(rf"(?<![\w-])@{re.escape(agent_id.lower())}(?![\w-])", lowered):
            mentioned.add(agent_id)
    return mentioned


def _slug_agent_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").casefold()).strip("-")
    return slug[:96] or "agent-session"


def _event_by_id(events: list[dict[str, object]], event_id: str) -> dict[str, object]:
    for event in reversed(events):
        if event.get("id") == event_id:
            return event
    return {}


def _latest_public_event_id(events: list[dict[str, object]]) -> str:
    for event in reversed(events):
        if event.get("type") == "message_final":
            return clean_lobby_text(event.get("id"), limit=128)
    return ""


def _dedupe_text_list(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = clean_lobby_text(value, limit=128)
        if text and text not in result:
            result.append(text)
    return result


def _merged_latency(existing: object, incoming: object) -> dict[str, object]:
    base = dict(existing) if isinstance(existing, dict) else {}
    if isinstance(incoming, dict):
        base.update({key: value for key, value in incoming.items() if value not in (None, "")})
    return base


def _safe_int_or_none(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _safe_bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    parsed = max(int(minimum), parsed)
    if maximum is not None:
        parsed = min(int(maximum), parsed)
    return parsed


def _now() -> str:
    return datetime.now(UTC).isoformat()
