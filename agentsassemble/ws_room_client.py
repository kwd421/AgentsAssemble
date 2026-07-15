"""Python WebSocket room client (WS-5 resident-migration groundwork).

Additive: a resident (McpRoomClient / LiveAgentRunner) can use this to connect to
/ws — handshake, subscribe, receive pushed events, send say — instead of the HTTP
poll loop. NOT wired into the working agent path yet; that swap needs a live
agent to verify. This module + its tests give the verified building block.

The protocol core (WsRoomClient) takes any socket-like object (sendall/recv/
settimeout/close), so it unit-tests with a fake socket. `connect_room_ws` is the
real-usage convenience: it fetches a ws-ticket over HTTP, opens a TCP socket, and
returns an opened+subscribed client.
"""
from __future__ import annotations

from collections import deque
import json
import socket as socket_module
import ssl
import threading
import urllib.request
from urllib.parse import urlparse
from uuid import uuid4

from agentsassemble.room_websocket import (
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    MessageAssembler,
    WebSocketProtocolError,
    client_handshake_request,
    encode_client_frame,
    encode_client_text,
    handshake_accept_ok,
)


class WsRoomSayRejected(Exception):
    """Raised when the room rejects a `say` operation over WS."""

    def __init__(self, message: str, *, category: str = "rejected") -> None:
        super().__init__(message)
        self.category = category


def _parse_response_headers(blob: bytes) -> tuple[bytes, dict[str, str]]:
    lines = blob.split(b"\r\n")
    status = lines[0] if lines else b""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" in line:
            key, _, value = line.partition(b":")
            headers[key.decode("latin-1").strip()] = value.decode("latin-1").strip()
    return status, headers


