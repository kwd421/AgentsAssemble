"""Route-table dispatcher + unified request identity for the GUI HTTP server.

R2 of docs/improvement-plan-20260611.md. Domain modules register handlers on a
Router with ``@router.get("/api/...")`` / ``@router.post("/api/...")``; gui.py
dispatches to the table first and falls back to the legacy do_GET/do_POST
if-chains for routes not yet migrated. New endpoints must register here
instead of growing the if-chains.

The RequestContext is also the single place that answers "who is calling?"
(host operator / invited session / anonymous), so endpoints stop
re-implementing token checks (R2-b). DB-3 extends this to account-based
identity.

Note: tests/test_legacy_react_parity_inventory.py parses route registrations
textually — keep the decorator call on one line with a string literal path.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from agentsassemble.attachments import FileAttachmentStore
from agentsassemble.web.security import (
    _LOOPBACK_HOSTNAMES,
    _is_loopback_host,
    _origin_is_loopback_or_empty,
    _split_authority_host_port,
)
from agentsassemble.identity.repository import IdentityBackend, device_auth_key
from agentsassemble.legacy.admission_projection import LegacyAdmissionProjection
from agentsassemble.identity.pairing import OperatorPairingService
from agentsassemble.public_invite_runtime import PublicInviteRuntime
from agentsassemble.admission.preflight import RoomAdmissionService
from agentsassemble.admission.coordinator import RoomAdmissionCoordinator
from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.room_repository import RoomRepository


@dataclass
class GuiDeps:
    """Server-scoped dependencies handed to route handlers.

    The lobby IO callables are still owned by gui.py; they ride along here so
    domain modules don't need circular imports. This shrinks as those helpers
    move into proper homes during the DB migration.
    """

    output_root: Path
    room_repository: RoomRepository | None = None
    identity_backend: IdentityBackend | None = None
    invite_application: InviteApplicationService | None = None
    room_sessions: RoomSessionService | None = None
    admission_preflight_service: RoomAdmissionService | None = None
    admission_coordinator: RoomAdmissionCoordinator | None = None
    operator_pairing_service: OperatorPairingService | None = None
    public_invite_runtime: PublicInviteRuntime | None = None
    attachment_store: FileAttachmentStore | None = None
    legacy_admission_projection: LegacyAdmissionProjection | None = None
    process_supervisor: Any = None
    read_lobby: Callable[..., list[dict[str, object]]] | None = None
    read_lobby_before: Callable[..., dict[str, object]] | None = None
    append_lobby_event: Callable[..., dict[str, object]] | None = None
    lobby_payload_with_attachments: Callable[..., dict[str, object]] | None = None
    public_lobby_allows_room_scope: Callable[[dict[str, object]], bool] | None = None
    history_page_limit: Callable[[dict[str, list[str]]], int] | None = None

    @property
    def rooms(self) -> RoomRepository:
        repository = self.room_repository
        if repository is None:
            raise RuntimeError("GUI room repository is not configured.")
        return repository

    @property
    def identities(self) -> IdentityBackend:
        backend = self.identity_backend
        if backend is None:
            raise RuntimeError("GUI identity backend is not configured.")
        return backend

    @property
    def invites(self) -> InviteApplicationService:
        service = self.invite_application
        if service is None:
            raise RuntimeError("GUI invite application service is not configured.")
        return service

    @property
    def sessions(self) -> RoomSessionService:
        service = self.room_sessions
        if service is None:
            raise RuntimeError("GUI room session service is not configured.")
        return service

    @property
    def admission_preflight(self) -> RoomAdmissionService:
        service = self.admission_preflight_service
        if service is None:
            raise RuntimeError("GUI admission preflight service is not configured.")
        return service

    @property
    def admission(self) -> RoomAdmissionCoordinator:
        service = self.admission_coordinator
        if service is None:
            raise RuntimeError("GUI admission coordinator is not configured.")
        return service

    @property
    def pairing(self) -> OperatorPairingService:
        service = self.operator_pairing_service
        if service is None:
            raise RuntimeError("GUI operator pairing service is not configured.")
        return service

    @property
    def public_invite(self) -> PublicInviteRuntime:
        runtime = self.public_invite_runtime
        if runtime is None:
            raise RuntimeError("GUI public invite runtime is not configured.")
        return runtime

    @property
    def media(self) -> FileAttachmentStore:
        store = self.attachment_store
        if store is None:
            raise RuntimeError("GUI attachment store is not configured.")
        return store

    @property
    def admission_projection(self) -> LegacyAdmissionProjection:
        projection = self.legacy_admission_projection
        if projection is None:
            raise RuntimeError("GUI legacy admission projection is not configured.")
        return projection


class RequestContext:
    """Per-request facade over the http.server handler + identity resolution.

    Route handlers talk to this instead of the raw BaseHTTPRequestHandler, so
    auth checks and body parsing live in exactly one place.
    """

    def __init__(self, handler: Any, deps: GuiDeps, parsed: Any, query: dict[str, list[str]]) -> None:
        self.handler = handler
        self.deps = deps
        self.parsed = parsed
        self.path: str = parsed.path
        self.query = query
        self._session_resolved = False
        self._session: dict[str, object] | None = None

    # -- plumbing ---------------------------------------------------------
    @property
    def headers(self) -> Any:
        return self.handler.headers

    def query_value(self, name: str, default: str = "") -> str:
        return str(self.query.get(name, [default])[0] or default)

    def request_server_url(self) -> str:
        return request_server_url(self.handler)

    def local_server_url(self) -> str:
        return local_server_url(self.handler.server.server_address)

    def uses_loopback_host(self) -> bool:
        host_name, _ = _split_authority_host_port(str(self.headers.get("Host") or ""))
        return host_name in _LOOPBACK_HOSTNAMES

    def is_local_operator(self) -> bool:
        return (
            _is_loopback_host(self.handler.server.server_address[0])
            and self.uses_loopback_host()
            and _origin_is_loopback_or_empty(self.headers.get("Origin"))
        )

    def send_json(self, payload: dict[str, object]) -> None:
        self.handler._send_json(payload)

    def send_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        code: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        if code:
            self.handler._send_error(status, message, code=code, details=details)
            return
        if details is None:
            self.handler._send_error(status, message)
            return
        self.handler._send_error(status, message, details=details)

    def read_json_body(
        self,
        *,
        coerce_non_object: bool = False,
        before_invalid_json_response: Callable[[], None] | None = None,
    ) -> dict[str, object] | None:
        """Parse the JSON request body; sends 400 and returns None when bad."""
        def reject_invalid_json() -> None:
            if before_invalid_json_response is not None:
                before_invalid_json_response()
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")

        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.handler.rfile.read(length).decode("utf-8")) if length else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            reject_invalid_json()
            return None
        if not isinstance(payload, dict) and coerce_non_object:
            return {}
        if not isinstance(payload, dict):
            reject_invalid_json()
            return None
        return payload

    def last_event_id(self) -> str | None:
        """Return the SSE resume cursor from the header or query string."""
        header_value = str(self.headers.get("Last-Event-ID") or "").strip()
        query_value = str(self.query.get("last_event_id", [""])[0] or "").strip()
        return header_value or query_value or None

    def send_sse_stream(
        self,
        event_name: str,
        stream: str,
        *,
        meeting_id: str | None = None,
        last_event_id: str | None = None,
    ) -> None:
        """Delegate a legacy SSE stream to the server transport."""
        self.handler._send_sse_stream(
            event_name,
            stream,
            meeting_id=meeting_id,
            last_event_id=last_event_id,
        )

    def send_room_events_sse_stream(self, *, room_id: str, cursor: str | None) -> bool:
        sender = getattr(self.handler, "_send_room_events_sse_stream", None)
        if not callable(sender):
            return False
        sender(room_id=room_id, cursor=cursor)
        return True

    def send_attachment_file(
        self,
        file_path: Path,
        metadata: dict[str, object],
        *,
        inline: bool,
    ) -> None:
        self.handler._send_attachment_file(file_path, metadata, inline=inline)

    # -- identity (R2-b): host / invited session / anonymous ---------------
    def bearer_token(self) -> str:
        auth = str(self.headers.get("Authorization") or "")
        if auth.startswith("Bearer "):
            return auth.removeprefix("Bearer ").strip()
        return ""

    def provided_host_token(self) -> str:
        token = str(self.headers.get("X-Host-Token") or "").strip()
        return token or self.bearer_token()

    def is_host(self) -> bool:
        """True when the caller presents the host credential (no response sent)."""
        return self.deps.public_invite.verify_host_token(self.provided_host_token())

    def require_host(self) -> bool:
        """Gate a moderation/admin endpoint; sends 403 when not the host."""
        if not self.is_host():
            self.send_error(HTTPStatus.FORBIDDEN, "host token required")
            return False
        return True

    def is_operator_session(self) -> bool:
        """True when the caller's invite session belongs to the operator account.

        This is what lets the host moderate from the public URL: their device
        token resolves to the operator user, so their guest session carries
        host-grade privileges without the raw host token leaving the machine.
        """
        session = self.session()
        return bool(
            session
            and self.deps.identities.participant_is_operator(
                str(session.get("agent_id") or "")
            )
        )

    def require_moderator(self) -> bool:
        """Gate moderation endpoints: host token OR operator session (403 otherwise)."""
        if self.is_host() or self.is_operator_session():
            return True
        self.send_error(HTTPStatus.FORBIDDEN, "host token or operator session required")
        return False

    def session(self) -> dict[str, object] | None:
        """The verified invite session for this request, or None (cached)."""
        if not self._session_resolved:
            self._session_resolved = True
            token = self.bearer_token()
            self._session = self.deps.sessions.verify(token) if token else None
        return self._session

    def preference_user_id(self) -> str:
        """Resolve the authenticated user that owns browser room preferences."""

        session = self.session()
        if session is not None:
            participant_id = str(session.get("agent_id") or "")
            user = self.deps.identities.user_for_participant(participant_id)
            return str((user or {}).get("user_id") or "")

        device_token = str(self.headers.get("X-Device-Token") or "").strip()
        auth_key = device_auth_key(device_token)
        if not auth_key:
            return ""
        user = self.deps.identities.resolve_credential_user(
            auth_key,
            provider="device",
            participant_type="human",
        )
        return str((user or {}).get("user_id") or "")

    def require_session(self) -> dict[str, object] | None:
        """Gate a guest endpoint; sends 401 when no valid session token."""
        token = self.bearer_token()
        if not token:
            self.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
            return None
        session = self.session()
        if not session:
            self.send_error(HTTPStatus.UNAUTHORIZED, "invalid or expired session")
            return None
        return session

    def require_posting_session(self, action: str = "post") -> dict[str, object] | None:
        """Gate a write endpoint; additionally rejects read-only invites (403)."""
        session = self.require_session()
        if session is None:
            return None
        if session.get("invite_scope") == "read_only":
            self.send_error(HTTPStatus.FORBIDDEN, f"read-only invite session cannot {action}")
            return None
        return session


RouteHandler = Callable[[RequestContext], None]
DynamicRouteHandler = Callable[[RequestContext, dict[str, str]], None]


class Router:
    """Exact-path route table for the GUI server (one handler per method+path)."""

    def __init__(self) -> None:
        self._routes: dict[str, dict[str, RouteHandler]] = {}
        self._dynamic_routes: dict[str, dict[str, DynamicRouteHandler]] = {}

    def add(self, method: str, path: str, handler: RouteHandler) -> None:
        method_routes = self._routes.setdefault(method.upper(), {})
        if path in method_routes:
            raise ValueError(f"duplicate route registration: {method} {path}")
        method_routes[path] = handler

    def _decorator(self, method: str, path: str) -> Callable[[RouteHandler], RouteHandler]:
        def register(handler: RouteHandler) -> RouteHandler:
            self.add(method, path, handler)
            return handler

        return register

    def get(self, path: str) -> Callable[[RouteHandler], RouteHandler]:
        return self._decorator("GET", path)

    def post(self, path: str) -> Callable[[RouteHandler], RouteHandler]:
        return self._decorator("POST", path)

    def delete(self, path: str) -> Callable[[RouteHandler], RouteHandler]:
        return self._decorator("DELETE", path)

    def add_dynamic(self, method: str, template: str, handler: DynamicRouteHandler) -> None:
        method_routes = self._dynamic_routes.setdefault(method.upper(), {})
        route_shape = _dynamic_route_shape(template)
        if any(_dynamic_route_shape(registered) == route_shape for registered in method_routes):
            raise ValueError(f"duplicate dynamic route registration: {method} {template}")
        method_routes[template] = handler

    def post_dynamic(self, template: str) -> Callable[[DynamicRouteHandler], DynamicRouteHandler]:
        def register(handler: DynamicRouteHandler) -> DynamicRouteHandler:
            self.add_dynamic("POST", template, handler)
            return handler

        return register

    def get_dynamic(self, template: str) -> Callable[[DynamicRouteHandler], DynamicRouteHandler]:
        def register(handler: DynamicRouteHandler) -> DynamicRouteHandler:
            self.add_dynamic("GET", template, handler)
            return handler

        return register

    def routes(self) -> list[tuple[str, str]]:
        return [
            (method, path)
            for method, method_routes in sorted(self._routes.items())
            for path in sorted(method_routes)
        ]

    def dynamic_routes(self) -> list[tuple[str, str]]:
        return [
            (method, template)
            for method, method_routes in sorted(self._dynamic_routes.items())
            for template in sorted(method_routes)
        ]

    def dispatch(self, method: str, ctx: RequestContext) -> bool:
        """Run the registered handler; False when no route matches (legacy fallback)."""
        handler = self._routes.get(method.upper(), {}).get(ctx.path)
        if handler is not None:
            handler(ctx)
            return True
        for template, dynamic_handler in self._dynamic_routes.get(method.upper(), {}).items():
            path_params = match_route_template(template, ctx.path)
            if path_params is not None:
                dynamic_handler(ctx, path_params)
                return True
        return False


def request_server_url(handler: Any) -> str:
    host = handler.headers.get("Host")
    if host:
        return f"http://{host}"
    address = handler.server.server_address
    return f"http://{address[0]}:{address[1]}"


def local_server_url(server_address: tuple[object, ...]) -> str:
    host, port = server_address[:2]
    host = str(host)
    if host in {"", "0.0.0.0"}:
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def match_route_template(template: str, path: str) -> dict[str, str] | None:
    template_parts = template.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(template_parts) != len(path_parts):
        return None
    values: dict[str, str] = {}
    for expected, actual in zip(template_parts, path_parts, strict=True):
        if expected.startswith("{") and expected.endswith("}"):
            decoded = unquote(actual)
            if not _valid_dynamic_route_value(decoded):
                return None
            values[expected[1:-1]] = decoded
        elif expected != actual:
            return None
    return values


def _dynamic_route_shape(template: str) -> tuple[str, ...]:
    return tuple(
        "{}" if part.startswith("{") and part.endswith("}") else part
        for part in template.strip("/").split("/")
    )


def _valid_dynamic_route_value(value: str) -> bool:
    return bool(value) and not (
        "/" in value
        or "\\" in value
        or value in {".", ".."}
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or len(value) > 256
    )
