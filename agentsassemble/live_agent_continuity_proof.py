from __future__ import annotations

import secrets
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from agentsassemble.codex_resident import CodexResidentCommandRunner
from agentsassemble.kiro_resident import KiroResidentCommandRunner
from agentsassemble.live_agent_runner import ResidentAgentConfig
from agentsassemble.meeting_events import clean_lobby_text


SUPPORTED_CONTINUITY_PROVIDER_KINDS = frozenset({"codex_live_session", "kiro_live_session"})
CONTINUITY_PROOF_LIMITATIONS = [
    "two_turn_provider_resume_recall_only",
    "does_not_prove_room_admission",
    "does_not_prove_tool_safety",
    "does_not_prove_stop_restart_or_official_turn_quality",
]


def run_live_agent_continuity_proof(
    config: ResidentAgentConfig,
    *,
    approve_real_providers: bool,
    command_runner: Any | None = None,
    code_factory: Callable[[], str] | None = None,
    cwd: Path | None = None,
) -> dict[str, object]:
    provider_kind = clean_lobby_text(config.provider_kind, limit=64)
    connection_kind = clean_lobby_text(config.connection_kind, limit=64)
    agent_id = clean_lobby_text(config.agent_id, limit=128) or "continuity-proof"
    if provider_kind not in SUPPORTED_CONTINUITY_PROVIDER_KINDS or connection_kind != "live_session":
        return {
            "status": "unsupported",
            "reason": "provider_resume_not_supported",
            "agent_id": agent_id,
            "provider_kind": provider_kind,
            "connection_kind": connection_kind,
            "approval_required": False,
            "approved": False,
            "diagnostic": True,
            "limitations": CONTINUITY_PROOF_LIMITATIONS,
        }
    if not approve_real_providers:
        return {
            "status": "approval_required",
            "reason": "current_operator_approval_required",
            "agent_id": agent_id,
            "provider_kind": provider_kind,
            "connection_kind": connection_kind,
            "approval_required": True,
            "approved": False,
            "diagnostic": True,
            "limitations": CONTINUITY_PROOF_LIMITATIONS,
        }
    code = (code_factory or _continuity_code)()
    suffix = code[-4:]
    first_prompt = _first_prompt(code)
    second_prompt = _second_prompt()
    runner = None
    normalized_config = replace(
        config,
        agent_id=agent_id,
        provider_kind=provider_kind,
        connection_kind=connection_kind,
    )
    try:
        runner = _runner_for_config(normalized_config, command_runner=command_runner, cwd=cwd)
        first_reply = runner([], first_prompt, timeout_seconds=max(1, int(normalized_config.timeout_seconds or 120)))
        second_reply = runner([], second_prompt, timeout_seconds=max(1, int(normalized_config.timeout_seconds or 120)))
    except Exception as error:
        return {
            "status": "failed",
            "reason": "provider_call_failed",
            "error_type": error.__class__.__name__,
            "agent_id": agent_id,
            "provider_kind": provider_kind,
            "connection_kind": connection_kind,
            "approval_required": True,
            "approved": True,
            "diagnostic": True,
            "method": "provider_resume_suffix_recall",
            "session_id_captured": bool(getattr(runner, "session_id", "") if runner is not None else ""),
            "session_id_suffix": _safe_session_suffix(getattr(runner, "session_id", "") if runner is not None else ""),
            "limitations": CONTINUITY_PROOF_LIMITATIONS,
        }
    finally:
        if runner is not None:
            _close_runner(runner)

    first_revealed_code = code in first_reply
    second_replayed_code = code in second_prompt
    expected_suffix_matched = second_reply.strip() == suffix
    session_id = str(getattr(runner, "session_id", "") or "")
    ok = bool(session_id) and not first_revealed_code and not second_replayed_code and expected_suffix_matched
    return {
        "status": "ok" if ok else "failed",
        "reason": "ok" if ok else _failure_reason(
            session_id_captured=bool(session_id),
            first_revealed_code=first_revealed_code,
            second_replayed_code=second_replayed_code,
            expected_suffix_matched=expected_suffix_matched,
        ),
        "agent_id": agent_id,
        "provider_kind": provider_kind,
        "connection_kind": connection_kind,
        "approval_required": True,
        "approved": True,
        "diagnostic": True,
        "method": "provider_resume_suffix_recall",
        "session_id_captured": bool(session_id),
        "session_id_suffix": _safe_session_suffix(session_id),
        "first_reply_length": len(first_reply),
        "second_reply_length": len(second_reply),
        "first_reply_revealed_code": first_revealed_code,
        "second_prompt_replayed_code": second_replayed_code,
        "expected_suffix_matched": expected_suffix_matched,
        "limitations": CONTINUITY_PROOF_LIMITATIONS,
    }


def fixed_continuity_code_factory(code: str) -> Callable[[], str]:
    return lambda: code


def _runner_for_config(config: ResidentAgentConfig, *, command_runner: Any | None, cwd: Path | None) -> Any:
    if config.provider_kind == "codex_live_session":
        return CodexResidentCommandRunner(config, command_runner=command_runner, cwd=cwd)
    if config.provider_kind == "kiro_live_session":
        return KiroResidentCommandRunner(config, command_runner=command_runner, cwd=cwd)
    raise ValueError(f"Provider does not support continuity proof: {config.provider_kind}")


def _close_runner(runner: Any) -> None:
    close = getattr(runner, "close", None)
    if close is not None:
        close()


def _continuity_code() -> str:
    return f"AAPROOF-{secrets.token_hex(8).upper()}"


def _first_prompt(code: str) -> str:
    return (
        "AgentsAssemble provider-owned continuity proof. "
        f"Store this continuity code for the next turn: {code}. "
        "Do not include the code in this reply. "
        "If the next turn asks for the suffix, you are allowed to answer with only the last 4 characters. "
        "Reply exactly READY."
    )


def _second_prompt() -> str:
    return (
        "Using only the continuity code already stored in this provider session, "
        "reply with exactly its last 4 characters. No other text."
    )


def _safe_session_suffix(session_id: object) -> str:
    text = clean_lobby_text(session_id, limit=128)
    return text[-6:] if text else ""


def _failure_reason(
    *,
    session_id_captured: bool,
    first_revealed_code: bool,
    second_replayed_code: bool,
    expected_suffix_matched: bool,
) -> str:
    if not session_id_captured:
        return "session_id_not_captured"
    if first_revealed_code:
        return "first_reply_revealed_code"
    if second_replayed_code:
        return "second_prompt_replayed_code"
    if not expected_suffix_matched:
        return "suffix_not_recalled"
    return "unknown"
