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
CALL_EXECUTION_JOIN_SEMANTICS = {
    "codex_exec_resume",
    "kiro_chat_resume",
    "antigravity_conversation_resume",
    "cursor_chat_resume",
    "grok_session_resume",
    "hermes_chat_resume",
    "stateless_prompt_call",
}
PERSISTENT_EXECUTION_JOIN_SEMANTICS = {
    "terminal_pty_prompt_bridge",
    "self_service_room_loop",
    "remote_bridge_room_loop",
    "jsonl_live_session",
}


def live_agent_context_contract(provider_kind: object, connection_kind: object) -> dict[str, str]:
    provider = clean_lobby_text(provider_kind, limit=64)
    connection = clean_lobby_text(connection_kind, limit=64)
    sandbox_enforcement = sandbox_launcher_for(provider, connection).enforcement
    if not provider and not connection:
        return _with_execution_contract({
            "join_semantics": "manual_room_loop",
            "context_durability": "external_owner_managed",
            "sandbox_enforcement": sandbox_enforcement,
        })
    if connection == "local_cli":
        return _with_execution_contract({
            "join_semantics": "stateless_prompt_call",
            "context_durability": "stateless_prompt",
            "sandbox_enforcement": sandbox_enforcement,
        })
    if connection == "terminal_session":
        return _with_execution_contract({
            "join_semantics": "terminal_pty_prompt_bridge",
            "context_durability": "process_lifetime",
            "sandbox_enforcement": sandbox_enforcement,
        })
    if connection == "remote_bridge":
        return _with_execution_contract({
            "join_semantics": "remote_bridge_room_loop",
            "context_durability": "remote_owner_managed",
            "sandbox_enforcement": sandbox_enforcement,
        })
    if connection == "self_service":
        return _with_execution_contract({
            "join_semantics": "self_service_room_loop",
            "context_durability": "provider_managed_room_loop",
            "sandbox_enforcement": sandbox_enforcement,
        })
    if connection == "native_remote_room_client":
        return _with_execution_contract({
            "join_semantics": "native_remote_room_loop",
            "context_durability": "remote_owner_managed",
            "sandbox_enforcement": sandbox_enforcement,
        })
    if connection == "codex_resume":
        return _with_execution_contract({
            "join_semantics": "codex_exec_resume",
            "context_durability": "provider_managed_resume",
            "sandbox_enforcement": sandbox_enforcement,
        })
    if connection == "live_session":
        if provider == "codex_live_session":
            return _with_execution_contract({
                "join_semantics": "codex_exec_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            })
        if provider == "kiro_live_session":
            return _with_execution_contract({
                "join_semantics": "kiro_chat_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            })
        if provider == "antigravity_live_session":
            return _with_execution_contract({
                "join_semantics": "antigravity_conversation_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            })
        if provider == "cursor_live_session":
            return _with_execution_contract({
                "join_semantics": "cursor_chat_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            })
        if provider == "grok_live_session":
            return _with_execution_contract({
                "join_semantics": "grok_session_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            })
        if provider == "hermes_live_session":
            return _with_execution_contract({
                "join_semantics": "hermes_chat_resume",
                "context_durability": "provider_managed_resume",
                "sandbox_enforcement": sandbox_enforcement,
            })
        return _with_execution_contract({
            "join_semantics": "jsonl_live_session",
            "context_durability": "process_lifetime",
            "sandbox_enforcement": sandbox_enforcement,
        })
    if connection == "manual":
        return _with_execution_contract({
            "join_semantics": "manual_room_loop",
            "context_durability": "external_owner_managed",
            "sandbox_enforcement": sandbox_enforcement,
        })
    return _with_execution_contract({
        "join_semantics": "unknown",
        "context_durability": "unknown",
        "sandbox_enforcement": "unknown",
    })


def live_agent_execution_contract(join_semantics: object) -> dict[str, str]:
    join = clean_lobby_text(join_semantics, limit=64)
    if join in CALL_EXECUTION_JOIN_SEMANTICS:
        return {
            "execution_mode": "call",
            "runner_residency": "resident_polling_runner",
            "provider_residency": "per_turn_exec_resume",
            "execution_summary": "Runner stays alive, but the provider is called per turn through exec/resume.",
        }
    if join in PERSISTENT_EXECUTION_JOIN_SEMANTICS:
        return {
            "execution_mode": "persistent",
            "runner_residency": "resident_process",
            "provider_residency": "persistent_provider_channel",
            "execution_summary": "Provider process, PTY, stream, or room loop stays attached while the agent is running.",
        }
    if join == "manual_room_loop":
        return {
            "execution_mode": "manual",
            "runner_residency": "manual",
            "provider_residency": "external_or_human",
            "execution_summary": "Manual participant; no provider execution is controlled by AgentsAssemble.",
        }
    return {
        "execution_mode": "unknown",
        "runner_residency": "unknown",
        "provider_residency": "unknown",
        "execution_summary": "Execution style is not proven.",
    }


def _with_execution_contract(contract: dict[str, str]) -> dict[str, str]:
    return {**contract, **live_agent_execution_contract(contract.get("join_semantics"))}


def safe_live_agent_join_semantics(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in LIVE_AGENT_JOIN_SEMANTICS else ""


def safe_live_agent_context_durability(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in LIVE_AGENT_CONTEXT_DURABILITY else ""


def safe_live_agent_sandbox_enforcement(value: object) -> str:
    return safe_sandbox_enforcement(value)
