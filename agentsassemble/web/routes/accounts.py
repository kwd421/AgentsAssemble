"""HTTP account status and Google identity connection routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.application.account_switch import ConfirmedGuestAccountSwitchService
from agentsassemble.identity.accounts import AccountLinkConflict
from agentsassemble.identity.google import GoogleAccountLoginService, GoogleLoginRejected
from agentsassemble.identity.repository import device_auth_key
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.web.security import _request_uses_trusted_public_https_proxy


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
        if not _account_login_transport_allowed(ctx):
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
                discard_guest_on_account_switch=(
                    payload.get("discard_guest_on_account_switch") is True
                ),
                switch_guest=lambda current, target, auth_key, switched_at: (
                    _account_switcher(ctx).switch(
                        current,
                        target,
                        auth_key,
                        switched_at,
                    )
                ),
            )
        except AccountLinkConflict as error:
            ctx.send_error(HTTPStatus.CONFLICT, str(error), code=error.code)
            return
        except GoogleLoginRejected as error:
            ctx.send_error(HTTPStatus.FORBIDDEN, str(error), code=error.code)
            return
        ctx.send_json(result)

    @router.post("/api/account/google/challenge")
    def start_google_account_login(ctx: RequestContext) -> None:
        if not _account_login_transport_allowed(ctx):
            return
        try:
            result = google.start_direct_login(
                current_user=ctx.authenticated_user(),
                device_auth_key=device_auth_key(
                    str(ctx.headers.get("X-Device-Token") or "")
                ),
            )
        except GoogleLoginRejected as error:
            ctx.send_error(HTTPStatus.FORBIDDEN, str(error), code=error.code)
            return
        ctx.send_json(result)

    @router.delete("/api/account/google")
    def disconnect_google_account(ctx: RequestContext) -> None:
        if not _account_login_transport_allowed(ctx):
            return
        try:
            result = google.disconnect(
                ctx.deps.identities,
                current_user=ctx.authenticated_user(),
            )
        except GoogleLoginRejected as error:
            ctx.send_error(HTTPStatus.FORBIDDEN, str(error), code=error.code)
            return
        ctx.send_json(result)

    @router.post("/api/account/google/handoff/start")
    def start_google_account_handoff(ctx: RequestContext) -> None:
        if not _account_login_transport_allowed(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            result = google.start_handoff(
                current_user=ctx.authenticated_user(),
                device_auth_key=device_auth_key(
                    str(ctx.headers.get("X-Device-Token") or "")
                ),
                discard_guest_on_account_switch=(
                    payload.get("discard_guest_on_account_switch") is True
                ),
            )
        except GoogleLoginRejected as error:
            ctx.send_error(HTTPStatus.FORBIDDEN, str(error), code=error.code)
            return
        ctx.send_json(result)

    @router.post("/api/account/google/handoff/configure")
    def configure_google_account_handoff(ctx: RequestContext) -> None:
        if not _account_login_transport_allowed(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            result = google.handoff_configuration(payload.get("token"))
        except GoogleLoginRejected as error:
            ctx.send_error(HTTPStatus.FORBIDDEN, str(error), code=error.code)
            return
        ctx.send_json(result)

    @router.post("/api/account/google/handoff/complete")
    def complete_google_account_handoff(ctx: RequestContext) -> None:
        if not _account_login_transport_allowed(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            result = google.connect_handoff(
                ctx.deps.identities,
                token=payload.get("token"),
                credential=payload.get("credential"),
                switch_guest=lambda current, target, auth_key, switched_at: (
                    _account_switcher(ctx).switch(
                        current,
                        target,
                        auth_key,
                        switched_at,
                    )
                ),
            )
        except AccountLinkConflict as error:
            ctx.send_error(HTTPStatus.CONFLICT, str(error), code=error.code)
            return
        except GoogleLoginRejected as error:
            ctx.send_error(HTTPStatus.FORBIDDEN, str(error), code=error.code)
            return
        ctx.send_json(result)


def _account_login_transport_allowed(ctx: RequestContext) -> bool:
    local_request = ctx.is_local_operator() and ctx.peer_is_loopback()
    if local_request:
        return True
    client_address = getattr(ctx.handler, "client_address", ())
    peer_host = client_address[0] if isinstance(client_address, tuple) and client_address else ""
    public_https_request = _request_uses_trusted_public_https_proxy(
        peer_host=peer_host,
        host_header=ctx.headers.get("Host"),
        forwarded_proto=ctx.headers.get("X-Forwarded-Proto"),
        public_url=ctx.deps.invites.public_url(),
    )
    if public_https_request:
        return True
    ctx.send_error(HTTPStatus.FORBIDDEN, "HTTPS is required for account login")
    return False


def _account_switcher(ctx: RequestContext) -> ConfirmedGuestAccountSwitchService:
    return ConfirmedGuestAccountSwitchService(
        identities=ctx.deps.identities,
        sessions=ctx.deps.sessions,
        handle_room_command=ctx.deps.handle_room_command,
    )


__all__ = ["register_account_routes"]
