"""Read-only room packets for retained resident-agent compatibility clients."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from agentsassemble.legacy_meeting_queries import LegacyMeetingQueryService
from agentsassemble.legacy_meeting_records import read_meeting_record, safe_meeting_dir
from agentsassemble.live_agent_probe import PROBE_REPLY_EVENT_TAIL_LIMIT
from agentsassemble.live_agents import read_live_agents
from agentsassemble.live_meeting_memory import load_live_meeting_memory_context
from agentsassemble.lobby_queries import read_lobby
from agentsassemble.meeting_events import clean_lobby_text, read_live_events
from agentsassemble.room_friend_dms import read_live_agent_dm_events
from agentsassemble.features.side_chat.service import read_side_chat


LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT = PROBE_REPLY_EVENT_TAIL_LIMIT


@dataclass(frozen=True)
class LegacyLiveAgentQueryService:
    """Read authority for the old resident CLI room and artifact endpoints."""

    output_root: Path
    meetings: LegacyMeetingQueryService

    @classmethod
    def build(cls, output_root: Path) -> "LegacyLiveAgentQueryService":
        return cls(output_root=output_root, meetings=LegacyMeetingQueryService(output_root))

    def room(self, agent_id: str) -> dict[str, object]:
        agent = require_live_agent(self.output_root, agent_id)
        meeting_id = str(agent.get("meeting_id") or "").strip()
        live_events: list[dict[str, object]] = []
        shared_memory: dict[str, object] = {}
        if meeting_id:
            meeting_dir = safe_meeting_dir(self.output_root, meeting_id)
            if meeting_dir.exists():
                try:
                    meeting = read_meeting_record(meeting_dir)
                except (ValueError, OSError, json.JSONDecodeError):
                    meeting = {}
                live_events = _live_events_with_projected_return_packets(
                    read_live_events(meeting_dir),
                    meeting_dir=meeting_dir,
                    meeting=meeting,
                    agent=agent,
                )
                shared_memory = load_live_meeting_memory_context(meeting_dir, meeting=meeting)
        return {
            "agent": agent,
            "agents": read_live_agents(self.output_root),
            "meetings": self.meetings.list(),
            "meeting_id": meeting_id,
            "shared_memory": shared_memory,
            "live_events": live_events,
            "dm_events": read_live_agent_dm_events(
                self.output_root,
                str(agent.get("agent_id") or agent_id),
                after_event_id=clean_lobby_text(agent.get("last_observed_dm_event_id"), limit=128),
            ),
            "lobby_events": read_lobby(
                self.output_root,
                limit=LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT,
                meeting_id=meeting_id,
            ),
            "side_chat_events": read_side_chat(self.output_root),
        }

    def return_packet(
        self,
        agent_id: str,
        *,
        meeting_id: str = "",
        source_event_id: str = "",
    ) -> dict[str, object]:
        agent = require_live_agent(self.output_root, agent_id)
        clean_source_event_id = clean_lobby_text(source_event_id, limit=128)
        requested_meeting_id = clean_lobby_text(meeting_id, limit=128)
        agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
        if not clean_source_event_id or not agent_meeting_id:
            raise ValueError("Return packet not found.")
        if requested_meeting_id and requested_meeting_id != agent_meeting_id:
            raise ValueError("Return packet not found.")
        try:
            meeting_dir = safe_meeting_dir(self.output_root, agent_meeting_id)
            meeting = read_meeting_record(meeting_dir)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("Return packet not found.") from error
        candidate = _return_packet_read_candidate(
            meeting_dir,
            meeting=meeting,
            agent_id=clean_lobby_text(agent.get("agent_id"), limit=64),
            source_event_id=clean_source_event_id,
        )
        if candidate is None:
            raise ValueError("Return packet not found.")
        try:
            packet_markdown = candidate["packet_path"].read_text(encoding="utf-8")
            packet_json = json.loads(candidate["packet_json_path"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Return packet not found.") from error
        return {
            "status": "ok",
            "agent_id": clean_lobby_text(agent.get("agent_id"), limit=64),
            "meeting_id": agent_meeting_id,
            "source_event_id": clean_source_event_id,
            "role_id": candidate["role_id"],
            "artifact_path": candidate["artifact_path"],
            "artifact_json_path": candidate["artifact_json_path"],
            "markdown": packet_markdown,
            "json": packet_json,
            "event": candidate["event"],
        }


def live_agent_room_payload(output_root: Path, agent_id: str) -> dict[str, object]:
    return LegacyLiveAgentQueryService.build(output_root).room(agent_id)


def live_agent_return_packet_payload(
    output_root: Path,
    agent_id: str,
    *,
    meeting_id: str = "",
    source_event_id: str = "",
) -> dict[str, object]:
    return LegacyLiveAgentQueryService.build(output_root).return_packet(
        agent_id,
        meeting_id=meeting_id,
        source_event_id=source_event_id,
    )


def require_live_agent(output_root: Path, agent_id: str) -> dict[str, object]:
    for agent in read_live_agents(output_root):
        if agent.get("agent_id") == agent_id:
            return agent
    raise ValueError(f"Live agent {agent_id} was not found.")


def live_events_visible_to_agent(
    events: list[dict[str, object]],
    agent_id: str,
) -> list[dict[str, object]]:
    return [event for event in events if _live_event_visible_to_agent(event, agent_id)]


def _live_events_with_projected_return_packets(
    events: list[dict[str, object]],
    *,
    meeting_dir: Path,
    meeting: dict[str, object],
    agent: dict[str, object],
) -> list[dict[str, object]]:
    agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
    if not agent_id:
        return []
    cursor = clean_lobby_text(agent.get("last_observed_live_event_id"), limit=128)
    visible_events = _visible_live_events_pending_for_agent(
        live_events_visible_to_agent(events, agent_id),
        agent_id,
        cursor,
    )
    projected_events = _projected_return_packet_events(
        meeting_dir,
        meeting=meeting,
        agent_id=agent_id,
        cursor=cursor,
        visible_events=visible_events,
    )
    existing_artifacts = {
        (str(event.get("artifact_path") or ""), str(event.get("artifact_json_path") or ""))
        for event in visible_events
        if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
    }
    for event in projected_events:
        artifact_key = (str(event.get("artifact_path") or ""), str(event.get("artifact_json_path") or ""))
        if artifact_key not in existing_artifacts:
            visible_events.append(event)
    return visible_events


def _projected_return_packet_events(
    meeting_dir: Path,
    *,
    meeting: dict[str, object],
    agent_id: str,
    cursor: str,
    visible_events: list[dict[str, object]],
) -> list[dict[str, object]]:
    role_names = {
        role_id: str(role.get("display_name") or role_id)
        for role in _as_dict_list(meeting.get("roles"))
        if (role_id := clean_lobby_text(role.get("id"), limit=128))
    }
    events: list[dict[str, object]] = []
    full_events: list[dict[str, object]] | None = None
    visible_artifacts = {
        (str(event.get("artifact_path") or ""), str(event.get("artifact_json_path") or ""))
        for event in visible_events
        if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
    }
    for binding in _as_dict_list(meeting.get("agent_bindings")):
        if clean_lobby_text(binding.get("agent_id"), limit=64) != agent_id:
            continue
        role_id = clean_lobby_text(binding.get("role_id"), limit=128)
        if not role_id:
            continue
        packet_path = meeting_dir / "return_packets" / f"{role_id}.md"
        packet_json_path = meeting_dir / "return_packets" / f"{role_id}.json"
        if not packet_path.exists() or not packet_json_path.exists():
            continue
        artifact_path = f"return_packets/{role_id}.md"
        artifact_json_path = f"return_packets/{role_id}.json"
        fallback_event_id = _projected_return_packet_event_id(
            meeting_id=clean_lobby_text(meeting.get("meeting_id"), limit=128) or meeting_dir.name,
            agent_id=agent_id,
            role_id=role_id,
            artifact_path=artifact_path,
        )
        if cursor == fallback_event_id or (artifact_path, artifact_json_path) in visible_artifacts:
            continue
        if full_events is None:
            full_events = read_live_events(meeting_dir, limit=None)
        original_event = _return_packet_artifact_event(
            full_events,
            agent_id=agent_id,
            artifact_path=artifact_path,
            artifact_json_path=artifact_json_path,
        )
        event_id = clean_lobby_text(original_event.get("id") if original_event else "", limit=128)
        if not event_id:
            event_id = fallback_event_id
        if _return_packet_event_observed(
            cursor,
            event_id=event_id,
            fallback_event_id=fallback_event_id,
            original_event=original_event,
            full_events=full_events,
        ):
            continue
        created_at = (
            clean_lobby_text(original_event.get("created_at"), limit=128)
            if original_event
            else _return_packet_projection_created_at(packet_path, packet_json_path)
        )
        events.append(
            {
                "id": event_id,
                "created_at": created_at,
                "kind": "artifact",
                "meeting_id": clean_lobby_text(meeting.get("meeting_id"), limit=128) or meeting_dir.name,
                "channel": "system",
                "audience": f"agent:{agent_id}",
                "official_record": False,
                "actor_id": "",
                "target_agent_id": agent_id,
                "source_event_id": "",
                "role_id": role_id,
                "display_name": role_names.get(role_id, role_id),
                "artifact_kind": "return_packet",
                "artifact_path": artifact_path,
                "artifact_json_path": artifact_json_path,
                "content": f"Return packet ready: {artifact_path}",
                "projected": True,
            }
        )
    return events


def _return_packet_read_candidate(
    meeting_dir: Path,
    *,
    meeting: dict[str, object],
    agent_id: str,
    source_event_id: str,
) -> dict[str, object] | None:
    if not agent_id or not source_event_id:
        return None
    meeting_id = clean_lobby_text(meeting.get("meeting_id"), limit=128) or meeting_dir.name
    full_events = read_live_events(meeting_dir, limit=None)
    for binding in _as_dict_list(meeting.get("agent_bindings")):
        if clean_lobby_text(binding.get("agent_id"), limit=64) != agent_id:
            continue
        role_id = clean_lobby_text(binding.get("role_id"), limit=128)
        paths = _return_packet_role_paths(meeting_dir, role_id)
        if paths is None:
            continue
        packet_path, packet_json_path, artifact_path, artifact_json_path = paths
        if not packet_path.exists() or not packet_json_path.exists():
            continue
        fallback_event_id = _projected_return_packet_event_id(
            meeting_id=meeting_id,
            agent_id=agent_id,
            role_id=role_id,
            artifact_path=artifact_path,
        )
        original_event = _return_packet_artifact_event(
            full_events,
            agent_id=agent_id,
            artifact_path=artifact_path,
            artifact_json_path=artifact_json_path,
        )
        original_event_id = clean_lobby_text(original_event.get("id") if original_event else "", limit=128)
        if source_event_id not in {original_event_id, fallback_event_id}:
            continue
        event = original_event or {
            "id": fallback_event_id,
            "kind": "artifact",
            "meeting_id": meeting_id,
            "channel": "system",
            "audience": f"agent:{agent_id}",
            "official_record": False,
            "target_agent_id": agent_id,
            "role_id": role_id,
            "artifact_kind": "return_packet",
            "artifact_path": artifact_path,
            "artifact_json_path": artifact_json_path,
            "projected": True,
        }
        return {
            "role_id": role_id,
            "artifact_path": artifact_path,
            "artifact_json_path": artifact_json_path,
            "packet_path": packet_path,
            "packet_json_path": packet_json_path,
            "event": event,
        }
    return None


def _return_packet_role_paths(meeting_dir: Path, role_id: str) -> tuple[Path, Path, str, str] | None:
    if not role_id:
        return None
    markdown_name = f"{role_id}.md"
    json_name = f"{role_id}.json"
    if Path(markdown_name).name != markdown_name or Path(json_name).name != json_name:
        return None
    packet_dir = (meeting_dir / "return_packets").resolve()
    packet_path = (packet_dir / markdown_name).resolve()
    packet_json_path = (packet_dir / json_name).resolve()
    if packet_path.parent != packet_dir or packet_json_path.parent != packet_dir:
        return None
    return packet_path, packet_json_path, f"return_packets/{markdown_name}", f"return_packets/{json_name}"


def _visible_live_events_pending_for_agent(
    events: list[dict[str, object]],
    agent_id: str,
    cursor: str,
) -> list[dict[str, object]]:
    if not cursor:
        return events
    pending_events: list[dict[str, object]] = []
    for event in events:
        if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet":
            meeting_id = clean_lobby_text(event.get("meeting_id"), limit=128)
            role_id = clean_lobby_text(event.get("role_id"), limit=128)
            artifact_path = clean_lobby_text(event.get("artifact_path"), limit=256)
            if meeting_id and role_id and artifact_path:
                fallback_event_id = _projected_return_packet_event_id(
                    meeting_id=meeting_id,
                    agent_id=agent_id,
                    role_id=role_id,
                    artifact_path=artifact_path,
                )
                if cursor == fallback_event_id:
                    continue
        pending_events.append(event)
    return pending_events


def _return_packet_artifact_event(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    artifact_path: str,
    artifact_json_path: str,
) -> dict[str, object]:
    matching_event: dict[str, object] = {}
    for event in events:
        if event.get("kind") != "artifact" or event.get("artifact_kind") != "return_packet":
            continue
        if str(event.get("target_agent_id") or "") != agent_id and str(event.get("audience") or "") != f"agent:{agent_id}":
            continue
        if str(event.get("artifact_path") or "") != artifact_path:
            continue
        if str(event.get("artifact_json_path") or "") != artifact_json_path:
            continue
        matching_event = event
    return matching_event


def _return_packet_event_observed(
    cursor: str,
    *,
    event_id: str,
    fallback_event_id: str,
    original_event: dict[str, object],
    full_events: list[dict[str, object]],
) -> bool:
    if not cursor:
        return False
    if cursor in {event_id, fallback_event_id}:
        return True
    cursor_index = _live_event_index(full_events, cursor)
    if not original_event:
        return cursor_index is not None
    event_index = _live_event_index(full_events, event_id)
    return cursor_index is not None and event_index is not None and cursor_index >= event_index


def _live_event_index(events: list[dict[str, object]], event_id: str) -> int | None:
    for index, event in enumerate(events):
        if str(event.get("id") or "") == event_id:
            return index
    return None


def _projected_return_packet_event_id(
    *,
    meeting_id: str,
    agent_id: str,
    role_id: str,
    artifact_path: str,
) -> str:
    value = f"agentsassemble:return-packet:{meeting_id}:{agent_id}:{role_id}:{artifact_path}"
    return uuid5(NAMESPACE_URL, value).hex[:12]


def _return_packet_projection_created_at(packet_path: Path, packet_json_path: Path) -> str:
    try:
        packet_stat = packet_path.stat()
        packet_json_stat = packet_json_path.stat()
    except OSError:
        return ""
    version_ns = max(packet_stat.st_mtime_ns, packet_json_stat.st_mtime_ns)
    return datetime.fromtimestamp(version_ns / 1_000_000_000, UTC).isoformat()


def _live_event_visible_to_agent(event: dict[str, object], agent_id: str) -> bool:
    if event.get("official_record") is True:
        return True
    target_agent_id = str(event.get("target_agent_id") or "")
    if target_agent_id:
        return target_agent_id == agent_id
    audience = str(event.get("audience") or "")
    if audience.startswith("agent:"):
        return audience == f"agent:{agent_id}"
    return True


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
