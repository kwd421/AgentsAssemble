"""Provider option discovery, catalog caching, and selection validation."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from agentsassemble.providers.claude_catalog import (
    discover_claude_model_ids,
    discover_claude_xhigh_model_ids,
)
from agentsassemble.providers.catalog_provenance import (
    annotate_subscription_catalog_provenance,
)
from agentsassemble.providers.catalog_revision import catalog_revision
from agentsassemble.providers.grok_catalog import classify_grok_models, discover_grok_custom_model_ids
from agentsassemble.providers.launch_specs import (
    NATIVE_CLI_PROVIDER_CATALOG,
    native_cli_provider_definition,
    split_cursor_model,
)
from agentsassemble.providers.native_harness_catalog import add_native_harness_catalog_controls
from agentsassemble.providers.opencode_catalog import opencode_model_options
from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.providers.remote_openai import (
    RemoteOpenAIProfile,
    discover_remote_openai_models,
    normalize_custom_openai_endpoint,
    remote_openai_catalog_payload,
    remote_openai_discovery_failure_payload,
    remote_openai_profiles,
)
from agentsassemble.providers.secrets import PROVIDER_SECRETS
from agentsassemble.providers.selection import (
    ProviderCatalogSelectionError,
    ValidatedProviderSelection,
)


ProbeRunner = Callable[[list[str], float], tuple[int, str, str]]
ClaudeModelDiscovery = Callable[[str], list[str]]
RemoteModelDiscovery = Callable[[RemoteOpenAIProfile, str], list[dict[str, object]]]
SecretResolver = Callable[[str], str]
CatalogListener = Callable[[dict[str, object]], None]


class _ProviderDiscoveryError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


# One discovery pass runs every provider CLI as a subprocess and fetches two
# remote model lists -- measured at 8.3s. Model catalogs change on the order of
# weeks, so a short expiry paid that cost to learn nothing. The states that do
# move (a login completing, a CLI appearing) already force a refresh of their
# own, and a stale login surfaces when the agent actually starts, so a daily
# floor is enough for the rest.
DEFAULT_CATALOG_TTL_SECONDS = 24 * 60 * 60.0


class ProviderCapabilityCatalog:
    """Fail-closed native option discovery with a bounded refresh cache."""

    def __init__(
        self,
        *,
        runner: ProbeRunner | None = None,
        resolver: Callable[[str], str | None] = shutil.which,
        claude_model_discovery: ClaudeModelDiscovery = discover_claude_model_ids,
        claude_xhigh_model_discovery: ClaudeModelDiscovery = discover_claude_xhigh_model_ids,
        remote_model_discovery: RemoteModelDiscovery = (
            lambda profile, api_key: discover_remote_openai_models(
                profile,
                api_key=api_key,
            )
        ),
        secret_resolver: SecretResolver = lambda _provider_id: "",
        grok_custom_model_discovery: Callable[[], set[str]] = discover_grok_custom_model_ids,
        ttl_seconds: float = DEFAULT_CATALOG_TTL_SECONDS,
    ) -> None:
        self._runner = runner or _run_probe
        self._resolver = resolver
        self._claude_model_discovery = claude_model_discovery
        self._claude_xhigh_model_discovery = claude_xhigh_model_discovery
        self._remote_model_discovery = remote_model_discovery
        self._secret_resolver = secret_resolver
        self._grok_custom_model_discovery = grok_custom_model_discovery
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._lock = threading.RLock()
        self._cached_at = 0.0
        self._cached: list[dict[str, object]] = []
        self._catalog_revision = ""
        self._discovered_at = ""
        self._status = "loading"
        self._refreshing = False
        self._listeners: dict[int, CatalogListener] = {}
        self._next_listener_id = 1
        self._listener_error_count = 0
        self._listener_last_type = ""
        self._listener_last_exception_type = ""
        self._listener_last_error_at = ""
        self._listener_last_category = ""

    def snapshot(self, *, refresh: bool = False) -> dict[str, object]:
        now = time.monotonic()
        with self._lock:
            if not refresh and self._cached and now - self._cached_at < self._ttl_seconds:
                return self._snapshot_locked()
            if not refresh:
                self._start_background_refresh_locked()
                return self._snapshot_locked()
            self._status = "ready" if self._cached else "loading"
            self._refreshing = True
        try:
            return self._refresh_snapshot()
        except Exception:
            self._mark_refresh_failed()
            raise

    def current_snapshot(self) -> dict[str, object]:
        """Return the current projection without starting provider discovery."""
        with self._lock:
            return self._snapshot_locked()

    def payload(self, *, refresh: bool = False) -> list[dict[str, object]]:
        return list(self.snapshot(refresh=refresh).get("providers") or [])

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "catalog_listener_error_count": self._listener_error_count,
                "listener_type": self._listener_last_type,
                "exception_type": self._listener_last_exception_type,
                "last_failure_at": self._listener_last_error_at,
                "category": self._listener_last_category,
            }

    def subscribe(self, listener: CatalogListener) -> Callable[[], None]:
        with self._lock:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener

        def remove() -> None:
            with self._lock:
                self._listeners.pop(listener_id, None)

        return remove

    def validate_selection(
        self,
        *,
        catalog_revision: str,
        provider_id: str,
        values: dict[str, str],
    ) -> ValidatedProviderSelection:
        with self._lock:
            if self._status != "ready" or not self._catalog_revision:
                raise ProviderCatalogSelectionError(
                    "Provider catalog is not ready.",
                    code="catalog_not_ready",
                )
            if catalog_revision != self._catalog_revision:
                raise ProviderCatalogSelectionError(
                    "Provider catalog changed; refresh the selection before creating the session.",
                    code="catalog_changed",
                )
            provider = next(
                (item for item in self._cached if str(item.get("id") or "") == provider_id),
                None,
            )
            if provider is None or provider.get("discovery_status") != "ready":
                raise ProviderCatalogSelectionError(
                    f"Provider {provider_id or 'unknown'} is not available in the current catalog.",
                    code="unsupported_provider",
                )
            controls = copy.deepcopy(list(provider.get("controls") or []))
            fixed_values = {
                str(key): str(value)
                for key, value in dict(provider.get("fixed_values") or {}).items()
            }
        resolved_values = {
            key: str(values.get(key) or fixed_values.get(key) or "")
            for key in (
                "model",
                "provider_endpoint",
                "reasoning_effort",
                "service_tier",
                "variant",
                "execution_harness",
                "permission_mode",
                "max_output_tokens",
            )
        }
        for key, fixed_value in fixed_values.items():
            requested = str(values.get(key) or "")
            if requested and requested != fixed_value:
                raise ProviderCatalogSelectionError(
                    f"Unsupported fixed {key} value for provider {provider_id}.",
                    code="unsupported_provider_option",
                )
        for control in controls:
            if not isinstance(control, dict):
                continue
            key = str(control.get("key") or "")
            if not key:
                continue
            allowed = {
                str(option.get("value") or "")
                for option in list(control.get("options") or [])
                if isinstance(option, dict)
            }
            value = resolved_values.get(key, "")
            if value not in allowed:
                code = {
                    "model": "unsupported_model",
                    "reasoning_effort": "unsupported_reasoning_effort",
                    "service_tier": "unsupported_service_tier",
                    "variant": "unsupported_variant",
                    "execution_harness": "unsupported_execution_harness",
                    "permission_mode": "unsupported_permission_mode",
                    "max_output_tokens": "unsupported_max_output_tokens",
                }.get(key, "unsupported_provider_option")
                raise ProviderCatalogSelectionError(
                    f"Unsupported {key} value for provider {provider_id}.",
                    code=code,
                )
        custom_model = bool(provider.get("custom_model"))
        if custom_model:
            if not resolved_values["model"]:
                raise ProviderCatalogSelectionError(
                    "Custom API model ID is required.", code="unsupported_model"
                )
            try:
                provider_endpoint = normalize_custom_openai_endpoint(
                    resolved_values["provider_endpoint"]
                )
            except ValueError as error:
                raise ProviderCatalogSelectionError(
                    str(error), code="invalid_provider_endpoint"
                ) from error
        else:
            provider_endpoint = ""
        model_control = next(
            (control for control in controls if isinstance(control, dict) and control.get("key") == "model"),
            None,
        )
        selected_model = resolved_values["model"]
        model_option = (
            next(
                (
                    option
                    for option in list(model_control.get("options") or [])
                    if isinstance(option, dict)
                    and str(option.get("value") or "") == selected_model
                ),
                None,
            )
            if isinstance(model_control, dict)
            else None
        )
        metadata = dict(model_option.get("metadata") or {}) if isinstance(model_option, dict) else {}
        self._validate_model_relation(
            provider_id=provider_id,
            metadata=metadata,
            metadata_key="reasoning_efforts",
            selected_value=resolved_values["reasoning_effort"],
            error_code="unsupported_model_effort_combination",
        )
        selected_tier = resolved_values["service_tier"]
        if selected_tier != "default":
            self._validate_model_relation(
                provider_id=provider_id,
                metadata=metadata,
                metadata_key="service_tiers",
                selected_value=selected_tier,
                error_code="unsupported_model_service_tier_combination",
            )
        self._validate_model_runtime_variant(
            provider_id=provider_id,
            metadata=metadata,
            reasoning_effort=resolved_values["reasoning_effort"],
            service_tier=selected_tier,
        )
        selection_kind = "exact" if custom_model else str(metadata.get("selection_kind") or "")
        if selection_kind not in {"exact", "alias"}:
            raise ProviderCatalogSelectionError(
                f"Provider {provider_id} model catalog entry is missing its selection kind.",
                code="catalog_invalid",
            )
        return ValidatedProviderSelection(
            catalog_revision=catalog_revision,
            provider_id=provider_id,
            provider_kind=str(provider.get("provider_kind") or ""),
            model=selected_model,
            model_selection_kind=selection_kind,
            reasoning_effort=resolved_values["reasoning_effort"],
            service_tier=selected_tier,
            variant=resolved_values["variant"],
            execution_harness=resolved_values["execution_harness"] or "builtin",
            permission_mode=resolved_values["permission_mode"],
            max_output_tokens=int(resolved_values["max_output_tokens"] or 0),
            context_contract_bytes=(
                int(metadata.get("context_length") or 0)
                if isinstance(metadata.get("context_length"), int)
                else 0
            ),
            provider_endpoint=provider_endpoint,
        )

    @staticmethod
    def _validate_model_relation(
        *,
        provider_id: str,
        metadata: dict[str, object],
        metadata_key: str,
        selected_value: str,
        error_code: str,
    ) -> None:
        relation_scope = str(metadata.get("relation_scope") or "")
        if not selected_value:
            if relation_scope == "per_model" and metadata_key in metadata:
                advertised_values = {
                    str(value)
                    for value in list(metadata.get(metadata_key) or [])
                    if str(value)
                }
                if advertised_values:
                    raise ProviderCatalogSelectionError(
                        f"The selected model and {metadata_key} value are not a supported combination for {provider_id}.",
                        code=error_code,
                    )
            return
        if relation_scope == "global":
            return
        if relation_scope != "per_model" or metadata_key not in metadata:
            raise ProviderCatalogSelectionError(
                f"Provider {provider_id} model relation metadata is incomplete.",
                code="catalog_invalid",
            )
        allowed = {
            str(value)
            for value in list(metadata.get(metadata_key) or [])
            if str(value)
        }
        if selected_value not in allowed:
            raise ProviderCatalogSelectionError(
                f"The selected model and {metadata_key} value are not a supported combination for {provider_id}.",
                code=error_code,
            )

    @staticmethod
    def _validate_model_runtime_variant(
        *,
        provider_id: str,
        metadata: dict[str, object],
        reasoning_effort: str,
        service_tier: str,
    ) -> None:
        if "runtime_variants" not in metadata:
            return
        variants = list(metadata.get("runtime_variants") or [])
        if not variants or any(not isinstance(variant, dict) for variant in variants):
            raise ProviderCatalogSelectionError(
                f"Provider {provider_id} runtime variant metadata is incomplete.",
                code="catalog_invalid",
            )
        requested = (
            reasoning_effort or "default",
            service_tier or "default",
        )
        allowed = {
            (
                str(variant.get("reasoning_effort") or "default"),
                str(variant.get("service_tier") or "default"),
            )
            for variant in variants
        }
        if requested not in allowed:
            raise ProviderCatalogSelectionError(
                f"The selected model, reasoning effort, and service tier are not a supported combination for {provider_id}.",
                code="unsupported_model_runtime_combination",
            )

    def _refresh_snapshot(self) -> dict[str, object]:
        with self._lock:
            previous_by_id = {
                str(provider.get("id") or ""): copy.deepcopy(provider)
                for provider in self._cached
            }
        payload = [self._native_payload(definition) for definition in NATIVE_CLI_PROVIDER_CATALOG]
        payload.append(self._opencode_payload())
        payload.extend(map(self._remote_openai_payload, remote_openai_profiles()))
        payload.append(self._ollama_payload())
        payload.append(self._lmstudio_payload())
        payload = add_native_harness_catalog_controls(payload, resolver=self._resolver)
        payload = annotate_subscription_catalog_provenance(payload)
        payload = [
            self._preserve_last_verified_provider(
                provider,
                previous_by_id.get(str(provider.get("id") or "")),
            )
            for provider in payload
        ]
        revision = catalog_revision(payload)
        discovered_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._cached = payload
            self._cached_at = time.monotonic()
            self._catalog_revision = revision
            self._discovered_at = discovered_at
            self._status = "ready"
            self._refreshing = False
            snapshot = self._snapshot_locked()
            listeners = list(self._listeners.values())
        self._notify_listeners(snapshot, listeners, category="refresh_ready")
        with self._lock:
            return self._snapshot_locked()

    def _start_background_refresh_locked(self) -> None:
        if self._refreshing:
            return
        if not self._cached:
            self._status = "loading"
        self._refreshing = True

        def refresh_catalog() -> None:
            try:
                self._refresh_snapshot()
            except Exception:
                with self._lock:
                    self._status = "ready" if self._cached else "failed"
                    self._refreshing = False
                    snapshot = self._snapshot_locked()
                    listeners = list(self._listeners.values())
                self._notify_listeners(snapshot, listeners, category="refresh_failed")

        threading.Thread(target=refresh_catalog, name="provider-capability-refresh", daemon=True).start()

    def _mark_refresh_failed(self) -> None:
        with self._lock:
            self._status = "ready" if self._cached else "failed"
            self._refreshing = False

    @staticmethod
    def _preserve_last_verified_provider(
        discovered: dict[str, object],
        previous: dict[str, object] | None,
    ) -> dict[str, object]:
        if (
            discovered.get("discovery_error_code") != "model_discovery_timeout"
            or not previous
            or previous.get("discovery_status") != "ready"
            or not previous.get("startable")
        ):
            return discovered
        preserved = copy.deepcopy(previous)
        preserved["catalog_source"] = "stale_cache"
        preserved["discovery_error_code"] = "model_discovery_timeout"
        preserved["discovery_error"] = (
            f"{discovered.get('discovery_error') or '모델 목록 갱신 시간이 초과되었습니다'} "
            "마지막으로 확인된 모델 목록을 계속 사용합니다."
        )
        return preserved

    def _notify_listeners(
        self,
        snapshot: dict[str, object],
        listeners: list[CatalogListener],
        *,
        category: str,
    ) -> None:
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception as error:
                listener_type = getattr(listener, "__qualname__", type(listener).__qualname__)
                with self._lock:
                    self._listener_error_count += 1
                    self._listener_last_type = str(listener_type)
                    self._listener_last_exception_type = type(error).__name__
                    self._listener_last_error_at = datetime.now(UTC).isoformat()
                    self._listener_last_category = category

    def _native_payload(self, definition) -> dict[str, object]:
        resolved = self._resolver(definition.executable)
        base = definition.public_payload()
        base.update(
            {
                "available": bool(resolved),
                "startable": False,
                "discovery_status": "failed" if not resolved else "loading",
                "catalog_source": "discovered",
                "controls": [],
            }
        )
        if not resolved:
            base["discovery_error_code"] = "command_missing"
            base["discovery_error"] = "configured command missing"
            return base
        try:
            discovered = self._discover(definition.provider_id, str(resolved))
        except subprocess.TimeoutExpired as error:
            timeout = float(error.timeout or 0.0)
            base["discovery_status"] = "failed"
            base["discovery_error_code"] = "model_discovery_timeout"
            base["discovery_error"] = (
                f"{definition.display_name} 모델 목록 조회가 {timeout:g}초 안에 끝나지 않았습니다."
            )
            return base
        except _ProviderDiscoveryError as error:
            base["discovery_status"] = "failed"
            base["discovery_error_code"] = error.code
            base["discovery_error"] = str(error)
            return base
        except Exception as error:
            base["discovery_status"] = "failed"
            base["discovery_error_code"] = "model_discovery_failed"
            base["discovery_error"] = (
                f"{definition.display_name} 모델 목록을 불러오지 못했습니다 ({type(error).__name__})."
            )
            return base
        if discovered:
            base["controls"] = discovered
            base["discovery_status"] = "ready"
            base["startable"] = True
        else:
            base["discovery_status"] = "failed"
            base["discovery_error_code"] = "no_supported_models"
            base["discovery_error"] = (
                f"{definition.display_name}에서 지원되는 모델을 찾지 못했습니다."
            )
        return base

    def _discover(self, provider_id: str, executable: str) -> list[dict[str, object]]:
        if provider_id == "codex":
            output = self._model_probe(
                provider_id,
                [executable, "debug", "models", "--bundled"],
                6.0,
            )
            return _codex_controls(output)
        if provider_id == "antigravity":
            output = self._model_probe(provider_id, [executable, "models"], 8.0)
            return _antigravity_controls(output)
        if provider_id == "grok":
            output = self._model_probe(provider_id, [executable, "models"], 5.0)
            return _grok_controls(output, self._grok_custom_model_discovery())
        if provider_id == "claude":
            help_output = self._model_probe(provider_id, [executable, "--help"], 5.0)
            try:
                xhigh_models = self._claude_xhigh_model_discovery(executable)
            except OSError:
                xhigh_models = []
            return _claude_controls(
                self._claude_model_discovery(executable),
                help_output=help_output,
                xhigh_models=xhigh_models,
                ultracode_available=self._claude_ultracode_available(executable),
            )
        if provider_id == "cursor":
            output = self._model_probe(provider_id, [executable, "models"], 8.0)
            return _cursor_controls(output)
        return []

    def _claude_ultracode_available(self, executable: str) -> bool:
        code, output, stderr = self._runner(
            [executable, "--effort", "ultracode", "--version"],
            5.0,
        )
        diagnostic = f"{output}\n{stderr}"
        return code == 0 and not re.search(
            r"unknown\s+--effort\s+value",
            diagnostic,
            flags=re.IGNORECASE,
        )

    def _model_probe(
        self,
        provider_id: str,
        command: list[str],
        timeout_seconds: float,
    ) -> str:
        code, output, stderr = self._runner(command, timeout_seconds)
        if code == 0:
            return output
        display_name = {
            "codex": "Codex",
            "antigravity": "Antigravity",
            "grok": "Grok",
            "claude": "Claude Code",
            "cursor": "Cursor",
            "opencode": "OpenCode",
            "ollama": "Ollama",
            "lmstudio": "LM Studio",
        }.get(provider_id, provider_id)
        normalized_error = stderr.casefold()
        if any(
            marker in normalized_error
            for marker in (
                "authentication required",
                "login required",
                "not authenticated",
                "not logged in",
            )
        ):
            raise _ProviderDiscoveryError(
                f"{display_name} CLI 로그인이 필요합니다. 로그인한 뒤 다시 시도하세요.",
                code="authentication_required",
            )
        raise _ProviderDiscoveryError(
            f"{display_name} 모델 목록 조회에 실패했습니다 (종료 코드 {code}).",
            code="model_discovery_failed",
        )

    def _opencode_payload(self) -> dict[str, object]:
        definition = native_cli_provider_definition("opencode")
        if definition is None:
            raise RuntimeError("OpenCode provider definition is missing.")
        resolved = self._resolver("opencode")
        controls: list[dict[str, object]] = []
        status = "failed" if not resolved else "loading"
        error = "configured command missing" if not resolved else ""
        error_code = "command_missing" if not resolved else ""
        if resolved:
            try:
                output = self._model_probe(
                    "opencode",
                    [str(resolved), "models", "--verbose"],
                    8.0,
                )
                model_options = opencode_model_options(output)
                if model_options:
                    controls = _opencode_controls(model_options)
                    status = "ready"
            except subprocess.TimeoutExpired as exception:
                error = f"OpenCode 모델 목록 조회가 {float(exception.timeout or 0.0):g}초 안에 끝나지 않았습니다."
                error_code = "model_discovery_timeout"
            except _ProviderDiscoveryError as exception:
                error = str(exception)
                error_code = exception.code
            except Exception as exception:
                error = f"OpenCode 모델 목록을 불러오지 못했습니다 ({type(exception).__name__})."
                error_code = "model_discovery_failed"
        if resolved and status != "ready" and not error:
            error = "OpenCode에서 지원되는 모델을 찾지 못했습니다."
            error_code = "no_supported_models"
        return {
            **definition.public_payload(),
            "available": bool(resolved),
            "startable": bool(resolved and controls),
            "discovery_status": status,
            "discovery_error": error,
            "discovery_error_code": error_code if status != "ready" else "",
            "catalog_source": "discovered",
            "controls": controls,
        }

    def _ollama_payload(self) -> dict[str, object]:
        definition = native_cli_provider_definition("ollama")
        if definition is None:
            raise RuntimeError("Ollama provider definition is missing.")
        resolved = self._resolver(definition.executable)
        if not resolved:
            return _unavailable_structured_payload(
                definition,
                error="Ollama CLI를 찾지 못했습니다.",
                error_code="command_missing",
            )
        try:
            output = self._model_probe(
                "ollama",
                [str(resolved), "list"],
                5.0,
            )
            candidates = _ollama_model_entries(output)
            model_options: list[dict[str, object]] = []
            for candidate in candidates[:32]:
                model = str(candidate["value"])
                details = self._model_probe(
                    "ollama",
                    [str(resolved), "show", model],
                    5.0,
                )
                if _ollama_supports_tools(details):
                    execution_location = str(candidate["execution_location"])
                    model_options.append(
                        _model_option(
                            model,
                            _ollama_model_label(model),
                            catalog_group=(
                                "subscription"
                                if execution_location == "cloud"
                                else "local"
                            ),
                            execution_location=execution_location,
                            **(
                                {"pricing": "free_tier"}
                                if execution_location == "cloud"
                                else {}
                            ),
                        )
                    )
        except subprocess.TimeoutExpired as error:
            return _unavailable_structured_payload(
                definition,
                available=True,
                error=f"Ollama 모델 목록 조회가 {float(error.timeout or 0.0):g}초 안에 끝나지 않았습니다.",
                error_code="model_discovery_timeout",
            )
        except _ProviderDiscoveryError as error:
            return _unavailable_structured_payload(
                definition,
                available=True,
                error=str(error),
                error_code=error.code,
            )
        if not model_options:
            return _unavailable_structured_payload(
                definition,
                available=True,
                error="Ollama에 도구 사용 가능한 모델이 없습니다. 모델을 먼저 pull 하세요.",
                error_code="no_supported_models",
            )
        return _local_openai_payload(
            definition,
            model_options=model_options,
            default_model=(
                "nemotron-3-super:cloud"
                if any(
                    option["value"] == "nemotron-3-super:cloud"
                    for option in model_options
                )
                else str(model_options[0]["value"])
            ),
        )

    def _lmstudio_payload(self) -> dict[str, object]:
        definition = native_cli_provider_definition("lmstudio")
        if definition is None:
            raise RuntimeError("LM Studio provider definition is missing.")
        resolved = self._resolver(definition.executable)
        if not resolved:
            return _unavailable_structured_payload(
                definition,
                error="LM Studio CLI를 찾지 못했습니다.",
                error_code="command_missing",
            )
        try:
            status = self._model_probe(
                "lmstudio",
                [str(resolved), "status"],
                5.0,
            )
            if not re.search(r"(?m)^Server:\s+ON\b", status):
                return _unavailable_structured_payload(
                    definition,
                    available=True,
                    error="LM Studio 로컬 서버가 꺼져 있습니다.",
                    error_code="local_server_unavailable",
                )
            output = self._model_probe(
                "lmstudio",
                [str(resolved), "ps", "--json"],
                8.0,
            )
            models = _lmstudio_tool_models(output)
        except subprocess.TimeoutExpired as error:
            return _unavailable_structured_payload(
                definition,
                available=True,
                error=f"LM Studio 모델 목록 조회가 {float(error.timeout or 0.0):g}초 안에 끝나지 않았습니다.",
                error_code="model_discovery_timeout",
            )
        except _ProviderDiscoveryError as error:
            return _unavailable_structured_payload(
                definition,
                available=True,
                error=str(error),
                error_code=error.code,
            )
        if not models:
            return _unavailable_structured_payload(
                definition,
                available=True,
                error="LM Studio에 불러온 도구 사용 모델이 없습니다.",
                error_code="no_supported_models",
            )
        return _local_openai_payload(
            definition,
            models=models,
            default_model=(
                "gemma-4-e4b-it"
                if "gemma-4-e4b-it" in models
                else models[0]
            ),
        )

    def _remote_openai_payload(
        self,
        profile: RemoteOpenAIProfile,
    ) -> dict[str, object]:
        if not profile.discovery_path:
            return remote_openai_catalog_payload(profile)
        try:
            models = self._remote_model_discovery(
                profile,
                self._secret_resolver(profile.provider_id),
            )
        except Exception as error:
            return remote_openai_discovery_failure_payload(profile, error)
        if not models:
            return remote_openai_catalog_payload(
                profile,
                discovery_error=f"{profile.display_name}에서 도구 사용 모델을 찾지 못했습니다.",
                discovery_error_code="no_supported_models",
            )
        return remote_openai_catalog_payload(profile, discovered_models=models)

    def _snapshot_locked(self) -> dict[str, object]:
        providers = copy.deepcopy(self._cached)
        if providers and self._status != "ready":
            for provider in providers:
                provider["startable"] = False
                provider["catalog_source"] = "stale_cache"
                if provider.get("discovery_status") == "ready":
                    provider["discovery_status"] = self._status
                    provider["discovery_error"] = (
                        "provider catalog refresh in progress"
                        if self._status == "loading"
                        else "provider catalog refresh failed"
                    )
        if not providers and self._status == "loading":
            providers = self._loading_payload()
        return {
            "status": self._status,
            "catalog_revision": self._catalog_revision,
            "discovered_at": self._discovered_at,
            "providers": providers,
        }

    def _loading_payload(self) -> list[dict[str, object]]:
        payload: list[dict[str, object]] = []
        for definition in NATIVE_CLI_PROVIDER_CATALOG:
            resolved = self._resolver(definition.executable)
            payload.append(
                {
                    **definition.public_payload(),
                    "available": bool(resolved),
                    "startable": False,
                    "discovery_status": "loading" if resolved else "failed",
                    "discovery_error": "" if resolved else "configured command missing",
                    "catalog_source": "discovered",
                    "controls": [],
                }
            )
        resolved = self._resolver("opencode")
        opencode_definition = native_cli_provider_definition("opencode")
        if opencode_definition is None:
            raise RuntimeError("OpenCode provider definition is missing.")
        payload.append(
            {
                **opencode_definition.public_payload(),
                "available": bool(resolved),
                "startable": False,
                "discovery_status": "loading" if resolved else "failed",
                "discovery_error": "" if resolved else "configured command missing",
                "catalog_source": "discovered",
                "controls": [],
            }
        )
        payload.extend(
            remote_openai_catalog_payload(
                profile,
                discovery_error="provider catalog refresh in progress",
                discovery_error_code="catalog_loading",
            )
            for profile in remote_openai_profiles()
        )
        for provider_id in ("ollama", "lmstudio"):
            definition = native_cli_provider_definition(provider_id)
            if definition is None:
                raise RuntimeError(f"{provider_id} provider definition is missing.")
            resolved = self._resolver(definition.executable)
            payload.append(
                {
                    **definition.public_payload(),
                    "available": bool(resolved),
                    "startable": False,
                    "discovery_status": "loading" if resolved else "failed",
                    "discovery_error": "" if resolved else "configured command missing",
                    "catalog_source": "discovered",
                    "controls": [],
                }
            )
        return payload


def _run_probe(command: list[str], timeout_seconds: float) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        env=sanitized_provider_environment(),
    )
    return int(completed.returncode), completed.stdout[-2_000_000:], completed.stderr[-16_000:]


def _option(value: str, label: str = "", **metadata: object) -> dict[str, object]:
    payload: dict[str, object] = {"value": value, "label": label or value}
    if metadata:
        payload["metadata"] = metadata
    return payload


def _model_option(
    value: str,
    label: str = "",
    *,
    selection_kind: str = "exact",
    **metadata: object,
) -> dict[str, object]:
    return _option(value, label, selection_kind=selection_kind, **metadata)


def _control(
    key: str,
    label: str,
    options: list[dict[str, object]],
    default_value: str,
    *,
    kind: str = "select",
) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "options": options,
        "default_value": default_value,
    }


def _permission_control(provider_id: str = "") -> dict[str, object]:
    native_labels = {
        "codex": {
            "meeting_read_only": "Codex · sandbox read-only / approval never",
            "workspace_write": "Codex · sandbox workspace-write / approval on-request",
        },
        "claude": {
            "meeting_read_only": "Claude Code · dontAsk / RoomPortal command only",
            "workspace_write": "Claude Code · acceptEdits",
        },
        "antigravity": {
            "meeting_read_only": "Antigravity · sandbox",
            "workspace_write": "Antigravity · accept-edits",
        },
        "cursor": {
            "meeting_read_only": "Cursor · sandbox enabled / mode ask",
            "workspace_write": "Cursor · sandbox enabled",
        },
        "opencode": {
            "meeting_read_only": "OpenCode · permission deny",
            "workspace_write": "OpenCode · permission ask",
        },
        "grok": {
            "meeting_read_only": "Grok · approval reject / RoomPortal only",
            "workspace_write": "Grok · permission acceptEdits",
        },
    }.get(provider_id, {})

    def permission_option(value: str, label: str) -> dict[str, object]:
        native = native_labels.get(value, "")
        return _option(
            value,
            label,
            native_choice=native,
            description=native,
        )

    return _control(
        "permission_mode",
        "권한",
        [
            permission_option("meeting_read_only", "읽기 전용"),
            permission_option("workspace_write", "작업 폴더 쓰기"),
        ],
        "meeting_read_only",
    )


def _reasoning_option(value: str, description: str = "") -> dict[str, object]:
    labels = {
        "low": "Low",
        "medium": "Medium",
        "high": "High",
        "xhigh": "Extra High",
        "max": "Max",
        "ultra": "Ultra",
        "ultracode": "UltraCode",
    }
    return _option(
        value,
        labels.get(value, value),
        description=description,
        effect="ultra" if value in {"ultra", "ultracode"} else "",
    )


def _codex_controls(output: str) -> list[dict[str, object]]:
    payload = json.loads(output)
    models = payload.get("models") if isinstance(payload, dict) else []
    model_options: list[dict[str, object]] = []
    efforts: list[str] = []
    effort_descriptions: dict[str, str] = {}
    service_tiers: list[str] = []
    for model in models if isinstance(models, list) else []:
        if not isinstance(model, dict) or not model.get("slug"):
            continue
        supported_efforts = [
            item
            for item in list(model.get("supported_reasoning_levels") or [])
            if isinstance(item, dict) and item.get("effort")
        ]
        model_efforts = [str(item["effort"]) for item in supported_efforts]
        effort_descriptions.update(
            {
                str(item["effort"]): str(item.get("description") or "")
                for item in supported_efforts
            }
        )
        model_tiers = [
            str(item.get("id"))
            for item in list(model.get("service_tiers") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        efforts.extend(model_efforts)
        service_tiers.extend(model_tiers)
        model_options.append(
            _model_option(
                str(model["slug"]),
                str(model.get("display_name") or model["slug"]),
                relation_scope="per_model",
                reasoning_efforts=model_efforts,
                service_tiers=model_tiers,
            )
        )
    if not model_options:
        return []
    default_model = "gpt-5.6-luna" if any(item["value"] == "gpt-5.6-luna" for item in model_options) else str(model_options[0]["value"])
    return [
        _control("model", "모델", model_options, default_model, kind="combobox"),
        _control(
            "reasoning_effort",
            "추론 강도",
            [
                _reasoning_option(value, effort_descriptions.get(value, ""))
                for value in _unique(efforts)
            ],
            "low",
        ),
        _control(
            "service_tier",
            "응답 속도",
            [_option("default", "기본"), *[_option(value, "Fast" if value == "priority" else value) for value in _unique(service_tiers)]],
            "default",
        ),
        _permission_control("codex"),
    ]


def _antigravity_controls(output: str) -> list[dict[str, object]]:
    discovered = [
        _antigravity_model_variant(line.strip())
        for line in output.splitlines()
        if line.strip() and not line.startswith(("Available", "Default"))
    ]
    discovered = [item for item in discovered if item is not None]
    if not discovered:
        return []
    grouped: dict[str, dict[str, object]] = {}
    for model, effort in discovered:
        entry = grouped.setdefault(
            model,
            {
                "label": _provider_model_label(model),
                "efforts": [],
            },
        )
        if effort:
            efforts = entry["efforts"]
            assert isinstance(efforts, list)
            efforts.append(effort)
    models = list(grouped)
    default_model = (
        "gemini-3.6-flash"
        if "gemini-3.6-flash" in grouped
        else "gemini-3.5-flash"
        if "gemini-3.5-flash" in grouped
        else models[0]
    )
    all_efforts = _unique(
        [
            effort
            for entry in grouped.values()
            for effort in list(entry["efforts"])
        ]
    )
    default_effort = (
        "medium"
        if "medium" in list(grouped[default_model]["efforts"])
        else str(list(grouped[default_model]["efforts"])[0])
        if list(grouped[default_model]["efforts"])
        else ""
    )
    return [
        _control(
            "model",
            "모델",
            [
                _model_option(
                    model,
                    str(grouped[model]["label"]),
                    relation_scope="per_model",
                    reasoning_efforts=_unique(list(grouped[model]["efforts"])),
                )
                for model in models
            ],
            default_model,
        ),
        _control(
            "reasoning_effort",
            "추론 강도",
            [_option("", "기본"), *[_option(value) for value in all_efforts]],
            default_effort,
        ),
        _permission_control("antigravity"),
    ]


def _antigravity_model_variant(value: str) -> tuple[str, str] | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    display_match = re.fullmatch(r"(.+?)\s+\((Low|Medium|High)\)", normalized, flags=re.IGNORECASE)
    if display_match:
        model = re.sub(r"[^a-z0-9.]+", "-", display_match.group(1).casefold()).strip("-")
        return model, display_match.group(2).casefold()
    slug_match = re.fullmatch(r"(.+?)-(low|medium|high)", normalized, flags=re.IGNORECASE)
    if slug_match:
        return slug_match.group(1), slug_match.group(2).casefold()
    return normalized, ""


def _provider_model_label(value: str) -> str:
    tokens = str(value or "").split("-")
    labels: list[str] = []
    for token in tokens:
        folded = token.casefold()
        if folded == "gpt":
            labels.append("GPT")
        elif folded == "oss":
            labels.append("OSS")
        elif re.fullmatch(r"\d+b", folded):
            labels.append(folded.upper())
        elif re.fullmatch(r"\d+", folded) and labels and re.fullmatch(r"\d+", labels[-1]):
            labels[-1] = f"{labels[-1]}.{folded}"
        else:
            labels.append(token.capitalize())
    return " ".join(labels)


def _grok_controls(output: str, custom_model_ids: set[str]) -> list[dict[str, object]]:
    models, default_model = classify_grok_models(
        output,
        custom_model_ids=custom_model_ids,
    )
    if not models:
        return []
    return [
        _control(
            "model",
            "모델",
            [_model_option(value, relation_scope="global") for value in models],
            default_model,
        ),
        _control("reasoning_effort", "추론 강도", [_option(value) for value in ("low", "medium", "high")], "medium"),
        _permission_control("grok"),
    ]


_CURSOR_EFFORT_LABELS = {
    "default": "기본",
    "minimal": "Minimal",
    "none": "None",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "extra-high": "Extra High",
    "xhigh": "Extra High",
    "max": "Max",
    "ultra": "Ultra",
    "thinking": "Thinking",
    "thinking-low": "Thinking Low",
    "thinking-medium": "Thinking Medium",
    "thinking-high": "Thinking High",
    "thinking-xhigh": "Thinking Extra High",
}


def _cursor_effort_label(token: str) -> str:
    return _CURSOR_EFFORT_LABELS.get(token, _provider_model_label(token))


def _cursor_controls(output: str) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for line in output.splitlines():
        match = re.match(r"^\s*([A-Za-z0-9._:/-]+)\s+-\s+(.+?)\s*$", line)
        if not match:
            continue
        base, effort, fast = split_cursor_model(match.group(1))
        if not base:
            continue
        if base not in grouped:
            grouped[base] = {"efforts": [], "runtime_variants": [], "plain_label": ""}
            order.append(base)
        entry = grouped[base]
        efforts = entry["efforts"]
        assert isinstance(efforts, list)
        effort_token = effort or "default"
        if effort_token not in efforts:
            efforts.append(effort_token)
        runtime_variants = entry["runtime_variants"]
        assert isinstance(runtime_variants, list)
        variant = {
            "reasoning_effort": effort_token,
            "service_tier": "fast" if fast else "default",
        }
        if variant not in runtime_variants:
            runtime_variants.append(variant)
        if not effort and not fast:
            entry["plain_label"] = match.group(2).strip()
    if not order:
        return []
    default = "auto" if "auto" in grouped else order[0]
    all_efforts = _unique(
        [token for base in order for token in list(grouped[base]["efforts"])]
    )
    real_efforts = [token for token in all_efforts if token != "default"]
    has_any_fast = any(
        any(
            variant.get("service_tier") == "fast"
            for variant in list(grouped[base]["runtime_variants"])
            if isinstance(variant, dict)
        )
        for base in order
    )
    controls: list[dict[str, object]] = [
        _control(
            "model",
            "모델",
            [
                _model_option(
                    base,
                    str(grouped[base]["plain_label"]) or _provider_model_label(base),
                    selection_kind="alias" if base == "auto" else "exact",
                    relation_scope="per_model",
                    reasoning_efforts=list(grouped[base]["efforts"]),
                    service_tiers=_unique(
                        [
                            str(variant.get("service_tier") or "")
                            for variant in list(grouped[base]["runtime_variants"])
                            if isinstance(variant, dict)
                            and variant.get("service_tier") == "fast"
                        ]
                    ),
                    runtime_variants=list(grouped[base]["runtime_variants"]),
                )
                for base in order
            ],
            default,
            kind="combobox",
        ),
    ]
    if real_efforts:
        controls.append(
            _control(
                "reasoning_effort",
                "추론 강도",
                [
                    _option("default", "기본"),
                    *[_option(token, _cursor_effort_label(token)) for token in real_efforts],
                ],
                "default",
            )
        )
    if has_any_fast:
        controls.append(
            _control(
                "service_tier",
                "응답 속도",
                [_option("default", "기본"), _option("fast", "Fast")],
                "default",
            )
        )
    controls.append(_permission_control("cursor"))
    return controls


def _opencode_controls(model_options: list[dict[str, object]]) -> list[dict[str, object]]:
    values = [str(option["value"]) for option in model_options]
    default = "opencode-go/glm-5.2" if "opencode-go/glm-5.2" in values else values[0]
    return [
        _control("model", "모델", model_options, default, kind="combobox"),
        _control("variant", "모델 변형", [_option("", "기본"), _option("high"), _option("max")], ""),
        _permission_control("opencode"),
    ]


def _ollama_model_entries(output: str) -> list[dict[str, str]]:
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        columns = re.split(r"\s{2,}", line.strip())
        if not columns or columns[0].casefold() == "name":
            continue
        model = columns[0]
        if model in seen:
            continue
        seen.add(model)
        size = columns[2] if len(columns) >= 3 else ""
        models.append(
            {
                "value": model,
                "execution_location": "cloud" if size == "-" else "local",
            }
        )
    return models


def _ollama_model_label(value: str) -> str:
    base, separator, tag = value.partition(":")
    normalized_base = re.sub(r"(?<=[A-Za-z])(?=\d)", "-", base)
    label = _provider_model_label(normalized_base)
    if separator and tag.casefold() not in {"cloud", "latest"}:
        normalized_tag = tag.upper() if re.fullmatch(r"\d+b", tag, re.IGNORECASE) else tag
        label = f"{label} {normalized_tag}"
    return label


def _ollama_supports_tools(output: str) -> bool:
    capabilities = False
    for line in output.splitlines():
        stripped = line.strip().casefold()
        if stripped == "capabilities":
            capabilities = True
            continue
        if capabilities and stripped == "tools":
            return True
        if capabilities and stripped and not line.startswith((" ", "\t")):
            capabilities = False
    return False


def _lmstudio_tool_models(output: str) -> list[str]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    models: list[str] = []
    for entry in payload if isinstance(payload, list) else []:
        if (
            not isinstance(entry, dict)
            or entry.get("type") != "llm"
            or entry.get("trainedForToolUse") is not True
        ):
            continue
        identifier = str(entry.get("identifier") or entry.get("modelKey") or "").strip()
        if identifier:
            models.append(identifier)
    return _unique(models)


def _local_openai_payload(
    definition,
    *,
    models: list[str] | None = None,
    model_options: list[dict[str, object]] | None = None,
    default_model: str,
) -> dict[str, object]:
    resolved_options = model_options or [
        _model_option(model, _provider_model_label(model))
        for model in (models or [])
    ]
    return {
        **definition.public_payload(),
        "available": True,
        "startable": True,
        "discovery_status": "ready",
        "discovery_error": "",
        "discovery_error_code": "",
        "catalog_source": "discovered",
        "fixed_values": {},
        "controls": [
            _control(
                "model",
                "모델",
                resolved_options,
                default_model,
                kind="combobox",
            ),
            _permission_control(definition.provider_id),
        ],
        "work_harness_available": True,
    }


def _unavailable_structured_payload(
    definition,
    *,
    available: bool = False,
    error: str,
    error_code: str,
) -> dict[str, object]:
    return {
        **definition.public_payload(),
        "available": available,
        "startable": False,
        "discovery_status": "failed",
        "discovery_error": error,
        "discovery_error_code": error_code,
        "catalog_source": "discovered",
        "fixed_values": {"permission_mode": "meeting_read_only"},
        "controls": [],
    }


def _claude_controls(
    models: list[str],
    *,
    help_output: str = "",
    xhigh_models: list[str] | None = None,
    ultracode_available: bool = False,
) -> list[dict[str, object]]:
    if not models:
        return []
    effort_match = re.search(
        r"--effort\s+<[^>]+>.*?\[possible values:\s*([^\]]+)\]",
        help_output,
        flags=re.IGNORECASE | re.DOTALL,
    )
    discovered_efforts = (
        [
            value.strip()
            for value in effort_match.group(1).split(",")
            if value.strip()
        ]
        if effort_match
        else []
    )
    efforts = tuple(discovered_efforts or ("low", "medium", "high", "xhigh", "max"))
    xhigh_model_set = set(xhigh_models or [])
    model_efforts: dict[str, list[str]] = {}
    for model in models:
        supported = [
            effort
            for effort in efforts
            if effort != "xhigh" or model in xhigh_model_set
        ]
        if ultracode_available and model in xhigh_model_set:
            supported.append("ultracode")
        model_efforts[model] = supported
    visible_efforts = _unique(
        [
            effort
            for model in models
            for effort in model_efforts[model]
        ]
    )
    return [
        _control(
            "model",
            "모델",
            [
                _model_option(
                    model,
                    _provider_model_label(model),
                    relation_scope="per_model",
                    reasoning_efforts=model_efforts[model],
                    service_tiers=["fast"],
                )
                for model in models
            ],
            "claude-haiku-4-5" if "claude-haiku-4-5" in models else models[0],
            kind="combobox",
        ),
        _control(
            "reasoning_effort",
            "추론 강도",
            [
                _reasoning_option(
                    value,
                    (
                        "xhigh effort + dynamic workflow orchestration · 현재 세션에만 적용"
                        if value == "ultracode"
                        else ""
                    ),
                )
                for value in visible_efforts
            ],
            "high",
        ),
        _control("service_tier", "응답 속도", [_option("default", "기본"), _option("fast", "Fast")], "default"),
        _permission_control("claude"),
    ]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


PROVIDER_CAPABILITIES = ProviderCapabilityCatalog(
    secret_resolver=PROVIDER_SECRETS.get,
)


def provider_catalog_payload(*, refresh: bool = False) -> list[dict[str, object]]:
    return PROVIDER_CAPABILITIES.payload(refresh=refresh)


def provider_catalog_snapshot(*, refresh: bool = False) -> dict[str, object]:
    return PROVIDER_CAPABILITIES.snapshot(refresh=refresh)
