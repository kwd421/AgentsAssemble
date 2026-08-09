"""Binary framing for one-shot secrets passed to an Agent Bridge child."""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from typing import BinaryIO


_HEADER = struct.Struct("!I")
MAX_SECURE_LAUNCH_PAYLOAD_BYTES = 64 * 1024


class SecureLaunchPayloadError(ValueError):
    """The private parent-to-child handoff was missing or malformed."""


def encode_secure_launch_payload(values: Mapping[str, object]) -> bytes:
    payload = json.dumps(
        dict(values),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if not payload or len(payload) > MAX_SECURE_LAUNCH_PAYLOAD_BYTES:
        raise SecureLaunchPayloadError("Agent Bridge secure launch payload is invalid.")
    return _HEADER.pack(len(payload)) + payload


def read_secure_launch_payload(stream: BinaryIO) -> dict[str, object]:
    header = _read_exact(stream, _HEADER.size)
    if not header:
        return {}
    if len(header) != _HEADER.size:
        raise SecureLaunchPayloadError("Agent Bridge secure launch header was truncated.")
    (payload_size,) = _HEADER.unpack(header)
    if payload_size <= 0 or payload_size > MAX_SECURE_LAUNCH_PAYLOAD_BYTES:
        raise SecureLaunchPayloadError("Agent Bridge secure launch payload size is invalid.")
    payload = _read_exact(stream, payload_size)
    if len(payload) != payload_size:
        raise SecureLaunchPayloadError("Agent Bridge secure launch payload was truncated.")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SecureLaunchPayloadError("Agent Bridge secure launch payload is invalid.") from error
    if not isinstance(decoded, dict):
        raise SecureLaunchPayloadError("Agent Bridge secure launch payload must be an object.")
    return decoded


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max(0, int(size))
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(bytes(chunk))
        remaining -= len(chunk)
    return b"".join(chunks)


__all__ = [
    "MAX_SECURE_LAUNCH_PAYLOAD_BYTES",
    "SecureLaunchPayloadError",
    "encode_secure_launch_payload",
    "read_secure_launch_payload",
]
