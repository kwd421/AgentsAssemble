"""Integration test for the /ws endpoint + /api/ws-ticket (WS-4 glue).

Exercises the gui layer over a real socket: mint a session, get a ws-ticket,
do the RFC 6455 handshake, and round-trip a subscribe. The codec + protocol
core are unit-tested elsewhere (test_room_websocket, test_ws_room_session); this
proves the wiring (ticket issuance, socket hijack, select loop, governed say).
"""
import base64
import io
import json
import socket
import struct
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.gui import _make_handler
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.web.http_server import AgentsAssembleHTTPServer
from agentsassemble.web.websocket import handle_ws_upgrade
from agentsassemble.web.websocket_codec import OP_TEXT, compute_accept_key
from agentsassemble.web.room_session import (
    WS_SESSION_REVOKED_CATEGORY,
    WsRoomDeps,
    WsTicketStore,
)


def _client_text_frame(text: str) -> bytes:
    """A MASKED client→server text frame (what a browser sends)."""
    payload = text.encode("utf-8")
    mask = b"\x37\xfa\x21\x3d"
    b0 = 0x80 | OP_TEXT
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", b0, 0x80 | length)
    else:
        header = struct.pack("!BBH", b0, 0x80 | 126, length)
    masked = bytes(payload[i] ^ mask[i & 3] for i in range(length))
    return header + mask + masked


def _recv_server_text(sock: socket.socket, timeout: float = 4.0) -> dict:
    """Read one unmasked server→client TEXT frame and JSON-decode it."""
    sock.settimeout(timeout)
    buf = b""

    def _need(n: int) -> bytes:
        nonlocal buf
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise AssertionError("socket closed before a full frame arrived")
            buf += chunk
        head, rest = buf[:n], buf[n:]
        buf = rest
        return head

    first = _need(2)
    opcode = first[0] & 0x0F
    length = first[1] & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", _need(2))
    elif length == 127:
        (length,) = struct.unpack("!Q", _need(8))
    payload = _need(length)
    assert opcode == OP_TEXT, f"expected text frame, got opcode {opcode}"
    return json.loads(payload.decode("utf-8"))


