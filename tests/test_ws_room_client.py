import json
import re
import tempfile
import threading
import unittest
from collections import deque
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import agentsassemble.web.room_client as ws_room_client
from agentsassemble.gui import _make_handler
from agentsassemble.admission.invite import create_room_invite, join_room_with_invite, reset_state
from agentsassemble.room_store import RoomStore
from agentsassemble.web.websocket_codec import (
    OP_PING,
    OP_PONG,
    OP_TEXT,
    WebSocketProtocolError,
    compute_accept_key,
    encode_frame,
    encode_text,
    parse_frame,
)
from agentsassemble.web.room_client import (
    WsRoomClient,
    connect_room_ws,
    connect_room_ws_with_ticket,
    join_agent_room_session,
    join_room_session,
)


class FakeSocket:
    """A socket-like that auto-answers the WS handshake and serves queued frames."""

    def __init__(self, *, auto_handshake: bool = True):
        self.sent = b""
        self.auto_handshake = auto_handshake
        self._recv_queue: deque[bytes] = deque()
        self.closed = False

    def settimeout(self, _t):
        pass

    def sendall(self, data: bytes):
        self.sent += bytes(data)
        if self.auto_handshake and b"GET " in data and b"Sec-WebSocket-Key:" in data:
            match = re.search(rb"Sec-WebSocket-Key:\s*(\S+)", data)
            key = match.group(1).decode("ascii")
            accept = compute_accept_key(key)
            self._recv_queue.append(
                f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode()
            )

    def queue_recv(self, data: bytes):
        self._recv_queue.append(bytes(data))

    def recv(self, _n: int) -> bytes:
        return self._recv_queue.popleft() if self._recv_queue else b""

    def close(self):
        self.closed = True

    # test helper: decode the client→server (masked) text frames it sent
    def sent_messages(self) -> list[dict]:
        out, buf = [], self.sent
        # skip the handshake request line block
        if b"\r\n\r\n" in buf and buf.startswith(b"GET "):
            buf = buf.split(b"\r\n\r\n", 1)[1]
        while buf:
            frame, rest = parse_frame(buf)
            if frame is None:
                break
            buf = rest
            if frame.opcode == OP_TEXT:
                out.append(json.loads(frame.payload.decode("utf-8")))
        return out


