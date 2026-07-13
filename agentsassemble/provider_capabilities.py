from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from agentsassemble.native_cli_providers import NATIVE_CLI_PROVIDER_CATALOG
from agentsassemble.process_environment import sanitized_provider_environment


ProbeRunner = Callable[[list[str], float], tuple[int, str, str]]
CatalogListener = Callable[[dict[str, object]], None]


class ProviderCatalogSelectionError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedProviderSelection:
    catalog_revision: str
    provider_id: str
    provider_kind: str
    model: str
    model_selection_kind: str
    reasoning_effort: str
    service_tier: str
    variant: str
    permission_mode: str


class ProviderCapabilityCatalog:
    """Fail-closed native option discovery with a bounded refresh cache."""

    def __init__(
        self,
        *,
        runner: ProbeRunner | None = None,
        resolver: Callable[[str], str | None] = shutil.which,
        ttl_seconds: float = 300.0,
    ) -> None:
        self._runner = runner or _run_probe
        self._resolver = resolver
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
            self._status = "loading"
            self._refreshing = True
        try:
            return self._refresh_snapshot()
        except Exception:
            self._mark_refresh_failed()
            raise

    def payload(self, *, refresh: bool = False) -> list[dict[str, object]]:
        return list(self.snapshot(refresh=refresh).get("providers") or [])

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
            for key in ("model", "reasoning_effort", "service_tier", "variant", "permission_mode")
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
                    "permission_mode": "unsupported_permission_mode",
                }.get(key, "unsupported_provider_option")
                raise ProviderCatalogSelectionError(
                    f"Unsupported {key} value for provider {provider_id}.",
                    code=code,
                )
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
        selection_kind = str(metadata.get("selection_kind") or "")
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
            permission_mode=resolved_values["permission_mode"],
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
        if not selected_value:
            return
        relation_scope = str(metadata.get("relation_scope") or "")
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

    def _refresh_snapshot(self) -> dict[str, object]:
        payload = [self._native_payload(definition) for definition in NATIVE_CLI_PROVIDER_CATALOG]
        payload.append(self._opencode_payload())
        payload.append(_deepseek_payload())
        revision = _catalog_revision(payload)
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
        self._status = "loading"
        self._refreshing = True

        def refresh_catalog() -> None:
            try:
                self._refresh_snapshot()
            except Exception:
                with self._lock:
                    self._status = "failed"
                    self._refreshing = False
                    snapshot = self._snapshot_locked()
                    listeners = list(self._listeners.values())
                self._notify_listeners(snapshot, listeners, category="refresh_failed")

        threading.Thread(target=refresh_catalog, name="provider-capability-refresh", daemon=True).start()

    def _mark_refresh_failed(self) -> None:
        with self._lock:
            self._status = "failed"
            self._refreshing = False

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
            base["discovery_error"] = "configured command missing"
            return base
        try:
            discovered = self._discover(definition.provider_id, str(resolved))
        except Exception as error:
            base["discovery_error"] = type(error).__name__
            return base
        if discovered:
            base["controls"] = discovered
            base["discovery_status"] = "ready"
            base["startable"] = True
            if definition.provider_id == "claude":
                base["catalog_source"] = "static_manifest"
        else:
            base["discovery_status"] = "failed"
            base["discovery_error"] = "model discovery returned no supported options"
        return base

    def _discover(self, provider_id: str, executable: str) -> list[dict[str, object]]:
        if provider_id == "codex":
            code, output, _stderr = self._runner([executable, "debug", "models", "--bundled"], 6.0)
            return _codex_controls(output) if code == 0 else []
        if provider_id == "antigravity":
            code, output, _stderr = self._runner([executable, "models"], 5.0)
            return _antigravity_controls(output) if code == 0 else []
        if provider_id == "grok":
            code, output, _stderr = self._runner([executable, "models"], 5.0)
            return _grok_controls(output) if code == 0 else []
        if provider_id == "claude":
            code, _output, _stderr = self._runner([executable, "--help"], 5.0)
            return _claude_manifest_controls() if code == 0 else []
        return []

    def _opencode_payload(self) -> dict[str, object]:
        resolved = self._resolver("opencode")
        controls: list[dict[str, object]] = []
        status = "failed" if not resolved else "loading"
        error = "configured command missing" if not resolved else ""
        if resolved:
            try:
                code, output, _stderr = self._runner([str(resolved), "models", "--verbose"], 8.0)
                models = _opencode_models(output) if code == 0 else []
                if models:
                    controls = _opencode_controls(models)
                    status = "ready"
            except Exception as exception:
                error = type(exception).__name__
        return {
            "id": "opencode",
            "display_name": "OpenCode",
            "provider_kind": "opencode_server",
            "runtime_kind": "opencode",
            "connection_kind": "native_cli_bridge",
            "executable": "opencode",
            "default_model": "opencode-go/glm-5.2",
            "interactive": True,
            "available": bool(resolved),
            "startable": bool(resolved and controls),
            "discovery_status": status,
            "discovery_error": error,
            "catalog_source": "discovered",
            "controls": controls,
        }

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
            "diagnostics": {
                "catalog_listener_error_count": self._listener_error_count,
                "listener_type": self._listener_last_type,
                "exception_type": self._listener_last_exception_type,
                "last_failure_at": self._listener_last_error_at,
                "category": self._listener_last_category,
            },
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
        payload.append(
            {
                "id": "opencode",
                "display_name": "OpenCode",
                "provider_kind": "opencode_server",
                "runtime_kind": "opencode",
                "connection_kind": "native_cli_bridge",
                "executable": "opencode",
                "default_model": "opencode-go/glm-5.2",
                "interactive": True,
                "available": bool(resolved),
                "startable": False,
                "discovery_status": "loading" if resolved else "failed",
                "discovery_error": "" if resolved else "configured command missing",
                "catalog_source": "discovered",
                "controls": [],
            }
        )
        payload.append(_deepseek_payload())
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


