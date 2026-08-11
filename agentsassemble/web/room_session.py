"""WebSocket room ticket and per-connection protocol core.

Separated from the pure codec (`web/websocket_codec.py`) so it can be unit-tested
without a socket: feed decoded messages, assert the outgoing frames.

Governance: identity + client_type are fixed at the handshake. Public messages
go through the canonical correlated-command path. Existing connections also
re-check their backing invite session so leave/kick revokes already-open
sockets, not just future HTTP requests.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from agentsassemble.admission.capacity import uses_reserved_room_capacity
from agentsassemble.identity.repository import (
    LOCAL_OPERATOR_DISPLAY_NAME_DEFAULT,
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
)
from agentsassemble.web.websocket_codec import (
    CLOSE_MESSAGE_TOO_BIG,
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
WS_MAX_PENDING_TICKETS_TOTAL = 2048
WS_PENDING_TICKET_RESERVE_DIVISOR = 8
WS_MAX_PENDING_TICKETS_PER_SESSION = 8
WS_STREAMS = ("lobby", "roster", "side_chat", "room_events", "plugin")
WS_DEFAULT_STREAMS = ("lobby", "roster", "side_chat")
WS_SESSION_TOKEN_KEY = "_ws_session_token"
WS_SESSION_REVOKED_CATEGORY = "session_revoked"
WS_MAX_CLIENT_MESSAGE_BYTES = 256 * 1024
WS_CLIENT_INGRESS_WINDOW_SECONDS = 60.0
WS_MAX_CLIENT_INGRESS_BYTES_PER_WINDOW = 8 * 1024 * 1024
WS_MAX_CLIENT_MESSAGES_PER_WINDOW = 1024
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
        "principal_is_operator": True,
        "principal_user_id": LOCAL_OPERATOR_USER_ID,
    }


class WsTicketStore:
    """Single-use, short-TTL tickets that bind a verified session to a WS open.

    Browsers can't set Authorization on `new WebSocket(...)`, and putting the
    long-lived session token in the URL violates the no-secrets-in-query rule.
    So: client GETs a ticket over authenticated HTTP, then opens `/ws?ticket=...`.
    The ticket is consumed once at handshake."""

    def __init__(
        self,
        *,
        ttl_seconds: float = WS_TICKET_TTL_SECONDS,
        max_pending_total: int = WS_MAX_PENDING_TICKETS_TOTAL,
        max_public_pending_total: int | None = None,
        max_pending_per_session: int = WS_MAX_PENDING_TICKETS_PER_SESSION,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._max_pending_total = max(1, int(max_pending_total))
        self._max_public_pending_total = max(
            1,
            min(
                int(
                    max_public_pending_total
                    if max_public_pending_total is not None
                    else max(
                        1,
                        self._max_pending_total
                        * (WS_PENDING_TICKET_RESERVE_DIVISOR - 1)
                        // WS_PENDING_TICKET_RESERVE_DIVISOR,
                    )
                ),
                self._max_pending_total,
            ),
        )
        self._max_pending_per_session = max(1, int(max_pending_per_session))
        self._now = now_fn
        self._tickets: dict[str, tuple[dict, str, str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, session: dict, *, session_token: str = "") -> str:
        with self._lock:
            self._prune()
            subject = _ticket_subject(session, session_token=session_token)
            if len(self._tickets) >= self._max_pending_total:
                raise WsTicketLimitError("too many pending WebSocket tickets")
            if not uses_reserved_room_capacity(session):
                public_count = sum(
                    not uses_reserved_room_capacity(entry_session)
                    for entry_session, _token, _subject, _expires_at in self._tickets.values()
                )
                if public_count >= self._max_public_pending_total:
                    raise WsTicketLimitError("too many pending public WebSocket tickets")
            subject_count = sum(
                1 for _session, _token, entry_subject, _expires_at in self._tickets.values()
                if entry_subject == subject
            )
            if subject_count >= self._max_pending_per_session:
                raise WsTicketLimitError("too many pending WebSocket tickets for this session")
            ticket = "wst_" + secrets.token_urlsafe(24)
            self._tickets[ticket] = (
                dict(session),
                str(session_token or ""),
                subject,
                self._now() + self._ttl,
            )
            return ticket

    def consume(self, ticket: str) -> dict | None:
        with self._lock:
            self._prune()
            entry = self._tickets.pop(str(ticket or ""), None)
            if entry is None:
                return None
            session, session_token, _subject, expires_at = entry
            if self._now() > expires_at:
                return None
            if session_token:
                session[WS_SESSION_TOKEN_KEY] = session_token
            return session

    def _prune(self) -> None:
        now = self._now()
        expired = [t for t, (_, _, _, exp) in self._tickets.items() if now > exp]
        for t in expired:
            self._tickets.pop(t, None)


class WsTicketLimitError(RuntimeError):
    """The bounded pending ticket pool cannot accept another ticket."""


def _ticket_subject(session: dict, *, session_token: str) -> str:
    token = str(session_token or "")
    if token:
        return "token:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    stable_identity = "\0".join(
        str(session.get(key) or "")
        for key in ("principal_user_id", "meeting_id", "session_id", "agent_id")
    )
    return "identity:" + hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()


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
    active_plugin_id: Callable[[str], str] = lambda room_id: ""


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
    _plugin_after_seq: int = 0
    handshake_complete: bool = False
    closed: bool = False
    ingress_clock: Callable[[], float] = time.monotonic
    max_client_message_bytes: int = WS_MAX_CLIENT_MESSAGE_BYTES
    max_ingress_bytes_per_window: int = WS_MAX_CLIENT_INGRESS_BYTES_PER_WINDOW
    max_messages_per_window: int = WS_MAX_CLIENT_MESSAGES_PER_WINDOW
    ingress_window_seconds: float = WS_CLIENT_INGRESS_WINDOW_SECONDS
    _ingress_window_started_at: float | None = None
    _ingress_window_bytes: int = 0
    _ingress_window_messages: int = 0

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
            rejected = self._admit_text_payload(payload)
            if rejected is not None:
                return rejected
            return self._handle_text(payload)
        return []

    def _admit_text_payload(self, payload: bytes) -> list[bytes] | None:
        payload_size = len(payload)
        if payload_size > self.max_client_message_bytes:
            self.closed = True
            return [encode_close(CLOSE_MESSAGE_TOO_BIG, "room message too large")]

        now = self.ingress_clock()
        if (
            self._ingress_window_started_at is None
            or now < self._ingress_window_started_at
            or now - self._ingress_window_started_at >= self.ingress_window_seconds
        ):
            self._ingress_window_started_at = now
            self._ingress_window_bytes = 0
            self._ingress_window_messages = 0

        next_bytes = self._ingress_window_bytes + payload_size
        next_messages = self._ingress_window_messages + 1
        if (
            next_bytes > self.max_ingress_bytes_per_window
            or next_messages > self.max_messages_per_window
        ):
            self.closed = True
            return [encode_close(CLOSE_POLICY_VIOLATION, "room input budget exceeded")]

        self._ingress_window_bytes = next_bytes
        self._ingress_window_messages = next_messages
        return None

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
        if op == "plugin":
            return self._on_plugin(msg)
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
        self.handshake_complete = True
        self._room_after_seq = _safe_nonnegative_int(msg.get("resume_from_seq"))
        self._plugin_after_seq = _safe_nonnegative_int(msg.get("plugin_resume_from_seq"))
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

    def _on_plugin(self, msg: dict) -> list[bytes]:
        """First-party activity plugin envelope: command / snapshot only."""

        from agentsassemble.plugin.host_service import handle_ws_plugin_message

        try:
            response = handle_ws_plugin_message(
                room_id=self.meeting_id,
                identity=self.identity,
                message=msg,
                active_plugin_id=self.deps.active_plugin_id(self.meeting_id),
            )
        except Exception as error:  # explicit plugin errors stay on the plugin channel
            return [
                encode_text(
                    json.dumps(
                        {
                            "op": "event",
                            "stream": "plugin",
                            "events": [
                                {
                                    "type": "plugin.error",
                                    "code": str(getattr(error, "code", "plugin_error") or "plugin_error"),
                                    "message": str(error) or "Plugin command failed.",
                                    "room_id": self.meeting_id,
                                }
                            ],
                        }
                    )
                )
            ]
        if not isinstance(response, dict):
            return []
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
        if "plugin" not in self.subscribed:
            return frames
        from agentsassemble.plugin.host_service import read_plugin_events

        plugin_events, latest_sequence, gap = read_plugin_events(
            self.meeting_id,
            after_sequence=self._plugin_after_seq,
        )
        if snapshot and self._plugin_after_seq == 0 and plugin_events:
            # A zero cursor means the browser has no resumable plugin state.
            # Replaying the retained 200ms simulation history makes a fresh
            # room open render hundreds of obsolete frames before it reaches
            # the current game. Start at the newest complete state instead;
            # non-zero cursors still receive the contiguous resume stream and
            # its gap checks below.
            newest_state_index = next(
                (
                    index
                    for index in range(len(plugin_events) - 1, -1, -1)
                    if plugin_events[index].get("type")
                    in {"plugin.snapshot", "plugin.delta"}
                ),
                len(plugin_events) - 1,
            )
            plugin_events = plugin_events[newest_state_index:]
        if gap:
            frames.append(
                encode_text(
                    json.dumps(
                        {
                            "op": "event",
                            "stream": "plugin",
                            "events": [
                                {
                                    "type": "plugin.error",
                                    "code": "plugin_event_gap",
                                    "message": "Plugin events were missed; request a fresh snapshot.",
                                    "room_id": self.meeting_id,
                                }
                            ],
                        }
                    )
                )
            )
        if plugin_events:
            self._plugin_after_seq = latest_sequence
            frames.append(
                encode_text(
                    json.dumps(
                        {
                            "op": "event",
                            "stream": "plugin",
                            "events": plugin_events,
                            "latest_seq": latest_sequence,
                            "snapshot": snapshot,
                        }
                    )
                )
            )
        elif snapshot:
            self._plugin_after_seq = latest_sequence
            frames.append(
                encode_text(
                    json.dumps(
                        {
                            "op": "event",
                            "stream": "plugin",
                            "events": [],
                            "latest_seq": latest_sequence,
                            "snapshot": True,
                        }
                    )
                )
            )
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
