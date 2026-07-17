import struct
import unittest

from agentsassemble.web.websocket_codec import (
    CLOSE_NORMAL,
    OP_BINARY,
    OP_CLOSE,
    OP_PING,
    OP_TEXT,
    Frame,
    MessageAssembler,
    WebSocketProtocolError,
    client_handshake_request,
    compute_accept_key,
    encode_client_frame,
    encode_client_text,
    encode_close,
    encode_frame,
    encode_ping,
    encode_text,
    handshake_accept_ok,
    handshake_response_lines,
    is_websocket_upgrade,
    parse_close,
    parse_frame,
    parse_server_frame,
)


def _client_frame(payload: bytes, *, opcode: int = OP_TEXT, fin: bool = True, mask=b"\x01\x02\x03\x04") -> bytes:
    """Build a MASKED client→server frame (what a browser sends)."""
    b0 = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", b0, 0x80 | length)
    elif length < 65536:
        header = struct.pack("!BBH", b0, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", b0, 0x80 | 127, length)
    masked = bytes(payload[i] ^ mask[i & 3] for i in range(length))
    return header + mask + masked


class HandshakeTests(unittest.TestCase):
    def test_accept_key_matches_rfc_example(self):
        # RFC 6455 §1.3 worked example
        self.assertEqual(compute_accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_is_websocket_upgrade(self):
        self.assertTrue(is_websocket_upgrade({"Upgrade": "websocket", "Connection": "Upgrade"}))
        self.assertTrue(is_websocket_upgrade({"upgrade": "WebSocket", "connection": "keep-alive, Upgrade"}))
        self.assertFalse(is_websocket_upgrade({"Upgrade": "h2c", "Connection": "Upgrade"}))
        self.assertFalse(is_websocket_upgrade({}))

    def test_handshake_response_lines(self):
        lines = handshake_response_lines(
            {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="},
            subprotocol="aas-room",
        )
        self.assertEqual(lines[0], "HTTP/1.1 101 Switching Protocols")
        self.assertIn("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", lines)
        self.assertIn("Sec-WebSocket-Protocol: aas-room", lines)

    def test_handshake_rejects_non_upgrade(self):
        with self.assertRaises(WebSocketProtocolError):
            handshake_response_lines({"Upgrade": "h2c"})

    def test_handshake_rejects_missing_key(self):
        with self.assertRaises(WebSocketProtocolError):
            handshake_response_lines({"Upgrade": "websocket", "Connection": "Upgrade"})


class EncodeTests(unittest.TestCase):
    def test_server_frames_are_unmasked(self):
        frame = encode_frame(b"hi", opcode=OP_TEXT)
        self.assertEqual(frame[0], 0x80 | OP_TEXT)
        self.assertFalse(frame[1] & 0x80)  # mask bit clear
        self.assertEqual(frame[1] & 0x7F, 2)
        self.assertEqual(frame[2:], b"hi")

    def test_16bit_length_encoding(self):
        payload = b"x" * 200
        frame = encode_frame(payload)
        self.assertEqual(frame[1] & 0x7F, 126)
        (length,) = struct.unpack("!H", frame[2:4])
        self.assertEqual(length, 200)
        self.assertEqual(frame[4:], payload)

    def test_64bit_length_encoding(self):
        payload = b"y" * 70000
        frame = encode_frame(payload)
        self.assertEqual(frame[1] & 0x7F, 127)
        (length,) = struct.unpack("!Q", frame[2:10])
        self.assertEqual(length, 70000)

    def test_encode_text_and_close_and_ping(self):
        self.assertEqual(encode_text("ok")[2:], b"ok")
        code, reason = parse_close(encode_close(CLOSE_NORMAL, "bye")[2:])
        self.assertEqual((code, reason), (CLOSE_NORMAL, "bye"))
        self.assertEqual(encode_ping(b"p")[0] & 0x0F, OP_PING)


class ParseTests(unittest.TestCase):
    def test_round_trip_masked_client_frame(self):
        wire = _client_frame(b"hello")
        frame, rest = parse_frame(wire)
        self.assertEqual(frame, Frame(fin=True, opcode=OP_TEXT, payload=b"hello"))
        self.assertEqual(rest, b"")

    def test_partial_buffer_returns_none(self):
        wire = _client_frame(b"hello world")
        frame, rest = parse_frame(wire[:4])  # truncated
        self.assertIsNone(frame)
        self.assertEqual(rest, wire[:4])

    def test_two_frames_in_one_buffer(self):
        wire = _client_frame(b"aaa") + _client_frame(b"bbb")
        frame1, rest = parse_frame(wire)
        self.assertEqual(frame1.payload, b"aaa")
        frame2, rest2 = parse_frame(rest)
        self.assertEqual(frame2.payload, b"bbb")
        self.assertEqual(rest2, b"")

    def test_16bit_masked_payload(self):
        payload = b"z" * 300
        frame, _ = parse_frame(_client_frame(payload))
        self.assertEqual(frame.payload, payload)

    def test_unmasked_client_frame_rejected(self):
        # server-style (unmasked) frame from a client is a protocol error
        with self.assertRaises(WebSocketProtocolError):
            parse_frame(encode_frame(b"hi"))

    def test_reserved_bits_rejected(self):
        wire = bytearray(_client_frame(b"hi"))
        wire[0] |= 0x40  # set RSV1
        with self.assertRaises(WebSocketProtocolError):
            parse_frame(bytes(wire))

    def test_oversized_control_frame_rejected(self):
        # a ping with >125 byte payload — build the header by hand
        b0 = 0x80 | OP_PING
        header = struct.pack("!BB", b0, 0x80 | 126) + struct.pack("!H", 200)
        wire = header + b"\x00\x00\x00\x00" + (b"\x00" * 200)
        with self.assertRaises(WebSocketProtocolError):
            parse_frame(wire)


class MessageAssemblerTests(unittest.TestCase):
    def test_single_message(self):
        asm = MessageAssembler()
        asm.feed(_client_frame(b"hi"))
        self.assertEqual(list(asm.messages()), [(OP_TEXT, b"hi")])

    def test_byte_at_a_time_streaming(self):
        asm = MessageAssembler()
        wire = _client_frame(b"streamed")
        out = []
        for byte in wire:
            asm.feed(bytes([byte]))
            out.extend(asm.messages())
        self.assertEqual(out, [(OP_TEXT, b"streamed")])

    def test_fragmented_message_reassembled(self):
        asm = MessageAssembler()
        asm.feed(_client_frame(b"Hel", opcode=OP_TEXT, fin=False))
        asm.feed(_client_frame(b"lo", opcode=0x0, fin=True))  # continuation
        self.assertEqual(list(asm.messages()), [(OP_TEXT, b"Hello")])

    def test_control_frame_interleaved_passes_through(self):
        asm = MessageAssembler()
        asm.feed(_client_frame(b"", opcode=OP_PING))
        asm.feed(_client_frame(b"data", opcode=OP_BINARY))
        self.assertEqual(list(asm.messages()), [(OP_PING, b""), (OP_BINARY, b"data")])

    def test_close_frame_surfaced(self):
        asm = MessageAssembler()
        asm.feed(_client_frame(struct.pack("!H", CLOSE_NORMAL) + b"bye", opcode=OP_CLOSE))
        msgs = list(asm.messages())
        self.assertEqual(msgs[0][0], OP_CLOSE)
        self.assertEqual(parse_close(msgs[0][1]), (CLOSE_NORMAL, "bye"))

    def test_continuation_without_start_raises(self):
        asm = MessageAssembler()
        asm.feed(_client_frame(b"x", opcode=0x0, fin=True))
        with self.assertRaises(WebSocketProtocolError):
            list(asm.messages())


class ClientCodecTests(unittest.TestCase):
    def test_encode_client_frame_is_masked_and_decodes_server_side(self):
        wire = encode_client_text("hello")
        self.assertTrue(wire[1] & 0x80, "client frame must set the mask bit")
        frame, rest = parse_frame(wire)  # server-side parse expects masking
        self.assertEqual(frame.payload, b"hello")
        self.assertEqual(rest, b"")

    def test_deterministic_mask(self):
        a = encode_client_frame(b"x", mask=b"\x01\x02\x03\x04")
        b = encode_client_frame(b"x", mask=b"\x01\x02\x03\x04")
        self.assertEqual(a, b)

    def test_random_masks_differ_but_decode_same(self):
        a = encode_client_text("same")
        b = encode_client_text("same")
        self.assertNotEqual(a, b)  # random masks → different wire bytes
        self.assertEqual(parse_frame(a)[0].payload, parse_frame(b)[0].payload)

    def test_parse_server_frame_accepts_unmasked(self):
        frame, _ = parse_server_frame(encode_frame(b"srv"))
        self.assertEqual(frame.payload, b"srv")

    def test_parse_server_frame_rejects_masked(self):
        with self.assertRaises(WebSocketProtocolError):
            parse_server_frame(encode_client_text("x"))

    def test_client_assembler_reads_server_frames(self):
        asm = MessageAssembler(expect_mask=False)
        asm.feed(encode_text("from server"))
        self.assertEqual(list(asm.messages()), [(OP_TEXT, b"from server")])

    def test_client_handshake_request_and_accept_roundtrip(self):
        request, key = client_handshake_request("/ws?ticket=abc", "host:8765")
        self.assertIn(b"GET /ws?ticket=abc HTTP/1.1", request)
        self.assertIn(b"Upgrade: websocket", request)
        # server computes the accept; client verifies it
        server_lines = handshake_response_lines(
            {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": key}
        )
        accept = [l.split(": ", 1)[1] for l in server_lines if l.startswith("Sec-WebSocket-Accept")][0]
        self.assertTrue(handshake_accept_ok({"Sec-WebSocket-Accept": accept}, key))
        self.assertFalse(handshake_accept_ok({"Sec-WebSocket-Accept": "wrong"}, key))


if __name__ == "__main__":
    unittest.main()
