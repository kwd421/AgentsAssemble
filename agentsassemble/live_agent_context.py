from __future__ import annotations

from agentsassemble.meeting_events import clean_lobby_text


LIVE_AGENT_JOIN_SEMANTICS = {
    "manual_room_loop",
    "stateless_prompt_call",
    "terminal_pty_prompt_bridge",
    "remote_bridge_room_loop",
    "self_service_room_loop",
    "codex_exec_resume",
    "jsonl_live_session",
    "unknown",
}
LIVE_AGENT_CONTEXT_DURABILITY = {
    "external_owner_managed",
    "stateless_prompt",
    "process_lifetime",
    "remote_owner_managed",
    "provider_managed_room_loop",
    "provider_managed_resume",
    "unknown",
}


def live_agent_context_contract(provider_kind: object, connection_kind: object) -> dict[str, str]:
    provider = clean_lobby_text(provider_kind, limit=64)
    connection = clean_lobby_text(connection_kind, limit=64)
    if connection == "local_cli":
        return {"join_semantics": "stateless_prompt_call", "context_durability": "stateless_prompt"}
    if connection == "terminal_session":
        return {"join_semantics": "terminal_pty_prompt_bridge", "context_durability": "process_lifetime"}
    if connection == "remote_bridge":
        return {"join_semantics": "remote_bridge_room_loop", "context_durability": "remote_owner_managed"}
    if connection == "self_service":
        return {"join_semantics": "self_service_room_loop", "context_durability": "provider_managed_room_loop"}
    if connection == "codex_resume":
        return {"join_semantics": "codex_exec_resume", "context_durability": "provider_managed_resume"}
    if connection == "live_session":
        if provider == "codex_live_session":
            return {"join_semantics": "codex_exec_resume", "context_durability": "provider_managed_resume"}
        return {"join_semantics": "jsonl_live_session", "context_durability": "process_lifetime"}
    if connection == "manual":
        return {"join_semantics": "manual_room_loop", "context_durability": "external_owner_managed"}
    return {"join_semantics": "unknown", "context_durability": "unknown"}


def safe_live_agent_join_semantics(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in LIVE_AGENT_JOIN_SEMANTICS else ""


def safe_live_agent_context_durability(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in LIVE_AGENT_CONTEXT_DURABILITY else ""
