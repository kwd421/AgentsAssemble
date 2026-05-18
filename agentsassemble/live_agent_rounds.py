from __future__ import annotations

from typing import Any

from agentsassemble.meeting_events import clean_lobby_text


DEFAULT_MAX_OFFICIAL_ROUND_TURNS = 12


def build_official_round_turns(
    meeting: dict[str, object],
    live_agents: list[dict[str, object]],
    *,
    meeting_id: str,
    round_id: str,
    instruction: object | None = None,
    role_ids: list[str] | None = None,
    max_turns: int = DEFAULT_MAX_OFFICIAL_ROUND_TURNS,
) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    clean_round_id = clean_lobby_text(round_id, limit=128)
    if not clean_round_id:
        raise ValueError("Official round id is required.")
    round_definition = _round_definition(meeting, clean_round_id)
    selected_role_ids = _selected_role_ids(meeting, round_definition, role_ids)
    if not selected_role_ids:
        raise ValueError(f"Official round {clean_round_id} has no speaker roles.")
    if len(selected_role_ids) > max_turns:
        raise ValueError(f"Official round supports at most {max_turns} turns.")

    content = clean_lobby_text(instruction, limit=4000) or clean_lobby_text(round_definition.get("instruction"), limit=4000)
    if not content:
        raise ValueError("Official round instruction is required.")

    roles = _roles_by_id(meeting)
    bindings = _bindings_by_role(meeting)
    agents = _live_agents_by_id(live_agents)
    turns = []
    for index, role_id in enumerate(selected_role_ids):
        role = roles.get(role_id)
        if role is None:
            raise ValueError(f"Unknown official round role: {role_id}.")
        binding = bindings.get(role_id)
        if binding is None:
            raise ValueError(f"No agent binding was found for role {role_id}.")
        agent_id = clean_lobby_text(binding.get("agent_id"), limit=64)
        if not agent_id:
            raise ValueError(f"No agent binding was found for role {role_id}.")
        agent = agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Live agent {agent_id} was not found.")
        if clean_lobby_text(agent.get("meeting_id"), limit=128) != clean_meeting_id:
            raise ValueError(f"Live agent {agent_id} is not attached to meeting {clean_meeting_id}.")
        display_name = (
            clean_lobby_text(role.get("display_name"), limit=64)
            or clean_lobby_text(agent.get("display_name"), limit=64)
            or agent_id
        )
        turns.append(
            {
                "agent_id": agent_id,
                "role_id": role_id,
                "display_name": display_name,
                "content": content,
                "turn_id": f"{clean_round_id}:{index}:{role_id}",
                "turn_index": index,
            }
        )
    return {"round_id": clean_round_id, "role_ids": selected_role_ids, "turns": turns}


def _round_definition(meeting: dict[str, object], round_id: str) -> dict[str, object]:
    template = meeting.get("meeting_template") if isinstance(meeting.get("meeting_template"), dict) else {}
    for item in _dict_items(template.get("rounds")):
        if clean_lobby_text(item.get("id"), limit=128) == round_id:
            return item
    raise ValueError(f"Official round {round_id} was not found.")


def _selected_role_ids(
    meeting: dict[str, object],
    round_definition: dict[str, object],
    role_ids: list[str] | None,
) -> list[str]:
    if role_ids:
        return _clean_unique_role_ids(role_ids)
    role_order = [clean_lobby_text(role.get("id"), limit=128) for role in _dict_items(meeting.get("roles"))]
    role_order = [role_id for role_id in role_order if role_id]
    turn_control = round_definition.get("turn_control") if isinstance(round_definition.get("turn_control"), dict) else {}
    if turn_control.get("selection") == "selected_roles":
        return _clean_unique_role_ids(
            [
                str(role_id)
                for role_id in turn_control.get("speaker_role_ids", [])
                if isinstance(role_id, str)
            ]
        )
    return role_order


def _clean_unique_role_ids(role_ids: list[str]) -> list[str]:
    cleaned = []
    seen = set()
    for raw_role_id in role_ids:
        role_id = clean_lobby_text(raw_role_id, limit=128)
        if not role_id:
            continue
        if role_id in seen:
            raise ValueError(f"Duplicate official round role: {role_id}.")
        seen.add(role_id)
        cleaned.append(role_id)
    return cleaned


def _roles_by_id(meeting: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        role_id: role
        for role in _dict_items(meeting.get("roles"))
        if (role_id := clean_lobby_text(role.get("id"), limit=128))
    }


def _bindings_by_role(meeting: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        role_id: binding
        for binding in _dict_items(meeting.get("agent_bindings"))
        if (role_id := clean_lobby_text(binding.get("role_id"), limit=128))
    }


def _live_agents_by_id(live_agents: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        agent_id: agent
        for agent in live_agents
        if (agent_id := clean_lobby_text(agent.get("agent_id"), limit=64))
    }


def _dict_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
