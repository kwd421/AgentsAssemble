"""Render the provider-visible text for an already-built room turn packet."""

from __future__ import annotations

import json


def agent_turn_prompt(packet: dict[str, object]) -> str:
    provider_input = str(packet.get("provider_input") or "")
    if provider_input:
        return provider_input
    return (
        "AgentsAssemble room context and supported media packet:\n\n"
        + json.dumps(packet, ensure_ascii=False, sort_keys=True)
        + "\n"
    )
