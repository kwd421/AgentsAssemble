"""Loopback-only browser return routes for central desktop login."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.application.central_login_callback import CentralLoginCallbackBroker
from agentsassemble.web.router import RequestContext, Router


CALLBACK_PATH = "/api/central-login/callback"

_SUCCESS_PAGE = """<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>AgentsAssemble 로그인 완료</title>
    <style>
      body { margin: 0; min-height: 100vh; display: grid; place-items: center;
        padding: 24px; box-sizing: border-box; background: #101114; color: #f2f3f5;
        font: 15px system-ui, sans-serif; }
      main { width: min(380px, 100%); padding: 30px; box-sizing: border-box;
        border: 1px solid #ffffff18; border-radius: 16px; background: #202126;
        box-shadow: 0 24px 70px #0008; }
      h1 { margin: 0 0 10px; font-size: 24px; }
      p { margin: 0; color: #b5bac1; line-height: 1.55; }
    </style>
  </head>
  <body><main><h1>로그인이 완료되었습니다</h1><p>AgentsAssemble 앱으로 돌아가세요. 이 탭은 닫아도 됩니다.</p></main></body>
</html>"""

_CANCELLED_PAGE = _SUCCESS_PAGE.replace(
    "로그인이 완료되었습니다",
    "Google 로그인을 취소했습니다",
).replace(
    "AgentsAssemble 앱으로 돌아가세요. 이 탭은 닫아도 됩니다.",
    "AgentsAssemble 앱으로 돌아가 다시 시도할 수 있습니다. 이 탭은 닫아도 됩니다.",
)


def register_central_login_callback_routes(
    router: Router,
    *,
    broker: CentralLoginCallbackBroker | None = None,
) -> None:
    callbacks = broker or CentralLoginCallbackBroker()

    def require_local_operator(ctx: RequestContext) -> bool:
        if ctx.is_local_operator():
            return True
        ctx.send_error(
            HTTPStatus.FORBIDDEN,
            "Central login callbacks are available only on the local app.",
            code="local_operator_required",
        )
        return False

    @router.post("/api/central-login/callback/start")
    def start_callback(ctx: RequestContext) -> None:
        if not require_local_operator(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            expires_at = callbacks.expect(payload.get("state"))
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "redirect_uri": f"{ctx.local_server_url()}{CALLBACK_PATH}",
                "expires_at": int(expires_at),
            }
        )

    @router.get("/api/central-login/callback")
    def complete_callback(ctx: RequestContext) -> None:
        if not require_local_operator(ctx):
            return
        try:
            callbacks.complete(
                ctx.query_value("state"),
                ctx.query_value("code"),
                ctx.query_value("error"),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except LookupError as error:
            ctx.send_error(HTTPStatus.GONE, str(error))
            return
        ctx.handler.send_response(HTTPStatus.SEE_OTHER)
        location = (
            "/central-login-complete?status=cancelled"
            if ctx.query_value("error")
            else "/central-login-complete"
        )
        ctx.handler.send_header("Location", location)
        ctx.handler.send_header("Cache-Control", "no-store")
        ctx.handler.send_header("Referrer-Policy", "no-referrer")
        ctx.handler.send_header("Content-Length", "0")
        ctx.handler.end_headers()

    @router.get("/central-login-complete")
    def callback_success(ctx: RequestContext) -> None:
        if not require_local_operator(ctx):
            return
        page = (
            _CANCELLED_PAGE
            if ctx.query_value("status") == "cancelled"
            else _SUCCESS_PAGE
        )
        ctx.handler._send_bytes(
            page.encode("utf-8"),
            "text/html; charset=utf-8",
            cache_control="no-store",
            referrer_policy="no-referrer",
        )

    @router.post("/api/central-login/callback/poll")
    def poll_callback(ctx: RequestContext) -> None:
        if not require_local_operator(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            completed = callbacks.poll(payload.get("state"))
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except LookupError as error:
            ctx.send_error(HTTPStatus.GONE, str(error))
            return
        if completed is None:
            ctx.send_json({"status": "pending"})
            return
        if completed.error:
            ctx.send_json({"status": "error", "error": "google_login_cancelled"})
            return
        ctx.send_json(
            {
                "status": "complete",
                "authorization_code": completed.authorization_code,
            }
        )


__all__ = ["CALLBACK_PATH", "register_central_login_callback_routes"]
