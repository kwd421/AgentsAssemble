"""Pure-stdlib RFC 6455 WebSocket codec + handshake (master plan WS 전환, WS-3).

No dependency (the project only depends on `mcp`); the server is stdlib
`http.server`, which has no WebSocket support, so we implement the handshake and
frame codec ourselves. This module is transport-only: handshake key, frame
encode/decode (with masking), control frames, and message reassembly. The `/ws`
endpoint and connection lifecycle live in `web.websocket`; room protocol state
and governance live above this codec.

Framing rules (RFC 6455):
- client→server frames MUST be masked (mask bit set, 4-byte key, payload XOR'd).
- server→client frames MUST NOT be masked.
- payload length: 0-125 inline (7-bit); 126 → next 2 bytes (u16); 127 → next 8 bytes (u64).
- opcodes: 0x0 continuation, 0x1 text, 0x2 binary, 0x8 close, 0x9 ping, 0xA pong.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import struct
from dataclasses import dataclass

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
CONTROL_OPCODES = frozenset({OP_CLOSE, OP_PING, OP_PONG})

# Close codes (subset).
CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_POLICY_VIOLATION = 1008
CLOSE_MESSAGE_TOO_BIG = 1009
CLOSE_INTERNAL_ERROR = 1011

# Control-frame payloads are capped at 125 bytes by spec; reject oversized
# frame and aggregate message payloads defensively (a room message is small).
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_MESSAGE_FRAGMENTS = 256


class WebSocketProtocolError(Exception):
    """Malformed frame or handshake — the connection should be closed."""

    def __init__(self, message: str, *, close_code: int = CLOSE_PROTOCOL_ERROR) -> None:
        super().__init__(message)
        self.close_code = close_code


# --------------------------------------------------------------------------- #
# Handshake
# --------------------------------------------------------------------------- #
def compute_accept_key(sec_websocket_key: str) -> str:
    """Sec-WebSocket-Accept = base64(sha1(key + GUID))."""
    digest = hashlib.sha1((str(sec_websocket_key).strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _header(headers: object, name: str) -> str:
    """Read a header case-insensitively from a dict or an http.client-style
    Message (which has .get). Returns '' when absent."""
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name) or getter(name.lower()) or getter(name.title())
        return str(value or "")
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value or "")
    return ""


def is_websocket_upgrade(headers: object) -> bool:
    upgrade = _header(headers, "Upgrade").strip().lower()
    connection = _header(headers, "Connection").strip().lower()
    return upgrade == "websocket" and "upgrade" in connection


def handshake_response_lines(headers: object, *, subprotocol: str = "") -> list[str]:
    """Build the 101 Switching Protocols response lines for a valid upgrade.

    Raises WebSocketProtocolError if the request is not a valid WS upgrade
    (missing Sec-WebSocket-Key). The caller writes these CRLF-joined + a blank
    line, then switches to frame I/O on the raw socket."""
    if not is_websocket_upgrade(headers):
        raise WebSocketProtocolError("Not a WebSocket upgrade request.")
    key = _header(headers, "Sec-WebSocket-Key").strip()
    if not key:
        raise WebSocketProtocolError("Missing Sec-WebSocket-Key.")
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Accept: {compute_accept_key(key)}",
    ]
    if subprotocol:
        lines.append(f"Sec-WebSocket-Protocol: {subprotocol}")
    return lines


# --------------------------------------------------------------------------- #
# Frame codec
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Frame:
    fin: bool
    opcode: int
    payload: bytes

    @property
    def is_control(self) -> bool:
        return self.opcode in CONTROL_OPCODES


def encode_frame(payload: bytes, *, opcode: int = OP_TEXT, fin: bool = True) -> bytes:
    """Encode a server→client frame (unmasked, per spec)."""
    if not isinstance(payload, (bytes, bytearray)):
        raise WebSocketProtocolError("Frame payload must be bytes.")
    payload = bytes(payload)
    b0 = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", b0, length)
    elif length < 65536:
        header = struct.pack("!BBH", b0, 126, length)
    else:
        header = struct.pack("!BBQ", b0, 127, length)
    return header + payload


def encode_text(text: str, *, fin: bool = True) -> bytes:
    return encode_frame(text.encode("utf-8"), opcode=OP_TEXT, fin=fin)


def encode_close(code: int = CLOSE_NORMAL, reason: str = "") -> bytes:
    body = struct.pack("!H", int(code)) + reason.encode("utf-8")
    return encode_frame(body, opcode=OP_CLOSE)


def encode_ping(payload: bytes = b"") -> bytes:
    return encode_frame(bytes(payload), opcode=OP_PING)


def encode_pong(payload: bytes = b"") -> bytes:
    return encode_frame(bytes(payload), opcode=OP_PONG)


def parse_close(payload: bytes) -> tuple[int, str]:
    if len(payload) < 2:
        return (CLOSE_NORMAL, "")
    (code,) = struct.unpack("!H", payload[:2])
    return code, payload[2:].decode("utf-8", "replace")


def _parse_frame(buffer: bytes, *, expect_mask: bool) -> tuple[Frame | None, bytes]:
    """Parse ONE frame. `expect_mask` True = client→server (must be masked),
    False = server→client (must NOT be masked). Returns (frame, rest), or
    (None, buffer) when more bytes are needed."""
    if len(buffer) < 2:
        return None, buffer
    b0, b1 = buffer[0], buffer[1]
    fin = bool(b0 & 0x80)
    if b0 & 0x70:  # reserved RSV1-3 bits must be zero (no extensions negotiated)
        raise WebSocketProtocolError("Reserved frame bits set.")
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    offset = 2
    if length == 126:
        if len(buffer) < offset + 2:
            return None, buffer
        (length,) = struct.unpack("!H", buffer[offset:offset + 2])
        offset += 2
    elif length == 127:
        if len(buffer) < offset + 8:
            return None, buffer
        (length,) = struct.unpack("!Q", buffer[offset:offset + 8])
        offset += 8
    if length > MAX_PAYLOAD_BYTES:
        raise WebSocketProtocolError(
            "Frame payload too large.",
            close_code=CLOSE_MESSAGE_TOO_BIG,
        )
    if opcode in CONTROL_OPCODES and length > 125:
        raise WebSocketProtocolError("Control frame payload exceeds 125 bytes.")
    if expect_mask and not masked:
        raise WebSocketProtocolError("Client frame is not masked.")
    if not expect_mask and masked:
        raise WebSocketProtocolError("Server frame must not be masked.")
    mask = b""
    if masked:
        if len(buffer) < offset + 4:
            return None, buffer
        mask = buffer[offset:offset + 4]
        offset += 4
    if len(buffer) < offset + length:
        return None, buffer
    raw = buffer[offset:offset + length]
    payload = bytes(raw[i] ^ mask[i & 3] for i in range(length)) if masked else bytes(raw)
    return Frame(fin=fin, opcode=opcode, payload=payload), buffer[offset + length:]


def parse_frame(buffer: bytes) -> tuple[Frame | None, bytes]:
    """Parse a client→server frame (must be masked). For the server side."""
    return _parse_frame(buffer, expect_mask=True)


def parse_server_frame(buffer: bytes) -> tuple[Frame | None, bytes]:
    """Parse a server→client frame (must be unmasked). For a WS client (e.g. a
    Python resident connecting to /ws — WS-5 resident migration groundwork)."""
    return _parse_frame(buffer, expect_mask=False)


def encode_client_frame(payload: bytes, *, opcode: int = OP_TEXT, fin: bool = True, mask: bytes | None = None) -> bytes:
    """Encode a client→server frame (MASKED, per spec). `mask` is random by
    default; pass a fixed 4-byte mask in tests for determinism."""
    payload = bytes(payload)
    mask = bytes(mask) if mask is not None else secrets.token_bytes(4)
    if len(mask) != 4:
        raise WebSocketProtocolError("Mask must be 4 bytes.")
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


def encode_client_text(text: str) -> bytes:
    return encode_client_frame(text.encode("utf-8"), opcode=OP_TEXT)


def client_handshake_request(path: str, host: str, *, key: str = "", subprotocol: str = "") -> tuple[bytes, str]:
    """Build a WS client upgrade GET request. Returns (request_bytes, key); pass
    the key to handshake_accept_ok() to verify the server's 101 response."""
    if not key:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if subprotocol:
        lines.append(f"Sec-WebSocket-Protocol: {subprotocol}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"), key


