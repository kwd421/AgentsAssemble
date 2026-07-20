from __future__ import annotations

import json
from pathlib import Path

from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def safe_meeting_dir(output_root: Path, meeting_id: str) -> Path:
    """Resolve a legacy meeting directory without allowing path traversal."""

    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id or clean_meeting_id in {".", ".."}:
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    if "/" in clean_meeting_id or "\\" in clean_meeting_id or Path(clean_meeting_id).name != clean_meeting_id:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.")
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / clean_meeting_id).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.") from error
    return meeting_dir


def read_meeting_record(meeting_dir: Path) -> dict[str, object]:
    """Read the best available legacy meeting record."""

    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    if meeting_path.exists():
        meeting = json.loads(meeting_path.read_text(encoding="utf-8"))
        return merge_live_progress_from_path(meeting, live_path)
    if live_path.exists():
        return json.loads(live_path.read_text(encoding="utf-8"))
    raise ValueError("Meeting record is missing.")


def load_meeting_record(meeting_dir: Path) -> tuple[dict[str, object], Path, bool]:
    """Load a final record when valid, otherwise retain the live-state fallback."""

    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    if meeting_path.exists():
        try:
            meeting = json.loads(meeting_path.read_text(encoding="utf-8"))
            return merge_live_progress_from_path(meeting, live_path), meeting_path, True
        except json.JSONDecodeError:
            if not live_path.exists():
                raise
    return json.loads(live_path.read_text(encoding="utf-8")), live_path, False


def merge_live_progress_from_path(
    meeting: dict[str, object],
    live_path: Path,
) -> dict[str, object]:
    if not live_path.exists():
        return meeting
    try:
        live_state = json.loads(live_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return meeting
    if not isinstance(live_state, dict):
        return meeting
    return _merge_live_progress(meeting, live_state)


def live_agent_admission_details(
    meeting: dict[str, object],
    agent: dict[str, object],
    *,
    agent_id: str,
) -> dict[str, object]:
    """Project host-approved legacy binding evidence for one resident agent."""

    binding = _meeting_binding_for_agent(meeting, agent_id)
    if not binding:
        return {"admission_status": "meeting_lobby_only", "host_approved_binding": False}

    provider_id = clean_lobby_text(binding.get("provider_id"), limit=128)
    providers = meeting.get("provider_configs") if isinstance(meeting.get("provider_configs"), dict) else {}
    provider = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else {}
    binding_provider_kind = clean_lobby_text(provider.get("kind"), limit=64)
    registered_provider_kind = clean_lobby_text(agent.get("provider_kind"), limit=64)
    conflicts: list[str] = []
    if not provider:
        conflicts.append("binding_provider_missing")
    elif binding_provider_kind and registered_provider_kind and binding_provider_kind != registered_provider_kind:
        conflicts.append("provider_kind_mismatch")

    admission_status = "binding_conflict" if conflicts else "bound_to_meeting"
    details: dict[str, object] = {
        "admission_status": admission_status,
        "host_approved_binding": admission_status == "bound_to_meeting",
        "binding_role_id": clean_lobby_text(binding.get("role_id"), limit=128),
        "binding_provider_id": provider_id,
        "binding_provider_kind": binding_provider_kind,
        "binding_permission_profile_id": clean_lobby_text(binding.get("permission_profile_id"), limit=128),
        "binding_join_mode": clean_lobby_text(binding.get("join_mode"), limit=64),
    }
    if conflicts:
        details["binding_conflicts"] = conflicts
    return details


def _merge_live_progress(
    meeting: dict[str, object],
    live_state: dict[str, object],
) -> dict[str, object]:
    merged = dict(meeting)
    live_rounds = _as_dict_list(live_state.get("debate_rounds"))
    if live_rounds:
        merged["debate_rounds"] = _merge_debate_round_records(
            _as_dict_list(meeting.get("debate_rounds")),
            live_rounds,
        )
    return merged


def _merge_debate_round_records(
    base_rounds: list[dict[str, object]],
    live_rounds: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged_rounds = [dict(item) for item in base_rounds]
    indexes = {
        round_id: index
        for index, item in enumerate(merged_rounds)
        if (round_id := clean_lobby_text(item.get("id") or item.get("round"), limit=128))
    }
    for live_item in live_rounds:
        round_id = clean_lobby_text(live_item.get("id") or live_item.get("round"), limit=128)
        if not round_id:
            continue
        if round_id in indexes:
            index = indexes[round_id]
            base_item = merged_rounds[index]
            base_status = clean_lobby_text(base_item.get("status"), limit=32)
            live_status = clean_lobby_text(live_item.get("status"), limit=32)
            merged_item = dict(base_item)
            merged_item.update(live_item)
            if base_status == "answered" and live_status != "answered":
                merged_item["status"] = "answered"
            merged_rounds[index] = merged_item
        else:
            indexes[round_id] = len(merged_rounds)
            merged_rounds.append(dict(live_item))
    return merged_rounds


def _meeting_binding_for_agent(meeting: dict[str, object], agent_id: str) -> dict[str, object]:
    for binding in _as_dict_list(meeting.get("agent_bindings")):
        if clean_lobby_text(binding.get("agent_id"), limit=64) == agent_id:
            return binding
    return {}


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