def _permission_control() -> dict[str, object]:
    return _control(
        "permission_mode",
        "권한",
        [
            _option("meeting_read_only", "읽기 전용"),
            _option("workspace_write", "작업 폴더 쓰기"),
        ],
        "meeting_read_only",
    )


def _codex_controls(output: str) -> list[dict[str, object]]:
    payload = json.loads(output)
    models = payload.get("models") if isinstance(payload, dict) else []
    model_options: list[dict[str, object]] = []
    efforts: list[str] = []
    service_tiers: list[str] = []
    for model in models if isinstance(models, list) else []:
        if not isinstance(model, dict) or not model.get("slug"):
            continue
        model_efforts = [
            str(item.get("effort"))
            for item in list(model.get("supported_reasoning_levels") or [])
            if isinstance(item, dict) and item.get("effort")
        ]
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
        _control("reasoning_effort", "추론 강도", [_option(value) for value in _unique(efforts)], "low"),
        _control(
            "service_tier",
            "응답 속도",
            [_option("default", "기본"), *[_option(value, "Fast" if value == "priority" else value) for value in _unique(service_tiers)]],
            "default",
        ),
        _permission_control(),
    ]


def _antigravity_controls(output: str) -> list[dict[str, object]]:
    models = [line.strip() for line in output.splitlines() if line.strip() and not line.startswith(("Available", "Default"))]
    if not models:
        return []
    default = "Gemini 3.5 Flash (Medium)" if "Gemini 3.5 Flash (Medium)" in models else models[0]
    return [_control("model", "모델", [_model_option(value) for value in models], default), _permission_control()]


def _grok_controls(output: str) -> list[dict[str, object]]:
    models = re.findall(r"(?:\*|-)[ \t]+([A-Za-z0-9._-]+)", output)
    default_match = re.search(r"Default model:\s*([A-Za-z0-9._-]+)", output)
    models = _unique(models)
    if not models:
        return []
    return [
        _control(
            "model",
            "모델",
            [_model_option(value, relation_scope="global") for value in models],
            default_match.group(1) if default_match else models[0],
        ),
        _control("reasoning_effort", "추론 강도", [_option(value) for value in ("low", "medium", "high")], "medium"),
        _permission_control(),
    ]


