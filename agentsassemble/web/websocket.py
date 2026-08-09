"""WebSocket HTTP upgrade and connection lifecycle for the GUI server."""
from __future__ import annotations

import json
import select
import time
from collections.abc import Callable
from http import HTTPStatus
from typing import Any

from agentsassemble.web.websocket_codec import (
    CLOSE_POLICY_VIOLATION,
    MessageAssembler,
    WebSocketProtocolError,
    compute_accept_key,
    encode_close,
    encode_ping,
    encode_text,
    is_websocket_upgrade,
)
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.web.sse_cadence import SSE_EVENT_POLL_INTERVAL_SECONDS
from agentsassemble.web.room_session import (
    WS_MAX_CLIENT_MESSAGE_BYTES,
    WS_SESSION_TOKEN_KEY,
    WS_TICKET_TTL_SECONDS,
    WsRoomSession,
    WsTicketLimitError,
    host_browser_ws_session,
)
from agentsassemble.room.event_broker import RoomConnectionLimitError

WS_APPLICATION_HANDSHAKE_TIMEOUT_SECONDS = 10.0
WS_HEARTBEAT_INTERVAL_SECONDS = 30.0
WS_CLIENT_IDLE_TIMEOUT_SECONDS = 300.0


def register_ws_ticket_route(
    router: Router,
    *,
    ws_ticket_store: Any,
    is_local_operator: Callable[[RequestContext], bool],
) -> None:
    @router.post("/api/ws-ticket")
    def issue_ws_ticket(ctx: RequestContext) -> None:
        body = ctx.read_json_body()
        if body is None:
            return
        session = ctx.session()
        session_token = ctx.bearer_token()
        if session is None:
            if not (ctx.is_host() or is_local_operator(ctx)):
                ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
                return
            try:
                session = host_browser_ws_session(str(body.get("meeting_id") or ""))
            except ValueError as error:
                ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            session_token = ""
        participant_id = str(session.get("agent_id") or "")
        user = ctx.deps.identities.user_for_participant(participant_id)
        if user is not None:
            session = {
                **session,
                "display_name": str(
                    user.get("display_name") or session.get("display_name") or ""
                ),
            }
        try:
            ticket = ws_ticket_store.issue(session, session_token=session_token)
        except WsTicketLimitError as error:
            ctx.send_error(HTTPStatus.TOO_MANY_REQUESTS, str(error))
            return
        ctx.send_json({"ticket": ticket, "ttl_seconds": WS_TICKET_TTL_SECONDS})