def handshake_accept_ok(response_headers: object, key: str) -> bool:
    """True iff the server's Sec-WebSocket-Accept matches our key."""
    return _header(response_headers, "Sec-WebSocket-Accept").strip() == compute_accept_key(key)


class MessageAssembler:
    """Accumulate bytes, yield complete messages, reassembling fragmented
    (continuation) data frames. Control frames pass through immediately.

    `expect_mask=True` (default) for the server side (client frames are masked);
    `expect_mask=False` for a WS client parsing unmasked server frames."""

    def __init__(self, *, expect_mask: bool = True) -> None:
        self._buffer = b""
        self._fragments: list[bytes] = []
        self._fragment_opcode: int | None = None
        self._fragment_bytes = 0
        self._fragment_count = 0
        self._expect_mask = expect_mask

    def feed(self, data: bytes) -> None:
        self._buffer += bytes(data)

    def messages(self):
        """Yield (opcode, payload) for each complete message/control frame."""
        while True:
            frame, rest = _parse_frame(self._buffer, expect_mask=self._expect_mask)
            if frame is None:
                return
            self._buffer = rest
            if frame.is_control:
                yield frame.opcode, frame.payload
                continue
            if frame.opcode == OP_CONTINUATION:
                if self._fragment_opcode is None:
                    raise WebSocketProtocolError("Continuation frame with no start.")
            else:
                if self._fragment_opcode is not None:
                    raise WebSocketProtocolError("New data frame before previous finished.")
                self._fragment_opcode = frame.opcode
            self._fragments.append(frame.payload)
            self._fragment_bytes += len(frame.payload)
            self._fragment_count += 1
            if (
                self._fragment_bytes > MAX_PAYLOAD_BYTES
                or self._fragment_count > MAX_MESSAGE_FRAGMENTS
            ):
                raise WebSocketProtocolError(
                    "WebSocket message exceeds fragmentation limits.",
                    close_code=CLOSE_MESSAGE_TOO_BIG,
                )
            if frame.fin:
                opcode = self._fragment_opcode
                payload = b"".join(self._fragments)
                self._fragments = []
                self._fragment_opcode = None
                self._fragment_bytes = 0
                self._fragment_count = 0
                yield opcode, payload
