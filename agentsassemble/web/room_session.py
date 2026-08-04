"""WebSocket room ticket and per-connection protocol core.

Separated from the pure codec (`web/websocket_codec.py`) so it can be unit-tested
without a socket: feed decoded messages, assert the outgoing frames.

Governance: identity + client_type are fixed at the handshake. Public messages
go through the canonical correlated-command path. Existing connections also
re-check their backing invite session so leave/kick revokes already-open
sockets, not just future HTTP requests.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from agentsassemble.identity.repository import (
    LOCAL_OPERATOR_DISPLAY_NAME_DEFAULT,
    LOCAL_OPERATOR_PARTICIPANT_ID,
)
from agentsassemble.web.websocket_codec import (
    CLOSE_NORMAL,
    CLOSE_POLICY_VIOLATION,
    CLOSE_PROTOCOL_ERROR,
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    encode_close,
    encode_pong,
    encode_text,
)

WS_TICKET_TTL_SECONDS = 30.0
WS_STREAMS = ("lobby", "roster", "side_chat", "room_events")
WS_DEFAULT_STREAMS = ("lobby", "roster", "side_chat")
WS_SESSION_TOKEN_KEY = "_ws_session_token"
WS_SESSION_REVOKED_CATEGORY = "session_revoked"
HOST_BROWSER_PARTICIPANT_ID = LOCAL_OPERATOR_PARTICIPANT_ID
HOST_BROWSER_DISPLAY_DEFAULT = LOCAL_OPERATOR_DISPLAY_NAME_DEFAULT


def host_browser_ws_session(meeting_id: str) -> dict[str, object]:
    """Synthetic invite session for the local host browser over WebSocket.

    Mirrors the trusted local operator identity used by /api/lobby so the host
    console can subscribe without a guest invite session token.
    """
    clean_meeting_id = str(meeting_id or "").strip()
    if not clean_meeting_id:
        raise ValueError("meeting_id is required for host WebSocket access.")
    return {
        "agent_id": HOST_BROWSER_PARTICIPANT_ID,
        "display_name": HOST_BROWSER_DISPLAY_DEFAULT,
        "participant_type": "human",
        "client_type": "browser",
        "invite_scope": "read_write",
        "meeting_id": clean_meeting_id,
        "operator": True,
    }


class WsTicketStore:
    """Single-use, short-TTL tickets that bind a verified session to a WS open.

    Browsers can't set Authorization on `new WebSocket(...)`, and putting the
    long-lived session token in the URL violates the no-secrets-in-query rule.
    So: client GETs a ticket over authenticated HTTP, then opens `/ws?ticket=...`.
    The ticket is consumed once at handshake."""

    def __init__(self, *, ttl_seconds: float = WS_TICKET_TTL_SECONDS, now_fn: Callable[[], float] = time.monotonic) -> None:
        self._ttl = float(ttl_seconds)
        self._now = now_fn
        self._tickets: dict[str, tuple[dict, str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, session: dict, *, session_token: str = "") -> str:
        with self._lock:
            self._prune()
            ticket = "wst_" + secrets.token_urlsafe(24)
            self._tickets[ticket] = (
                dict(session),
                str(session_token or ""),
                self._now() + self._ttl,
            )
            return ticket

    def consume(self, ticket: str) -> dict | None:
        with self._lock:
            self._prune()
            entry = self._tickets.pop(str(ticket or ""), None)
            if entry is None:
                return None
            session, session_token, expires_at = entry
            if self._now() > expires_at:
                return None
            if session_token:
                session[WS_SESSION_TOKEN_KEY] = session_token
            return session

    def _prune(self) -> None:
        now = self._now()
        expired = [t for t, (_, _, exp) in self._tickets.items() if now > exp]
        for t in expired:
            self._tickets.pop(t, None)


@dataclass
class WsRoomDeps:
    """Room operations the session needs, injected so the core stays testable.

    read_lobby_after(meeting_id, after_id) -> (events, latest_id)
    read_roster(meeting_id) -> (members, signature)
    """

    read_lobby_after: Callable[[str, str], tuple[list, str]]
    read_roster: Callable[[str], tuple[list, str]]
    read_side_chat_after: Callable[[str, str], tuple[list, str]]
    set_thinking: Callable[[dict, bool], None]
    is_session_active: Callable[[str], bool] = lambda token: True
    room_snapshot: Callable[[dict, int], dict[str, object]] = lambda identity, after_seq: {}
    execute_command: Callable[[dict, dict], dict[str, object]] = lambda identity, message: {}
    on_subscribe: Callable[[dict, set[str], int], None] = lambda identity, streams, after_seq: None


@dataclass
class WsRoomSession:
    """Per-connection protocol core. `identity` is resolved ONCE at handshake:
    {agent_id, display_name, participant_type, client_type, invite_scope,
     meeting_id, operator}."""

    identity: dict
    deps: WsRoomDeps
    session_token: str = ""
    subscribed: set = field(default_factory=set)
    _cursors: dict = field(default_factory=dict)
    _roster_sig: str = ""
    _room_after_seq: int = 0
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
        if not self._session_is_active():
            return [self._error(WS_SESSION_REVOKED_CATEGORY, "This room session has ended.")]
        try:
            msg = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._invalid_message("Frame is not valid JSON.")
        if not isinstance(msg, dict):
            return self._invalid_message("Message must be a JSON object.")
        op = str(msg.get("op") or "")
        if op == "subscribe":
            return self._on_subscribe(msg)
        if op == "thinking":
            return self._on_thinking(msg)
        if op == "command":
            return self._on_command(msg)
        if op == "ping":  # app-level ping (in addition to control-frame ping)
            return [encode_text(json.dumps({"op": "pong"}))]
        return [self._error("unknown_op", f"Unknown op: {op!r}")]

    def _invalid_message(self, message: str) -> list[bytes]:
        frames = [self._error("bad_message", message)]
        if self.identity.get("client_type") == "agent_bridge":
            self.closed = True
            frames.append(encode_close(CLOSE_PROTOCOL_ERROR))
        return frames

    def _on_thinking(self, msg: dict) -> list[bytes]:
        """A resident signals it started/finished generating. We mark its roster
        status working/online; the roster push then lights up the typing
        indicator for everyone (no pub/sub needed — reuses the roster stream)."""
        on = bool(msg.get("on"))
        try:
            self.deps.set_thinking(self.identity, on)
        except Exception:  # thinking is best-effort UX; never break the connection
            return [self._error("status_failed", "Could not update thinking status.")]
        return [encode_text(json.dumps({"op": "thinking_ack", "on": on}))]

    # -- ops --------------------------------------------------------------- #
    def _on_subscribe(self, msg: dict) -> list[bytes]:
        requested = msg.get("streams") or list(WS_DEFAULT_STREAMS)
        streams = [s for s in requested if s in WS_STREAMS]
        self.subscribed = set(streams)
        self._room_after_seq = _safe_nonnegative_int(msg.get("resume_from_seq"))
        for stream in streams:
            resume = str(msg.get("resume_from_id") or "") if stream in {"lobby", "side_chat"} else ""
            self._cursors[stream] = resume
        try:
            self.deps.on_subscribe(self.identity, set(streams), self._room_after_seq)
        except Exception:
            return [self._error("subscribe_failed", "Could not subscribe to room events.")]
        frames = [encode_text(json.dumps({"op": "subscribed", "streams": sorted(self.subscribed)}))]
        frames.extend(self.poll(snapshot=True))  # immediate snapshot after subscribe
        return frames

    def _on_command(self, msg: dict) -> list[bytes]:
        request_id = str(msg.get("request_id") or "").strip()
        if not request_id:
            return [self._nack("", "bad_request", "request_id is required.")]
        try:
            response = self.deps.execute_command(self.identity, msg)
        except WsCommandRejected as rejected:
            return [self._nack(request_id, rejected.code, str(rejected))]
        except Exception as error:
            code = str(getattr(error, "code", "command_failed") or "command_failed")
            return [self._nack(request_id, code, str(error) or "Room command failed.")]
        if not isinstance(response, dict):
            return [self._nack(request_id, "command_failed", "Room command returned an invalid response.")]
        return [encode_text(json.dumps(response))]

    # -- delivery ---------------------------------------------------------- #
    def poll(self, *, snapshot: bool = False) -> list[bytes]:
        """Read new events for subscribed streams; return push frames. Reuses the
        existing snapshot readers (no pub/sub yet — WS-6)."""
        if self.closed:
            return []
        if not self._session_is_active():
            return [self._error(WS_SESSION_REVOKED_CATEGORY, "This room session has ended.")]
        frames: list[bytes] = []
        if "lobby" in self.subscribed:
            events, latest = self.deps.read_lobby_after(self.meeting_id, self._cursors.get("lobby", ""))
            if events:
                self._cursors["lobby"] = latest or self._cursors.get("lobby", "")
                message: dict[str, object] = {"op": "event", "stream": "lobby", "events": events}
                if snapshot:
                    message["snapshot"] = True
                frames.append(encode_text(json.dumps(message)))
            elif snapshot:
                frames.append(encode_text(json.dumps({
                    "op": "event",
                    "stream": "lobby",
                    "events": [],
                    "snapshot": True,
                })))
        if "roster" in self.subscribed:
            members, signature = self.deps.read_roster(self.meeting_id)
            if signature != self._roster_sig:
                self._roster_sig = signature
                frames.append(encode_text(json.dumps({"op": "event", "stream": "roster", "members": members})))
        if "side_chat" in self.subscribed:
            events, latest = self.deps.read_side_chat_after(
                self.meeting_id,
                self._cursors.get("side_chat", ""),
            )
            if events:
                self._cursors["side_chat"] = latest or self._cursors.get("side_chat", "")
                message = {"op": "event", "stream": "side_chat", "events": events}
                if snapshot:
                    message["snapshot"] = True
                frames.append(encode_text(json.dumps(message)))
            elif snapshot:
                frames.append(encode_text(json.dumps({
                    "op": "event",
                    "stream": "side_chat",
                    "events": [],
                    "snapshot": True,
                })))
        if snapshot and "room_events" in self.subscribed:
            payload = dict(self.deps.room_snapshot(self.identity, self._room_after_seq))
            payload["op"] = "snapshot"
            payload["stream"] = "room_events"
            frames.append(encode_text(json.dumps(payload)))
        return frames

    def _session_is_active(self) -> bool:
        if not self.session_token:
            return True
        try:
            active = bool(self.deps.is_session_active(self.session_token))
        except Exception:
            active = False
        if active:
            return True
        self.closed = True
        return False

    def _error(self, category: str, message: str) -> bytes:
        return encode_text(json.dumps({"op": "error", "category": category, "message": message}))

    @staticmethod
    def _nack(request_id: str, code: str, message: str) -> bytes:
        return encode_text(
            json.dumps(
                {
                    "op": "nack",
                    "request_id": request_id,
                    "accepted": False,
                    "error": {"code": code, "message": message},
                }
            )
        )


class WsCommandRejected(Exception):
    def __init__(self, message: str, *, code: str = "rejected") -> None:
        super().__init__(message)
        self.code = code


def _safe_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
