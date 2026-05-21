from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.artifacts import write_public_artifacts
from agentsassemble.live_meeting_memory import build_live_meeting_memory, write_live_meeting_memory_artifacts
from agentsassemble.live_transcript import official_live_transcript_events, render_live_transcript
from agentsassemble.meeting_events import append_live_event, clean_lobby_text, read_live_events, write_live_state
from agentsassemble.meeting_record import derive_failure_state


def finalize_live_agent_meeting(meeting_dir: Path, *, force: bool = False) -> dict[str, object]:
    if not meeting_dir.exists():
        raise ValueError(f"Meeting {meeting_dir.name} was not found.")
    events = read_live_events(meeting_dir, limit=None)
    pending_requests = _pending_turn_requests(events)
    if pending_requests:
        pending_ids = ", ".join(str(event.get("id") or "unknown") for event in pending_requests)
        raise ValueError(f"Cannot finalize meeting with pending official turn requests: {pending_ids}.")
    if _has_complete_finalization(meeting_dir) and not force:
        meeting = _read_json(meeting_dir / "meeting.json")
        return {
            "status": "already_finalized",
            "meeting_id": str(meeting.get("meeting_id") or meeting_dir.name),
            "official_event_count": len(official_live_transcript_events(events)),
            "shared_memory": _shared_memory_result(
                meeting.get("shared_memory") if isinstance(meeting.get("shared_memory"), dict) else {}
            ),
        }

    live_meeting = _read_live_meeting(meeting_dir)
    official_events = official_live_transcript_events(events)
    if not official_events:
        raise ValueError("No official live events are available to finalize.")

    meeting = build_finalized_live_meeting_record(live_meeting, events)
    transcript_text = render_live_transcript(events, meeting=meeting)
    shared_memory = write_live_meeting_memory_artifacts(meeting_dir, meeting=meeting)
    meeting["shared_memory"] = shared_memory
    write_public_artifacts(meeting_dir, meeting, transcript_text=transcript_text)
    write_live_state(meeting_dir, meeting)
    return_packet_events = _append_return_packet_ready_events(meeting_dir, meeting)
    artifact_event = append_live_event(
        meeting_dir,
        {
            "kind": "artifact",
            "meeting_id": meeting["meeting_id"],
            "content": "Resident live-agent meeting artifacts were finalized.",
        },
    )
    return {
        "status": "finalized",
        "meeting_id": meeting["meeting_id"],
        "official_event_count": len(official_events),
        "artifact_event_id": artifact_event["id"],
        "return_packet_event_count": len(return_packet_events),
        "return_packet_event_ids": [
            str(event.get("id") or "") for event in return_packet_events if str(event.get("id") or "").strip()
        ],
        "artifacts": meeting["artifacts"],
        "shared_memory": _shared_memory_result(shared_memory),
    }


def build_finalized_live_meeting_record(
    live_meeting: dict[str, object],
    events: list[dict[str, object]],
) -> dict[str, object]:
    official_events = official_live_transcript_events(events)
    debate_rounds = live_events_to_debate_rounds(live_meeting, official_events)
    synthesis = resident_no_synthesis_record(live_meeting, official_events)
    evidence_gate = resident_live_evidence_gate()
    decision_gate = resident_needs_user_decision_gate()
    meeting = dict(live_meeting)
    meeting["shared_memory"] = build_live_meeting_memory(events, meeting=meeting)
    meeting.setdefault("room_chat", [])
    meeting.setdefault("memory_context", {"recent_episodes": [], "agent_memories": {}})
    meeting.setdefault("memory_input", {"research_summaries": []})
    meeting.setdefault("follow_up", {"parent_meeting_id": None, "note": None})
    meeting["debate_rounds"] = debate_rounds
    meeting["moderator_synthesis"] = synthesis
    meeting["evidence_gate"] = evidence_gate
    meeting["decision_gate"] = decision_gate
    meeting["decision_status"] = {
        "status": "pending_user",
        "winner": "Undetermined",
        "confidence": "low",
        "evidence_gate_status": evidence_gate["status"],
        "caveat_count": len(synthesis["caveats"]),
        "decision_gate_status": decision_gate["status"],
        "next_actions": [
            "Review transcript.md and decide whether to accept, continue, or rerun the live meeting.",
            "Do not start implementation until the decision gate is resolved.",
        ],
    }
    meeting["failure_state"] = derive_failure_state(
        synthesis=synthesis,
        evidence_gate=evidence_gate,
        decision_gate=decision_gate,
        debate_rounds=debate_rounds,
        room_chat=_as_dict_list(meeting.get("room_chat")),
    )
    meeting["artifacts"] = _final_artifact_refs(meeting)
    meeting["event_log"] = [
        *(_as_dict_list(meeting.get("event_log"))),
        {
            "created_at": datetime.now(UTC).isoformat(),
            "scope": "meeting",
            "kind": "artifacts_written",
            "actor_id": "system",
            "message": "Resident live-agent meeting artifacts finalized.",
            "payload": {"official_event_count": len(official_events)},
        },
    ]
    meeting["live_status"] = "complete"
    meeting["live_finalization"] = {
        "status": "finalized",
        "finalized_at": datetime.now(UTC).isoformat(),
        "official_event_count": len(official_events),
        "official_event_ids": [str(event.get("id") or "") for event in official_events if event.get("id")],
        "source": "live_events.jsonl",
        "decision_policy": "user_decision_required",
    }
    return meeting


