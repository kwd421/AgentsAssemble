from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agents import delete_live_agent, detach_live_agent_from_meeting, read_live_agents
from agentsassemble.live_agent_processes import clean_live_agent_group_id
from agentsassemble.meeting_events import clean_lobby_text, write_live_state
from agentsassemble.multi_host_invites import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room_invite import revoke_sessions_for_participant


def expel_live_agent_from_room_payload(
    output_root: Path,
    process_supervisor: object,
    payload: dict[str, object],
) -> dict[str, object]:
    meeting_id = _clean_existing_meeting_id(payload.get("meeting_id"))
    agent_id = _clean_existing_agent_id(payload.get("agent_id"))
    requested_group_id = _clean_optional_group_id(payload.get("group_id"))
    agent = _live_agent_entry(output_root, agent_id)
    revoked_sessions = revoke_sessions_for_participant(meeting_id, agent_id)
    group = _find_agent_process_group(
        process_supervisor,
        meeting_id=meeting_id,
        agent_id=agent_id,
        requested_group_id=requested_group_id or clean_lobby_text(agent.get("process_group_id"), limit=128),
    )
    stopped_group = _stop_running_agent_owned_group(
        process_supervisor,
        group,
        meeting_id=meeting_id,
        agent_id=agent_id,
    )
    meeting_dir = _existing_meeting_dir(output_root, meeting_id)
    meeting = _read_meeting(meeting_dir)
    updated_meeting, removed = _meeting_without_agent(meeting, agent_id, require_binding=False)
    write_live_state(meeting_dir, updated_meeting)
    detached_agent = _remove_or_detach_expelled_agent(output_root, agent, agent_id=agent_id, meeting_id=meeting_id)
    return {
        "status": "expelled",
        "meeting_id": meeting_id,
        "agent_id": agent_id,
        "removed": removed,
        "revoked_sessions": revoked_sessions,
        "agent": detached_agent,
        "stopped_group": stopped_group,
    }


def delete_live_agent_session_payload(
    output_root: Path,
    process_supervisor: object,
    payload: dict[str, object],
) -> dict[str, object]:
    meeting_id = _clean_existing_meeting_id(payload.get("meeting_id"))
    agent_id = _clean_existing_agent_id(payload.get("agent_id"))
    requested_group_id = _clean_optional_group_id(payload.get("group_id"))
    agent = _live_agent_entry(output_root, agent_id)
    group = _find_agent_process_group(
        process_supervisor,
        meeting_id=meeting_id,
        agent_id=agent_id,
        requested_group_id=requested_group_id or clean_lobby_text(agent.get("process_group_id"), limit=128),
    )
    stopped_group = _stop_running_agent_owned_group(
        process_supervisor,
        group,
        meeting_id=meeting_id,
        agent_id=agent_id,
    )
    deleted_group = _delete_agent_owned_process_group(
        process_supervisor,
        group,
        meeting_id=meeting_id,
        agent_id=agent_id,
    )
    deleted_configs = _delete_owned_session_configs(output_root, agent, group, agent_id=agent_id)
    meeting_dir = _existing_meeting_dir(output_root, meeting_id)
    meeting = _read_meeting(meeting_dir)
    updated_meeting, removed = _meeting_without_agent(meeting, agent_id)
    write_live_state(meeting_dir, updated_meeting)
    deleted_agent = delete_live_agent(output_root, agent_id)
    return {
        "status": "deleted",
        "meeting_id": meeting_id,
        "agent_id": agent_id,
        "removed": removed,
        "agent": deleted_agent,
        "stopped_group": stopped_group,
        "deleted_group": deleted_group,
        "deleted_configs": deleted_configs,
    }


DeleteSessionCommand = Callable[[Path, object, dict[str, object]], dict[str, object]]


@dataclass(frozen=True)
class LegacyLiveAgentRoomSessionService:
    """Delete one retained frontend-created session and record its audit."""

    output_root: Path
    process_supervisor: object
    delete_command: DeleteSessionCommand = delete_live_agent_session_payload

    def delete(self, payload: dict[str, object]) -> dict[str, object]:
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
        meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
        try:
            result = self.delete_command(self.output_root, self.process_supervisor, payload)
        except (OSError, ValueError) as error:
            self._record(
                status="failed",
                target_id=agent_id,
                error=str(error),
                details={"meeting_id": meeting_id},
            )
            raise
        self._record(
            status="success",
            target_id=str(result.get("agent_id") or agent_id),
            summary="deleted frontend live agent session",
            details={"meeting_id": str(result.get("meeting_id") or meeting_id)},
        )
        return result

    def record_invalid_json(self) -> None:
        self._record(status="failed", target_id="", error="Invalid JSON")

    def _record(
        self,
        *,
        status: str,
        target_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation="frontend_agent.delete_session",
            status=status,
            target_id=target_id,
            summary=summary,
            error=error,
            details=details or {},
        )


