"""Provider catalog selection contracts shared with room services."""

from __future__ import annotations

from dataclasses import dataclass


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
    max_output_tokens: int = 0
    provider_endpoint: str = ""
    execution_harness: str = "builtin"
