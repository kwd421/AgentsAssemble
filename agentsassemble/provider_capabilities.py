from __future__ import annotations

import json
import hashlib
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from agentsassemble.native_cli_providers import NATIVE_CLI_PROVIDER_CATALOG
from agentsassemble.process_environment import sanitized_provider_environment


ProbeRunner = Callable[[list[str], float], tuple[int, str, str]]
CatalogListener = Callable[[dict[str, object]], None]


class ProviderCatalogSelectionError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


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

    def snapshot(self, *, refresh: bool = False) -> dict[str, object]:
        now = time.monotonic()
        with self._lock:
            if not refresh and self._cached and now - self._cached_at < self._ttl_seconds:
                return self._snapshot_locked()
            if not refresh:
                self._start_background_refresh_locked()
                return self._snapshot_locked()
        return self._refresh_snapshot()

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
    ) -> None:
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
            controls = list(provider.get("controls") or [])
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
            value = str(values.get(key) or "")
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
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                continue
        return snapshot

    def _start_background_refresh_locked(self) -> None:
        if self._refreshing:
            return
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
                for listener in listeners:
                    try:
                        listener(snapshot)
                    except Exception:
                        continue

        threading.Thread(target=refresh_catalog, name="provider-capability-refresh", daemon=True).start()

    def _native_payload(self, definition) -> dict[str, object]:
        resolved = self._resolver(definition.executable)
        base = definition.public_payload()
        base.update(
            {
                "available": bool(resolved),
                "startable": False,
                "resolved_executable": str(resolved or ""),
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
            "resolved_executable": str(resolved or ""),
            "discovery_status": status,
            "discovery_error": error,
            "catalog_source": "discovered",
            "controls": controls,
        }

    def _snapshot_locked(self) -> dict[str, object]:
        providers = [dict(item) for item in self._cached]
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
                    "resolved_executable": str(resolved or ""),
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
                "resolved_executable": str(resolved or ""),
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
            _option(
                str(model["slug"]),
                str(model.get("display_name") or model["slug"]),
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
    return [_control("model", "모델", [_option(value) for value in models], default), _permission_control()]


def _grok_controls(output: str) -> list[dict[str, object]]:
    models = re.findall(r"(?:\*|-)[ \t]+([A-Za-z0-9._-]+)", output)
    default_match = re.search(r"Default model:\s*([A-Za-z0-9._-]+)", output)
    models = _unique(models)
    if not models:
        return []
    return [
        _control("model", "모델", [_option(value) for value in models], default_match.group(1) if default_match else models[0]),
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
        _control("model", "모델", [_option(value) for value in models], default, kind="combobox"),
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
        "resolved_executable": "",
        "discovery_status": "ready",
        "catalog_source": "static_manifest",
        "controls": [
            _control(
                "model",
                "모델",
                [_option("deepseek-v4-flash", "DeepSeek V4 Flash"), _option("deepseek-v4-pro", "DeepSeek V4 Pro")],
                "deepseek-v4-flash",
            ),
            _control("reasoning_effort", "추론 강도", [_option("high"), _option("max")], "high"),
            _control("variant", "Thinking", [_option("thinking", "사용"), _option("non_thinking", "사용 안 함")], "thinking"),
        ],
    }


def _claude_manifest_controls() -> list[dict[str, object]]:
    return [
        _control("model", "모델", [_option("haiku"), _option("sonnet"), _option("opus")], "haiku", kind="combobox"),
        _control("reasoning_effort", "추론 강도", [_option(value) for value in ("low", "medium", "high", "xhigh", "max")], "high"),
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
