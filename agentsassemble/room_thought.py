"""Shared helpers for streaming a resident's thinking into the room.

- post_room_thought: best-effort POST of one live thought (operator ambience,
  kind="thinking"). Never raises — a streaming hiccup must not break the turn.
- ThoughtChunker: accumulate token-level deltas (grok/agy) and yield room-sized
  chunks on sentence/newline boundaries (or a size cap), so a token stream
  doesn't flood the room one word per message.
"""
from __future__ import annotations

import json
import urllib.request

_SENTENCE_ENDERS = ".?!…。\n"
_CHUNK_SOFT_LIMIT = 180


def post_room_thought(config: object, text: str, *, kind: str) -> None:
    """POST one thinking event to the room for this resident. Best-effort."""
    body = (text or "").strip()
    server = str(getattr(config, "server", "") or "").rstrip("/")
    meeting_id = str(getattr(config, "meeting_id", "") or "")
    agent_id = str(getattr(config, "agent_id", "") or "")
    if not body or not server or not meeting_id:
        return
    payload = {
        "name": getattr(config, "display_name", "") or agent_id,
        "message": body,
        "kind": "thinking",
        "channel": "lobby",
        "audience": "operator",
        "actor_type": "agent",
        "actor_id": agent_id,
        "flow_meeting_id": meeting_id,
        "thinking_kind": kind,
    }
    try:
        request = urllib.request.Request(
            f"{server}/api/lobby",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=5).read()
    except Exception:
        return


class ThoughtChunker:
    """Buffer token deltas and emit room-sized chunks on natural boundaries."""

    def __init__(self, *, soft_limit: int = _CHUNK_SOFT_LIMIT) -> None:
        self._buffer = ""
        self._soft_limit = max(40, int(soft_limit))

    def add(self, delta: str) -> list[str]:
        """Append a token; return any chunks ready to post now."""
        self._buffer += str(delta or "")
        chunks: list[str] = []
        # Flush whole sentences/lines as they complete.
        while True:
            cut = _last_boundary(self._buffer)
            if cut <= 0:
                break
            piece, self._buffer = self._buffer[:cut], self._buffer[cut:]
            piece = piece.strip()
            if piece:
                chunks.append(piece)
        # Avoid an unbounded buffer when a "sentence" never ends.
        if len(self._buffer) >= self._soft_limit:
            piece, self._buffer = self._buffer.strip(), ""
            if piece:
                chunks.append(piece)
        return chunks

    def flush(self) -> str:
        """Return whatever remains (call at stream end)."""
        piece, self._buffer = self._buffer.strip(), ""
        return piece


def _last_boundary(text: str) -> int:
    """Index just past the last sentence/line ender in text, or 0 if none."""
    cut = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_ENDERS:
            cut = index + 1
    return cut
