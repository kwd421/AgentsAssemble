"""Static model catalog for the optional API-provider lane."""
from __future__ import annotations

import os


# cost_owner values: "byok" (user's key) / "free" (provider gives it free) /
# "subscription" (we pay) / "local" (user's machine, no key).
PROVIDER_CATALOG: dict[str, dict] = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "env": "NVIDIA_API_KEY",
        "default_cost_owner": "free",
        "models": {
            "minimaxai/minimax-m2": {
                "name": "MiniMax M2 (NVIDIA free)",
                "limit": {"context": 1000000, "output": 8192},
                "capability": {
                    "text": True,
                    "vision": False,
                    "tool_call": True,
                },
            },
            "meta/llama-3.3-70b-instruct": {
                "name": "Llama 3.3 70B (NVIDIA free)",
                "limit": {"context": 131072, "output": 8192},
                "capability": {
                    "text": True,
                    "vision": False,
                    "tool_call": True,
                },
            },
        },
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env": "OPENROUTER_API_KEY",
        "default_cost_owner": "byok",
        "models": {
            "meta-llama/llama-3.3-70b-instruct:free": {
                "name": "Llama 3.3 70B (OpenRouter free)",
                "limit": {"context": 131072, "output": 8192},
                "capability": {
                    "text": True,
                    "vision": False,
                    "tool_call": True,
                },
                "cost_owner": "free",
            },
        },
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "env": "",
        "default_cost_owner": "local",
        "models": {
            "local-model": {
                "name": "LM Studio (loaded model)",
                "limit": {"context": 0, "output": 0},
                "capability": {
                    "text": True,
                    "vision": False,
                    "tool_call": False,
                },
            },
        },
    },
}

# Ordered provider/model references retained for the optional API lane.
FALLBACK_CHAIN: list[str] = [
    "nvidia/minimaxai/minimax-m2",
    "nvidia/meta/llama-3.3-70b-instruct",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
]

DEFAULT_CAPABILITY = {
    "text": True,
    "vision": False,
    "tool_call": False,
}


def list_providers() -> list[str]:
    return sorted(PROVIDER_CATALOG)


def get_provider(provider_id: str) -> dict | None:
    return PROVIDER_CATALOG.get(str(provider_id or "").strip())


def get_model(provider_id: str, model_id: str) -> dict | None:
    provider = get_provider(provider_id)
    if not provider:
        return None
    return (provider.get("models") or {}).get(str(model_id or "").strip())


def split_ref(ref: str) -> tuple[str, str]:
    """Return provider and model; model IDs may contain slashes."""

    text = str(ref or "").strip()
    provider, _, model = text.partition("/")
    return provider, model


def model_capability(provider_id: str, model_id: str) -> dict:
    model = get_model(provider_id, model_id)
    capability = dict(DEFAULT_CAPABILITY)
    if model and isinstance(model.get("capability"), dict):
        capability.update(
            {
                key: bool(value)
                for key, value in model["capability"].items()
            }
        )
    return capability


def model_cost_owner(
    provider_id: str,
    model_id: str,
    *,
    key_source: str = "",
) -> str:
    """Resolve explicit key source, model override, then provider default."""

    if key_source:
        return key_source
    model = get_model(provider_id, model_id) or {}
    if model.get("cost_owner"):
        return str(model["cost_owner"])
    provider = get_provider(provider_id) or {}
    return str(provider.get("default_cost_owner") or "")


def resolve_api_key(provider_id: str) -> str:
    """Resolve the configured environment key without exposing it in payloads."""

    provider = get_provider(provider_id)
    if not provider:
        return ""
    env_name = str(provider.get("env") or "").strip()
    return os.environ.get(env_name, "").strip() if env_name else ""


def fallback_models() -> list[tuple[str, str]]:
    return [split_ref(ref) for ref in FALLBACK_CHAIN]


def catalog_payload() -> dict:
    """Return catalog metadata without secret values."""

    providers: dict[str, dict] = {}
    for provider_id, provider in PROVIDER_CATALOG.items():
        providers[provider_id] = {
            "base_url": provider.get("base_url", ""),
            "env": provider.get("env", ""),
            "key_present": (
                bool(resolve_api_key(provider_id))
                if provider.get("env")
                else True
            ),
            "default_cost_owner": provider.get("default_cost_owner", ""),
            "models": {
                model_id: {
                    "name": model.get("name", model_id),
                    "limit": model.get("limit", {}),
                    "capability": model_capability(provider_id, model_id),
                    "cost_owner": model_cost_owner(provider_id, model_id),
                }
                for model_id, model in (provider.get("models") or {}).items()
            },
        }
    return {
        "providers": providers,
        "fallback_chain": list(FALLBACK_CHAIN),
    }


__all__ = [
    "DEFAULT_CAPABILITY",
    "FALLBACK_CHAIN",
    "PROVIDER_CATALOG",
    "catalog_payload",
    "fallback_models",
    "get_model",
    "get_provider",
    "list_providers",
    "model_capability",
    "model_cost_owner",
    "resolve_api_key",
    "split_ref",
]
