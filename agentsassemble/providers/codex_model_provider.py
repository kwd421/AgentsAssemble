"""Process-local Codex model-provider overrides for native API harnesses."""

from __future__ import annotations

import json

from agentsassemble.room.text import clean_room_text


def codex_model_provider_command_args(
    profile_settings: dict[str, object],
) -> list[str]:
    provider = clean_room_text(profile_settings.get("model_provider"), limit=64)
    base_url = clean_room_text(
        profile_settings.get("provider_base_url"),
        limit=1000,
    ).rstrip("/")
    if not provider:
        return []
    arguments = _string_override("model_provider", provider)
    if not base_url:
        return arguments
    provider_key = "model_providers.agentsassemble_harness"
    arguments.extend(
        _string_override(
            f"{provider_key}.name",
            "AgentsAssemble harness gateway",
        )
    )
    arguments.extend(_string_override(f"{provider_key}.base_url", base_url))
    arguments.extend(_string_override(f"{provider_key}.wire_api", "responses"))
    arguments.extend(["-c", f"{provider_key}.requires_openai_auth=false"])
    return arguments


def _string_override(key: str, value: str) -> list[str]:
    return ["-c", f"{key}={json.dumps(value, ensure_ascii=True)}"]


__all__ = ["codex_model_provider_command_args"]