def _opencode_models(output: str) -> list[str]:
    candidates = re.findall(r"(?m)^\s*([A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+)\s*$", output)
    if not candidates:
        candidates = re.findall(r'"id"\s*:\s*"([A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+)"', output)
    return _unique(candidates)


def _opencode_controls(models: list[str]) -> list[dict[str, object]]:
    default = "opencode-go/glm-5.2" if "opencode-go/glm-5.2" in models else models[0]
    return [
        _control("model", "모델", [_model_option(value) for value in models], default, kind="combobox"),
        _control("variant", "모델 변형", [_option("", "기본"), _option("high"), _option("max")], ""),
        _permission_control(),
    ]


def _deepseek_payload() -> dict[str, object]:
    return {
        "id": "deepseek",
        "display_name": "DeepSeek API",
        "provider_kind": "deepseek_api",
        "runtime_kind": "api",
        "connection_kind": "native_cli_bridge",
        "executable": "",
        "default_model": "deepseek-v4-flash",
        "interactive": True,
        "available": True,
        "startable": True,
        "discovery_status": "ready",
        "catalog_source": "static_manifest",
        "fixed_values": {"permission_mode": "meeting_read_only"},
        "controls": [
            _control(
                "model",
                "모델",
                [
                    _model_option("deepseek-v4-flash", "DeepSeek V4 Flash", relation_scope="global"),
                    _model_option("deepseek-v4-pro", "DeepSeek V4 Pro", relation_scope="global"),
                ],
                "deepseek-v4-flash",
            ),
            _control("reasoning_effort", "추론 강도", [_option("high"), _option("max")], "high"),
            _control("variant", "Thinking", [_option("thinking", "사용"), _option("non_thinking", "사용 안 함")], "thinking"),
        ],
    }


def _claude_manifest_controls() -> list[dict[str, object]]:
    efforts = ("low", "medium", "high", "xhigh", "max")
    relation = {
        "relation_scope": "per_model",
        "reasoning_efforts": list(efforts),
        "service_tiers": ["fast"],
    }
    return [
        _control(
            "model",
            "모델",
            [
                _model_option("claude-haiku-4-5", "Claude Haiku 4.5", **relation),
                _model_option("claude-sonnet-4-6", "Claude Sonnet 4.6", **relation),
                _model_option("claude-sonnet-5", "Claude Sonnet 5", **relation),
                _model_option("claude-opus-4-6", "Claude Opus 4.6", **relation),
                _model_option("haiku", "Haiku (latest alias)", selection_kind="alias", **relation),
                _model_option("sonnet", "Sonnet (latest alias)", selection_kind="alias", **relation),
                _model_option("opus", "Opus (latest alias)", selection_kind="alias", **relation),
            ],
            "claude-haiku-4-5",
            kind="combobox",
        ),
        _control("reasoning_effort", "추론 강도", [_option(value) for value in efforts], "high"),
        _control("service_tier", "응답 속도", [_option("default", "기본"), _option("fast", "Fast")], "default"),
        _permission_control(),
    ]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _catalog_revision(providers: list[dict[str, object]]) -> str:
    public_contract = [
        {
            "id": provider.get("id"),
            "source": provider.get("catalog_source"),
            "status": provider.get("discovery_status"),
            "controls": provider.get("controls"),
            "fixed_values": provider.get("fixed_values"),
        }
        for provider in providers
    ]
    encoded = json.dumps(public_contract, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"cat-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


PROVIDER_CAPABILITIES = ProviderCapabilityCatalog()


def provider_catalog_payload(*, refresh: bool = False) -> list[dict[str, object]]:
    return PROVIDER_CAPABILITIES.payload(refresh=refresh)


def provider_catalog_snapshot(*, refresh: bool = False) -> dict[str, object]:
    return PROVIDER_CAPABILITIES.snapshot(refresh=refresh)
