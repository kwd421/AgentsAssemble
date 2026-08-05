"""Grok CLI catalog provenance helpers."""

from __future__ import annotations

import tomllib
from pathlib import Path
import re


class GrokCatalogConfigError(RuntimeError):
    """Raised when Grok's custom-model registry cannot be classified safely."""


def discover_grok_custom_model_ids(config_path: Path | None = None) -> set[str]:
    """Return model IDs registered by the user in Grok's local CLI config."""

    path = config_path or Path.home() / ".grok" / "config.toml"
    if not path.is_file():
        return set()
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise GrokCatalogConfigError(
            "Grok 사용자 모델 설정을 읽을 수 없어 구독 모델을 안전하게 구분하지 못했습니다."
        ) from error
    models = document.get("model")
    if models is None:
        return set()
    if not isinstance(models, dict):
        raise GrokCatalogConfigError(
            "Grok 사용자 모델 설정 형식이 올바르지 않아 구독 모델을 안전하게 구분하지 못했습니다."
        )
    return {
        str(model_id).strip()
        for model_id in models
        if str(model_id).strip()
    }


def classify_grok_models(
    output: str,
    *,
    custom_model_ids: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[str], str]:
    """Separate CLI-native model IDs from user-registered custom models."""

    discovered = re.findall(r"(?:\*|-)[ \t]+([A-Za-z0-9._-]+)", output)
    models = list(dict.fromkeys(model for model in discovered if model not in custom_model_ids))
    if not models:
        return [], ""
    default_match = re.search(r"Default model:\s*([A-Za-z0-9._-]+)", output)
    default_model = default_match.group(1) if default_match else models[0]
    return models, default_model if default_model in models else models[0]


__all__ = [
    "GrokCatalogConfigError",
    "classify_grok_models",
    "discover_grok_custom_model_ids",
]
