from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from agentsassemble.character_mode import (
    clean_first_message_index,
    clean_persona_card_id,
    clean_persona_variables,
    normalize_character_mode,
)
from agentsassemble.models import (
    AgentBinding,
    CouncilConfig,
    MeetingRound,
    ModeratorConfig,
    PermissionProfile,
    ProviderConfig,
    Role,
    RoundTurnControl,
    normalize_engagement_mode,
    normalize_meeting_mode,
)
from agentsassemble.templates import DEMO_MEETING_TEMPLATE


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "demo-council.json"


def load_council_config(path: Path | str | None = None) -> CouncilConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data = json.loads(config_path.read_text(encoding="utf-8"))
    roles = [_role_from_dict(role_data) for role_data in data["roles"]]
    return CouncilConfig(
        topic=data["topic"],
        display_topic=data.get("display_topic", data["topic"]),
        question=data["question"],
        display_question=data.get("display_question", data["question"]),
        roles=roles,
        meeting_template_id=_meeting_template_id(data),
        meeting_template_name=_meeting_template_name(data),
        rounds=_rounds_from_dict(data, {role.id for role in roles}),
        meeting_mode=normalize_meeting_mode(data.get("meeting_mode")),
        moderator=_moderator_from_dict(data.get("moderator")),
    )


def _role_from_dict(data: dict[str, Any]) -> Role:
    return Role(
        id=data["id"],
        display_name=data["display_name"],
        lens=data["lens"],
        research_focus=data["research_focus"],
        personality=data.get("personality"),
        source_preferences=data.get("source_preferences"),
    )


def _meeting_template_id(data: dict[str, Any]) -> str:
    template = data.get("meeting_template") or {}
    return template.get("id", DEMO_MEETING_TEMPLATE["id"])


def _meeting_template_name(data: dict[str, Any]) -> str:
    template = data.get("meeting_template") or {}
    return template.get("display_name", DEMO_MEETING_TEMPLATE["display_name"])


def _rounds_from_dict(data: dict[str, Any], valid_role_ids: set[str]) -> list[MeetingRound]:
    template = data.get("meeting_template") or {}
    round_data = template.get("rounds")
    if not round_data:
        return list(DEMO_MEETING_TEMPLATE["rounds"])
    return [
        MeetingRound(
            id=round_definition["id"],
            title=round_definition.get("title", round_definition["id"]),
            report_label=round_definition.get("report_label", round_definition.get("title", round_definition["id"])),
            context_scope=round_definition.get("context_scope", "public_debate"),
            instruction=round_definition["instruction"],
            turn_control=_turn_control_from_dict(round_definition.get("turn_control") or {}, valid_role_ids),
        )
        for round_definition in round_data
    ]


def _turn_control_from_dict(data: dict[str, Any], valid_role_ids: set[str]) -> RoundTurnControl:
    selection = data.get("selection", "all_roles")
    if selection not in {"all_roles", "selected_roles"}:
        selection = "all_roles"
    speaker_role_ids = [role_id for role_id in data.get("speaker_role_ids", []) if isinstance(role_id, str)]
    unknown = [role_id for role_id in speaker_role_ids if role_id not in valid_role_ids]
    if unknown:
        raise ValueError(f"Unknown turn_control speaker role: {', '.join(unknown)}")
    return RoundTurnControl(
        selection=selection,
        speaker_role_ids=speaker_role_ids,
        non_speaker_mode=normalize_engagement_mode(data.get("non_speaker_mode"), default="watch"),
        moderator_instruction=data.get("moderator_instruction") if isinstance(data.get("moderator_instruction"), str) else None,
    )


def _moderator_from_dict(data: Any) -> ModeratorConfig:
    if not isinstance(data, dict):
        return ModeratorConfig()
    return ModeratorConfig(enabled=bool(data.get("enabled", True)))


def load_agent_runtime_config(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Agent runtime config must be a JSON object.")
    return data


def providers_from_config(data: dict[str, Any]) -> dict[str, ProviderConfig]:
    providers = {}
    for provider_data in data.get("providers", []):
        provider = ProviderConfig(
            id=provider_data["id"],
            kind=provider_data["kind"],
            display_name=provider_data.get("display_name", provider_data["id"]),
            default_model=provider_data.get("default_model"),
            endpoint=provider_data.get("endpoint"),
            auth_ref=provider_data.get("auth_ref"),
            timeout_seconds=provider_data.get("timeout_seconds"),
            search_enabled=bool(provider_data.get("search_enabled", False)),
            notes=provider_data.get("notes"),
            command=_command_from_config(provider_data.get("command")),
        )
        providers[provider.id] = provider
    return providers


def _command_from_config(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError("Provider command must be a string or a list of strings.")


def permissions_from_config(data: dict[str, Any]) -> dict[str, PermissionProfile]:
    permissions = {}
    for permission_data in data.get("permission_profiles", []):
        permission = PermissionProfile(
            id=permission_data["id"],
            meeting_read=permission_data.get("meeting_read", True),
            lobby_chat=permission_data.get("lobby_chat", True),
            official_turn=permission_data.get("official_turn", True),
            web_search=permission_data.get("web_search", False),
            tool_use=permission_data.get("tool_use", False),
            filesystem_read=permission_data.get("filesystem_read", False),
            filesystem_write=permission_data.get("filesystem_write", False),
            git_write=permission_data.get("git_write", False),
            push=permission_data.get("push", False),
            secrets=permission_data.get("secrets", False),
            implementation=permission_data.get("implementation", False),
        )
        permissions[permission.id] = permission
    return permissions


def agent_bindings_from_config(data: dict[str, Any]) -> list[AgentBinding]:
    bindings = []
    for binding_data in data.get("agent_bindings", []):
        raw_mode = binding_data.get("engagement_mode")
        engagement_mode = (
            normalize_engagement_mode(raw_mode, default="manual")
            if raw_mode is not None
            else "moderator_called"
        )
        persona_card_id = clean_persona_card_id(binding_data.get("persona_card_id") or binding_data.get("persona_id"))
        bindings.append(
            AgentBinding(
                agent_id=binding_data["agent_id"],
                role_id=binding_data["role_id"],
                owner_id=binding_data.get("owner_id", "local-user"),
                provider_id=binding_data["provider_id"],
                model_id=binding_data.get("model_id"),
                permission_profile_id=binding_data["permission_profile_id"],
                memory_profile_id=binding_data.get("memory_profile_id"),
                join_mode=binding_data.get("join_mode", "fresh"),
                engagement_mode=engagement_mode,
                session_id=binding_data.get("session_id") if isinstance(binding_data.get("session_id"), str) else None,
                persona_card_id=persona_card_id,
                character_mode=normalize_character_mode(binding_data.get("character_mode"), has_card=bool(persona_card_id)),
                first_message_index=clean_first_message_index(binding_data.get("first_message_index")),
                persona_variables=clean_persona_variables(binding_data.get("persona_variables")),
            )
        )
    return bindings
