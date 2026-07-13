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

from agentsassemble.room_invite import verify_host_token, verify_session_token
from agentsassemble.room_users import participant_is_operator


@dataclass
class GuiDeps:
    """Server-scoped dependencies handed to route handlers.

    The lobby IO callables are still owned by gui.py; they ride along here so
    domain modules don't need circular imports. This shrinks as those helpers
    move into proper homes during the DB migration.
    """

    output_root: Path
    process_supervisor: Any = None
    read_lobby: Callable[..., list[dict[str, object]]] | None = None
    read_lobby_before: Callable[..., dict[str, object]] | None = None
    append_lobby_event: Callable[..., dict[str, object]] | None = None
    lobby_payload_with_attachments: Callable[..., dict[str, object]] | None = None
    public_lobby_allows_room_scope: Callable[[dict[str, object]], bool] | None = None
    history_page_limit: Callable[[dict[str, list[str]]], int] | None = None


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

    def read_json_body(self, *, coerce_non_object: bool = False) -> dict[str, object] | None:
        """Parse the JSON request body; sends 400 and returns None when bad."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.handler.rfile.read(length).decode("utf-8")) if length else {}
        except json.JSONDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
            return None
        if not isinstance(payload, dict) and coerce_non_object:
            return {}
        if not isinstance(payload, dict):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
            return None
        return payload

    def last_event_id(self) -> str | None:
        """Return the SSE resume cursor from the header or query string."""
        return self.handler._last_event_id(self.query)

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
        return verify_host_token(self.provided_host_token())

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
        return bool(session and participant_is_operator(str(session.get("agent_id") or "")))

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
            self._session = verify_session_token(token) if token else None
        return self._session

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