def live_events_to_debate_rounds(
    live_meeting: dict[str, object],
    official_events: list[dict[str, object]],
) -> list[dict[str, object]]:
    round_definitions = _round_definitions_by_id(live_meeting)
    rounds: list[dict[str, object]] = []
    indexes: dict[str, int] = {}
    for event in official_events:
        if str(event.get("kind") or "") != "message":
            continue
        round_id = _event_round_id(event)
        if round_id not in indexes:
            indexes[round_id] = len(rounds)
            round_definition = round_definitions.get(round_id, {})
            rounds.append(
                {
                    "id": round_id,
                    "title": str(round_definition.get("title") or _round_title(round_id)),
                    "context_scope": round_definition.get("context_scope"),
                    "instruction": round_definition.get("instruction"),
                    "turn_control": round_definition.get("turn_control", {}),
                    "status": "answered",
                    "messages": [],
                }
            )
        message = _debate_message_from_event(event, round_id)
        rounds[indexes[round_id]]["messages"].append(message)  # type: ignore[index]
    for round_record in rounds:
        messages = _as_dict_list(round_record.get("messages"))
        round_record["role_ids"] = [
            role_id
            for message in messages
            if (role_id := clean_lobby_text(message.get("role_id"), limit=128))
        ]
        round_record["turn_count"] = len(messages)
        round_record["answered_count"] = len(messages)
        round_record["timeout_count"] = 0
        round_record["skipped_count"] = 0
    return rounds


def resident_no_synthesis_record(
    live_meeting: dict[str, object],
    official_events: list[dict[str, object]],
) -> dict[str, object]:
    summary = _resident_summary(official_events)
    role_names = [
        str(role.get("display_name") or role.get("id"))
        for role in _as_dict_list(live_meeting.get("roles"))
        if str(role.get("display_name") or role.get("id") or "").strip()
    ]
    tasks = {
        role_id: "Review transcript.md and wait for the user decision before implementation."
        for role in _as_dict_list(live_meeting.get("roles"))
        if (role_id := clean_lobby_text(role.get("id"), limit=128))
    }
    return {
        "status": "needs_user_decision",
        "winner": "Undetermined",
        "ranking": role_names or ["Undetermined"],
        "confidence": "low",
        "summary": summary,
        "caveats": [
            "Resident live meeting was finalized from official live events without model synthesis.",
            "No winner, implementation task, or evidence conclusion is inferred from free-form replies.",
        ],
        "tasks": tasks,
    }


def resident_needs_user_decision_gate() -> dict[str, object]:
    return {
        "status": "needs_user_decision",
        "can_finalize": False,
        "required_action": "user_decision",
        "reasons": ["resident_live_finalization_requires_user_decision"],
        "minority_positions": [],
        "ambiguous_positions": [],
        "final_state": False,
    }


