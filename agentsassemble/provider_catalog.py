"""Compatibility exports for the optional API-provider model catalog."""

from agentsassemble.providers.catalog import (
    DEFAULT_CAPABILITY,
    PROVIDER_CATALOG,
    catalog_payload,
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
    "PROVIDER_CATALOG",
    "catalog_payload",
    "get_model",
    "get_provider",
    "list_providers",
    "model_capability",
    "model_cost_owner",
    "resolve_api_key",
    "split_ref",
]
