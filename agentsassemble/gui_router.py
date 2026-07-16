"""Compatibility exports for GUI HTTP routing and request context."""
from agentsassemble.web.router import (
    DynamicRouteHandler,
    GuiDeps,
    RequestContext,
    RouteHandler,
    Router,
    local_server_url,
    match_route_template,
    request_server_url,
)

__all__ = [
    "DynamicRouteHandler",
    "GuiDeps",
    "RequestContext",
    "RouteHandler",
    "Router",
    "local_server_url",
    "match_route_template",
    "request_server_url",
]
