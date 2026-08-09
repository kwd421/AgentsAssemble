from __future__ import annotations

import socket
import threading
from collections import deque
from uuid import uuid4

from agentsassemble.admission.capacity import uses_reserved_room_capacity

from agentsassemble.room.projection import public_event_for_identity
from agentsassemble.room.text import clean_room_text as clean_lobby_text

ROOM_EVENT_STREAM = "room_events"
ROOM_MAX_SOCKET_CONNECTIONS = 512
ROOM_MAX_PUBLIC_SOCKET_CONNECTIONS = 384
ROOM_MAX_SOCKET_CONNECTIONS_PER_SESSION = 8


class RoomConnectionLimitError(RuntimeError):
    """The bounded room socket pool cannot accept another connection."""


class RoomSocketChannel:
    """Bounded outbound queue for one room WebSocket connection."""

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
        self.dropped_delta_count = 0
        self.resync_count = 0

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
            if len(self._queue) >= self.max_messages and not self._make_room(message):
                return False
            self._queue.append(dict(message))
            if was_empty:
                self._wake()
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

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "connection_id": self.connection_id,
                "room_id": self.room_id,
                "queued_messages": len(self._queue),
                "max_messages": self.max_messages,
                "dropped_delta_count": self.dropped_delta_count,
                "resync_count": self.resync_count,
                "closed": self.closed,
            }

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

    def _make_room(self, incoming: dict[str, object]) -> bool:
        for index, queued in enumerate(self._queue):
            if _contains_message_delta(queued):
                del self._queue[index]
                self.dropped_delta_count += 1
                return True
        if _contains_message_delta(incoming):
            self.dropped_delta_count += 1
            return False
        self._queue.clear()
        self._queue.append(
            {
                "op": "resync_required",
                "stream": ROOM_EVENT_STREAM,
                "reason": "outbound_backpressure",
            }
        )
        self.resync_count += 1
        return True

    def _wake(self) -> None:
        try:
            self._write_socket.send(b"\x01")
        except (BlockingIOError, OSError):
            pass


