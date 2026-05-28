from __future__ import annotations

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.sandbox_launcher import sandbox_launcher_for, safe_sandbox_enforcement


LIVE_AGENT_JOIN_SEMANTICS = {
    "manual_room_loop",
    "unsupported_evidence",
    "stateless_prompt_call",
    "terminal_pty_prompt_bridge",
    "remote_bridge_room_loop",
    "self_service_room_loop",
    "codex_exec_resume",
    "kiro_chat_resume",
    "antigravity_conversation_resume",
    "cursor_chat_resume",
    "grok_session_resume",
    "hermes_chat_resume",
    "jsonl_live_session",
    "unknown",
}
LIVE_AGENT_CONTEXT_DURABILITY = {
    "external_owner_managed",
    "not_proven",
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
    sandbox_enforcement = sandbox_launcher_for(provider, connection).enforcement
    if not provider and not connection:
        return {
            "join_semantics": "manual_room_loop",
            "context_durability": "external_owner_managed",
            "sandbox_enforcement": sandbox_enforcement,
        }
    if connection == "local_cli":
        return {
            "join_semantics": "stateless_prompt_call",
            "context_durability": "stateless_prompt",
            "sandbox_enforcement": sandbox_enforcement,
        }
    if connection == "terminal_session":
        return {
            "join_semantics": "terminal_pty_prompt_bridge",
            "context_durability": "process_lifetime",
            "sandbox_enforcement": sandbox_enforcement,
        }
    if connection == "remote_bridge":
        return {
            "join_semantics": "remote_bridge_room_loop",
            "context_durability": "remote_owner_managed",
            "sandbox_enforcement": sandbox_enforcement,
        }
    if connection == "self_service":
        return {
            "join_semantics": "self_service_room_loop",
            "context_durability": "provider_managed_room_loop",
            "sandbox_enforcement": sandbox_enforcement,
        }
    if connection == "codex_resume":
        return {
            "join_semantics": "codex_exec_resume",
            "context_durability": "provider_managed_resume",
            "sandbox_enforcement": sandbox_enforcement,
        }
    if connection == "live_session":
        if provider == "codex_live_session":
            return {
                "join_semantics": "codex_exec_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            }
        if provider == "kiro_live_session":
            return {
                "join_semantics": "kiro_chat_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            }
        if provider == "antigravity_live_session":
            return {
                "join_semantics": "antigravity_conversation_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            }
        if provider == "cursor_live_session":
            return {
                "join_semantics": "cursor_chat_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            }
        if provider == "grok_live_session":
            return {
                "join_semantics": "grok_session_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            }
        if provider == "hermes_live_session":
            return {
                "join_semantics": "hermes_chat_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            }
        return {
            "join_semantics": "jsonl_live_session",
            "context_durability": "process_lifetime",
            "sandbox_enforcement": sandbox_enforcement,
        }
    if connection == "manual":
        return {
            "join_semantics": "manual_room_loop",
            "context_durability": "external_owner_managed",
            "sandbox_enforcement": sandbox_enforcement,
        }
    return {"join_semantics": "unknown", "context_durability": "unknown", "sandbox_enforcement": "unknown"}


def safe_live_agent_join_semantics(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in LIVE_AGENT_JOIN_SEMANTICS else ""


def safe_live_agent_context_durability(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in LIVE_AGENT_CONTEXT_DURABILITY else ""


def safe_live_agent_sandbox_enforcement(value: object) -> str:
    return safe_sandbox_enforcement(value)
