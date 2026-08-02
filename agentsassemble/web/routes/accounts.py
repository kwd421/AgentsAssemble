"""HTTP account status and Google identity connection routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.identity.accounts import AccountLinkConflict
from agentsassemble.identity.google import GoogleAccountLoginService, GoogleLoginRejected
from agentsassemble.identity.repository import device_auth_key
from agentsassemble.web.router import RequestContext, Router


def register_account_routes(
    router: Router,
    *,
    google: GoogleAccountLoginService,
) -> None:
    @router.get("/api/account")
    def account_status(ctx: RequestContext) -> None:
        ctx.send_json(google.configuration(ctx.deps.identities, ctx.authenticated_user()))

    @router.post("/api/account/google")
    def connect_google_account(ctx: RequestContext) -> None:
        local_request = ctx.is_local_operator() and ctx.peer_is_loopback()
        if not local_request and (
            str(ctx.headers.get("X-Forwarded-Proto") or "").lower() != "https"
        ):
            ctx.send_error(HTTPStatus.FORBIDDEN, "HTTPS is required for account login")
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            result = google.connect(
                ctx.deps.identities,
                current_user=ctx.authenticated_user(),
                device_auth_key=device_auth_key(
                    str(ctx.headers.get("X-Device-Token") or "")
                ),
                credential=payload.get("credential"),
                nonce=payload.get("nonce"),
            )
        except AccountLinkConflict as error:
            ctx.send_error(HTTPStatus.CONFLICT, str(error), code=error.code)
            return
        except GoogleLoginRejected as error:
            ctx.send_error(HTTPStatus.FORBIDDEN, str(error), code=error.code)
            return
        ctx.send_json(result)


__all__ = ["register_account_routes"]