def _clean_existing_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or meeting_id in {".", ".."}:
        raise ValueError("Meeting was not found.")
    if "/" in meeting_id or "\\" in meeting_id or Path(meeting_id).name != meeting_id:
        raise ValueError(f"Meeting {meeting_id} was not found.")
    return meeting_id


def _clean_existing_agent_id(value: object) -> str:
    agent_id = clean_lobby_text(value, limit=128)
    if not agent_id:
        raise ValueError("Agent id is required.")
    return agent_id


def _clean_optional_group_id(value: object) -> str:
    raw = str(value or "").strip()
    return clean_live_agent_group_id(raw) if raw else ""


def _existing_meeting_dir(output_root: Path, meeting_id: str) -> Path:
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / meeting_id).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {meeting_id} was not found.") from error
    if not (meeting_dir / "live_state.json").exists():
        raise ValueError(f"Meeting {meeting_id} was not found.")
    return meeting_dir


def _read_meeting(meeting_dir: Path) -> dict[str, object]:
    data = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Meeting state is invalid.")
    return data


def _live_agent_entry(output_root: Path, agent_id: str) -> dict[str, object]:
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") == agent_id:
            return dict(agent)
    raise ValueError(f"Live agent {agent_id} was not found.")


def _meeting_without_agent(
    meeting: dict[str, object],
    agent_id: str,
    *,
    require_binding: bool = True,
) -> tuple[dict[str, object], dict[str, object]]:
    bindings = _as_dict_list(meeting.get("agent_bindings"))
    removed_bindings = [binding for binding in bindings if str(binding.get("agent_id") or "") == agent_id]
    if require_binding and not removed_bindings:
        raise ValueError(f"Meeting has no bound live agent {agent_id}.")
    remaining_bindings = [binding for binding in bindings if str(binding.get("agent_id") or "") != agent_id]
    removed_role_ids = {str(binding.get("role_id") or "") for binding in removed_bindings if str(binding.get("role_id") or "")}
    removed_provider_ids = {
        str(binding.get("provider_id") or "") for binding in removed_bindings if str(binding.get("provider_id") or "")
    }
    referenced_role_ids = {str(binding.get("role_id") or "") for binding in remaining_bindings}
    referenced_provider_ids = {str(binding.get("provider_id") or "") for binding in remaining_bindings}

    updated = dict(meeting)
    updated["agent_bindings"] = remaining_bindings
    updated["roles"] = [
        role
        for role in _as_dict_list(meeting.get("roles"))
        if str(role.get("id") or "") not in removed_role_ids or str(role.get("id") or "") in referenced_role_ids
    ]
    providers = _as_dict_map(meeting.get("provider_configs"))
    updated["provider_configs"] = {
        provider_id: provider
        for provider_id, provider in providers.items()
        if provider_id not in removed_provider_ids or provider_id in referenced_provider_ids
    }
    return updated, {
        "binding_count": len(removed_bindings),
        "role_ids": sorted(removed_role_ids),
        "provider_ids": sorted(removed_provider_ids),
    }


def _remove_or_detach_expelled_agent(
    output_root: Path,
    agent: dict[str, object],
    *,
    agent_id: str,
    meeting_id: str,
) -> dict[str, object]:
    if _is_invite_only_remote_agent(agent):
        return delete_live_agent(output_root, agent_id)
    return detach_live_agent_from_meeting(output_root, agent_id, meeting_id)


def _is_invite_only_remote_agent(agent: dict[str, object]) -> bool:
    return (
        str(agent.get("connection_kind") or "") == NATIVE_REMOTE_ROOM_CLIENT_KIND
        and not clean_lobby_text(agent.get("live_agent_config_path"), limit=2048)
        and not clean_lobby_text(agent.get("process_group_id"), limit=128)
    )


def _find_agent_process_group(
    process_supervisor: object,
    *,
    meeting_id: str,
    agent_id: str,
    requested_group_id: str = "",
) -> dict[str, object]:
    groups = _snapshot_process_groups(process_supervisor)
    if requested_group_id:
        requested = _find_group(groups, requested_group_id)
        if _group_matches_agent(requested, meeting_id=meeting_id, agent_id=agent_id):
            return requested
    matching = [
        group
        for group in groups
        if str(group.get("meeting_id") or "") == meeting_id and agent_id in _process_agent_ids(group.get("agents"))
    ]
    for group in matching:
        if _process_agent_ids(group.get("agents")) == [agent_id]:
            return group
    return matching[0] if matching else {}


