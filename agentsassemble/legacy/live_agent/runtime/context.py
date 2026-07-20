from __future__ import annotations

from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.providers.sandbox_launcher import sandbox_launcher_for, safe_sandbox_enforcement
from agentsassemble.providers.live_session_adapter import (
    CALL_RESUME_JOIN_SEMANTICS,
    PROVIDER_PERSISTENT_JOIN_SEMANTICS,
    PROVIDER_TOOL_LOOP_JOIN_SEMANTICS,
    RUNTIME_MANAGED_ROOM_TURN_JOIN_SEMANTICS,
    UNVERIFIED_TOOL_LOOP_JOIN_SEMANTICS,
    live_session_runtime_contract,
)


LIVE_AGENT_JOIN_SEMANTICS = {
    "manual_room_loop",
    "unsupported_evidence",
    "stateless_prompt_call",
    "terminal_pty_prompt_bridge",
    "remote_bridge_room_loop",
    "self_service_room_loop",
    "native_remote_room_loop",
    "runtime_managed_room_turn",
    "mcp_tool_loop",
    "cli_tool_loop",
    "provider_tool_loop",
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
CALL_EXECUTION_JOIN_SEMANTICS = CALL_RESUME_JOIN_SEMANTICS
PERSISTENT_EXECUTION_JOIN_SEMANTICS = PROVIDER_PERSISTENT_JOIN_SEMANTICS
TOOL_LOOP_EXECUTION_JOIN_SEMANTICS = PROVIDER_TOOL_LOOP_JOIN_SEMANTICS
UNVERIFIED_TOOL_LOOP_EXECUTION_JOIN_SEMANTICS = UNVERIFIED_TOOL_LOOP_JOIN_SEMANTICS
RUNTIME_MANAGED_EXECUTION_JOIN_SEMANTICS = RUNTIME_MANAGED_ROOM_TURN_JOIN_SEMANTICS


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
    if connection == "api_call":
        # API-provider lane (master plan 1단계 B): a per-turn OpenAI-compatible
        # model call. Same execution contract as local_cli (stateless per turn),
        # but the runner invokes the model API in-process instead of a subprocess.
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


def live_agent_execution_contract(join_semantics: object) -> dict[str, object]:
    runtime = live_session_runtime_contract(clean_lobby_text(join_semantics, limit=64))
    runtime_mode = str(runtime.get("runtime_mode") or "unknown")
    return {
        "execution_mode": "persistent" if runtime_mode == "provider_persistent" else runtime_mode,
        "runner_residency": str(runtime.get("runner_residency") or "unknown"),
        "provider_residency": str(runtime.get("provider_residency") or "unknown"),
        "provider_persistent": bool(runtime.get("provider_persistent")),
        "execution_summary": str(runtime.get("runtime_summary") or "Execution style is not proven."),
        "tool_loop_unverified_reason": str(runtime.get("tool_loop_unverified_reason") or ""),
    }


def _with_execution_contract(contract: dict[str, str]) -> dict[str, str]:
    return {**contract, **live_agent_execution_contract(contract.get("join_semantics"))}


def live_agent_context_contract_with_join_semantics(
    provider_kind: object,
    connection_kind: object,
    join_semantics: object,
) -> dict[str, object]:
    contract: dict[str, object] = dict(live_agent_context_contract(provider_kind, connection_kind))
    safe_join = safe_live_agent_join_semantics(join_semantics)
    if not safe_join or not _join_semantics_override_allowed(
        connection_kind,
        safe_join=safe_join,
        default_join=str(contract.get("join_semantics") or ""),
    ):
        return contract
    contract["join_semantics"] = safe_join
    contract.update(live_agent_execution_contract(safe_join))
    return contract


def safe_live_agent_join_semantics(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in LIVE_AGENT_JOIN_SEMANTICS else ""


def _join_semantics_override_allowed(connection_kind: object, *, safe_join: str, default_join: str) -> bool:
    if safe_join == default_join:
        return True
    connection = clean_lobby_text(connection_kind, limit=64)
    if safe_join == "runtime_managed_room_turn":
        return connection in {"codex_resume", "live_session"}
    if safe_join in {"mcp_tool_loop", "cli_tool_loop", "provider_tool_loop"}:
        return connection in {"manual", "self_service", "native_remote_room_client", "live_session"}
    return False


def safe_live_agent_context_durability(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in LIVE_AGENT_CONTEXT_DURABILITY else ""


def safe_live_agent_sandbox_enforcement(value: object) -> str:
    return safe_sandbox_enforcement(value)