def resident_live_evidence_gate() -> dict[str, object]:
    return {
        "status": "not_applicable",
        "total_supported_claims": 0,
        "total_unsupported_claims": 0,
        "total_weak_claims": 0,
        "total_verifier_rejected_claims": 0,
    }


def _has_complete_finalization(meeting_dir: Path) -> bool:
    required_shared_memory = (
        meeting_dir / "shared_memory" / "rolling-summary.md",
        meeting_dir / "shared_memory" / "open-questions.md",
        meeting_dir / "shared_memory" / "action-items.md",
        meeting_dir / "shared_memory" / "index.json",
    )
    if not all((meeting_dir / name).exists() for name in ("meeting.json", "transcript.md", "decision.md")):
        return False
    if not all(path.exists() for path in required_shared_memory):
        return False
    try:
        meeting = _read_json(meeting_dir / "meeting.json")
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    finalization = meeting.get("live_finalization") if isinstance(meeting.get("live_finalization"), dict) else {}
    if meeting.get("live_status") != "complete" or finalization.get("status") != "finalized":
        return False
    if not (meeting_dir / "tasks").is_dir():
        return False
    if not (meeting_dir / "delegate_packets").is_dir():
        return False
    if not (meeting_dir / "return_packets").is_dir():
        return False
    for role in _as_dict_list(meeting.get("roles")):
        role_id = clean_lobby_text(role.get("id"), limit=128)
        if not role_id:
            continue
        required = (
            meeting_dir / "tasks" / f"{role_id}.md",
            meeting_dir / "delegate_packets" / f"{role_id}.json",
            meeting_dir / "delegate_packets" / f"{role_id}.md",
            meeting_dir / "return_packets" / f"{role_id}.json",
            meeting_dir / "return_packets" / f"{role_id}.md",
        )
        if not all(path.exists() for path in required):
            return False
    return True


def _read_live_meeting(meeting_dir: Path) -> dict[str, object]:
    live_path = meeting_dir / "live_state.json"
    meeting_path = meeting_dir / "meeting.json"
    if live_path.exists():
        return _read_json(live_path)
    if meeting_path.exists():
        return _read_json(meeting_path)
    raise ValueError("Meeting record is missing.")


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Meeting record is invalid.")
    return payload


def _pending_turn_requests(events: list[dict[str, object]]) -> list[dict[str, object]]:
    pending = []
    for event in events:
        if str(event.get("kind") or "") != "live_agent_turn_request":
            continue
        if _is_review_checkpoint_turn_request(event):
            continue
        request_id = str(event.get("id") or "")
        agent_id = str(event.get("target_agent_id") or "")
        if not request_id or not agent_id or _official_transcript_reply(events, agent_id=agent_id, source_event_id=request_id) is None:
            pending.append(event)
    return pending


def _is_review_checkpoint_turn_request(event: dict[str, object]) -> bool:
    return (
        bool(clean_lobby_text(event.get("review_checkpoint_id"), limit=128))
        and event.get("channel") == "review"
        and event.get("official_record") is False
    )