def _snapshot_process_groups(process_supervisor: object) -> list[dict[str, object]]:
    if hasattr(process_supervisor, "snapshot_groups"):
        groups = process_supervisor.snapshot_groups()
    elif hasattr(process_supervisor, "list_groups"):
        groups = process_supervisor.list_groups()
    else:
        groups = []
    return [group for group in groups if isinstance(group, dict)] if isinstance(groups, list) else []


def _find_group(groups: list[dict[str, object]], group_id: str) -> dict[str, object]:
    for group in groups:
        if str(group.get("group_id") or "") == group_id:
            return group
    return {}


def _group_matches_agent(group: dict[str, object], *, meeting_id: str, agent_id: str) -> bool:
    return bool(
        group
        and str(group.get("meeting_id") or "") == meeting_id
        and agent_id in _process_agent_ids(group.get("agents"))
    )


def _stop_running_agent_owned_group(
    process_supervisor: object,
    group: dict[str, object],
    *,
    meeting_id: str,
    agent_id: str,
) -> dict[str, object]:
    if not group or str(group.get("status") or "") not in {"running", "restarting"}:
        return {}
    group_id = str(group.get("group_id") or "")
    if _process_agent_ids(group.get("agents")) != [agent_id]:
        raise ValueError(
            f"Live agent {agent_id} is in multi-agent group {group_id}; stop that group before removing this agent."
        )
    if hasattr(process_supervisor, "stop_group_if_owned"):
        return process_supervisor.stop_group_if_owned(group_id, meeting_id=meeting_id, agent_ids=[agent_id])
    stopped = process_supervisor.stop_group(group_id)
    if _process_agent_ids(stopped.get("agents")) != [agent_id]:
        raise ValueError(f"Live agent group {group_id} is not an agent-owned process.")
    return stopped


def _delete_agent_owned_process_group(
    process_supervisor: object,
    group: dict[str, object],
    *,
    meeting_id: str,
    agent_id: str,
) -> dict[str, object]:
    if not group:
        return {}
    group_id = str(group.get("group_id") or "")
    if _process_agent_ids(group.get("agents")) != [agent_id]:
        raise ValueError(
            f"Live agent {agent_id} is in multi-agent group {group_id}; stop that group before deleting this session."
        )
    if hasattr(process_supervisor, "delete_group_record_if_owned"):
        return process_supervisor.delete_group_record_if_owned(group_id, meeting_id=meeting_id, agent_ids=[agent_id])
    return {"status": "unsupported", "group_id": group_id}


def _delete_owned_session_configs(
    output_root: Path,
    agent: dict[str, object],
    group: dict[str, object],
    *,
    agent_id: str,
) -> list[dict[str, object]]:
    raw_paths = [
        clean_lobby_text(agent.get("live_agent_config_path"), limit=2048),
        clean_lobby_text(group.get("config_path"), limit=2048),
    ]
    results = []
    seen: set[Path] = set()
    for raw_path in raw_paths:
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        results.append(_delete_owned_session_config(output_root, resolved, agent_id=agent_id))
    return results


def _delete_owned_session_config(output_root: Path, path: Path, *, agent_id: str) -> dict[str, object]:
    if not _path_under_any(path, _managed_config_roots(output_root)):
        return {"path": str(path), "status": "skipped", "reason": "outside_managed_config_root"}
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    if not _config_file_owned_by_agent(path, agent_id):
        return {"path": str(path), "status": "skipped", "reason": "not_agent_owned_config"}
    path.unlink()
    return {"path": str(path), "status": "deleted"}


def _managed_config_roots(output_root: Path) -> list[Path]:
    return [
        (output_root / "live-agent-created").resolve(strict=False),
        (output_root / "live-agent-runs" / "per-agent-configs").resolve(strict=False),
    ]


def _path_under_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _config_file_owned_by_agent(path: Path, agent_id: str) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, list) or len(agents) != 1 or not isinstance(agents[0], dict):
        return False
    return str(agents[0].get("agent_id") or "") == agent_id


def _process_agent_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    agent_ids = []
    for item in value:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        if agent_id:
            agent_ids.append(agent_id)
    return agent_ids


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_dict_map(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): dict(item)
        for key, item in value.items()
        if str(key) and isinstance(item, dict)
    }
