from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from agentsassemble.legacy.live_agent.runtime.timing import DEFAULT_LIVE_AGENT_POLL_INTERVAL


DEFAULT_INVITE_CONFIG_PATH = Path(".agentsassemble") / "codex-live-session.local.json"
DEFAULT_LIVE_AGENT_CONFIG_PATH = Path(".agentsassemble") / "live-agents.codex-session.local.json"
CODEX_LIVE_PROVIDER_ID = "codex-live"
CODEX_LIVE_PERMISSION_ID = "codex_live_meeting_readonly"
CODEX_LIVE_MODEL_ID = "local-codex-session"

CODEX_LIVE_PROVIDER = {
    "id": CODEX_LIVE_PROVIDER_ID,
    "kind": "codex_live_session",
    "display_name": "Codex CLI Live Session",
    "default_model": CODEX_LIVE_MODEL_ID,
    "timeout_seconds": 240,
    "search_enabled": True,
}

CODEX_LIVE_PERMISSION = {
    "id": CODEX_LIVE_PERMISSION_ID,
    "meeting_read": True,
    "lobby_chat": True,
    "official_turn": True,
    "web_search": True,
    "tool_use": False,
    "filesystem_read": False,
    "filesystem_write": False,
    "git_write": False,
    "push": False,
    "secrets": False,
    "implementation": False,
}


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def list_codex_sessions(
    *,
    index_path: Path | str | None = None,
    limit: int | None = 20,
) -> list[dict[str, str]]:
    path = Path(index_path) if index_path is not None else codex_home() / "session_index.jsonl"
    if not path.exists():
        return []

    sessions: list[tuple[int, dict[str, str]]] = []
    for ordinal, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        session_id = record.get("id")
        if not isinstance(session_id, str) or not session_id.strip():
            continue
        sessions.append(
            (
                ordinal,
                {
                    "id": session_id.strip(),
                    "thread_name": _string_field(record.get("thread_name")),
                    "updated_at": _string_field(record.get("updated_at")),
                },
            )
        )

    sessions.sort(key=lambda item: (item[1]["updated_at"], item[0]), reverse=True)
    result = [session for _, session in sessions]
    if limit is None:
        return result
    return result[: max(limit, 0)]


def build_codex_live_invite_config(
    *,
    session_id: str,
    role_id: str,
    role_ids: list[str],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_session_id = session_id.strip()
    if not clean_session_id:
        raise ValueError("Session id is required.")
    if role_id not in role_ids:
        raise ValueError(f"Unknown role for Codex live invite: {role_id}")

    config = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    config.setdefault("incoming_agents", [])
    config["providers"] = _upsert_by_id(
        _dict_list(config.get("providers")),
        CODEX_LIVE_PROVIDER,
        force_defaults=True,
    )
    config["permission_profiles"] = _upsert_by_id(
        _dict_list(config.get("permission_profiles")),
        CODEX_LIVE_PERMISSION,
        force_defaults=True,
    )

    existing_bindings = _dict_list(config.get("agent_bindings"))
    bindings_by_role = {
        str(binding["role_id"]): copy.deepcopy(binding)
        for binding in existing_bindings
        if isinstance(binding.get("role_id"), str)
    }
    for default_role_id in role_ids:
        bindings_by_role.setdefault(default_role_id, _fresh_codex_live_binding(default_role_id))

    for binding in bindings_by_role.values():
        if binding.get("role_id") == role_id:
            continue
        if binding.get("session_id") == clean_session_id:
            raise ValueError(f"Session id {clean_session_id} is already invited for role {binding.get('role_id')}.")

    target = copy.deepcopy(bindings_by_role[role_id])
    target.update(
        {
            "agent_id": target.get("agent_id") or _default_agent_id(role_id),
            "role_id": role_id,
            "owner_id": target.get("owner_id") or "host",
            "provider_id": CODEX_LIVE_PROVIDER_ID,
            "model_id": target.get("model_id") or CODEX_LIVE_MODEL_ID,
            "permission_profile_id": CODEX_LIVE_PERMISSION_ID,
            "join_mode": "current_session",
            "session_id": clean_session_id,
        }
    )
    bindings_by_role[role_id] = target

    ordered_role_ids: list[str] = []
    for binding in existing_bindings:
        existing_role_id = binding.get("role_id")
        if isinstance(existing_role_id, str) and existing_role_id not in ordered_role_ids:
            ordered_role_ids.append(existing_role_id)
    for default_role_id in role_ids:
        if default_role_id not in ordered_role_ids:
            ordered_role_ids.append(default_role_id)
    config["agent_bindings"] = [bindings_by_role[ordered_role_id] for ordered_role_id in ordered_role_ids]
    return config


def build_codex_live_agent_config(
    invite_config: dict[str, Any],
    *,
    server: str,
    meeting_id: str = "",
    engagement_mode: str = "moderator_called",
) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    for binding in _dict_list(invite_config.get("agent_bindings")):
        if binding.get("provider_id") != CODEX_LIVE_PROVIDER_ID:
            continue
        agent_id = _string_field(binding.get("agent_id")).strip()
        if not agent_id:
            raise ValueError("Codex live binding requires agent_id.")
        agents.append(
            {
                "agent_id": agent_id,
                "display_name": _string_field(binding.get("display_name")).strip() or agent_id,
                "provider_kind": "codex_live_session",
                "connection_kind": "live_session",
                "session_id": _string_field(binding.get("session_id")).strip(),
                "meeting_id": meeting_id,
                "engagement_mode": engagement_mode,
                "timeout_seconds": int(CODEX_LIVE_PROVIDER["timeout_seconds"]),
            }
        )
    if not agents:
        raise ValueError("No Codex live bindings found.")
    return {
        "server": server,
        "poll_interval": DEFAULT_LIVE_AGENT_POLL_INTERVAL,
        "heartbeat_interval": 30,
        "cooldown": 5,
        "max_chain_depth": 1,
        "agents": agents,
    }


def read_agent_config(path: Path | str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Agent runtime config must be a JSON object.")
    return data


def write_agent_config(path: Path | str, config: dict[str, Any]) -> Path:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def _fresh_codex_live_binding(role_id: str) -> dict[str, Any]:
    return {
        "agent_id": _default_agent_id(role_id),
        "role_id": role_id,
        "owner_id": "host",
        "provider_id": CODEX_LIVE_PROVIDER_ID,
        "model_id": CODEX_LIVE_MODEL_ID,
        "permission_profile_id": CODEX_LIVE_PERMISSION_ID,
        "join_mode": "fresh",
    }


def _default_agent_id(role_id: str) -> str:
    return f"codex-live-{role_id.replace('_', '-')}"


def _upsert_by_id(items: list[dict[str, Any]], defaults: dict[str, Any], *, force_defaults: bool) -> list[dict[str, Any]]:
    target_id = defaults["id"]
    updated: list[dict[str, Any]] = []
    found = False
    for item in items:
        if item.get("id") == target_id:
            found = True
            updated.append({**item, **defaults} if force_defaults else {**defaults, **item})
        else:
            updated.append(item)
    if not found:
        updated.append(copy.deepcopy(defaults))
    return updated


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [copy.deepcopy(item) for item in value if isinstance(item, dict)]


def _string_field(value: Any) -> str:
    return value if isinstance(value, str) else ""