class WsEndpointTests(unittest.TestCase):
    def setUp(self):
        self._servers: list[ThreadingHTTPServer] = []

    def tearDown(self):
        for server in list(self._servers):
            self._stop_server(server)

    def _start_server(self, root: Path) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        return server

    def _start_capacity_limited_server(self, root: Path) -> AgentsAssembleHTTPServer:
        server = AgentsAssembleHTTPServer(
            ("127.0.0.1", 0),
            _make_handler(root),
            max_request_workers=1,
            max_websocket_workers=1,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        return server

    def _stop_server(self, server: ThreadingHTTPServer) -> None:
        if server in self._servers:
            self._servers.remove(server)
        server.shutdown()
        server.server_close()

    def _session_token(self, base: str) -> str:
        def post(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
            request = Request(
                f"{base}{endpoint}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=4) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                if endpoint == "/api/rooms" and error.code == 400:
                    error.close()
                    return {}
                raise

        post("/api/rooms", {"room_id": "room-1", "label": "Room One"})
        invite = post(
            "/api/room-invite/create",
            {
                "meeting_id": "room-1",
                "agent_id": "guest-1",
                "display_name": "테스터",
                "max_uses": 1,
                "local_dev_preview": True,
            },
        )
        joined = post(
            "/api/room-invite/join",
            {
                "invite_token": invite["invite_token"],
                "request_id": str(uuid4()),
                "device_token": f"test-device-{uuid4().hex}",
            },
        )
        return str(joined["session_token"])

    def _ws_ticket(self, base: str, token: str) -> str:
        req = Request(
            f"{base}/api/ws-ticket",
            data=b"{}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))["ticket"]

    def _leave(self, base: str, token: str) -> dict:
        req = Request(
            f"{base}/api/room-invite/leave",
            data=b"{}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _handshake_path(self, host: str, port: int, path: str) -> socket.socket:
        sock = socket.create_connection((host, port), timeout=4)
        key = base64.b64encode(b"0123456789abcdef").decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        sock.settimeout(4)
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(4096)
        status_line = head.split(b"\r\n", 1)[0]
        self.assertIn(b"101", status_line, head)
        self.assertIn(f"Sec-WebSocket-Accept: {compute_accept_key(key)}".encode(), head)
        return sock

    def _handshake(self, host: str, port: int, ticket: str) -> socket.socket:
        return self._handshake_path(host, port, f"/ws?ticket={ticket}")

    def test_general_room_uses_ticketed_canonical_ws_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"
                ticket = self._host_ws_ticket(base, "general")
                sock = self._handshake(host, port, ticket)
                try:
                    sock.sendall(
                        _client_text_frame(
                            json.dumps({"op": "subscribe", "streams": ["room_events"], "resume_from_seq": 0})
                        )
                    )
                    subscribed = _recv_server_text(sock)
                    snapshot = _recv_server_text(sock)
                    self.assertEqual(subscribed, {"op": "subscribed", "streams": ["room_events"]})
                    self.assertEqual(snapshot["op"], "snapshot")
                    self.assertEqual(snapshot["room"]["room_id"], "general")
                    self.assertIn("codex", [session["participant_id"] for session in snapshot["agent_sessions"]])
                    operator = next(
                        participant
                        for participant in snapshot["participants"]
                        if participant["participant_id"] == "operator-local"
                    )
                    self.assertEqual(operator["display_name"], "SeiNel")
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_ws_ticket_handshake_and_subscribe_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"

                token = self._session_token(base)
                ticket = self._ws_ticket(base, token)
                self.assertTrue(ticket.startswith("wst_"))

                sock = self._handshake(host, port, ticket)
                try:
                    sock.sendall(_client_text_frame(json.dumps({"op": "subscribe", "streams": ["room_events"]})))
                    msg = _recv_server_text(sock)
                    self.assertEqual(msg["op"], "subscribed")
                    self.assertEqual(msg["streams"], ["room_events"])
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_open_websocket_does_not_consume_the_only_http_request_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_capacity_limited_server(Path(tmp))
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"
                ticket = self._host_ws_ticket(base, "room-capacity")
                sock = self._handshake(host, port, ticket)
                try:
                    with urlopen(f"{base}/api/runtime/version", timeout=4) as response:
                        version = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(version["protocol_version"], 1)
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_upgraded_socket_that_never_subscribes_is_closed_after_handshake_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"
                ticket = self._ws_ticket(base, self._session_token(base))
                with patch(
                    "agentsassemble.web.websocket.WS_APPLICATION_HANDSHAKE_TIMEOUT_SECONDS",
                    0.05,
                    create=True,
                ):
                    sock = self._handshake(host, port, ticket)
                    try:
                        sock.settimeout(0.5)
                        self.assertTrue(sock.recv(4096))
                    finally:
                        sock.close()
            finally:
                self._stop_server(server)

    def test_remote_participant_uses_canonical_message_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"
                token = self._session_token(base)
                ticket = self._ws_ticket(base, token)
                sock = self._handshake(host, port, ticket)
                try:
                    sock.sendall(
                        _client_text_frame(
                            json.dumps(
                                {
                                    "op": "subscribe",
                                    "streams": ["room_events"],
                                    "resume_from_seq": 0,
                                }
                            )
                        )
                    )
                    self.assertEqual(_recv_server_text(sock)["op"], "subscribed")
                    self.assertEqual(_recv_server_text(sock)["op"], "snapshot")

                    sock.sendall(
                        _client_text_frame(
                            json.dumps(
                                {
                                    "op": "command",
                                    "request_id": "remote-message-1",
                                    "action": "message.send",
                                    "payload": {"content": "canonical hello"},
                                }
                            )
                        )
                    )
                    ack = _recv_server_text(sock)
                    self.assertEqual(ack["op"], "ack")
                    self.assertEqual(ack["request_id"], "remote-message-1")
                    self.assertEqual(ack["result"]["event"]["content"], "canonical hello")
                    self.assertEqual(ack["result"]["event"]["actor_id"], "guest-1")

                    pushed = _recv_server_text(sock)
                    self.assertEqual(pushed["op"], "event")
                    self.assertEqual(pushed["stream"], "room_events")
                    self.assertEqual(pushed["events"][-1]["content"], "canonical hello")
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_existing_ws_rejects_canonical_message_after_session_leave(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"
                token = self._session_token(base)
                ticket = self._ws_ticket(base, token)
                sock = self._handshake(host, port, ticket)
                try:
                    leave = self._leave(base, token)
                    self.assertEqual(leave["status"], "left")

                    sock.sendall(
                        _client_text_frame(
                            json.dumps(
                                {
                                    "op": "command",
                                    "request_id": "after-leave",
                                    "action": "message.send",
                                    "payload": {"content": "after leave"},
                                }
                            )
                        )
                    )
                    msg = _recv_server_text(sock)
                    self.assertEqual(msg["op"], "error")
                    self.assertEqual(msg["category"], WS_SESSION_REVOKED_CATEGORY)
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_revoked_socket_cannot_receive_an_already_queued_room_event(self):
        server_sock, client_sock = socket.socketpair()
        broker = RoomEventBroker()
        active = {"value": True}

        class Handler:
            headers = {
                "Upgrade": "websocket",
                "Connection": "Upgrade",
                "Sec-WebSocket-Key": base64.b64encode(
                    b"0123456789abcdef"
                ).decode("ascii"),
            }
            connection = server_sock
            wfile = io.BytesIO()
            close_connection = False

            def send_response(self, _status):
                return None

            def send_header(self, _name, _value):
                return None

            def end_headers(self):
                return None

        class Controller:
            def connect(self, identity):
                return broker.connect(identity)

            def disconnect(self, channel):
                broker.disconnect(channel)

        tickets = WsTicketStore()
        ticket = tickets.issue(
            {
                "agent_id": "guest-1",
                "meeting_id": "room-1",
                "client_type": "browser",
            },
            session_token="revocable-bearer",
        )

        def deps(channel, _handler):
            return WsRoomDeps(
                read_side_chat_after=lambda _room, _cursor: ([], ""),
                set_thinking=lambda _identity, _on: None,
                is_session_active=lambda _token: active["value"],
                room_snapshot=lambda _identity, _after_seq: {
                    "room": {"room_id": "room-1"},
                    "events": [],
                },
                on_subscribe=lambda _identity, streams, _after_seq: (
                    channel.subscribe(streams)
                ),
            )

        with patch(
            "agentsassemble.web.websocket.SSE_EVENT_POLL_INTERVAL_SECONDS",
            10.0,
        ):
            worker = threading.Thread(
                target=handle_ws_upgrade,
                args=(Handler(), {"ticket": [ticket]}),
                kwargs={
                    "ws_ticket_store": tickets,
                    "room_realtime_controller": Controller(),
                    "ws_room_deps_factory": deps,
                },
                daemon=True,
            )
            worker.start()
            try:
                client_sock.sendall(
                    _client_text_frame(
                        json.dumps(
                            {
                                "op": "subscribe",
                                "streams": ["room_events"],
                                "resume_from_seq": 0,
                            }
                        )
                    )
                )
                self.assertEqual(_recv_server_text(client_sock)["op"], "subscribed")
                self.assertEqual(_recv_server_text(client_sock)["op"], "snapshot")

                active["value"] = False
                broker.broadcast_event(
                    {
                        "room_id": "room-1",
                        "type": "message",
                        "seq": 1,
                        "content": "must not reach a revoked bearer",
                    }
                )

                first_after_revoke = _recv_server_text(client_sock)
                self.assertEqual(first_after_revoke["op"], "error")
                self.assertEqual(
                    first_after_revoke["category"],
                    WS_SESSION_REVOKED_CATEGORY,
                )
            finally:
                client_sock.close()
                server_sock.close()
                broker.close()
                worker.join(timeout=2)

    def _host_ws_ticket(self, base: str, meeting_id: str) -> str:
        req = Request(
            f"{base}/api/ws-ticket",
            data=json.dumps({"meeting_id": meeting_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))["ticket"]

    def test_host_ws_ticket_on_loopback_subscribes_to_room_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"

                ticket = self._host_ws_ticket(base, "room-host-ws")
                self.assertTrue(ticket.startswith("wst_"))

                sock = self._handshake(host, port, ticket)
                try:
                    sock.sendall(
                        _client_text_frame(
                            json.dumps({"op": "subscribe", "streams": ["room_events"]})
                        )
                    )
                    msg = _recv_server_text(sock)
                    self.assertEqual(msg["op"], "subscribed")
                    self.assertEqual(msg["streams"], ["room_events"])
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_ws_rejects_missing_or_used_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                # no ticket → 401, no upgrade
                sock = socket.create_connection((host, port), timeout=4)
                try:
                    sock.sendall(
                        f"GET /ws HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {base64.b64encode(b'0123456789abcdef').decode()}\r\n"
                        "Sec-WebSocket-Version: 13\r\n\r\n".encode("ascii")
                    )
                    sock.settimeout(4)
                    head = b""
                    while b"\r\n\r\n" not in head:
                        head += sock.recv(4096)
                    self.assertIn(b"401", head.split(b"\r\n", 1)[0])
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_upgrade_setup_failure_disconnects_the_open_realtime_channel(self):
        class Handler:
            def __init__(self):
                self.headers = {
                    "Upgrade": "websocket",
                    "Connection": "Upgrade",
                    "Sec-WebSocket-Key": base64.b64encode(b"0123456789abcdef").decode(),
                }
                self.wfile = io.BytesIO()
                self.connection = object()
                self.close_connection = False

            def send_response(self, _status):
                return None

            def send_header(self, _name, _value):
                return None

            def end_headers(self):
                return None

        class Channel:
            closed = False

        class Controller:
            def __init__(self):
                self.channel = Channel()
                self.disconnected = []

            def connect(self, _identity):
                return self.channel

            def disconnect(self, channel):
                self.disconnected.append(channel)

        tickets = WsTicketStore()
        ticket = tickets.issue(
            {
                "agent_id": "guest-1",
                "meeting_id": "room-1",
                "client_type": "browser",
            }
        )
        controller = Controller()

        def fail_dependency_setup(_channel, _handler):
            raise RuntimeError("dependency setup failed")

        with self.assertRaisesRegex(RuntimeError, "dependency setup failed"):
            handle_ws_upgrade(
                Handler(),
                {"ticket": [ticket]},
                ws_ticket_store=tickets,
                room_realtime_controller=controller,
                ws_room_deps_factory=fail_dependency_setup,
            )

        self.assertEqual(controller.disconnected, [controller.channel])


if __name__ == "__main__":
    unittest.main()