class HandshakeUnitTests(unittest.TestCase):
    def test_open_succeeds_with_valid_accept(self):
        sock = FakeSocket()
        client = WsRoomClient(sock, host="localhost")
        client.open("/ws?ticket=abc")  # should not raise
        self.assertIn(b"GET /ws?ticket=abc HTTP/1.1", sock.sent)

    def test_open_rejects_wrong_accept(self):
        sock = FakeSocket(auto_handshake=False)
        sock.queue_recv(
            b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: wrong\r\n\r\n"
        )
        with self.assertRaises(WebSocketProtocolError):
            WsRoomClient(sock).open("/ws?ticket=abc")
        self.assertTrue(sock.closed)

    def test_open_rejects_non_101(self):
        sock = FakeSocket(auto_handshake=False)
        sock.queue_recv(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
        with self.assertRaises(WebSocketProtocolError):
            WsRoomClient(sock).open("/ws?ticket=abc")
        self.assertTrue(sock.closed)


class SendUnitTests(unittest.TestCase):
    def _opened(self):
        sock = FakeSocket()
        client = WsRoomClient(sock)
        client.open("/ws?ticket=t")
        return client, sock

    def test_subscribe_sends_masked_frame(self):
        client, sock = self._opened()
        client.subscribe(["lobby", "roster"])
        msgs = sock.sent_messages()
        self.assertEqual(msgs[-1], {"op": "subscribe", "streams": ["lobby", "roster"]})

    def test_say_sends_message(self):
        client, sock = self._opened()
        client.say("hello", kind="message")
        self.assertEqual(sock.sent_messages()[-1], {"op": "say", "message": "hello", "kind": "message"})

    def test_command_sends_correlated_room_command(self):
        client, sock = self._opened()
        request_id = client.command("message.send", {"content": "hello"}, request_id="req-1")

        self.assertEqual(request_id, "req-1")
        self.assertEqual(
            sock.sent_messages()[-1],
            {
                "op": "command",
                "request_id": "req-1",
                "action": "message.send",
                "payload": {"content": "hello"},
            },
        )

    def test_say_can_wait_for_ack(self):
        client, sock = self._opened()
        sock.queue_recv(encode_text(json.dumps({"op": "ack", "event": {"id": "evt1"}})))
        ack = client.say("hello", wait_for_ack=True)
        self.assertIsNotNone(ack)
        self.assertEqual(ack["event"]["id"], "evt1")
        self.assertEqual(sock.sent_messages()[-1], {"op": "say", "message": "hello"})

    def test_say_wait_for_ack_raises_on_error(self):
        client, sock = self._opened()
        sock.queue_recv(encode_text(json.dumps({"op": "error", "category": "muted", "message": "muted"})))
        with self.assertRaisesRegex(Exception, "muted"):
            client.say("hello", wait_for_ack=True)


class ReceiveUnitTests(unittest.TestCase):
    def _opened(self):
        sock = FakeSocket()
        client = WsRoomClient(sock)
        client.open("/ws?ticket=t")
        return client, sock

    def test_receive_parses_server_event(self):
        client, sock = self._opened()
        sock.queue_recv(encode_text(json.dumps({"op": "event", "stream": "lobby", "events": [{"id": "e1"}]})))
        msgs = client.receive()
        self.assertEqual(msgs[0]["op"], "event")
        self.assertEqual(msgs[0]["events"][0]["id"], "e1")

    def test_receive_auto_pongs_ping(self):
        client, sock = self._opened()
        sock.queue_recv(encode_frame(b"hb", opcode=OP_PING))  # server ping (unmasked)
        client.receive()
        # the client must have replied with a (masked) pong
        last = sock.sent[-(len(encode_frame(b"hb", opcode=OP_PONG)) + 2):]
        self.assertIn(OP_PONG, [sock.sent[i] & 0x0F for i in range(len(sock.sent)) if sock.sent[i] & 0x80])

    def test_receive_eof_marks_closed(self):
        client, sock = self._opened()
        # no queued data → recv returns b"" → closed
        self.assertEqual(client.receive(), [])
        self.assertTrue(client.closed)

    def test_receive_invalid_server_json_closes_instead_of_ignoring_it(self):
        client, sock = self._opened()
        sock.queue_recv(encode_text("{not json"))

        with self.assertRaisesRegex(WebSocketProtocolError, "invalid JSON"):
            client.receive()

        self.assertTrue(client.closed)
        self.assertTrue(sock.closed)

    def test_receive_non_object_server_json_closes_as_a_protocol_error(self):
        client, sock = self._opened()
        sock.queue_recv(encode_text(json.dumps(["not", "an", "object"])))

        with self.assertRaisesRegex(WebSocketProtocolError, "JSON object"):
            client.receive()

        self.assertTrue(client.closed)
        self.assertTrue(sock.closed)


class LiveRoundTripTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self._servers: list[ThreadingHTTPServer] = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()
        reset_state()

    def _start(self, root: Path) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        host, port = server.server_address
        return f"http://{host}:{port}"

    def _drain(self, client: WsRoomClient, want_op: str, tries: int = 30) -> dict:
        for _ in range(tries):
            for msg in client.receive():
                if msg.get("op") == want_op:
                    return msg
        raise AssertionError(f"no {want_op} message received")

    def test_python_client_talks_to_real_ws_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            RoomStore(root).create_room("room-1", label="Room 1")
            base = self._start(root)
            invite = create_room_invite(
                room_url=base, meeting_id="room-1", agent_id="agent-ws", display_name="WS봇", max_uses=1
            )
            token = str(join_room_with_invite(str(invite["invite_token"]))["session_token"])

            client = connect_room_ws(base, token, ["lobby"])
            try:
                self._drain(client, "subscribed")
                client.say("hello from python client")
                ack = self._drain(client, "ack")
                self.assertEqual(ack["event"]["message"], "hello from python client")
                self.assertEqual(ack["event"]["actor_id"], "agent-ws")
            finally:
                client.close()

    def test_join_room_session_returns_admitted_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            RoomStore(root).create_room("room-join", label="Join room")
            base = self._start(root)
            invite = create_room_invite(
                room_url=base,
                meeting_id="room-join",
                agent_id="runner",
                display_name="Runner",
                max_uses=1,
            )

            joined = join_room_session(
                base,
                str(invite["invite_token"]),
                display_name="runner",
                participant_type="agent",
            )

            self.assertIsInstance(joined, dict)
            self.assertTrue(str(joined.get("session_token") or "").startswith("aas1."))
            self.assertEqual(joined.get("agent_id"), "runner")
            self.assertEqual(joined.get("display_name"), "runner")
            self.assertEqual(joined.get("meeting_id"), "room-join")

    def test_join_agent_room_session_uses_native_admission_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            RoomStore(root).create_room("room-agent", label="Agent room")
            base = self._start(root)
            invite = create_room_invite(
                room_url=base,
                meeting_id="room-agent",
                agent_id="remote-codex",
                display_name="Remote Codex",
                max_uses=1,
                client_type="agent_bridge",
                provider_kind="codex",
            )

            joined = join_agent_room_session(
                base,
                str(invite["invite_token"]),
                display_name="Remote Codex",
                provider_kind="codex",
            )

            self.assertTrue(str(joined.get("session_token") or "").startswith("aas1."))
            self.assertEqual(joined.get("agent_id"), "remote-codex")
            self.assertEqual(joined.get("client_type"), "agent_bridge")
            self.assertEqual(joined.get("provider_kind"), "codex_live_session")


class ConnectRoomWsTests(unittest.TestCase):
    def test_connect_with_internal_ticket_uses_same_ws_endpoint(self):
        sock = FakeSocket()
        with patch("agentsassemble.web.room_client.socket_module.create_connection", return_value=sock):
            client = connect_room_ws_with_ticket(
                "http://room.example",
                "internal-ticket",
                ["room_events"],
            )
        try:
            self.assertIn(b"GET /ws?ticket=internal-ticket HTTP/1.1", sock.sent)
            self.assertEqual(sock.sent_messages()[-1], {"op": "subscribe", "streams": ["room_events"]})
        finally:
            client.close()

    def test_https_room_wraps_socket_with_tls(self):
        sock = FakeSocket()

        class FakeContext:
            def __init__(self):
                self.wrapped = []

            def wrap_socket(self, raw_sock, *, server_hostname: str):
                self.wrapped.append((raw_sock, server_hostname))
                return raw_sock

        context = FakeContext()

        class FakeSslModule:
            @staticmethod
            def create_default_context():
                return context

        with (
            patch("agentsassemble.web.room_client.request_ws_ticket", return_value="ticket"),
            patch("agentsassemble.web.room_client.socket_module.create_connection", return_value=sock),
            patch.object(ws_room_client, "ssl", FakeSslModule, create=True),
        ):
            client = connect_room_ws("https://room.example", "session-token", ["lobby"])

        try:
            self.assertEqual(context.wrapped, [(sock, "room.example")])
            self.assertIn(b"GET /ws?ticket=ticket HTTP/1.1", sock.sent)
        finally:
            client.close()

    def test_connect_room_ws_preserves_public_url_path_prefix(self):
        sock = FakeSocket()

        with (
            patch("agentsassemble.web.room_client.request_ws_ticket", return_value="ticket"),
            patch("agentsassemble.web.room_client.socket_module.create_connection", return_value=sock),
        ):
            client = connect_room_ws("http://room.example/aa", "session-token", ["lobby"])

        try:
            self.assertIn(b"GET /aa/ws?ticket=ticket HTTP/1.1", sock.sent)
        finally:
            client.close()

    def test_tls_setup_failure_closes_the_connected_socket(self):
        sock = FakeSocket()

        class FailingContext:
            def wrap_socket(self, _raw_sock, *, server_hostname: str):
                raise OSError(f"TLS setup failed for {server_hostname}")

        class FakeSslModule:
            @staticmethod
            def create_default_context():
                return FailingContext()

        with (
            patch(
                "agentsassemble.web.room_client.socket_module.create_connection",
                return_value=sock,
            ),
            patch.object(ws_room_client, "ssl", FakeSslModule, create=True),
            self.assertRaisesRegex(OSError, "TLS setup failed"),
        ):
            connect_room_ws_with_ticket(
                "https://room.example",
                "internal-ticket",
                ["room_events"],
            )

        self.assertTrue(sock.closed)


if __name__ == "__main__":
    unittest.main()
