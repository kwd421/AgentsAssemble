"""Compatibility exports for provider HTTP routes."""
from agentsassemble.web.routes.providers import (
    ProviderSecretStore,
    model_catalog_payload,
    provider_catalog_payload,
    register_provider_routes,
)

__all__ = [
    "ProviderSecretStore",
    "model_catalog_payload",
    "provider_catalog_payload",
    "register_provider_routes",
]