def handle_ws_upgrade(
    handler: Any,
    query: dict[str, list[str]],
    *,
    ws_ticket_store: Any,
    room_realtime_controller: Any,
    ws_room_deps_factory: Callable[[Any, Any], Any],
) -> None:
    """Run one authenticated WebSocket connection until either side closes it."""
    if not is_websocket_upgrade(handler.headers):
        handler._send_error(HTTPStatus.BAD_REQUEST, "WebSocket upgrade required")
        return
    ticket = (query.get("ticket") or [""])[0]
    session = ws_ticket_store.consume(ticket)
    if not session:
        handler._send_error(HTTPStatus.UNAUTHORIZED, "invalid or expired ws ticket")
        return
    session_token = str(session.pop(WS_SESSION_TOKEN_KEY, "") or "")
    key = str(handler.headers.get("Sec-WebSocket-Key") or "")
    if not key:
        handler._send_error(HTTPStatus.BAD_REQUEST, "missing Sec-WebSocket-Key")
        return
    principal_user_id = str(session.get("principal_user_id") or "")
    principal_is_operator = bool(session.get("principal_is_operator"))
    identity = {
        "agent_id": str(session.get("agent_id") or ""),
        "display_name": str(session.get("display_name") or ""),
        "participant_type": str(session.get("participant_type") or "human"),
        "client_type": str(session.get("client_type") or session.get("connection_kind") or "browser"),
        "invite_scope": str(session.get("invite_scope") or "read_write"),
        "meeting_id": str(session.get("meeting_id") or ""),
        "operator": principal_is_operator,
        "principal_is_operator": principal_is_operator,
        "session_id": str(session.get("session_id") or session.get("agent_id") or ""),
        "provider_kind": str(session.get("provider_kind") or ""),
        "principal_user_id": principal_user_id,
        "user_id": principal_user_id,
    }
    channel = None
    try:
        channel = room_realtime_controller.connect(identity)
    except RoomConnectionLimitError as error:
        handler._send_error(HTTPStatus.TOO_MANY_REQUESTS, str(error))
        return
    sock = handler.connection
    try:
        handler.close_connection = True
        handler.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", compute_accept_key(key))
        handler.end_headers()
        handler.wfile.flush()
    except BaseException:
        room_realtime_controller.disconnect(channel)
        raise

    def _send_all(frames: list[bytes]) -> bool:
        # Processed room side effects must survive a peer closing during send.
        for frame in frames:
            try:
                sock.sendall(frame)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False
        return True

    try:
        ws = WsRoomSession(
            identity=identity,
            deps=ws_room_deps_factory(channel, handler),
            session_token=session_token,
        )
        assembler = MessageAssembler(max_message_bytes=WS_MAX_CLIENT_MESSAGE_BYTES)
        opened_at = time.monotonic()
        last_client_activity_at = opened_at
        next_heartbeat_at = opened_at + WS_HEARTBEAT_INTERVAL_SECONDS
        while not ws.closed:
            if channel.closed:
                break
            now = time.monotonic()
            deadline = (
                opened_at + WS_APPLICATION_HANDSHAKE_TIMEOUT_SECONDS
                if not ws.handshake_complete
                else last_client_activity_at + WS_CLIENT_IDLE_TIMEOUT_SECONDS
            )
            wait_seconds = max(
                0.0,
                min(
                    SSE_EVENT_POLL_INTERVAL_SECONDS,
                    deadline - now,
                    next_heartbeat_at - now,
                ),
            )
            ready, _, _ = select.select([sock, channel], [], [], wait_seconds)
            if sock in ready:
                data = sock.recv(65536)
                if not data:
                    break
                assembler.feed(data)
                # Handle every received frame before sending so a final say is appended
                # even when the client closes immediately afterward.
                outbound: list[bytes] = []
                messages = list(assembler.messages())
                if messages:
                    last_client_activity_at = time.monotonic()
                for opcode, payload in messages:
                    outbound.extend(ws.handle_frame(opcode, payload))
                if not _send_all(outbound):
                    break
            polled = ws.poll()
            if not _send_all(polled):
                break
            if ws.closed:
                break
            if channel in ready:
                pushed = [
                    encode_text(json.dumps(message, ensure_ascii=False))
                    for message in channel.drain()
                ]
                if not _send_all(pushed):
                    break
            now = time.monotonic()
            if not ws.handshake_complete and now >= opened_at + WS_APPLICATION_HANDSHAKE_TIMEOUT_SECONDS:
                _send_all([encode_close(CLOSE_POLICY_VIOLATION, "room subscription required")])
                break
            if ws.handshake_complete and now >= last_client_activity_at + WS_CLIENT_IDLE_TIMEOUT_SECONDS:
                _send_all([encode_close(CLOSE_POLICY_VIOLATION, "room socket idle timeout")])
                break
            if now >= next_heartbeat_at:
                if not _send_all([encode_ping(b"aa")]):
                    break
                next_heartbeat_at = now + WS_HEARTBEAT_INTERVAL_SECONDS
    except WebSocketProtocolError as error:
        try:
            sock.sendall(encode_close(error.close_code, str(error)[:100]))
        except OSError:
            pass
    except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
        pass
    finally:
        if channel is not None:
            room_realtime_controller.disconnect(channel)
