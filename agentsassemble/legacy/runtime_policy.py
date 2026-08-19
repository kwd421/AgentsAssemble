"""Runtime quarantine policy for retained legacy HTTP and CLI surfaces."""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

from agentsassemble.web.router import (
    DynamicRouteHandler,
    RouteHandler,
    Router,
)

UNSAFE_LEGACY_MUTATIONS_ENV = "AGENTSASSEMBLE_UNSAFE_ENABLE_LEGACY_MUTATIONS"
RETAINED_LEGACY_CLI_COMMANDS = frozenset(
    {
        "demo",
        "lobby",
        "live-agent",
        "memory-capsule",
        "mcp",
        "sessions",
    }
)
_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_HandlerT = TypeVar("_HandlerT", RouteHandler, DynamicRouteHandler)


def unsafe_legacy_mutations_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the emergency legacy mutation escape hatch is enabled.

    The opt-in intentionally accepts only the exact value ``1``. It exists for
    short-lived rollback and migration work; normal operation must leave it
    unset.
    """

    source = os.environ if environ is None else environ
    return str(source.get(UNSAFE_LEGACY_MUTATIONS_ENV, "")) == "1"


def legacy_cli_command_quarantined(
    command: object,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether a retained legacy top-level CLI command is disabled."""

    normalized = str(command or "").strip()
    return (
        normalized in RETAINED_LEGACY_CLI_COMMANDS
        and not unsafe_legacy_mutations_enabled(environ)
    )


def legacy_cli_quarantine_message(command: object) -> str:
    """Return the operator-facing explanation for a quarantined CLI command."""

    normalized = str(command or "").strip() or "<unknown>"
    return (
        f"Legacy CLI command '{normalized}' is disabled by default. "
        f"Set {UNSAFE_LEGACY_MUTATIONS_ENV}=1 only for short-lived isolated "
        "migration or rollback work."
    )


class LegacyRoutePolicyRouter:
    """Registration proxy that keeps legacy reads and drops legacy mutations."""

    def __init__(
        self,
        delegate: Router,
        *,
        allow_mutations: bool,
    ) -> None:
        self._delegate = delegate
        self._allow_mutations = allow_mutations
        self._blocked_routes: list[tuple[str, str]] = []

    def _method_allowed(self, method: str) -> bool:
        return self._allow_mutations or method.upper() in _READ_ONLY_METHODS

    def add(self, method: str, path: str, handler: RouteHandler) -> None:
        if self._method_allowed(method):
            self._delegate.add(method, path, handler)
            return
        self._blocked_routes.append((method.upper(), path))

    def add_dynamic(
        self,
        method: str,
        template: str,
        handler: DynamicRouteHandler,
    ) -> None:
        if self._method_allowed(method):
            self._delegate.add_dynamic(method, template, handler)
            return
        self._blocked_routes.append((method.upper(), template))

    def _decorator(
        self,
        method: str,
        path: str,
        *,
        dynamic: bool = False,
    ) -> Callable[[_HandlerT], _HandlerT]:
        def register(handler: _HandlerT) -> _HandlerT:
            if dynamic:
                self.add_dynamic(
                    method,
                    path,
                    cast(DynamicRouteHandler, handler),
                )
            else:
                self.add(method, path, cast(RouteHandler, handler))
            return handler

        return register

    def get(self, path: str) -> Callable[[RouteHandler], RouteHandler]:
        return self._decorator("GET", path)

    def post(self, path: str) -> Callable[[RouteHandler], RouteHandler]:
        return self._decorator("POST", path)

    def delete(self, path: str) -> Callable[[RouteHandler], RouteHandler]:
        return self._decorator("DELETE", path)

    def get_dynamic(
        self,
        template: str,
    ) -> Callable[[DynamicRouteHandler], DynamicRouteHandler]:
        return self._decorator("GET", template, dynamic=True)

    def post_dynamic(
        self,
        template: str,
    ) -> Callable[[DynamicRouteHandler], DynamicRouteHandler]:
        return self._decorator("POST", template, dynamic=True)

    def blocked_routes(self) -> list[tuple[str, str]]:
        return list(self._blocked_routes)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def quarantined_legacy_router(
    router: Router,
    *,
    environ: Mapping[str, str] | None = None,
) -> Router:
    """Return a Router-compatible legacy registration surface.

    Legacy GET routes remain available for migration and historical viewing.
    Mutation and process-control routes are not registered unless the explicit
    unsafe rollback environment variable is set.
    """

    return cast(
        Router,
        LegacyRoutePolicyRouter(
            router,
            allow_mutations=unsafe_legacy_mutations_enabled(environ),
        ),
    )
