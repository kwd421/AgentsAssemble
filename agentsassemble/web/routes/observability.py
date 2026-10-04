"""Read-only local resource and release-health HTTP routes."""
from __future__ import annotations

from agentsassemble.diagnostics.local_resources import cached_local_resource_snapshot
from agentsassemble.diagnostics.release_health import (
    release_health_catalog_payload,
    release_health_queue_payload,
)
from agentsassemble.web.router import RequestContext, Router


def register_observability_routes(router: Router) -> None:
    """Register safe, read-only host observability projections."""

    @router.get("/api/local-resources")
    def local_resources(ctx: RequestContext) -> None:
        ctx.send_json(
            cached_local_resource_snapshot(supervised_pids=set())
        )

    @router.get("/api/release-health")
    def release_health(ctx: RequestContext) -> None:
        ctx.send_json(release_health_catalog_payload())

    @router.get("/api/release-health/queue")
    def release_health_queue(ctx: RequestContext) -> None:
        ctx.send_json(
            release_health_queue_payload(output_root=ctx.deps.output_root)
        )

__all__ = ["register_observability_routes"]