class WsRoomClient:
    """Per-connection WS client over an injectable socket-like object."""

    def __init__(self, sock, *, host: str = "localhost") -> None:
        self.sock = sock
        self.host = host
        self.closed = False
        self._assembler = MessageAssembler(expect_mask=False)
        self._pending_messages: deque[dict] = deque()
        self._send_lock = threading.Lock()

    def open(self, path: str) -> None:
        """Send the upgrade request, verify the 101 + accept key, and buffer any
        frame bytes that arrived with the response."""
        request, key = client_handshake_request(path, self.host)
        self.sock.sendall(request)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketProtocolError("Connection closed during handshake.")
            head += chunk
        header_blob, _, rest = head.partition(b"\r\n\r\n")
        status, headers = _parse_response_headers(header_blob)
        if b"101" not in status:
            raise WebSocketProtocolError(f"WebSocket upgrade rejected: {status.decode('latin-1', 'replace')}")
        if not handshake_accept_ok(headers, key):
            raise WebSocketProtocolError("Sec-WebSocket-Accept mismatch.")
        if rest:
            self._assembler.feed(rest)

    def subscribe(self, streams: list[str], *, resume_from_id: str = "") -> None:
        message: dict[str, object] = {"op": "subscribe", "streams": list(streams)}
        if resume_from_id:
            message["resume_from_id"] = resume_from_id
        self._send(message)

    def say(
        self,
        message: str,
        *,
        wait_for_ack: bool = False,
        ack_rounds: int = 5,
        **extra: object,
    ) -> dict | None:
        self._send({"op": "say", "message": message, **extra})
        if wait_for_ack:
            return self._wait_for_say_ack(ack_rounds=ack_rounds)
        return None

    def thinking(self, on: bool) -> None:
        """Signal that the agent started/finished generating (lights up the
        room's typing indicator). Best-effort — a failed send doesn't matter."""
        try:
            self._send({"op": "thinking", "on": bool(on)})
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.closed = True

    def command(
        self,
        action: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str = "",
    ) -> str:
        correlated_id = str(request_id or f"req-{uuid4().hex[:16]}")
        self._send(
            {
                "op": "command",
                "request_id": correlated_id,
                "action": str(action or ""),
                "payload": dict(payload or {}),
            }
        )
        return correlated_id

    def set_receive_timeout(self, seconds: float) -> None:
        """Bound a blocking receive so callers can service local deadlines.

        This does not send a network request or poll the server; it only changes
        how long the existing WebSocket read may block while no frame arrives.
        """
        self.sock.settimeout(max(0.05, float(seconds)))

    def _send(self, obj: dict) -> None:
        with self._send_lock:
            self.sock.sendall(encode_client_text(json.dumps(obj)))

    def receive(self) -> list[dict]:
        """One recv; return parsed server messages (dicts). Auto-responds to ping;
        marks closed on close/EOF. The resident drives this in a loop."""
        if self._pending_messages:
            out = list(self._pending_messages)
            self._pending_messages.clear()
            return out
        return self._receive_from_socket()

    def _receive_from_socket(self) -> list[dict]:
        out: list[dict] = []
        try:
            data = self.sock.recv(65536)
        except (TimeoutError, socket_module.timeout):
            return out  # idle this round — NOT a disconnect
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.closed = True
            return out
        if not data:
            self.closed = True
            return out
        self._assembler.feed(data)
        for opcode, payload in self._assembler.messages():
            if opcode == OP_PING:
                self._safe_send(encode_client_frame(payload, opcode=OP_PONG))
            elif opcode == OP_PONG:
                continue
            elif opcode == OP_CLOSE:
                self.closed = True
            elif opcode == OP_TEXT:
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as error:
                    self._close_protocol_connection()
                    raise WebSocketProtocolError("Server sent invalid JSON.") from error
                if not isinstance(message, dict):
                    self._close_protocol_connection()
                    raise WebSocketProtocolError("Server message must be a JSON object.")
                out.append(message)
        return out

    def _close_protocol_connection(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
        self.closed = True

    def _wait_for_say_ack(self, *, ack_rounds: int) -> dict:
        for _ in range(max(1, int(ack_rounds))):
            messages = self._receive_from_socket()
            for message in messages:
                op = str(message.get("op") or "")
                if op == "ack":
                    return message
                if op == "error":
                    category = str(message.get("category") or "rejected")
                    detail = str(message.get("message") or category)
                    raise WsRoomSayRejected(detail, category=category)
                self._pending_messages.append(message)
            if self.closed:
                break
        raise TimeoutError("No acknowledgement received for WS say.")

    def _safe_send(self, frame: bytes) -> None:
        try:
            with self._send_lock:
                self.sock.sendall(frame)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.closed = True

    def close(self) -> None:
        self._safe_send(encode_client_frame(b"", opcode=OP_CLOSE))
        try:
            self.sock.close()
        except OSError:
            pass
        self.closed = True


def request_ws_ticket(server_url: str, session_token: str, *, timeout: float = 5.0) -> str:
    """POST /api/ws-ticket (Bearer) → ticket. Uses urllib (resident already does)."""
    request = urllib.request.Request(
        f"{server_url.rstrip('/')}/api/ws-ticket",
        data=b"{}",
        headers={"Authorization": f"Bearer {session_token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return str(json.loads(response.read().decode("utf-8"))["ticket"])


def join_room_session(
    server_url: str,
    invite_token: str,
    *,
    display_name: str = "",
    participant_type: str = "agent",
    device_token: str = "",
    request_id: str = "",
    timeout: float = 5.0,
) -> dict[str, object]:
    """POST /api/room-invite/join and return the server-admitted session.

    The session token opens the WebSocket; the rest of the payload is just as
    important because the server may choose the real participant id/display name
    for reusable invites and stable device identities.
    """
    body = {
        "invite_token": invite_token,
        "display_name": display_name,
        "participant_type": participant_type,
        "device_token": device_token,
        "request_id": request_id or str(uuid4()),
    }
    request = urllib.request.Request(
        f"{server_url.rstrip('/')}/api/room-invite/join",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = str(payload.get("session_token") or "")
    if not token:
        raise WebSocketProtocolError("Join did not return a session token.")
    return payload


def meeting_id_from_invite_token(invite_token: str) -> str:
    """Best-effort: read meeting_id out of the invite token's payload (the
    middle base64 segment of `aai1.<payload>.<sig>`), so a resident launched with
    only --invite-token still knows its room. Returns "" if it can't be read."""
    import base64

    token = str(invite_token or "")
    parts = token.split(".")
    if len(parts) < 2:
        return ""
    segment = parts[1]
    try:
        padded = segment + "=" * (-len(segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return ""
    return str(payload.get("meeting_id") or "") if isinstance(payload, dict) else ""


def fetch_room_conversation_mode(server_url: str, meeting_id: str, *, timeout: float = 5.0) -> str:
    """GET /api/room-settings → the room's conversation_mode ("turn"/"free").
    Best-effort: returns "turn" on any error so a resident never fails to launch."""
    try:
        request = urllib.request.Request(
            f"{server_url.rstrip('/')}/api/room-settings?room_id={urllib.parse.quote(str(meeting_id or ''))}",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return "turn"
    settings = payload.get("settings") if isinstance(payload, dict) else None
    if isinstance(settings, dict):
        return str(settings.get("conversation_mode") or "turn")
    return "turn"


def connect_room_ws(
    server_url: str,
    session_token: str,
    streams: list[str],
    *,
    timeout: float = 5.0,
) -> WsRoomClient:
    """Real-usage convenience: ws-ticket → TCP connect → handshake → subscribe."""
    ticket = request_ws_ticket(server_url, session_token, timeout=timeout)
    return connect_room_ws_with_ticket(server_url, ticket, streams, timeout=timeout)


def connect_room_ws_with_ticket(
    server_url: str,
    ticket: str,
    streams: list[str],
    *,
    timeout: float = 5.0,
) -> WsRoomClient:
    """Open the canonical room socket with a pre-issued single-use ticket.

    Server-owned Agent Bridges receive an internal ticket directly, while
    browsers and remote residents first exchange a session token for one.
    Both continue through the exact same WebSocket endpoint and protocol.
    """
    parsed = urlparse(server_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    sock = socket_module.create_connection((host, port), timeout=timeout)
    if parsed.scheme == "https":
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    client = WsRoomClient(sock, host=f"{host}:{port}")
    prefix = parsed.path.rstrip("/")
    ws_path = f"{prefix}/ws" if prefix else "/ws"
    client.open(f"{ws_path}?ticket={ticket}")
    client.subscribe(streams)
    return client
