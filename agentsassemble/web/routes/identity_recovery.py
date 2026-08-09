"""HTTP boundary for issuing and redeeming guest recovery codes."""
from __future__ import annotations

import hashlib
import ipaddress
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from urllib.parse import quote, urlencode

from agentsassemble.identity.recovery import (
    GuestIdentityRecoveryService,
    normalize_recovery_code,
)
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.web.security import TRUSTED_PROXY_CLIENT_IP_HEADER


class _RecoveryAttemptLimiter:
    def __init__(
        self,
        *,
        attempts: int = 8,
        window_seconds: float = 60.0,
        max_keys: int = 4096,
    ) -> None:
        self._attempts = attempts
        self._window_seconds = window_seconds
        self._max_keys = max(1, int(max_keys))
        self._seen: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allows(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if key not in self._seen and len(self._seen) >= self._max_keys:
                for known_key in list(self._seen):
                    seen = self._seen[known_key]
                    while seen and seen[0] <= now - self._window_seconds:
                        seen.popleft()
                    if not seen:
                        self._seen.pop(known_key, None)
                if len(self._seen) >= self._max_keys:
                    return False
            seen = self._seen[key]
            while seen and seen[0] <= now - self._window_seconds:
                seen.popleft()
            if len(seen) >= self._attempts:
                return False
            seen.append(now)
            return True


def register_identity_recovery_routes(router: Router) -> None:
    global_limiter = _RecoveryAttemptLimiter(attempts=256, max_keys=1)
    network_limiter = _RecoveryAttemptLimiter(attempts=16, max_keys=512)
    credential_limiter = _RecoveryAttemptLimiter(attempts=8, max_keys=512)

    def recovery(ctx: RequestContext) -> GuestIdentityRecoveryService:
        return GuestIdentityRecoveryService(
            identities=ctx.deps.identities,
            rooms=ctx.deps.rooms,
            sessions=ctx.deps.sessions,
        )

    @router.post("/api/identity/recovery-code")
    def issue_recovery_code(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        user = ctx.authenticated_user()
        if user is None:
            ctx.send_error(HTTPStatus.UNAUTHORIZED, "authenticated identity required")
            return
        room_id = str(payload.get("room_id") or (ctx.session() or {}).get("meeting_id") or "").strip()
        if room_id and not ctx.require_room_access(room_id):
            return
        code = recovery(ctx).issue(str(user.get("user_id") or ""))
        query = urlencode({"recover": "1", "room": room_id})
        recovery_base = ctx.deps.invites.public_url() or ctx.request_server_url()
        recovery_url = f"{recovery_base.rstrip('/')}/?{query}#recovery={quote(code)}"
        ctx.send_json(
            {
                "status": "issued",
                "server_id": ctx.deps.identities.server_id(),
                "room_id": room_id,
                "recovery_code": code,
                "recovery_url": recovery_url,
            }
        )

    @router.post("/api/identity/recovery-code/redeem")
    def redeem_recovery_code(ctx: RequestContext) -> None:
        if not _recovery_transport_is_secure(ctx):
            ctx.send_error(HTTPStatus.FORBIDDEN, "HTTPS is required for identity recovery")
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        network_key = _recovery_network_key(ctx)
        raw_code = str(payload.get("recovery_code") or "")
        normalized_code = normalize_recovery_code(raw_code)
        credential_material = normalized_code or raw_code.strip().upper()[:256]
        credential_key = hashlib.sha256(credential_material.encode("utf-8")).hexdigest()
        if (
            not global_limiter.allows("all")
            or not network_limiter.allows(network_key)
            or not credential_limiter.allows(credential_key)
        ):
            ctx.send_error(HTTPStatus.TOO_MANY_REQUESTS, "too many recovery attempts")
            return
        try:
            result = recovery(ctx).redeem(
                recovery_code=str(payload.get("recovery_code") or ""),
                room_id=str(payload.get("room_id") or ""),
                device_token=str(payload.get("device_token") or ""),
                client_id=str(payload.get("client_id") or ""),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.CONFLICT, str(error), code="recovery_device_conflict")
            return
        if result.get("status") != "recovered":
            reason = str(result.get("reason") or "recovery_invalid")
            ctx.send_error(HTTPStatus.FORBIDDEN, reason, code=reason)
            return
        ctx.send_json(result)


def _recovery_network_key(ctx: RequestContext) -> str:
    client_address = getattr(ctx.handler, "client_address", ())
    peer = str(client_address[0] if isinstance(client_address, tuple) and client_address else "unknown")
    ingress_kind = ctx.trusted_public_https_ingress_kind()
    if ingress_kind in {"cloudflare", "authenticated_proxy"}:
        header_name = (
            "CF-Connecting-IP"
            if ingress_kind == "cloudflare"
            else TRUSTED_PROXY_CLIENT_IP_HEADER
        )
        candidate = str(ctx.headers.get(header_name) or "").strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return "unknown"


def _recovery_transport_is_secure(ctx: RequestContext) -> bool:
    return ctx.is_local_operator() or _recovery_uses_trusted_public_proxy(ctx)


def _recovery_uses_trusted_public_proxy(ctx: RequestContext) -> bool:
    return bool(ctx.trusted_public_https_ingress_kind())


__all__ = ["register_identity_recovery_routes"]
