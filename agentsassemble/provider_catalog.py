"""Static model catalog for the API-provider lane (master plan 1단계 B).

Design (room consensus with Codex + opencode/cli-jaw study):
- **Data, not code.** A vendored static seed — NOT a runtime fetch of
  models.dev (that's overkill + fights local-first). Adding a model = a dict
  entry here, no new code. One OpenAI-compatible adapter (see room_api_provider)
  serves every provider by swapping base_url + key.
- **This is a SECOND lane** next to the existing CLI residents (`*_resident.py`).
  Those stay code (each CLI's quirks aren't reducible to data). This lane is for
  direct HTTP-to-model-API providers (NVIDIA build, OpenRouter, LM Studio, BYOK).
- **cost_owner**: catalog default per provider, overridable per model, and the
  caller may override at runtime by where the key came from (byok vs free).
- **capability** (text/vision/tool_call) lives in the catalog as booleans; the
  runner gates on it (e.g. only send images to vision models).
- **fallback chain** (cli-jaw idea): ordered list; if one is rate-limited the
  next picks up.
- **keys**: resolved from env var now (the `env` field); a per-user BYOK table
  in identity.db lands when web mode needs it (resolve_api_key is the seam).
"""
from __future__ import annotations

import os

# cost_owner values: "byok" (user's key) / "free" (provider gives it free) /
# "subscription" (we pay) / "local" (user's machine, no key).
PROVIDER_CATALOG: dict[str, dict] = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "env": "NVIDIA_API_KEY",
        "default_cost_owner": "free",  # build.nvidia.com free endpoints
        "models": {
            "minimaxai/minimax-m2": {
                "name": "MiniMax M2 (NVIDIA free)",
                "limit": {"context": 1000000, "output": 8192},
                "capability": {"text": True, "vision": False, "tool_call": True},
            },
            "meta/llama-3.3-70b-instruct": {
                "name": "Llama 3.3 70B (NVIDIA free)",
                "limit": {"context": 131072, "output": 8192},
                "capability": {"text": True, "vision": False, "tool_call": True},
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
                "capability": {"text": True, "vision": False, "tool_call": True},
                "cost_owner": "free",  # :free tier — overrides provider default
            },
        },
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "env": "",  # local server, no key
        "default_cost_owner": "local",
        "models": {
            "local-model": {
                "name": "LM Studio (loaded model)",
                "limit": {"context": 0, "output": 0},  # depends on the loaded model
                "capability": {"text": True, "vision": False, "tool_call": False},
            },
        },
    },
}

# Ordered fallback: try first; on rate-limit/unavailable, the caller moves to
# the next (cli-jaw pattern). "provider/model" refs.
FALLBACK_CHAIN: list[str] = [
    "nvidia/minimaxai/minimax-m2",
    "nvidia/meta/llama-3.3-70b-instruct",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
]

DEFAULT_CAPABILITY = {"text": True, "vision": False, "tool_call": False}


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
    """'provider/model' -> (provider, model). Model id may itself contain '/'."""
    text = str(ref or "").strip()
    provider, _, model = text.partition("/")
    return provider, model


def model_capability(provider_id: str, model_id: str) -> dict:
    model = get_model(provider_id, model_id)
    cap = dict(DEFAULT_CAPABILITY)
    if model and isinstance(model.get("capability"), dict):
        cap.update({k: bool(v) for k, v in model["capability"].items()})
    return cap


def model_cost_owner(provider_id: str, model_id: str, *, key_source: str = "") -> str:
    """cost_owner resolution (room consensus): explicit runtime key_source wins,
    then the model's own cost_owner, then the provider default."""
    if key_source:
        return key_source
    model = get_model(provider_id, model_id) or {}
    if model.get("cost_owner"):
        return str(model["cost_owner"])
    provider = get_provider(provider_id) or {}
    return str(provider.get("default_cost_owner") or "")


def resolve_api_key(provider_id: str) -> str:
    """API key for a provider. Env var now (the catalog `env` field); a per-user
    BYOK table in identity.db is the future seam for web mode."""
    provider = get_provider(provider_id)
    if not provider:
        return ""
    env_name = str(provider.get("env") or "").strip()
    return os.environ.get(env_name, "").strip() if env_name else ""


def fallback_models() -> list[tuple[str, str]]:
    """Ordered (provider, model) pairs for the fallback chain (cli-jaw)."""
    return [split_ref(ref) for ref in FALLBACK_CHAIN]


def catalog_payload() -> dict:
    """Safe catalog for the frontend / API — base_url + models + capability,
    NEVER keys. (`env` names are fine; values are never exposed.)"""
    out: dict[str, dict] = {}
    for pid, provider in PROVIDER_CATALOG.items():
        out[pid] = {
            "base_url": provider.get("base_url", ""),
            "env": provider.get("env", ""),
            "key_present": bool(resolve_api_key(pid)) if provider.get("env") else True,
            "default_cost_owner": provider.get("default_cost_owner", ""),
            "models": {
                mid: {
                    "name": m.get("name", mid),
                    "limit": m.get("limit", {}),
                    "capability": model_capability(pid, mid),
                    "cost_owner": model_cost_owner(pid, mid),
                }
                for mid, m in (provider.get("models") or {}).items()
            },
        }
    return {"providers": out, "fallback_chain": list(FALLBACK_CHAIN)}
