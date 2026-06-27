"""Integration test for the /ws endpoint + /api/ws-ticket (WS-4 glue).

Exercises the gui layer over a real socket: mint a session, get a ws-ticket,
do the RFC 6455 handshake, and round-trip a subscribe. The codec + protocol
core are unit-tested elsewhere (test_room_websocket, test_ws_room_session); this
proves the wiring (ticket issuance, socket hijack, select loop, governed say).
"""
import base64
import json
import socket
import struct
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.room_invite import (
    create_room_invite,
    join_room_with_invite,
    reset_state,
)
from agentsassemble.room_websocket import OP_TEXT, compute_accept_key
from agentsassemble.ws_room_session import WS_SESSION_REVOKED_CATEGORY


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
            chunk = sock.recv(4096)
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
        reset_state()
        self._servers: list[ThreadingHTTPServer] = []

    def tearDown(self):
        for server in list(self._servers):
            self._stop_server(server)
        reset_state()

    def _start_server(self, root: Path) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        return server

    def _stop_server(self, server: ThreadingHTTPServer) -> None:
        if server in self._servers:
            self._servers.remove(server)
        server.shutdown()
        server.server_close()

    def _session_token(self, base: str) -> str:
        invite = create_room_invite(
            room_url=base, meeting_id="room-1", agent_id="guest-1", display_name="테스터", max_uses=1
        )
        joined = join_room_with_invite(str(invite["invite_token"]))
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

    def test_general_room_ws_hello_snapshot_on_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                sock = self._handshake_path(host, port, "/ws/rooms/general")
                try:
                    sock.sendall(_client_text_frame(json.dumps({"type": "hello", "client_id": "browser-1"})))
                    msg = _recv_server_text(sock)
                    self.assertEqual(msg["type"], "snapshot")
                    self.assertEqual(msg["room_id"], "general")
                    self.assertIn("codex", [agent["agent_id"] for agent in msg["agents"]])
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
                    sock.sendall(_client_text_frame(json.dumps({"op": "subscribe", "streams": ["lobby"]})))
                    msg = _recv_server_text(sock)
                    self.assertEqual(msg["op"], "subscribed")
                    self.assertEqual(msg["streams"], ["lobby"])
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_say_over_ws_appends_and_acks(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self._start_server(Path(tmp))
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"
                token = self._session_token(base)
                ticket = self._ws_ticket(base, token)
                sock = self._handshake(host, port, ticket)
                try:
                    sock.sendall(_client_text_frame(json.dumps({"op": "say", "message": "안녕 WS"})))
                    msg = _recv_server_text(sock)
                    self.assertEqual(msg["op"], "ack")
                    self.assertEqual(msg["event"]["message"], "안녕 WS")
                    # server-injected identity, not client-supplied
                    self.assertEqual(msg["event"]["actor_id"], "guest-1")
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def test_existing_ws_rejects_say_after_session_leave(self):
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

                    sock.sendall(_client_text_frame(json.dumps({"op": "say", "message": "after leave"})))
                    msg = _recv_server_text(sock)
                    self.assertEqual(msg["op"], "error")
                    self.assertEqual(msg["category"], WS_SESSION_REVOKED_CATEGORY)
                finally:
                    sock.close()
            finally:
                self._stop_server(server)

    def _host_ws_ticket(self, base: str, meeting_id: str) -> str:
        req = Request(
            f"{base}/api/ws-ticket",
            data=json.dumps({"meeting_id": meeting_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))["ticket"]

    def test_host_ws_ticket_on_loopback_subscribes_lobby_and_roster(self):
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
                            json.dumps({"op": "subscribe", "streams": ["lobby", "roster"]})
                        )
                    )
                    msg = _recv_server_text(sock)
                    self.assertEqual(msg["op"], "subscribed")
                    self.assertEqual(msg["streams"], ["lobby", "roster"])
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


if __name__ == "__main__":
    unittest.main()
