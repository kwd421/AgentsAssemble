from __future__ import annotations

import json
import math
import os
import shutil
from collections.abc import Callable
from http.client import HTTPConnection
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from agentsassemble.adapters.registry import default_provider_registry, validate_binding
from agentsassemble.config import (
    agent_bindings_from_config,
    load_agent_runtime_config,
    permissions_from_config,
    providers_from_config,
)
from agentsassemble.models import AgentBinding, PermissionProfile, ProviderConfig


AUTH_REQUIRED_PROVIDER_KINDS = {"anthropic", "gemini", "grok"}
ENDPOINT_REQUIRED_PROVIDER_KINDS = {"remote_http_bridge"}
LOCAL_COMMAND_PROVIDER_KINDS = {"local_cli"}
CODEX_COMMAND_PROVIDER_KINDS = {"codex", "codex_live_session"}
DEFAULT_LOCAL_OPENAI_ENDPOINT = "http://127.0.0.1:1234/v1"
PROBE_MODES = {"none", "local"}
LOCAL_PROBE_PROVIDER_KINDS = {"local_openai_compatible"}
LOOPBACK_HOSTS = {"localhost"}

ProbeRequester = Callable[[str, float], dict[str, object]]


def provider_health_report(
    config_path: Path,
    *,
    command_resolver: Callable[[str], str | None] | None = None,
    probe_mode: str = "none",
    probe_requester: ProbeRequester | None = None,
    probe_timeout_seconds: float = 2.0,
) -> dict[str, object]:
    if probe_mode not in PROBE_MODES:
        raise ValueError("Provider health probe_mode must be 'none' or 'local'.")
    try:
        probe_timeout = float(probe_timeout_seconds)
    except (TypeError, ValueError) as error:
        raise ValueError("Provider health probe_timeout_seconds must be a finite non-negative number.") from error
    if not math.isfinite(probe_timeout) or probe_timeout < 0:
        raise ValueError("Provider health probe_timeout_seconds must be a finite non-negative number.")
    resolver = command_resolver or _resolve_command_path
    requester = probe_requester or _request_probe_json
    try:
        data = load_agent_runtime_config(config_path)
        if not isinstance(data, dict):
            raise ValueError("Agent runtime config must be a JSON object.")
        providers = providers_from_config(data)
        permissions = permissions_from_config(data)
        bindings = agent_bindings_from_config(data)
    except Exception as error:
        return _failed_config_report(config_path, str(error), probe_mode=probe_mode)

    registry = default_provider_registry()
    catalog = {entry["kind"]: entry for entry in registry.catalog()}
    top_checks = [
        {"id": "config_load", "status": "ok", "message": "Agent runtime config loaded."},
        *_duplicate_checks(data),
    ]
    provider_reports = [
        _provider_report(
            provider,
            registry_catalog=catalog,
            command_resolver=resolver,
            probe_mode=probe_mode,
            probe_requester=requester,
            probe_timeout_seconds=probe_timeout,
        )
        for provider in providers.values()
    ]
    provider_reports_by_id = {str(report["provider_id"]): report for report in provider_reports}
    binding_reports = [
        _binding_report(binding, providers, permissions, provider_reports_by_id)
        for binding in bindings
    ]
    summary = _summary(top_checks, provider_reports, binding_reports)
    status = "failed" if summary["checks_failed"] else "degraded" if summary["warnings"] else "ok"
    return {
        "status": status,
        "config_path": str(config_path),
        "probe_mode": probe_mode,
        "summary": summary,
        "checks": top_checks,
        "providers": provider_reports,
        "bindings": binding_reports,
    }


def _failed_config_report(config_path: Path, message: str, *, probe_mode: str = "none") -> dict[str, object]:
    return {
        "status": "failed",
        "config_path": str(config_path),
        "probe_mode": probe_mode,
        "summary": {
            "providers": 0,
            "failed_providers": 0,
            "bindings": 0,
            "failed_bindings": 0,
            "checks_failed": 1,
            "warnings": 0,
        },
        "checks": [{"id": "config_load", "status": "failed", "message": message}],
        "providers": [],
        "bindings": [],
    }


def _duplicate_checks(data: dict[str, object]) -> list[dict[str, str]]:
    return [
        _duplicate_check(data.get("providers"), "provider_ids", "id", "Provider ids"),
        _duplicate_check(data.get("permission_profiles"), "permission_ids", "id", "Permission profile ids"),
        _duplicate_check(data.get("agent_bindings"), "agent_ids", "agent_id", "Agent ids"),
        _duplicate_role_binding_check(data.get("agent_bindings")),
    ]


