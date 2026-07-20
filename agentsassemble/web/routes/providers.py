"""Provider catalog, local login, and credential HTTP routes."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from http import HTTPStatus
from typing import Protocol

from agentsassemble.providers.adapters import default_provider_registry
from agentsassemble.providers import catalog as provider_catalog
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.providers.secrets import PROVIDER_SECRETS


class ProviderSecretStore(Protocol):
    def status(self, provider_id: str) -> Mapping[str, object]: ...

    def set(self, provider_id: str, value: str) -> Mapping[str, object]: ...

    def delete(self, provider_id: str) -> Mapping[str, object]: ...


class ProviderLogin(Protocol):
    def start(self, payload: dict[str, object]) -> dict[str, object]: ...

    def record_invalid_json(self) -> None: ...


def provider_catalog_payload() -> dict[str, object]:
    return {"providers": default_provider_registry().catalog()}


def model_catalog_payload() -> dict[str, object]:
    """Return the static API-provider model catalog without exposing keys."""
    return provider_catalog.catalog_payload()


def _safe_status_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Keep provider secret responses to the store's public status fields."""
    return {
        "configured": bool(payload.get("configured")),
        "source": str(payload.get("source") or "missing"),
    }


def register_provider_routes(
    router: Router,
    *,
    credentials_allowed: Callable[[RequestContext], bool],
    is_local_operator: Callable[[RequestContext], bool],
    login_service: ProviderLogin,
    secret_store: ProviderSecretStore | None = None,
) -> None:
    """Register provider discovery, login, and credential-management routes."""
    store = PROVIDER_SECRETS if secret_store is None else secret_store

    def _send_store_status(ctx: RequestContext, operation: Callable[[], Mapping[str, object]]) -> None:
        ctx.send_json(_safe_status_payload(operation()))

    @router.get("/api/providers")
    def providers(ctx: RequestContext) -> None:
        ctx.send_json(provider_catalog_payload())

    @router.get("/api/model-catalog")
    def model_catalog(ctx: RequestContext) -> None:
        ctx.send_json(model_catalog_payload())

    @router.post("/api/live-agent-create/login")
    def provider_login(ctx: RequestContext) -> None:
        if not is_local_operator(ctx):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "provider login can only be started from the local operator UI",
            )
            return
        payload = ctx.read_json_body()
        if payload is None:
            login_service.record_invalid_json()
            return
        try:
            result = login_service.start(payload)
        except (OSError, ValueError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)

    @router.get("/api/provider-credentials/deepseek")
    def provider_credentials_status(ctx: RequestContext) -> None:
        if not credentials_allowed(ctx):
            return
        _send_store_status(ctx, lambda: store.status("deepseek"))

    @router.post("/api/provider-credentials/deepseek")
    def provider_credentials_set(ctx: RequestContext) -> None:
        if not credentials_allowed(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            status = store.set("deepseek", str(payload.get("api_key") or ""))
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except RuntimeError:
            ctx.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "secure_store_unavailable")
            return
        ctx.send_json(_safe_status_payload(status))

    @router.delete("/api/provider-credentials/deepseek")
    def provider_credentials_delete(ctx: RequestContext) -> None:
        if not credentials_allowed(ctx):
            return
        _send_store_status(ctx, lambda: store.delete("deepseek"))
