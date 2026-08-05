"""Parse OpenCode's mixed provider catalog into managed subscription models."""

from __future__ import annotations

import json
import re

from agentsassemble.providers.catalog_provenance import filter_subscription_model_ids


def opencode_model_options(output: str) -> list[dict[str, object]]:
    pattern = re.compile(r"(?m)^\s*([A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+)\s*$")
    matches = list(pattern.finditer(output))
    options: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        value = match.group(1)
        if value in seen:
            continue
        seen.add(value)
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(output)
        metadata_block = output[match.end() : block_end].strip()
        discovered: dict[str, object] = {}
        if metadata_block.startswith("{"):
            try:
                parsed = json.loads(metadata_block)
                if isinstance(parsed, dict):
                    discovered = parsed
            except json.JSONDecodeError:
                discovered = {}
        provider_id, model_id = value.split("/", 1)
        label = str(discovered.get("name") or "").strip() or _model_label(model_id)
        pricing = _pricing(discovered.get("cost"))
        if pricing == "free":
            label = re.sub(r"\s+Free$", "", label, flags=re.IGNORECASE)
        metadata: dict[str, object] = {
            "selection_kind": "exact",
            "group": _provider_group(provider_id),
            "provider_id": provider_id,
        }
        family = str(discovered.get("family") or "").strip()
        if family:
            metadata["family"] = family
        if pricing:
            metadata["pricing"] = pricing
        options.append({"value": value, "label": label, "metadata": metadata})

    allowed_model_ids = set(
        filter_subscription_model_ids(
            "opencode",
            (str(option["value"]) for option in options),
        )
    )
    return [
        option
        for option in options
        if str(option["value"]) in allowed_model_ids
    ]


def _provider_group(provider_id: str) -> str:
    return {
        "opencode": "Zen",
        "opencode-go": "Go",
    }.get(provider_id.casefold(), _model_label(provider_id))


def _pricing(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    input_cost = value.get("input")
    output_cost = value.get("output")
    if (
        isinstance(input_cost, (int, float))
        and not isinstance(input_cost, bool)
        and isinstance(output_cost, (int, float))
        and not isinstance(output_cost, bool)
        and float(input_cost) == 0.0
        and float(output_cost) == 0.0
    ):
        return "free"
    return ""


def _model_label(value: str) -> str:
    tokens = str(value or "").split("-")
    labels: list[str] = []
    for token in tokens:
        folded = token.casefold()
        if folded == "gpt":
            labels.append("GPT")
        elif folded == "oss":
            labels.append("OSS")
        elif re.fullmatch(r"\d+b", folded):
            labels.append(folded.upper())
        elif re.fullmatch(r"\d+", folded) and labels and re.fullmatch(r"\d+", labels[-1]):
            labels[-1] = f"{labels[-1]}.{folded}"
        else:
            labels.append(token.capitalize())
    return " ".join(labels)


__all__ = ["opencode_model_options"]