def _duplicate_check(value: object, check_id: str, field: str, label: str) -> dict[str, str]:
    if not isinstance(value, list):
        return {"id": check_id, "status": "ok", "message": f"{label} are unique."}
    counts: dict[str, int] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get(field) or "")
        if item_id:
            counts[item_id] = counts.get(item_id, 0) + 1
    duplicates = sorted(item_id for item_id, count in counts.items() if count > 1)
    if duplicates:
        return {
            "id": check_id,
            "status": "failed",
            "message": f"Duplicate {label.casefold()}: {', '.join(duplicates)}",
        }
    return {"id": check_id, "status": "ok", "message": f"{label} are unique."}


def _duplicate_role_binding_check(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {"id": "role_bindings", "status": "ok", "message": "Role bindings are unique."}
    counts: dict[str, int] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        role_id = str(item.get("role_id") or "")
        if role_id:
            counts[role_id] = counts.get(role_id, 0) + 1
    duplicates = sorted(role_id for role_id, count in counts.items() if count > 1)
    if duplicates:
        return {"id": "role_bindings", "status": "failed", "message": f"Duplicate role bindings: {', '.join(duplicates)}"}
    return {"id": "role_bindings", "status": "ok", "message": "Role bindings are unique."}


def _provider_report(
    provider: ProviderConfig,
    *,
    registry_catalog: dict[str, dict[str, object]],
    command_resolver: Callable[[str], str | None],
    probe_mode: str,
    probe_requester: ProbeRequester,
    probe_timeout_seconds: float,
) -> dict[str, object]:
    checks: list[dict[str, object]] = [
        _provider_kind_check(provider, registry_catalog),
        _endpoint_check(provider),
        _auth_ref_check(provider),
        _command_check(provider, command_resolver),
    ]
    if probe_mode == "local":
        checks.append(_local_probe_check(provider, probe_requester, probe_timeout_seconds))
    status = _status_from_checks(checks)
    command_path = ""
    for check in checks:
        if check["id"] == "command" and check["status"] == "ok":
            command_path = str(check.get("path") or "")
    catalog_entry = registry_catalog.get(provider.kind) if isinstance(provider.kind, str) else {}
    catalog_entry = catalog_entry or {}
    return {
        "provider_id": provider.id,
        "kind": provider.kind,
        "display_name": provider.display_name,
        "status": status,
        "endpoint_configured": bool(provider.endpoint),
        "auth_ref_configured": bool(provider.auth_ref),
        "command_configured": bool(provider.command),
        "command_path": command_path,
        "capabilities": catalog_entry.get("capabilities", {}),
        "checks": checks,
    }


def _provider_kind_check(
    provider: ProviderConfig,
    registry_catalog: dict[str, dict[str, object]],
) -> dict[str, str]:
    if not isinstance(provider.kind, str):
        return {"id": "provider_kind", "status": "failed", "message": "Provider kind must be a string."}
    catalog_entry = registry_catalog.get(provider.kind)
    if not catalog_entry:
        return {
            "id": "provider_kind",
            "status": "failed",
            "message": f"Provider kind {provider.kind} is not registered.",
        }
    if catalog_entry.get("status") != "available":
        return {
            "id": "provider_kind",
            "status": "failed",
            "message": f"Provider kind {provider.kind} is planned, not available for execution.",
        }
    return {
        "id": "provider_kind",
        "status": "ok",
        "message": f"Provider kind {provider.kind} is available.",
    }


def _endpoint_check(provider: ProviderConfig) -> dict[str, str]:
    if not isinstance(provider.kind, str):
        return {"id": "endpoint", "status": "ok", "message": "Endpoint check skipped until provider kind is valid."}
    if provider.endpoint is not None and not isinstance(provider.endpoint, str):
        return {"id": "endpoint", "status": "failed", "message": "Endpoint must be a string."}
    if provider.kind in ENDPOINT_REQUIRED_PROVIDER_KINDS and not provider.endpoint:
        return {
            "id": "endpoint",
            "status": "failed",
            "message": f"Provider kind {provider.kind} requires endpoint.",
        }
    if provider.endpoint:
        return {"id": "endpoint", "status": "ok", "message": "Endpoint is configured."}
    if provider.kind == "local_openai_compatible":
        return {"id": "endpoint", "status": "ok", "message": "Endpoint uses the local OpenAI-compatible default."}
    return {"id": "endpoint", "status": "ok", "message": "Endpoint uses the adapter default or is not required."}


def _auth_ref_check(provider: ProviderConfig) -> dict[str, str]:
    if not isinstance(provider.kind, str):
        return {"id": "auth_ref", "status": "ok", "message": "auth_ref check skipped until provider kind is valid."}
    if provider.auth_ref is not None and not isinstance(provider.auth_ref, str):
        return {"id": "auth_ref", "status": "failed", "message": "auth_ref must be a string."}
    if provider.kind in ENDPOINT_REQUIRED_PROVIDER_KINDS and not provider.auth_ref:
        return {"id": "auth_ref", "status": "failed", "message": f"Provider kind {provider.kind} requires auth_ref."}
    if provider.kind in AUTH_REQUIRED_PROVIDER_KINDS:
        if _auth_ref_available(provider.auth_ref):
            return {"id": "auth_ref", "status": "ok", "message": "Required auth_ref is available."}
        return {"id": "auth_ref", "status": "failed", "message": "Required auth_ref is not available."}
    if provider.auth_ref and not _auth_ref_available(provider.auth_ref):
        return {"id": "auth_ref", "status": "failed", "message": "Configured auth_ref is not available."}
    if provider.auth_ref:
        return {"id": "auth_ref", "status": "ok", "message": "Configured auth_ref is available."}
    return {"id": "auth_ref", "status": "ok", "message": "auth_ref is not required."}


def _auth_ref_available(auth_ref: object) -> bool:
    if not isinstance(auth_ref, str) or not auth_ref:
        return False
    if auth_ref.startswith("env:"):
        env_name = auth_ref.removeprefix("env:")
        return bool(env_name) and env_name in os.environ
    if auth_ref.startswith("literal:"):
        return bool(auth_ref.removeprefix("literal:"))
    return True


def _command_check(
    provider: ProviderConfig,
    command_resolver: Callable[[str], str | None],
) -> dict[str, str]:
    if not isinstance(provider.kind, str):
        return {"id": "command", "status": "ok", "message": "Command check skipped until provider kind is valid."}
    if provider.kind in LOCAL_COMMAND_PROVIDER_KINDS:
        executable = str((provider.command or [""])[0]).strip()
        if not executable:
            return {"id": "command", "status": "failed", "message": "Command is empty."}
        resolved = command_resolver(executable)
        if resolved:
            return {"id": "command", "status": "ok", "message": f"Command found: {executable}", "path": resolved}
        return {"id": "command", "status": "failed", "message": f"Command not found: {executable}"}
    if provider.kind in CODEX_COMMAND_PROVIDER_KINDS:
        resolved = command_resolver("codex")
        if resolved:
            return {"id": "command", "status": "ok", "message": "Command found: codex", "path": resolved}
        return {"id": "command", "status": "failed", "message": "Command not found: codex"}
    return {"id": "command", "status": "ok", "message": "Provider kind does not require a local command."}


def _local_probe_check(
    provider: ProviderConfig,
    requester: ProbeRequester,
    timeout_seconds: float,
) -> dict[str, object]:
    if not isinstance(provider.kind, str):
        return {"id": "local_probe", "status": "ok", "message": "Local probe skipped until provider kind is valid."}
    if provider.kind not in LOCAL_PROBE_PROVIDER_KINDS:
        return {
            "id": "local_probe",
            "status": "ok",
            "message": "Local probe is not applicable for this provider kind.",
        }
    if provider.endpoint is not None and not isinstance(provider.endpoint, str):
        return {"id": "local_probe", "status": "failed", "message": "Endpoint must be a string."}
    probe_url = _local_openai_models_url(provider.endpoint or DEFAULT_LOCAL_OPENAI_ENDPOINT)
    if probe_url is None:
        return {
            "id": "local_probe",
            "status": "failed",
            "message": "Local probe only allows loopback HTTP endpoints.",
        }
    try:
        payload = requester(probe_url, timeout_seconds)
    except Exception:
        return {
            "id": "local_probe",
            "status": "failed",
            "message": "Local OpenAI-compatible models endpoint is unreachable.",
        }
    models = _model_list_from_probe_payload(payload)
    if models is None:
        return {
            "id": "local_probe",
            "status": "failed",
            "message": "Local OpenAI-compatible models endpoint did not return a model list.",
        }
    if not models:
        return {
            "id": "local_probe",
            "status": "failed",
            "message": "Local OpenAI-compatible models endpoint returned no models.",
            "models": 0,
        }
    return {
        "id": "local_probe",
        "status": "ok",
        "message": "Local OpenAI-compatible models endpoint is reachable.",
        "models": len(models),
    }


def _local_openai_models_url(endpoint: str) -> str | None:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or not _is_loopback_host(parsed.hostname):
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    base_path = parsed.path.rstrip("/")
    models_path = f"{base_path}/models" if base_path else "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, models_path, "", ""))


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.casefold() in LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _model_list_from_probe_payload(payload: object) -> list[object] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        return data
    models = payload.get("models")
    if isinstance(models, list):
        return models
    return None


def _request_probe_json(url: str, timeout_seconds: float) -> dict[str, object]:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not _is_loopback_host(parsed.hostname):
        raise ValueError("probe URL is not an approved loopback HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("probe URL includes disallowed URL components")
    if parsed.path.rstrip("/").split("/")[-1] != "models":
        raise ValueError("probe URL path is not a models endpoint")

    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout_seconds)
    try:
        connection.request("GET", parsed.path or "/", headers={"Accept": "application/json"})
        response = connection.getresponse()
        data = response.read(1_000_000)
        if response.status != 200:
            raise ValueError("probe endpoint returned a non-200 status")
    finally:
        connection.close()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def _binding_report(
    binding: AgentBinding,
    providers: dict[str, ProviderConfig],
    permissions: dict[str, PermissionProfile],
    provider_reports_by_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    registry = default_provider_registry()
    checks: list[dict[str, str]] = []
    provider_id = binding.provider_id if isinstance(binding.provider_id, str) else ""
    permission_profile_id = binding.permission_profile_id if isinstance(binding.permission_profile_id, str) else ""
    provider = providers.get(provider_id) if provider_id else None
    permission = permissions.get(permission_profile_id) if permission_profile_id else None
    if not isinstance(binding.provider_id, str):
        checks.append({"id": "provider_defined", "status": "failed", "message": "Provider id must be a string."})
    else:
        checks.append(
            {"id": "provider_defined", "status": "ok", "message": "Provider is defined."}
            if provider
            else {"id": "provider_defined", "status": "failed", "message": f"Provider {provider_id} is not defined."}
        )
    if not isinstance(binding.permission_profile_id, str):
        checks.append(
            {"id": "permission_defined", "status": "failed", "message": "Permission profile id must be a string."}
        )
    else:
        checks.append(
            {"id": "permission_defined", "status": "ok", "message": "Permission profile is defined."}
            if permission
            else {
                "id": "permission_defined",
                "status": "failed",
                "message": f"Permission profile {permission_profile_id} is not defined.",
            }
        )
    provider_report = provider_reports_by_id.get(provider_id) if provider_id else None
    if provider_report and provider_report.get("status") == "failed":
        checks.append(
            {
                "id": "provider_ready",
                "status": "failed",
                "message": f"Provider {binding.provider_id} is not ready.",
            }
        )
    elif provider_report:
        checks.append({"id": "provider_ready", "status": "ok", "message": "Provider checks passed."})
    if provider and permission:
        try:
            capabilities = registry.capabilities_for(provider)
            validate_binding(binding, provider, permission, capabilities)
        except Exception as error:
            checks.append({"id": "permissions", "status": "failed", "message": str(error)})
        else:
            checks.append({"id": "permissions", "status": "ok", "message": "Binding permissions are compatible."})
        if permission.secrets:
            checks.append(
                {
                    "id": "secrets",
                    "status": "failed",
                    "message": f"Agent {binding.agent_id} requests secret access during a meeting-only run.",
                }
            )
        else:
            checks.append({"id": "secrets", "status": "ok", "message": "Binding does not request secret access."})
    status = _status_from_checks(checks)
    return {
        "agent_id": binding.agent_id,
        "role_id": binding.role_id,
        "provider_id": binding.provider_id,
        "permission_profile_id": binding.permission_profile_id,
        "status": status,
        "checks": checks,
    }


def _summary(
    top_checks: list[dict[str, object]],
    providers: list[dict[str, object]],
    bindings: list[dict[str, object]],
) -> dict[str, int]:
    provider_checks = [check for provider in providers for check in _checks(provider)]
    binding_checks = [check for binding in bindings for check in _checks(binding)]
    checks = top_checks + provider_checks + binding_checks
    return {
        "providers": len(providers),
        "failed_providers": sum(1 for provider in providers if provider.get("status") == "failed"),
        "bindings": len(bindings),
        "failed_bindings": sum(1 for binding in bindings if binding.get("status") == "failed"),
        "checks_failed": sum(1 for check in checks if check.get("status") == "failed"),
        "warnings": sum(1 for check in checks if check.get("status") == "warning"),
    }


def _checks(report: dict[str, object]) -> list[dict[str, object]]:
    checks = report.get("checks")
    return [check for check in checks if isinstance(check, dict)] if isinstance(checks, list) else []


def _status_from_checks(checks: list[dict[str, object]]) -> str:
    if any(check.get("status") == "failed" for check in checks):
        return "failed"
    if any(check.get("status") == "warning" for check in checks):
        return "warning"
    return "ok"


def _resolve_command_path(command: str) -> str | None:
    expanded = Path(command).expanduser()
    if os.sep in command or (os.altsep and os.altsep in command):
        return str(expanded) if expanded.is_file() and os.access(expanded, os.X_OK) else None
    return shutil.which(command)
