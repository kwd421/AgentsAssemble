from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.character_mode import character_mode_snapshot, clean_persona_card_id
from agentsassemble.live_agent_finalization import finalize_live_agent_meeting
from agentsassemble.meeting_events import append_live_event, write_live_state
from agentsassemble.models import AgentBinding
from agentsassemble.persona_cards import load_persona_card, render_persona_prompt, save_persona_card


def run_live_agent_persona_smoke(
    *,
    output_root: Path,
    card_path: Path,
    meeting_id: str = "",
    character_mode: str = "on",
    context: str = "",
) -> dict[str, object]:
    loaded_card = load_persona_card(card_path)
    card = replace(loaded_card, id=_safe_card_id(loaded_card.id))
    clean_meeting_id = _smoke_meeting_id(meeting_id)
    persona_card_path = output_root / "personas" / card.id / "card.json"
    save_persona_card(persona_card_path, card)
    meeting_dir = output_root / "meetings" / clean_meeting_id
    meeting_dir.mkdir(parents=True, exist_ok=True)

    binding = AgentBinding(
        agent_id="persona-smoke-agent",
        role_id="persona_smoke",
        owner_id="local-user",
        provider_id="fake-persona-smoke",
        model_id=None,
        permission_profile_id="meeting_readonly",
        persona_card_id=card.id,
        character_mode=character_mode,
    )
    render = render_persona_prompt(
        card,
        recent_messages=context or "The room asks the character to keep speech and artifacts separate.",
        mode=character_mode,
        surface="work_speech" if character_mode == "work_speech_only" else "play_speech",
    )
    meeting = _persona_smoke_meeting(clean_meeting_id, binding, output_root=output_root)
    write_live_state(meeting_dir, meeting)
    request = append_live_event(
        meeting_dir,
        {
            "kind": "live_agent_turn_request",
            "meeting_id": clean_meeting_id,
            "target_agent_id": binding.agent_id,
            "role_id": binding.role_id,
            "display_name": card.display_name,
            "content": "Fake provider smoke: answer once without leaking persona card bodies into artifacts.",
            "turn_id": "persona_smoke:0:persona_smoke",
            "turn_index": 0,
        },
    )
    append_live_event(
        meeting_dir,
        {
            "kind": "message",
            "meeting_id": clean_meeting_id,
            "actor_id": binding.agent_id,
            "target_agent_id": binding.agent_id,
            "source_event_id": request["id"],
            "role_id": binding.role_id,
            "display_name": card.display_name,
            "content": "Character mode smoke passed: speech style stayed separate from professional artifacts.",
            "turn_id": "persona_smoke:0:persona_smoke",
            "turn_index": 0,
        },
    )
    finalization = finalize_live_agent_meeting(meeting_dir, force=True)
    meeting_record = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
    contract = meeting_record.get("persona_artifact_contract") if isinstance(meeting_record.get("persona_artifact_contract"), dict) else {}
    contract_status = str(contract.get("status") or "missing")
    contract_artifact_count = int(contract.get("artifact_count") or 0)
    status = (
        "ok"
        if finalization.get("status") == "finalized" and contract_status == "pass" and contract_artifact_count > 0
        else "warning"
    )
    return {
        "status": status,
        "meeting_id": clean_meeting_id,
        "meeting_dir": str(meeting_dir),
        "persona": card.safe_summary(),
        "character_mode": character_mode,
        "render": {
            "mode": render.mode,
            "surface": render.surface,
            "line_count": len(render.lines),
            "active_lore_count": len(render.scan.entries),
            "ignored_features": render.scan.ignored_features,
        },
        "finalization": {
            "status": finalization.get("status"),
            "official_event_count": finalization.get("official_event_count", 0),
        },
        "persona_artifact_contract": {
            "status": contract_status,
            "violation_count": int(contract.get("violation_count") or 0),
            "artifact_count": contract_artifact_count,
        },
    }


def _persona_smoke_meeting(meeting_id: str, binding: AgentBinding, *, output_root: Path) -> dict[str, object]:
    role = {
        "id": binding.role_id,
        "display_name": "Persona Smoke",
        "lens": "character runtime smoke",
        "research_focus": "verify character prompts and artifact guard",
        "personality": {},
        "source_preferences": [],
    }
    return {
        "meeting_id": meeting_id,
        "question": "Can character mode keep speech and artifacts separate?",
        "display_question": "Can character mode keep speech and artifacts separate?",
        "topic": "persona smoke",
        "display_topic": "persona smoke",
        "meeting_mode": "debate",
        "moderator": {"enabled": True},
        "roles": [role],
        "meeting_template": {
            "id": "persona_smoke",
            "display_name": "Persona smoke",
            "rounds": [
                {
                    "id": "persona_smoke",
                    "title": "Persona Smoke",
                    "context_scope": "meeting",
                    "instruction": "Verify character mode without leaking card bodies.",
                    "turn_control": {"selection": "all_roles"},
                }
            ],
        },
        "research_depth": {"name": "smoke"},
        "research_steering": {"prompt": None},
        "memory_context": {"recent_episodes": [], "agent_memories": {}},
        "memory_input": {"research_summaries": []},
        "agent_bindings": [binding.to_dict()],
        "character_mode": character_mode_snapshot(output_root, [binding]),
        "provider_configs": {"fake-persona-smoke": {"kind": "mock", "display_name": "Fake Persona Smoke"}},
        "permission_profiles": {"meeting_readonly": {"meeting_read": True, "lobby_chat": True, "official_turn": True}},
        "agent_config_source": "persona-smoke",
        "debate_rounds": [],
        "room_chat": [],
        "moderator_synthesis": {},
        "decision_gate": {},
        "artifacts": {"agenda": "agenda.md"},
        "event_log": [
            {
                "created_at": datetime.now(UTC).isoformat(),
                "scope": "meeting",
                "kind": "persona_smoke_started",
                "actor_id": "system",
                "message": "Persona smoke meeting created.",
            }
        ],
        "live_status": "running",
    }


def _smoke_meeting_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "_.-" else "-" for char in str(value or "").strip()).strip(".-")
    if cleaned:
        return cleaned[:128]
    return "persona-smoke-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _safe_card_id(value: str) -> str:
    return clean_persona_card_id(value) or "persona-smoke-card"
