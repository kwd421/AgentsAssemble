"""Subscription model-catalog provenance and filtering policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SubscriptionCatalogProvenance:
    source: str
    managed_namespaces: frozenset[str] = frozenset()
    excludes_registered_models: bool = False


SUBSCRIPTION_CATALOG_PROVENANCE: dict[str, SubscriptionCatalogProvenance] = {
    "codex": SubscriptionCatalogProvenance(source="managed_cli_catalog"),
    "antigravity": SubscriptionCatalogProvenance(source="managed_cli_catalog"),
    "grok": SubscriptionCatalogProvenance(
        source="managed_cli_catalog",
        excludes_registered_models=True,
    ),
    "claude": SubscriptionCatalogProvenance(source="embedded_registry"),
    "cursor": SubscriptionCatalogProvenance(source="managed_cli_catalog"),
    "freebuff": SubscriptionCatalogProvenance(source="live_cli_label_scan"),
    "opencode": SubscriptionCatalogProvenance(
        source="managed_provider_namespaces",
        managed_namespaces=frozenset({"opencode", "opencode-go"}),
    ),
    "ollama": SubscriptionCatalogProvenance(source="installed_model_inventory"),
}


class MissingSubscriptionCatalogProvenance(RuntimeError):
    pass


def subscription_catalog_provenance(
    provider_id: str,
) -> SubscriptionCatalogProvenance:
    try:
        return SUBSCRIPTION_CATALOG_PROVENANCE[provider_id]
    except KeyError as error:
        raise MissingSubscriptionCatalogProvenance(
            f"Subscription provider {provider_id!r} has no model-catalog provenance policy."
        ) from error


def filter_subscription_model_ids(
    provider_id: str,
    model_ids: Iterable[str],
    *,
    registered_model_ids: Iterable[str] = (),
) -> list[str]:
    policy = subscription_catalog_provenance(provider_id)
    registered = {
        str(model_id).strip()
        for model_id in registered_model_ids
        if str(model_id).strip()
    }
    filtered: list[str] = []
    seen: set[str] = set()
    for raw_model_id in model_ids:
        model_id = str(raw_model_id).strip()
        if not model_id or model_id in seen:
            continue
        if policy.excludes_registered_models and model_id in registered:
            continue
        if policy.managed_namespaces:
            namespace, separator, _model = model_id.partition("/")
            if not separator or namespace.casefold() not in policy.managed_namespaces:
                continue
        seen.add(model_id)
        filtered.append(model_id)
    return filtered


def annotate_subscription_catalog_provenance(
    providers: list[dict[str, object]],
) -> list[dict[str, object]]:
    for provider in providers:
        if provider.get("catalog_group") != "subscription":
            continue
        provider_id = str(provider.get("id") or "")
        policy = subscription_catalog_provenance(provider_id)
        provider["model_catalog_provenance"] = policy.source
    return providers


__all__ = [
    "MissingSubscriptionCatalogProvenance",
    "SUBSCRIPTION_CATALOG_PROVENANCE",
    "SubscriptionCatalogProvenance",
    "annotate_subscription_catalog_provenance",
    "filter_subscription_model_ids",
    "subscription_catalog_provenance",
]
