"""Cursor and event observation health for retained resident agents."""

from __future__ import annotations

from pathlib import Path

from agentsassemble.legacy.live_agent.health import (
    is_diagnostic_agent,
    is_diagnostic_process_group,
    safe_health_identity,
    safe_health_timestamp,
)
from agentsassemble.legacy.meeting.records import safe_meeting_dir
from agentsassemble.live_agent_turns import (
    is_official_turn_cancellation_event,
    is_official_turn_reply_event,
    is_review_checkpoint_reply_event,
)
from agentsassemble.lobby_queries import read_lobby
from agentsassemble.meeting_events import clean_lobby_text, read_live_events


def live_agent_observation_health_summary(
    output_root: Path,
    groups: list[dict[str, object]],
    agents: list[dict[str, object]],
    session_summary: dict[str, object],
    *,
    diagnostic_group_ids: set[str] | None = None,
) -> dict[str, object]:
    diagnostic_group_ids = diagnostic_group_ids or set()
    agents_by_id = {
        safe_health_identity(agent.get("agent_id")): agent
        for agent in agents
        if safe_health_identity(agent.get("agent_id")) and not is_diagnostic_agent(agent)
    }
    groups_by_session = {
        (
            safe_health_identity(group.get("meeting_id")),
            safe_health_identity(group.get("group_id")),
        ): group
        for group in groups
        if not is_diagnostic_process_group(group, diagnostic_group_ids)
    }
    lobby_event = latest_lobby_event(output_root)
    latest_lobby_event_id = safe_health_identity(lobby_event.get("id")) if lobby_event else ""
    latest_lobby_actor_id = safe_health_identity(lobby_event.get("actor_id")) if lobby_event else ""
    events_by_meeting: dict[str, list[dict[str, object]]] = {}
    items: list[dict[str, object]] = []
    attention: list[str] = []
    lobby_behind_count = 0
    live_behind_count = 0
    error_count = 0
    latest_live_request_count = 0

    for session_item in _as_dict_list(session_summary.get("items")):
        if str(session_item.get("status") or "") != "ready":
            continue
        meeting_id = safe_health_identity(session_item.get("meeting_id"))
        group_id = safe_health_identity(session_item.get("group_id"))
        if not meeting_id or not group_id:
            continue
        group = groups_by_session.get((meeting_id, group_id))
        if group is None:
            continue
        meeting_events = live_agent_observation_events(output_root, meeting_id, events_by_meeting)
        for manifest_agent in _as_dict_list(group.get("agents")):
            agent_id = safe_health_identity(manifest_agent.get("agent_id"))
            if not agent_id:
                continue
            agent = agents_by_id.get(agent_id, {})
            latest_request = latest_live_agent_turn_request_for_agent(meeting_events, agent_id)
            if latest_request:
                latest_live_request_count += 1
            item = _live_agent_observation_item(
                agent,
                meeting_id=meeting_id,
                group_id=group_id,
                agent_id=agent_id,
                latest_lobby_event_id=latest_lobby_event_id,
                latest_lobby_actor_id=latest_lobby_actor_id,
                latest_live_request=latest_request,
                meeting_events=meeting_events,
            )
            if item["lobby_status"] == "behind":
                lobby_behind_count += 1
                attention.append(f"{meeting_id}:{group_id}:{agent_id}:lobby_cursor_behind")
            if item["live_status"] == "behind":
                live_behind_count += 1
                attention.append(f"{meeting_id}:{group_id}:{agent_id}:live_cursor_behind")
            if _live_agent_observation_has_active_error(agent):
                error_count += 1
            items.append(item)

    return {
        "ready_agent_count": len(items),
        "lobby_behind_count": lobby_behind_count,
        "live_behind_count": live_behind_count,
        "error_count": error_count,
        "latest_lobby_event_id": latest_lobby_event_id,
        "latest_live_request_count": latest_live_request_count,
        "attention": attention,
        "items": items,
    }


def latest_lobby_event(output_root: Path) -> dict[str, object]:
    events = read_lobby(output_root, limit=1)
    return events[-1] if events else {}


