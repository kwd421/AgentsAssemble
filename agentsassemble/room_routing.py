from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.providers.launch_specs import NativeCliProviderSpec


@dataclass(frozen=True)
class RoomRoutingDecision:
    targets: tuple[str, ...]
    actor_id: str
    actor_type: str
    relay_depth: int


def route_message_targets(
    event: dict[str, object],
    providers: Mapping[str, NativeCliProviderSpec],
    *,
    max_agent_relay_depth: int,
    relay_agent_messages: bool = False,
) -> RoomRoutingDecision:
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    actor_id = clean_lobby_text(actor.get("participant_id") or event.get("participant_id"), limit=128)
    actor_type = clean_lobby_text(actor.get("participant_type") or event.get("participant_type"), limit=32)
    content = clean_lobby_text(event.get("content"), limit=12000)
    relay_depth = max(0, int(event.get("relay_depth") or 0))
    if actor_type == "agent" and relay_depth >= max(0, int(max_agent_relay_depth)):
        targets: set[str] = set()
    else:
        mentioned = _mentioned_agents(content, providers)
        target_agent_id = clean_lobby_text(event.get("target_agent_id"), limit=128)
        if target_agent_id in providers:
            mentioned.add(target_agent_id)
        if "@all" in content.casefold():
            targets = set(providers)
        elif mentioned:
            targets = mentioned
        elif actor_type != "agent" or relay_agent_messages:
            targets = {agent_id for agent_id, spec in providers.items() if spec.default_responder}
        else:
            targets = set()
    targets.discard(actor_id)
    return RoomRoutingDecision(
        targets=tuple(sorted(targets)),
        actor_id=actor_id,
        actor_type=actor_type,
        relay_depth=relay_depth,
    )


def direct_message_targets(
    event: dict[str, object],
    providers: Mapping[str, NativeCliProviderSpec],
) -> tuple[str, ...]:
    """Return specifically addressed providers in message order.

    ``@all`` is intentionally not a direct target in a one-speaker room.
    """
    content = clean_lobby_text(event.get("content"), limit=12000)
    target_agent_id = clean_lobby_text(event.get("target_agent_id"), limit=128)
    ordered: list[tuple[int, str]] = []
    if target_agent_id in providers:
        ordered.append((-1, target_agent_id))
    lowered = content.casefold()
    for agent_id in providers:
        match = re.search(
            rf"(?<![\w-])@{re.escape(agent_id.casefold())}(?![\w-])",
            lowered,
        )
        if match is not None:
            ordered.append((match.start(), agent_id))
    result: list[str] = []
    for _position, agent_id in sorted(ordered):
        if agent_id not in result:
            result.append(agent_id)
    return tuple(result)


def _mentioned_agents(
    content: str,
    providers: Mapping[str, NativeCliProviderSpec],
) -> set[str]:
    lowered = content.casefold()
    return {
        agent_id
        for agent_id in providers
        if re.search(rf"(?<![\w-])@{re.escape(agent_id.casefold())}(?![\w-])", lowered)
    }
