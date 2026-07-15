"""Public invite host-token, URL, and tunnel administration routes."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Protocol

from agentsassemble.gui_router import RequestContext, Router


class PublicTunnelControl(Protocol):
    def set_local_url(self, local_url: str) -> None: ...

    def status(self) -> dict[str, object]: ...

    def start(self) -> dict[str, object]: ...

    def stop(self) -> dict[str, object]: ...


def register_public_invite_admin_routes(
    router: Router,
    *,
    tunnel: PublicTunnelControl,
    is_local_operator: Callable[[RequestContext], bool],
    local_server_url: Callable[[RequestContext], str],
) -> None:
    """Register local-operator controls for making room invites public."""

    def status_payload(
        ctx: RequestContext,
        tunnel_status: dict[str, object] | None = None,
    ) -> dict[str, object]:
        current_tunnel = tunnel_status or tunnel.status()
        runtime = ctx.deps.public_invite
        token_configured = bool(runtime.host_token())
        return {
            "host_token_configured": token_configured,
            "host_gate_required": runtime.host_gate_required(),
            "public_url": runtime.public_url(),
            "tunnel": current_tunnel,
            "can_generate_host_token": (
                (not token_configured and not bool(runtime.public_url()))
                or (runtime.has_runtime_host_token() and is_local_operator(ctx))
            ),
        }

    @router.get("/api/public-invite/status")
    def public_invite_status(ctx: RequestContext) -> None:
        ctx.send_json(status_payload(ctx))

    @router.post("/api/public-invite/host-token")
    def public_invite_host_token(ctx: RequestContext) -> None:
        runtime = ctx.deps.public_invite
        if runtime.host_token():
            if not runtime.verify_host_token(ctx.provided_host_token()):
                if runtime.has_runtime_host_token() and is_local_operator(ctx):
                    token = runtime.generate_host_token()
                    ctx.send_json(
                        {
                            "status": "regenerated",
                            "host_token": token,
                            "host_token_configured": True,
                            "public_invite": status_payload(ctx),
                        }
                    )
                    return
                ctx.send_error(HTTPStatus.FORBIDDEN, "host token required")
                return
            ctx.send_json({"status": "already_configured", "host_token_configured": True})
            return
        if not is_local_operator(ctx):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "host token can only be generated from the local operator UI",
            )
            return
        if runtime.public_url():
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "host token must be configured before public URL mode",
            )
            return
        token = runtime.generate_host_token()
        ctx.send_json(
            {
                "status": "generated",
                "host_token": token,
                "host_token_configured": True,
                "public_invite": status_payload(ctx),
            }
        )

    @router.post("/api/public-invite/public-url")
    def public_invite_public_url(ctx: RequestContext) -> None:
        runtime = ctx.deps.public_invite
        if not runtime.host_token():
            ctx.send_error(HTTPStatus.FORBIDDEN, "host token must be configured before public URL")
            return
        if not ctx.require_host():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            public_url = runtime.set_public_url(str(payload.get("public_url") or ""))
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "status": "configured",
                "public_url": public_url,
                "public_invite": status_payload(ctx),
            }
        )

    @router.post("/api/public-invite/tunnel/start")
    def public_invite_tunnel_start(ctx: RequestContext) -> None:
        runtime = ctx.deps.public_invite
        generated_host_token = ""
        if not runtime.host_token():
            if not is_local_operator(ctx):
                ctx.send_error(
                    HTTPStatus.FORBIDDEN,
                    "host token must be configured before starting a public tunnel",
                )
                return
            generated_host_token = runtime.generate_host_token()
        if not generated_host_token and not ctx.require_host():
            return
        tunnel.set_local_url(local_server_url(ctx))
        payload: dict[str, object] = {
            "status": "ok",
            "public_invite": status_payload(ctx, tunnel.start()),
        }
        if generated_host_token:
            payload["host_token"] = generated_host_token
        ctx.send_json(payload)

    @router.post("/api/public-invite/tunnel/stop")
    def public_invite_tunnel_stop(ctx: RequestContext) -> None:
        if not ctx.require_host():
            return
        ctx.send_json(
            {
                "status": "ok",
                "public_invite": status_payload(ctx, tunnel.stop()),
            }
        )
