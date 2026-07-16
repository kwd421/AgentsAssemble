"""Compatibility exports for the optional API-provider model catalog."""

from agentsassemble.providers.catalog import (
    DEFAULT_CAPABILITY,
    FALLBACK_CHAIN,
    PROVIDER_CATALOG,
    catalog_payload,
    fallback_models,
    get_model,
    get_provider,
    list_providers,
    model_capability,
    model_cost_owner,
    resolve_api_key,
    split_ref,
)


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
