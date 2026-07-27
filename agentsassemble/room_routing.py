from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.providers.launch_specs import NativeCliProviderSpec


_DISPLAY_ALIAS_SPLIT = re.compile(r"[\s/|·—–:()]+")
_MENTION_ALIAS = re.compile(r"^[\w.-]+$", re.UNICODE)


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
    last_positions: dict[str, int] = {}
    if target_agent_id in providers:
        last_positions[target_agent_id] = -1
    lowered = content.casefold()
    for alias, agent_id in _provider_mention_aliases(providers).items():
        matches = tuple(
            re.finditer(
                rf"(?<![\w-])@{re.escape(alias)}(?![\w-])",
                lowered,
            )
        )
        if matches:
            last_positions[agent_id] = max(
                last_positions.get(agent_id, -1),
                matches[-1].start(),
            )
    return tuple(
        agent_id
        for agent_id, _position in sorted(
            last_positions.items(),
            key=lambda item: item[1],
        )
    )


def _mentioned_agents(
    content: str,
    providers: Mapping[str, NativeCliProviderSpec],
) -> set[str]:
    lowered = content.casefold()
    return {
        agent_id
        for alias, agent_id in _provider_mention_aliases(providers).items()
        if re.search(rf"(?<![\w-])@{re.escape(alias)}(?![\w-])", lowered)
    }


def _provider_mention_aliases(
    providers: Mapping[str, NativeCliProviderSpec],
) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for agent_id, spec in providers.items():
        display_name = clean_lobby_text(spec.display_name, limit=128)
        aliases = [agent_id]
        if display_name and not any(character.isspace() for character in display_name):
            aliases.append(display_name)
        aliases.extend(_DISPLAY_ALIAS_SPLIT.split(display_name))
        for value in aliases:
            alias = str(value or "").strip(" @,;!?[]{}").casefold()
            if not alias or alias == "all" or _MENTION_ALIAS.fullmatch(alias) is None:
                continue
            candidates.setdefault(alias, set()).add(agent_id)
    return {
        alias: next(iter(agent_ids))
        for alias, agent_ids in candidates.items()
        if len(agent_ids) == 1
    }
