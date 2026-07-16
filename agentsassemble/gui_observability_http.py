"""Read-only local resource and release-health HTTP routes."""
from __future__ import annotations

from typing import Protocol

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy.admission_projection import LegacyAdmissionProjection
from agentsassemble.local_resources import cached_local_resource_snapshot
from agentsassemble.release_health import release_health_catalog_payload, release_health_queue_payload


class ProcessSnapshotSource(Protocol):
    def snapshot_groups(self) -> list[dict[str, object]]: ...


def _supervised_pids(processes: ProcessSnapshotSource) -> set[int]:
    try:
        groups = processes.snapshot_groups()
    except Exception:
        return set()
    pids: set[int] = set()
    for group in groups:
        if not isinstance(group, dict) or str(group.get("status") or "") not in {"running", "restarting"}:
            continue
        try:
            pid = int(group.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            pids.add(pid)
    return pids


def register_observability_routes(
    router: Router,
    *,
    processes: ProcessSnapshotSource,
    admission_projection: LegacyAdmissionProjection,
) -> None:
    """Register safe, read-only host observability projections."""

    @router.get("/api/local-resources")
    def local_resources(ctx: RequestContext) -> None:
        ctx.send_json(cached_local_resource_snapshot(supervised_pids=_supervised_pids(processes)))

    @router.get("/api/release-health")
    def release_health(ctx: RequestContext) -> None:
        ctx.send_json(release_health_catalog_payload())

    @router.get("/api/release-health/queue")
    def release_health_queue(ctx: RequestContext) -> None:
        ctx.send_json(release_health_queue_payload(output_root=ctx.deps.output_root))

    @router.get("/api/diagnostics/legacy-admission-projection")
    def legacy_admission_projection(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        ctx.send_json(admission_projection.diagnostics())
