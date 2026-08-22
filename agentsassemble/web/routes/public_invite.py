"""Public invite host-token, URL, and tunnel administration routes."""
from __future__ import annotations

import os
import secrets
import time
from collections.abc import Callable
from http import HTTPStatus
from typing import Protocol

from agentsassemble.application.central_directory_host import (
    CENTRAL_URL_ENV,
    HostIdentity,
)
from agentsassemble.application.server_identity_challenge import (
    signed_server_identity_challenge,
)
from agentsassemble.web.router import RequestContext, Router


class PublicTunnelControl(Protocol):
    def set_local_url(self, local_url: str) -> None: ...

    def status(self) -> dict[str, object]: ...

    def start(self) -> dict[str, object]: ...

    def stop(self) -> dict[str, object]: ...

    def set_manual_public_url(self, public_url: str) -> str: ...


def register_public_invite_admin_routes(
    router: Router,
    *,
    tunnel: PublicTunnelControl,
    is_local_operator: Callable[[RequestContext], bool],
    local_server_url: Callable[[RequestContext], str],
) -> None:
    """Register public identity metadata and local public-invite controls."""

    def host_identity(ctx: RequestContext) -> HostIdentity:
        return HostIdentity(
            output_root=ctx.deps.output_root,
            server_id=ctx.deps.identities.server_id(),
        )

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

    @router.get("/api/server-info")
    def public_server_info(ctx: RequestContext) -> None:
        ctx.send_json(
            host_identity(ctx).server_info(
                central_status={"enabled": bool(os.environ.get(CENTRAL_URL_ENV))}
            )
        )

    @router.post("/api/server-info/challenge")
    def public_server_info_challenge(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            proof = signed_server_identity_challenge(
                host_identity(ctx),
                payload.get("challenge"),
                origin=(
                    ctx.deps.public_invite.public_url()
                    or ctx.request_server_url()
                ),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(proof)

    @router.post("/api/central-directory/registration-proof")
    def central_directory_registration_proof(ctx: RequestContext) -> None:
        if not is_local_operator(ctx):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "server registration proof is available only to the local operator",
                code="local_operator_required",
            )
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        owner_person_id = str(payload.get("owner_person_id") or "").strip()
        if (
            len(owner_person_id) < 8
            or len(owner_person_id) > 128
            or not all(
                character.isalnum() or character in "._:-"
                for character in owner_person_id
            )
        ):
            ctx.send_error(HTTPStatus.BAD_REQUEST, "owner_person_id is invalid")
            return
        identity = host_identity(ctx)
        issued_at = int(time.time())
        nonce = secrets.token_urlsafe(18)
        canonical = "\n".join(
            [
                "AA-HOST-REGISTER-1",
                identity.server_id,
                owner_person_id,
                str(issued_at),
                nonce,
            ]
        ).encode()
        ctx.send_json(
            {
                "server_id": identity.server_id,
                "host_public_key_jwk": identity.public_jwk(),
                "host_key_fingerprint": identity.fingerprint(),
                "host_registration_proof": {
                    "owner_person_id": owner_person_id,
                    "issued_at": issued_at,
                    "nonce": nonce,
                    "signature": identity.sign(canonical),
                },
            }
        )

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
                code="local_operator_required",
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
        requested_public_url = str(payload.get("public_url") or "").strip()
        if not requested_public_url:
            tunnel.set_manual_public_url("")
            ctx.send_json(
                {
                    "status": "cleared",
                    "public_url": "",
                    "public_invite": status_payload(ctx),
                }
            )
            return
        try:
            public_url = tunnel.set_manual_public_url(requested_public_url)
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
