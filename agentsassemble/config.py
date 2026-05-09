from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentsassemble.models import AgentBinding, CouncilConfig, PermissionProfile, ProviderConfig, Role


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
        )
        providers[provider.id] = provider
    return providers


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
    return [
        AgentBinding(
            agent_id=binding_data["agent_id"],
            role_id=binding_data["role_id"],
            owner_id=binding_data.get("owner_id", "local-user"),
            provider_id=binding_data["provider_id"],
            model_id=binding_data.get("model_id"),
            permission_profile_id=binding_data["permission_profile_id"],
            memory_profile_id=binding_data.get("memory_profile_id"),
            join_mode=binding_data.get("join_mode", "fresh"),
        )
        for binding_data in data.get("agent_bindings", [])
    ]
