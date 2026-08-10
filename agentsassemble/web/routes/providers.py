"""Provider catalog, local login, and credential HTTP routes."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from http import HTTPStatus
from typing import Protocol

from agentsassemble.providers.adapters import default_provider_registry
from agentsassemble.providers import catalog as provider_catalog
from agentsassemble.providers.capabilities import PROVIDER_CAPABILITIES
from agentsassemble.providers.provider_usage import (
    ProviderUsageRegistry,
    ProviderUsageUnavailable,
    default_provider_usage_registry,
)
from agentsassemble.providers.opencode_usage import build_opencode_go_credential
from agentsassemble.providers.workspace_picker import (
    WorkspacePickerUnavailable,
    choose_workspace_folder,
)
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.providers.secrets import (
    PROVIDER_SECRETS,
    ProviderSecretStoreUnavailable,
)
from agentsassemble.providers.sessions import (
    ProviderSessionListing,
    inspect_provider_sessions,
)


class ProviderSecretStore(Protocol):
    def status(self, provider_id: str) -> Mapping[str, object]: ...

    def set(self, provider_id: str, value: str) -> Mapping[str, object]: ...

    def delete(self, provider_id: str) -> Mapping[str, object]: ...


class ProviderLogin(Protocol):
    def start(self, payload: dict[str, object]) -> dict[str, object]: ...

    def record_invalid_json(self) -> None: ...


class ProviderUsage(Protocol):
    def read(
        self,
        provider_id: str,
        *,
        model: str = "",
        refresh: bool = False,
    ) -> dict[str, object]: ...


class ProviderCatalogRefresh(Protocol):
    def snapshot(self, *, refresh: bool = False) -> dict[str, object]: ...


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
    usage_service: ProviderUsageRegistry | ProviderUsage | None = None,
    capability_catalog: ProviderCatalogRefresh | None = None,
    workspace_picker: Callable[[], str] = choose_workspace_folder,
    session_inspector: Callable[..., ProviderSessionListing] = inspect_provider_sessions,
) -> None:
    """Register provider discovery, login, and credential-management routes."""
    store = PROVIDER_SECRETS if secret_store is None else secret_store
    usage = default_provider_usage_registry() if usage_service is None else usage_service
    capabilities = PROVIDER_CAPABILITIES if capability_catalog is None else capability_catalog

    def _send_store_status(ctx: RequestContext, operation: Callable[[], Mapping[str, object]]) -> None:
        try:
            status = operation()
        except ProviderSecretStoreUnavailable:
            ctx.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "secure_store_unavailable")
            return
        ctx.send_json(_safe_status_payload(status))

    @router.get("/api/providers")
    def providers(ctx: RequestContext) -> None:
        ctx.send_json(provider_catalog_payload())

    @router.get("/api/model-catalog")
    def model_catalog(ctx: RequestContext) -> None:
        ctx.send_json(model_catalog_payload())

    @router.get("/api/provider-sessions/local")
    def local_provider_sessions(ctx: RequestContext) -> None:
        """Sessions the provider CLI already stores for one workspace.

        Reading another user's CLI history is not something a room guest gets
        to do, and the paths involved are the operator's own machine, so this
        stays behind the same gate as provider login.
        """
        if not is_local_operator(ctx):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "provider session listing is limited to the local operator UI",
            )
            return
        provider_kind = ctx.query_value("provider_kind")
        workspace = ctx.query_value("workspace")
        if not provider_kind:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "provider_kind is required")
            return
        if not workspace:
            # Without a folder the answer would be every project's history.
            ctx.send_error(HTTPStatus.BAD_REQUEST, "workspace is required")
            return
        listing = session_inspector(provider_kind, workspace=workspace)
        ctx.send_json(
            {
                "provider_kind": provider_kind,
                "workspace": workspace,
                **listing.payload(),
            }
        )

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

    @router.post("/api/provider-catalog/refresh")
    def refresh_provider_catalog(ctx: RequestContext) -> None:
        if not is_local_operator(ctx):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "provider catalog refresh can only be started from the local operator UI",
            )
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        force = payload.get("force") is not False
        try:
            snapshot = capabilities.snapshot(refresh=force)
        except Exception:
            ctx.send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "provider catalog refresh failed",
            )
            return
        ctx.send_json(snapshot)

    @router.post("/api/local/workspace-picker")
    def local_workspace_picker(ctx: RequestContext) -> None:
        if not is_local_operator(ctx):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "workspace picker can only be opened from the local operator UI",
            )
            return
        try:
            path = workspace_picker()
        except WorkspacePickerUnavailable as error:
            ctx.send_error(HTTPStatus.SERVICE_UNAVAILABLE, str(error))
            return
        ctx.send_json({"selected": bool(path), "path": path})

    def _credential_status(ctx: RequestContext, provider_id: str) -> None:
        if not credentials_allowed(ctx):
            return
        _send_store_status(ctx, lambda: store.status(provider_id))

    @router.get("/api/provider-credentials/deepseek")
    def deepseek_credentials_status(ctx: RequestContext) -> None:
        _credential_status(ctx, "deepseek")

    @router.get("/api/provider-credentials/cerebras")
    def cerebras_credentials_status(ctx: RequestContext) -> None:
        _credential_status(ctx, "cerebras")

    @router.get("/api/provider-credentials/openrouter")
    def openrouter_credentials_status(ctx: RequestContext) -> None:
        _credential_status(ctx, "openrouter")

    @router.get("/api/provider-credentials/vercel")
    def vercel_credentials_status(ctx: RequestContext) -> None:
        _credential_status(ctx, "vercel")

    @router.get("/api/provider-credentials/llmgateway")
    def llmgateway_credentials_status(ctx: RequestContext) -> None:
        _credential_status(ctx, "llmgateway")

    @router.get("/api/provider-credentials/tokenrouter")
    def tokenrouter_credentials_status(ctx: RequestContext) -> None:
        _credential_status(ctx, "tokenrouter")

    @router.get("/api/provider-credentials/custom_api")
    def custom_api_credentials_status(ctx: RequestContext) -> None:
        _credential_status(ctx, "custom_api")

    @router.get("/api/provider-credentials/opencode")
    def opencode_credentials_status(ctx: RequestContext) -> None:
        _credential_status(ctx, "opencode")

    def _send_provider_usage(ctx: RequestContext, provider_id: str) -> None:
        if not credentials_allowed(ctx):
            return
        try:
            ctx.send_json(
                usage.read(
                    provider_id,
                    model=ctx.query_value("model"),
                    refresh=ctx.query_value("refresh") == "1",
                )
            )
        except ProviderUsageUnavailable as error:
            ctx.send_error(HTTPStatus.SERVICE_UNAVAILABLE, str(error))

    @router.get("/api/provider-usage/claude")
    def claude_provider_usage(ctx: RequestContext) -> None:
        _send_provider_usage(ctx, "claude")

    @router.get("/api/provider-usage/codex")
    def codex_provider_usage(ctx: RequestContext) -> None:
        _send_provider_usage(ctx, "codex")

    @router.get("/api/provider-usage/antigravity")
    def antigravity_provider_usage(ctx: RequestContext) -> None:
        _send_provider_usage(ctx, "antigravity")

    @router.get("/api/provider-usage/grok")
    def grok_provider_usage(ctx: RequestContext) -> None:
        _send_provider_usage(ctx, "grok")

    @router.get("/api/provider-usage/deepseek")
    def deepseek_provider_usage(ctx: RequestContext) -> None:
        _send_provider_usage(ctx, "deepseek")

    @router.get("/api/provider-usage/opencode")
    def opencode_provider_usage(ctx: RequestContext) -> None:
        _send_provider_usage(ctx, "opencode")

    def _credential_set(ctx: RequestContext, provider_id: str) -> None:
        if not credentials_allowed(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            credential = str(payload.get("api_key") or "")
            if provider_id == "opencode":
                credential = build_opencode_go_credential(
                    payload.get("workspace_id"),
                    credential,
                )
            status = store.set(provider_id, credential)
        except (ProviderUsageUnavailable, ValueError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except ProviderSecretStoreUnavailable:
            ctx.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "secure_store_unavailable")
            return
        ctx.send_json(_safe_status_payload(status))

    @router.post("/api/provider-credentials/deepseek")
    def deepseek_credentials_set(ctx: RequestContext) -> None:
        _credential_set(ctx, "deepseek")

    @router.post("/api/provider-credentials/cerebras")
    def cerebras_credentials_set(ctx: RequestContext) -> None:
        _credential_set(ctx, "cerebras")

    @router.post("/api/provider-credentials/openrouter")
    def openrouter_credentials_set(ctx: RequestContext) -> None:
        _credential_set(ctx, "openrouter")

    @router.post("/api/provider-credentials/vercel")
    def vercel_credentials_set(ctx: RequestContext) -> None:
        _credential_set(ctx, "vercel")

    @router.post("/api/provider-credentials/llmgateway")
    def llmgateway_credentials_set(ctx: RequestContext) -> None:
        _credential_set(ctx, "llmgateway")

    @router.post("/api/provider-credentials/tokenrouter")
    def tokenrouter_credentials_set(ctx: RequestContext) -> None:
        _credential_set(ctx, "tokenrouter")

    @router.post("/api/provider-credentials/custom_api")
    def custom_api_credentials_set(ctx: RequestContext) -> None:
        _credential_set(ctx, "custom_api")

    @router.post("/api/provider-credentials/opencode")
    def opencode_credentials_set(ctx: RequestContext) -> None:
        _credential_set(ctx, "opencode")

    def _credential_delete(ctx: RequestContext, provider_id: str) -> None:
        if not credentials_allowed(ctx):
            return
        _send_store_status(ctx, lambda: store.delete(provider_id))

    @router.delete("/api/provider-credentials/deepseek")
    def deepseek_credentials_delete(ctx: RequestContext) -> None:
        _credential_delete(ctx, "deepseek")

    @router.delete("/api/provider-credentials/cerebras")
    def cerebras_credentials_delete(ctx: RequestContext) -> None:
        _credential_delete(ctx, "cerebras")

    @router.delete("/api/provider-credentials/openrouter")
    def openrouter_credentials_delete(ctx: RequestContext) -> None:
        _credential_delete(ctx, "openrouter")

    @router.delete("/api/provider-credentials/vercel")
    def vercel_credentials_delete(ctx: RequestContext) -> None:
        _credential_delete(ctx, "vercel")

    @router.delete("/api/provider-credentials/llmgateway")
    def llmgateway_credentials_delete(ctx: RequestContext) -> None:
        _credential_delete(ctx, "llmgateway")

    @router.delete("/api/provider-credentials/tokenrouter")
    def tokenrouter_credentials_delete(ctx: RequestContext) -> None:
        _credential_delete(ctx, "tokenrouter")

    @router.delete("/api/provider-credentials/custom_api")
    def custom_api_credentials_delete(ctx: RequestContext) -> None:
        _credential_delete(ctx, "custom_api")

    @router.delete("/api/provider-credentials/opencode")
    def opencode_credentials_delete(ctx: RequestContext) -> None:
        _credential_delete(ctx, "opencode")
