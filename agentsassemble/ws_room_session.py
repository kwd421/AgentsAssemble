"""WebSocket room session: ticket store + per-connection protocol core (WS-4).

Separated from the pure codec (`room_websocket.py`) so it can be unit-tested
without a socket: feed decoded messages, assert the outgoing frames.

Governance (deliberately minimal — the user's stated problem was *wrong connection
path*, not spam): identity + client_type are fixed at the handshake (the single
governed entry), plus the existing per-room controls (invite scope + mute). No
burst/dedup limiter — that was explicitly rejected as throwaway pre-WS work.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

from agentsassemble.room_websocket import (
    CLOSE_NORMAL,
    CLOSE_POLICY_VIOLATION,
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    encode_close,
    encode_pong,
    encode_text,
)

WS_TICKET_TTL_SECONDS = 30.0
WS_STREAMS = ("lobby", "roster", "side_chat")


class WsTicketStore:
    """Single-use, short-TTL tickets that bind a verified session to a WS open.

    Browsers can't set Authorization on `new WebSocket(...)`, and putting the
    long-lived session token in the URL violates the no-secrets-in-query rule.
    So: client GETs a ticket over authenticated HTTP, then opens `/ws?ticket=...`.
    The ticket is consumed once at handshake."""

    def __init__(self, *, ttl_seconds: float = WS_TICKET_TTL_SECONDS, now_fn: Callable[[], float] = time.monotonic) -> None:
        self._ttl = float(ttl_seconds)
        self._now = now_fn
        self._tickets: dict[str, tuple[dict, float]] = {}

    def issue(self, session: dict) -> str:
        self._prune()
        ticket = "wst_" + secrets.token_urlsafe(24)
        self._tickets[ticket] = (dict(session), self._now() + self._ttl)
        return ticket

    def consume(self, ticket: str) -> dict | None:
        self._prune()
        entry = self._tickets.pop(str(ticket or ""), None)
        if entry is None:
            return None
        session, expires_at = entry
        if self._now() > expires_at:
            return None
        return session

    def _prune(self) -> None:
        now = self._now()
        expired = [t for t, (_, exp) in self._tickets.items() if now > exp]
        for t in expired:
            self._tickets.pop(t, None)


@dataclass
class WsRoomDeps:
    """Room operations the session needs, injected so the core stays testable.

    read_lobby_after(meeting_id, after_id) -> (events, latest_id)
    read_roster(meeting_id) -> (members, signature)
    post_say(identity, payload) -> event dict          (append already-governed)
    is_muted(meeting_id, agent_id) -> bool
    """

    read_lobby_after: Callable[[str, str], tuple[list, str]]
    read_roster: Callable[[str], tuple[list, str]]
    post_say: Callable[[dict, dict], dict]
    is_muted: Callable[[str, str], bool]


@dataclass
class WsRoomSession:
    """Per-connection protocol core. `identity` is resolved ONCE at handshake:
    {agent_id, display_name, participant_type, client_type, invite_scope,
     meeting_id, operator}."""

    identity: dict
    deps: WsRoomDeps
    subscribed: set = field(default_factory=set)
    _cursors: dict = field(default_factory=dict)
    _roster_sig: str = ""
    closed: bool = False

    @property
    def meeting_id(self) -> str:
        return str(self.identity.get("meeting_id") or "")

    # -- frame routing ----------------------------------------------------- #
    def handle_frame(self, opcode: int, payload: bytes) -> list[bytes]:
        """Route a decoded client frame; return outgoing frames to send."""
        if opcode == OP_PING:
            return [encode_pong(payload)]
        if opcode == OP_PONG:
            return []
        if opcode == OP_CLOSE:
            self.closed = True
            return [encode_close(CLOSE_NORMAL)]
        if opcode == OP_TEXT:
            return self._handle_text(payload)
        return []

    def _handle_text(self, payload: bytes) -> list[bytes]:
        try:
            msg = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return [self._error("bad_message", "Frame is not valid JSON.")]
        if not isinstance(msg, dict):
            return [self._error("bad_message", "Message must be a JSON object.")]
        op = str(msg.get("op") or "")
        if op == "subscribe":
            return self._on_subscribe(msg)
        if op == "say":
            return self._on_say(msg)
        if op == "ping":  # app-level ping (in addition to control-frame ping)
            return [encode_text(json.dumps({"op": "pong"}))]
        return [self._error("unknown_op", f"Unknown op: {op!r}")]

    # -- ops --------------------------------------------------------------- #
    def _on_subscribe(self, msg: dict) -> list[bytes]:
        requested = msg.get("streams") or list(WS_STREAMS)
        streams = [s for s in requested if s in WS_STREAMS]
        self.subscribed = set(streams)
        for stream in streams:
            resume = str(msg.get("resume_from_id") or "") if stream == "lobby" else ""
            self._cursors[stream] = resume
        frames = [encode_text(json.dumps({"op": "subscribed", "streams": sorted(self.subscribed)}))]
        frames.extend(self.poll())  # immediate snapshot after subscribe
        return frames

    def _on_say(self, msg: dict) -> list[bytes]:
        if str(self.identity.get("invite_scope") or "") == "read_only":
            return [self._error("read_only", "This session cannot post.")]
        if self.deps.is_muted(self.meeting_id, str(self.identity.get("agent_id") or "")):
            return [self._error("muted", "You are muted by the room host.")]
        message = msg.get("message")
        if not isinstance(message, str) or not message.strip():
            return [self._error("empty", "Message is required.")]
        payload = {
            "message": message,
            "kind": msg.get("kind"),
            "vote_id": msg.get("vote_id"),
            "vote_question": msg.get("vote_question"),
            "vote_options": msg.get("vote_options"),
            "vote_choice": msg.get("vote_choice"),
        }
        try:
            event = self.deps.post_say(self.identity, payload)
        except WsSayRejected as rejected:
            return [self._error(rejected.category, str(rejected))]
        frames = [encode_text(json.dumps({"op": "ack", "event": event}))]
        frames.extend(self.poll())  # push the just-posted event to this connection too
        return frames

    # -- delivery ---------------------------------------------------------- #
    def poll(self) -> list[bytes]:
        """Read new events for subscribed streams; return push frames. Reuses the
        existing snapshot readers (no pub/sub yet — WS-6)."""
        if self.closed:
            return []
        frames: list[bytes] = []
        if "lobby" in self.subscribed:
            events, latest = self.deps.read_lobby_after(self.meeting_id, self._cursors.get("lobby", ""))
            if events:
                self._cursors["lobby"] = latest or self._cursors.get("lobby", "")
                frames.append(encode_text(json.dumps({"op": "event", "stream": "lobby", "events": events})))
        if "roster" in self.subscribed:
            members, signature = self.deps.read_roster(self.meeting_id)
            if signature != self._roster_sig:
                self._roster_sig = signature
                frames.append(encode_text(json.dumps({"op": "event", "stream": "roster", "members": members})))
        return frames

    def _error(self, category: str, message: str) -> bytes:
        return encode_text(json.dumps({"op": "error", "category": category, "message": message}))


class WsSayRejected(Exception):
    """Raised by deps.post_say to reject a message with a category (e.g. turn_conflict)."""

    def __init__(self, message: str, *, category: str = "rejected") -> None:
        super().__init__(message)
        self.category = category