def live_agent_observation_events(
    output_root: Path,
    meeting_id: str,
    events_by_meeting: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    if meeting_id not in events_by_meeting:
        try:
            events_by_meeting[meeting_id] = read_live_events(safe_meeting_dir(output_root, meeting_id), limit=200)
        except ValueError:
            events_by_meeting[meeting_id] = []
    return events_by_meeting[meeting_id]


def latest_live_agent_turn_request_for_agent(
    events: list[dict[str, object]],
    agent_id: str,
) -> dict[str, object]:
    for event in reversed(events):
        if event.get("kind") != "live_agent_turn_request":
            continue
        if safe_health_identity(event.get("target_agent_id")) != agent_id:
            continue
        return event
    return {}


def _live_agent_observation_item(
    agent: dict[str, object],
    *,
    meeting_id: str,
    group_id: str,
    agent_id: str,
    latest_lobby_event_id: str,
    latest_lobby_actor_id: str,
    latest_live_request: dict[str, object],
    meeting_events: list[dict[str, object]],
) -> dict[str, object]:
    last_observed_lobby = safe_health_identity(agent.get("last_observed_event_id"))
    last_observed_live = safe_health_identity(agent.get("last_observed_live_event_id"))
    latest_live_event_id = safe_health_identity(latest_live_request.get("id"))
    return {
        "meeting_id": meeting_id,
        "group_id": group_id,
        "agent_id": agent_id,
        "lobby_status": live_agent_lobby_observation_status(
            latest_lobby_event_id,
            last_observed_lobby,
            latest_actor_id=latest_lobby_actor_id,
            agent_id=agent_id,
        ),
        "live_status": live_agent_live_observation_status(
            meeting_events,
            agent_id=agent_id,
            latest_request_id=latest_live_event_id,
            last_observed_live_event_id=last_observed_live,
        ),
        "latest_lobby_event_id": latest_lobby_event_id,
        "latest_live_event_id": latest_live_event_id,
        "last_observed_event_id": last_observed_lobby,
        "last_observed_live_event_id": last_observed_live,
        "last_reply_at": safe_health_timestamp(agent.get("last_reply_at")),
    }


def live_agent_lobby_observation_status(
    latest_event_id: str,
    last_observed_event_id: str,
    *,
    latest_actor_id: str,
    agent_id: str,
) -> str:
    if not latest_event_id:
        return "none"
    if latest_actor_id and latest_actor_id == agent_id:
        return "self"
    return "current" if last_observed_event_id == latest_event_id else "behind"


def live_agent_live_observation_status(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    latest_request_id: str,
    last_observed_live_event_id: str,
) -> str:
    if not latest_request_id:
        return "none"
    terminal_status = _live_agent_turn_terminal_status_in_events(
        events,
        agent_id=agent_id,
        source_event_id=latest_request_id,
    )
    if terminal_status:
        return terminal_status
    event_index = _live_event_index_by_id(events)
    latest_index = event_index.get(latest_request_id)
    observed_index = event_index.get(last_observed_live_event_id)
    if latest_index is not None and observed_index is not None and observed_index >= latest_index:
        return "current"
    return "current" if last_observed_live_event_id == latest_request_id else "behind"


def _live_agent_turn_terminal_status_in_events(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> str:
    for event in events:
        if is_official_turn_cancellation_event(event):
            if safe_health_identity(event.get("target_agent_id")) != agent_id:
                continue
            if safe_health_identity(event.get("source_event_id")) == source_event_id:
                return "cancelled"
            continue
        if not is_official_turn_reply_event(event) and not is_review_checkpoint_reply_event(event):
            continue
        if safe_health_identity(event.get("actor_id")) != agent_id:
            continue
        if safe_health_identity(event.get("source_event_id")) == source_event_id:
            return "answered"
    return ""


def _live_agent_reply_for_request_in_events(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> bool:
    for event in events:
        if not is_official_turn_reply_event(event) and not is_review_checkpoint_reply_event(event):
            continue
        if safe_health_identity(event.get("actor_id")) != agent_id:
            continue
        if safe_health_identity(event.get("source_event_id")) == source_event_id:
            return True
    return False


def _live_event_index_by_id(events: list[dict[str, object]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, event in enumerate(events):
        event_id = safe_health_identity(event.get("id"))
        if event_id:
            result[event_id] = index
    return result


def _live_agent_observation_has_active_error(agent: dict[str, object]) -> bool:
    return clean_lobby_text(agent.get("status"), limit=64) == "error"


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