class RoomEventBroker:
    """Non-blocking fanout for canonical room events and targeted bridge turns."""

    def __init__(
        self,
        *,
        max_connections: int = ROOM_MAX_SOCKET_CONNECTIONS,
        max_public_connections: int | None = None,
        max_connections_per_session: int = ROOM_MAX_SOCKET_CONNECTIONS_PER_SESSION,
    ) -> None:
        self._lock = threading.RLock()
        self._channels: dict[str, RoomSocketChannel] = {}
        self._active_bridges: dict[tuple[str, str], tuple[str, int]] = {}
        self._max_connections = max(1, int(max_connections))
        self._max_public_connections = max(
            1,
            int(
                max_public_connections
                if max_public_connections is not None
                else min(ROOM_MAX_PUBLIC_SOCKET_CONNECTIONS, self._max_connections)
            ),
        )
        self._max_connections_per_session = max(1, int(max_connections_per_session))

    def connect(self, identity: dict[str, object]) -> RoomSocketChannel:
        with self._lock:
            if len(self._channels) >= self._max_connections:
                raise RoomConnectionLimitError("room WebSocket capacity reached")
            if not uses_reserved_room_capacity(identity):
                public_connections = sum(
                    not uses_reserved_room_capacity(existing.identity)
                    for existing in self._channels.values()
                )
                if public_connections >= self._max_public_connections:
                    raise RoomConnectionLimitError(
                        "public room WebSocket capacity reached"
                    )
            subject = _connection_subject(identity)
            subject_connections = sum(
                1
                for existing in self._channels.values()
                if _connection_subject(existing.identity) == subject
            )
            if subject_connections >= self._max_connections_per_session:
                raise RoomConnectionLimitError("room WebSocket session capacity reached")
            channel = RoomSocketChannel(identity)
            channel.identity["connection_id"] = channel.connection_id
            identity["connection_id"] = channel.connection_id
            self._channels[channel.connection_id] = channel
        return channel

    def disconnect(self, channel: RoomSocketChannel) -> bool:
        was_active = False
        with self._lock:
            self._channels.pop(channel.connection_id, None)
            key = self._bridge_key(channel)
            if key and self._active_bridges.get(key, ("", 0))[0] == channel.connection_id:
                self._active_bridges.pop(key, None)
                was_active = True
        channel.close()
        return was_active

    def channel(self, connection_id: str) -> RoomSocketChannel | None:
        with self._lock:
            return self._channels.get(connection_id)

    def activate_bridge(self, channel: RoomSocketChannel) -> int:
        key = self._bridge_key(channel)
        if key is None:
            raise ValueError("Only Agent Bridge channels can acquire a bridge lease.")
        previous: RoomSocketChannel | None = None
        with self._lock:
            previous_id, previous_generation = self._active_bridges.get(key, ("", 0))
            generation = previous_generation + 1
            self._active_bridges[key] = (channel.connection_id, generation)
            channel.identity["bridge_generation"] = generation
            # WsRoomSession owns the original identity mapping used for command
            # dispatch; keep the lease generation on both views.
            if previous_id and previous_id != channel.connection_id:
                previous = self._channels.get(previous_id)
        if previous is not None:
            previous.send({"op": "error", "code": "bridge_superseded", "recoverable": False})
            previous.close()
        return generation

    def broadcast_event(self, event: dict[str, object]) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        with self._lock:
            channels = list(self._channels.values())
        for channel in channels:
            if channel.room_id == room_id and channel.subscribed(ROOM_EVENT_STREAM):
                channel.send(
                    {
                        "op": "event",
                        "stream": ROOM_EVENT_STREAM,
                        "events": [public_event_for_identity(event, channel.identity)],
                    }
                )

    def direct_to_bridge(self, room_id: str, participant_id: str, message: dict[str, object]) -> bool:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        with self._lock:
            active = self._active_bridges.get((clean_room_id, clean_participant_id))
            channel = self._channels.get(active[0]) if active else None
        return bool(channel and not channel.closed and channel.send(message))

    def has_bridge(self, room_id: str, participant_id: str) -> bool:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        with self._lock:
            active = self._active_bridges.get((clean_room_id, clean_participant_id))
            channel = self._channels.get(active[0]) if active else None
            return bool(channel and not channel.closed)

    def diagnostics(self) -> list[dict[str, object]]:
        with self._lock:
            return [channel.diagnostics() for channel in self._channels.values()]

    def broadcast_control(
        self,
        room_id: str,
        message: dict[str, object],
        *,
        client_type: str = "",
    ) -> None:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        with self._lock:
            channels = [
                channel
                for channel in self._channels.values()
                if channel.room_id == clean_room_id
                and (
                    not client_type
                    or clean_lobby_text(channel.identity.get("client_type"), limit=64) == client_type
                )
            ]
        for channel in channels:
            channel.send(message)

    def disconnect_participant(self, room_id: str, participant_id: str) -> None:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        with self._lock:
            channels = [
                channel
                for channel in self._channels.values()
                if channel.room_id == clean_room_id
                and clean_lobby_text(channel.identity.get("agent_id"), limit=128) == clean_participant_id
            ]
        for channel in channels:
            self.disconnect(channel)

    def disconnect_room(self, room_id: str) -> None:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        with self._lock:
            channels = [channel for channel in self._channels.values() if channel.room_id == clean_room_id]
        for channel in channels:
            self.disconnect(channel)

    def close(self) -> None:
        with self._lock:
            channels = list(self._channels.values())
            self._channels.clear()
            self._active_bridges.clear()
        for channel in channels:
            channel.close()

    @staticmethod
    def _bridge_key(channel: RoomSocketChannel) -> tuple[str, str] | None:
        if channel.identity.get("client_type") != "agent_bridge":
            return None
        participant_id = clean_lobby_text(channel.identity.get("agent_id"), limit=128)
        return (channel.room_id, participant_id) if channel.room_id and participant_id else None


def _contains_message_delta(message: dict[str, object]) -> bool:
    events = message.get("events") if isinstance(message.get("events"), list) else []
    return any(isinstance(event, dict) and event.get("type") == "message_delta" for event in events)


__all__ = [
    "ROOM_EVENT_STREAM",
    "RoomEventBroker",
    "RoomConnectionLimitError",
    "RoomSocketChannel",
]


def _connection_subject(identity: dict[str, object]) -> tuple[str, str]:
    room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
    session_id = clean_lobby_text(
        identity.get("session_id")
        or identity.get("principal_user_id")
        or identity.get("agent_id"),
        limit=128,
    )
    return room_id, session_id