def _official_transcript_reply(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> dict[str, object] | None:
    for event in official_live_transcript_events(events):
        if str(event.get("kind") or "") != "message":
            continue
        if str(event.get("actor_id") or "") != agent_id:
            continue
        if str(event.get("source_event_id") or "") != source_event_id:
            continue
        return event
    return None


def _round_definitions_by_id(meeting: dict[str, object]) -> dict[str, dict[str, object]]:
    template = meeting.get("meeting_template") if isinstance(meeting.get("meeting_template"), dict) else {}
    return {
        round_id: item
        for item in _as_dict_list(template.get("rounds"))
        if (round_id := clean_lobby_text(item.get("id"), limit=128))
    }


def _event_round_id(event: dict[str, object]) -> str:
    explicit_round = clean_lobby_text(event.get("round"), limit=128)
    if explicit_round:
        return explicit_round
    turn_id = clean_lobby_text(event.get("turn_id"), limit=128)
    if ":" in turn_id:
        return turn_id.split(":", 1)[0]
    return "live_official"


def _round_title(round_id: str) -> str:
    if round_id == "live_official":
        return "Live Official Turns"
    return round_id.replace("_", " ").title()


def _debate_message_from_event(event: dict[str, object], round_id: str) -> dict[str, object]:
    message: dict[str, object] = {
        "role_id": event.get("role_id") or event.get("actor_id") or "unknown",
        "display_name": event.get("display_name") or event.get("actor_id") or event.get("role_id") or "Unknown Speaker",
        "round": round_id,
        "turn_id": event.get("turn_id") or "",
        "turn_index": event.get("turn_index"),
        "engagement_mode": event.get("engagement_mode") or "moderator_called",
        "content": event.get("content") or "",
    }
    for key in (
        "position",
        "stance_status",
        "stance_delta",
        "changed_by",
        "change_reason",
        "remaining_resistance",
        "emotion",
        "change_conditions",
        "confidence",
    ):
        if key in event and event.get(key) not in (None, "", [], {}):
            message[key] = event[key]
    return message


def _resident_summary(official_events: list[dict[str, object]]) -> str:
    synthesis_text = "\n\n".join(
        str(event.get("content") or "").strip()
        for event in official_events
        if str(event.get("kind") or "") == "synthesis" and str(event.get("content") or "").strip()
    ).strip()
    if synthesis_text:
        return synthesis_text
    message_count = sum(1 for event in official_events if str(event.get("kind") or "") == "message")
    return (
        "Resident live meeting produced "
        f"{message_count} official live message{'s' if message_count != 1 else ''}. "
        "Review transcript.md before choosing a winner or assigning implementation work."
    )


def _final_artifact_refs(meeting: dict[str, object]) -> dict[str, object]:
    artifacts = dict(meeting.get("artifacts") if isinstance(meeting.get("artifacts"), dict) else {})
    artifacts.update(
        {
            "agenda": "agenda.md",
            "transcript": "transcript.md",
            "decision": "decision.md",
            "meeting": "meeting.json",
            "shared_memory": "shared_memory/",
            "shared_memory_index": "shared_memory/index.json",
            "tasks": "tasks/",
            "delegate_packets": "delegate_packets/",
            "return_packets": "return_packets/",
        }
    )
    return artifacts


def _append_return_packet_ready_events(meeting_dir: Path, meeting: dict[str, object]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    role_names = {
        role_id: str(role.get("display_name") or role_id)
        for role in _as_dict_list(meeting.get("roles"))
        if (role_id := clean_lobby_text(role.get("id"), limit=128))
    }
    for binding in _as_dict_list(meeting.get("agent_bindings")):
        role_id = clean_lobby_text(binding.get("role_id"), limit=128)
        agent_id = clean_lobby_text(binding.get("agent_id"), limit=64)
        if not role_id or not agent_id:
            continue
        packet_path = meeting_dir / "return_packets" / f"{role_id}.md"
        packet_json_path = meeting_dir / "return_packets" / f"{role_id}.json"
        if not packet_path.exists() or not packet_json_path.exists():
            continue
        artifact_path = f"return_packets/{role_id}.md"
        artifact_json_path = f"return_packets/{role_id}.json"
        events.append(
            append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "meeting_id": meeting.get("meeting_id"),
                    "channel": "system",
                    "official_record": False,
                    "audience": f"agent:{agent_id}",
                    "target_agent_id": agent_id,
                    "role_id": role_id,
                    "display_name": role_names.get(role_id, role_id),
                    "artifact_kind": "return_packet",
                    "artifact_path": artifact_path,
                    "artifact_json_path": artifact_json_path,
                    "content": f"Return packet ready: {artifact_path}",
                },
            )
        )
    return events


def _shared_memory_result(memory: dict[str, object]) -> dict[str, object]:
    return {
        "official_event_count": int(memory.get("official_event_count") or 0),
        "last_official_event_id": clean_lobby_text(memory.get("last_official_event_id"), limit=128),
        "decision_count": len(memory.get("decisions") if isinstance(memory.get("decisions"), list) else []),
        "open_question_count": len(memory.get("open_questions") if isinstance(memory.get("open_questions"), list) else []),
        "action_item_count": len(memory.get("action_items") if isinstance(memory.get("action_items"), list) else []),
    }


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
