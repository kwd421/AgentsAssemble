from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.artifacts import write_agenda
from agentsassemble.character_mode import character_mode_snapshot
from agentsassemble.config import load_council_config
from agentsassemble.live_agents import connect_live_agent, read_live_agents, update_live_agent_engagement
from agentsassemble.meeting import _moderator_control_snapshot
from agentsassemble.meeting_events import append_live_event, clean_lobby_text, write_live_state
from agentsassemble.meeting_setup import prepare_meeting_setup
from agentsassemble.memory import load_memory_context
from agentsassemble.models import AgentBinding, ProviderConfig, Role


def start_live_agent_meeting(
    output_root: Path,
    *,
    council_config_path: Path | None = None,
    agent_config_path: Path | None = None,
    meeting_id: str = "",
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = now or datetime.now(UTC)
    config = load_council_config(council_config_path)
    setup = prepare_meeting_setup(
        config.roles,
        "mock",
        None,
        True,
        agent_config_path,
    )
    bound_agent_bindings = [_official_turn_binding(binding) for binding in setup.agent_bindings]
    clean_meeting_id = _clean_meeting_id(meeting_id) or _new_meeting_id(current_time)
    root = output_root
    meeting_dir = _new_meeting_dir(root, clean_meeting_id)
    meeting_dir.mkdir(parents=True, exist_ok=False)

    roles = [asdict(role) for role in config.roles]
    memory_context = load_memory_context(root, config.roles)
    meeting = {
        "meeting_id": clean_meeting_id,
        "question": config.question,
        "display_question": config.display_question,
        "topic": config.topic,
        "display_topic": config.display_topic,
        "roles": roles,
        "meeting_template": {
            "id": config.meeting_template_id,
            "display_name": config.meeting_template_name,
            "rounds": [
                {
                    "id": round_definition.id,
                    "title": round_definition.title,
                    "report_label": round_definition.report_label,
                    "context_scope": round_definition.context_scope,
                    "instruction": round_definition.instruction,
                    "turn_control": round_definition.turn_control.to_dict(),
                }
                for round_definition in config.rounds
            ],
        },
        "meeting_mode": config.meeting_mode,
        "moderator": config.moderator.to_dict(),
        "moderator_control": _moderator_control_snapshot(config),
        "debate_rounds": [],
        "room_chat": [],
        "moderator_synthesis": {},
        "decision_gate": {},
        "agent_bindings": [binding.to_dict() for binding in bound_agent_bindings],
        "character_mode": character_mode_snapshot(root, bound_agent_bindings),
        "provider_configs": {
            provider_id: provider.public_dict()
            for provider_id, provider in setup.providers.items()
        },
        "permission_profiles": {
            profile_id: profile.to_dict()
            for profile_id, profile in setup.permissions.items()
        },
        "agent_config_source": setup.config_source,
        "memory_context": memory_context,
        "research_depth": {"name": "resident_live"},
        "research_steering": {"stance": "open", "prompt": None},
        "artifacts": {"agenda": "agenda.md"},
        "live_status": "running",
    }
    write_live_state(meeting_dir, meeting)
    write_agenda(meeting_dir, meeting)
    append_live_event(
        meeting_dir,
        {
            "kind": "status",
            "meeting_id": clean_meeting_id,
            "content": "Resident live-agent meeting created.",
        },
    )
    _register_bound_live_agents(root, config.roles, bound_agent_bindings, setup.providers, meeting_id=clean_meeting_id)
    return {
        "meeting_id": clean_meeting_id,
        "path": str(meeting_dir),
        "meeting": meeting,
        "agents": read_live_agents(root),
    }


def _register_bound_live_agents(
    output_root: Path,
    roles: list[Role],
    bindings: list[AgentBinding],
    providers: dict[str, ProviderConfig],
    *,
    meeting_id: str,
) -> None:
    roles_by_id = {role.id: role for role in roles}
    for binding in bindings:
        role = roles_by_id.get(binding.role_id)
        provider = providers.get(binding.provider_id)
        if provider is None:
            continue
        connection_kind = _connection_kind_for_provider(provider.kind)
        connect_live_agent(
            output_root,
            {
                "agent_id": binding.agent_id,
                "display_name": role.display_name if role is not None else binding.agent_id,
                "provider_kind": provider.kind,
                "connection_kind": connection_kind,
                "meeting_id": meeting_id,
                "session_id": binding.session_id or "",
                "endpoint": (provider.endpoint or "") if connection_kind == "remote_bridge" else "",
                "engagement_mode": "moderator_called",
                "persona_card_id": binding.persona_card_id,
                "character_mode": binding.character_mode,
                "status": "offline",
                "capabilities": ["room_chat", "official_turn"],
            },
        )
        update_live_agent_engagement(output_root, binding.agent_id, "moderator_called")


def _connection_kind_for_provider(provider_kind: str) -> str:
    if provider_kind == "remote_http_bridge":
        return "remote_bridge"
    if provider_kind == "local_cli":
        return "local_cli"
    if provider_kind == "codex_live_session":
        return "codex_resume"
    if provider_kind == "kiro_live_session":
        return "live_session"
    if provider_kind == "grok_live_session":
        return "live_session"
    if provider_kind == "antigravity_live_session":
        return "live_session"
    if provider_kind == "hermes_live_session":
        return "live_session"
    return "manual"


def _official_turn_binding(binding: AgentBinding) -> AgentBinding:
    return replace(binding, engagement_mode="moderator_called")


def _new_meeting_dir(output_root: Path, meeting_id: str) -> Path:
    clean_meeting_id = _clean_meeting_id(meeting_id)
    if not clean_meeting_id:
        raise ValueError("Meeting id is required.")
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / clean_meeting_id).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.") from error
    return meeting_dir


def _clean_meeting_id(meeting_id: str) -> str:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id:
        return ""
    if clean_meeting_id in {".", ".."}:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.")
    if "/" in clean_meeting_id or "\\" in clean_meeting_id or Path(clean_meeting_id).name != clean_meeting_id:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.")
    return clean_meeting_id


def _new_meeting_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
