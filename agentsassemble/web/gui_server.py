"""HTTP handler transport for the local GUI server."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.application.gui import GuiApplicationServices
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.room.repository import RoomRepository
from agentsassemble.web.response import (
    GuiResponseMethods,
    _last_payload_event_id,
    _sse_event,
)
from agentsassemble.web.request_limits import RequestDeadlineHandlerMixin
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.web.security import (
    _LOOPBACK_HOSTNAMES,
    _PUBLIC_INVITE_CORS_HEADERS,
    _PUBLIC_INVITE_CORS_METHODS,
    _host_header_is_trusted,
    _origin_matches_public_url,
    _public_invite_route_allowed,
    _public_server_identity_route_allowed,
    _request_trusted,
    _split_authority_host_port,
)
from agentsassemble.web.sse_cadence import (
    SSE_EVENT_POLL_INTERVAL_SECONDS,
    SSE_KEEPALIVE_INTERVAL_SECONDS,
)
from agentsassemble.web.static import ReactStaticTransport
from agentsassemble.web.websocket import handle_ws_upgrade


def make_gui_http_handler(
    *,
    output_root: Path,
    services: GuiApplicationServices,
    route_table: Router,
    route_deps: GuiDeps,
    static_transport: ReactStaticTransport,
    ws_ticket_store: object,
    room_realtime_controller: RoomRealtimeController,
    ws_room_deps_factory: Callable[..., object],
    room_repository: RoomRepository,
    stream_snapshot_payload: Callable[..., dict[str, object]],
    room_sse_frames_after_cursor: Callable[..., list[str]],
    sse_stream_error_payload: Callable[..., dict[str, object]],
    sse_frame_id: Callable[[str], str],
    payload_signature: Callable[[dict[str, object]], str | None],
) -> type[BaseHTTPRequestHandler]:
    """Build the transport handler around already-composed GUI services."""

    class AgentsAssembleHandler(
        RequestDeadlineHandlerMixin,
        GuiResponseMethods,
        BaseHTTPRequestHandler,
    ):
        def _effective_request_host(self) -> object:
            if services.public_invite.verify_managed_ingress_origin(
                self.headers.get("Host"),
            ):
                return self.headers.get("X-Forwarded-Host")
            raw_host = self.headers.get("Host")
            host_name, _ = _split_authority_host_port(str(raw_host or ""))
            if (
                services.public_invite.managed_ingress_origin_host()
                and host_name not in _LOOPBACK_HOSTNAMES
            ):
                return ""
            return raw_host

        def _request_is_trusted(self, *, path: str, method: str) -> bool:
            effective_host = self._effective_request_host()
            trusted = _request_trusted(
                self.server.server_address[0],
                effective_host,
                self.headers.get("Origin"),
                path=path,
                method=method,
                public_url=services.public_invite.public_url(),
            )
            if trusted:
                self._agentsassemble_effective_host = effective_host
            return trusted

        def _public_invite_cors_origin(self, *, requested_method: str = "") -> str:
            origin = str(self.headers.get("Origin") or "").strip()
            if not origin:
                return ""
            effective_host = self._effective_request_host()
            host_name, _ = _split_authority_host_port(str(effective_host or ""))
            if host_name in _LOOPBACK_HOSTNAMES or not _host_header_is_trusted(
                effective_host,
                public_url=services.public_invite.public_url(),
            ):
                return ""
            path = urlparse(self.path).path
            method = (requested_method or self.command or "").upper()
            if method == "OPTIONS":
                method = str(self.headers.get("Access-Control-Request-Method") or "").upper()
            if not method or not _public_invite_route_allowed(path, method):
                return ""
            if _public_server_identity_route_allowed(path, method):
                return "*"
            if _origin_matches_public_url(
                origin,
                public_url=services.public_invite.public_url(),
            ):
                return origin
            return ""

        def _send_public_invite_cors_headers(self, *, origin: str = "") -> None:
            allow_origin = origin or self._public_invite_cors_origin()
            if not allow_origin:
                return
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            if allow_origin == "*":
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            else:
                self.send_header("Access-Control-Allow-Methods", _PUBLIC_INVITE_CORS_METHODS)
                self.send_header("Access-Control-Allow-Headers", _PUBLIC_INVITE_CORS_HEADERS)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Max-Age", "600")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if not self._request_is_trusted(path=path, method="GET"):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            if path == "/ws":
                self._handle_ws_upgrade(query)
                return
            if route_table.dispatch("GET", RequestContext(self, route_deps, parsed, query)):
                return
            if static_transport.dispatch_get(self, path=path, query=query):
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_OPTIONS(self) -> None:
            parsed = urlparse(self.path)
            requested_method = str(self.headers.get("Access-Control-Request-Method") or "").upper()
            if requested_method and not _public_invite_route_allowed(parsed.path, requested_method):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            if not self._request_is_trusted(path=parsed.path, method="OPTIONS"):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            allow_origin = self._public_invite_cors_origin(requested_method=requested_method)
            if not allow_origin:
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_public_invite_cors_headers(origin=allow_origin)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not self._request_is_trusted(path=parsed.path, method="POST"):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            if route_table.dispatch(
                "POST",
                RequestContext(self, route_deps, parsed, parse_qs(parsed.query)),
            ):
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            if not self._request_is_trusted(path=parsed.path, method="DELETE"):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            query = parse_qs(parsed.query)
            if route_table.dispatch("DELETE", RequestContext(self, route_deps, parsed, query)):
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_ws_upgrade(self, query: dict) -> None:
            """Upgrade the one authenticated room socket used by browsers and bridges."""
            return handle_ws_upgrade(
                self,
                query,
                ws_ticket_store=ws_ticket_store,
                room_realtime_controller=room_realtime_controller,
                ws_room_deps_factory=ws_room_deps_factory,
            )

        def _send_sse_stream(
            self,
            event_name: str,
            stream: str,
            meeting_id: str | None = None,
            last_event_id: str | None = None,
        ) -> None:
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._send_public_invite_cors_headers()
            self.end_headers()
            current_last_event_id = last_event_id
            current_payload_signature: str | None = None
            last_write_at = 0.0
            while True:
                try:
                    payload = stream_snapshot_payload(
                        output_root,
                        stream,
                        meeting_id=meeting_id,
                        last_event_id=current_last_event_id,
                        repository=room_repository,
                        sessions=(
                            services.sessions.active_summary()
                            if stream == "roster"
                            else None
                        ),
                    )
                    latest_event_id = _last_payload_event_id(payload)
                    wrote_frame = False
                    if latest_event_id:
                        self.wfile.write(_sse_event(event_name, payload, event_id=latest_event_id))
                        current_last_event_id = latest_event_id
                        current_payload_signature = payload_signature(payload)
                        wrote_frame = True
                    elif payload_signature(payload) and payload_signature(payload) != current_payload_signature:
                        self.wfile.write(_sse_event(event_name, payload))
                        current_payload_signature = payload_signature(payload)
                        wrote_frame = True
                    elif time.monotonic() - last_write_at >= SSE_KEEPALIVE_INTERVAL_SECONDS:
                        self.wfile.write(b": keep-alive\n\n")
                        wrote_frame = True
                    if wrote_frame:
                        self.wfile.flush()
                        last_write_at = time.monotonic()
                    time.sleep(SSE_EVENT_POLL_INTERVAL_SECONDS)
                except (ValueError, FileNotFoundError) as error:
                    error_payload = sse_stream_error_payload(stream, error, meeting_id=meeting_id)
                    try:
                        self.wfile.write(_sse_event("error", error_payload))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    return
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _send_room_events_sse_stream(self, *, room_id: str, cursor: str | None = None) -> None:
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._send_public_invite_cors_headers()
            self.end_headers()
            current_cursor = cursor or ""
            last_write_at = time.monotonic()
            while True:
                try:
                    frames = room_sse_frames_after_cursor(
                        output_root,
                        room_id,
                        cursor=current_cursor,
                        include_heartbeat=False,
                        repository=room_repository,
                    )
                    for frame in frames:
                        self.wfile.write(frame.encode("utf-8"))
                        event_id = sse_frame_id(frame)
                        if event_id:
                            current_cursor = event_id
                    if frames:
                        self.wfile.flush()
                        last_write_at = time.monotonic()
                    time.sleep(SSE_EVENT_POLL_INTERVAL_SECONDS)
                    if time.monotonic() - last_write_at >= SSE_KEEPALIVE_INTERVAL_SECONDS:
                        self.wfile.write(b"event: heartbeat\ndata: {}\n\n")
                        self.wfile.flush()
                        last_write_at = time.monotonic()
                except (ValueError, FileNotFoundError) as error:
                    try:
                        self.wfile.write(
                            _sse_event(
                                "error",
                                sse_stream_error_payload(
                                    "room_events",
                                    error,
                                    meeting_id=room_id,
                                ),
                            )
                        )
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    return
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _send_error(
            self,
            status: HTTPStatus,
            message: str,
            *,
            code: str = "",
            details: dict[str, object] | None = None,
        ) -> None:
            payload: dict[str, object] = {"error": message}
            if code:
                payload["code"] = code
            if details:
                payload["details"] = details
                meeting_id = details.get("meeting_id")
                if meeting_id:
                    payload["meeting_id"] = meeting_id
                group_id = details.get("group_id")
                if group_id:
                    payload["group_id"] = group_id
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._send_public_invite_cors_headers()
            self.end_headers()
            self.wfile.write(data)

    AgentsAssembleHandler.application_services = services
    AgentsAssembleHandler.room_realtime_controller = room_realtime_controller
    AgentsAssembleHandler.room_repository = room_repository
    AgentsAssembleHandler.gui_deps = route_deps
    return AgentsAssembleHandler
