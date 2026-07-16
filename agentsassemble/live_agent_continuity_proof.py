from __future__ import annotations

import secrets
import re
import string
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from agentsassemble.providers.cursor_resident import CursorResidentCommandRunner, cursor_error_category
from agentsassemble.providers.hermes_resident import HermesResidentCommandRunner, hermes_error_category
from agentsassemble.providers.kiro_resident import KiroResidentCommandRunner
from agentsassemble.live_agent_runner import ResidentAgentConfig
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.providers.antigravity_resident import (
    AntigravityResidentCommandRunner,
    antigravity_error_category,
)
from agentsassemble.providers.codex_resident import CodexResidentCommandRunner
from agentsassemble.providers.grok_resident import GrokResidentCommandRunner, grok_error_category


SUPPORTED_CONTINUITY_PROVIDER_KINDS = frozenset(
    {
        "codex_live_session",
        "kiro_live_session",
        "cursor_live_session",
        "grok_live_session",
        "antigravity_live_session",
        "hermes_live_session",
    }
)
FORMATTING_TOLERANT_CONTINUITY_PROVIDER_KINDS = frozenset(
    {"antigravity_live_session", "hermes_live_session"}
)
ISOLATED_CWD_CONTINUITY_PROVIDER_KINDS = frozenset(
    {"antigravity_live_session", "hermes_live_session"}
)
CONTINUITY_PROOF_LIMITATIONS = [
    "two_turn_provider_resume_recall_only",
    "does_not_prove_room_admission",
    "does_not_prove_tool_safety",
    "does_not_prove_stop_restart_or_official_turn_quality",
]
_READY_MARKER_MAX_LENGTH = 8
_READY_MARKER_TERMINAL_PUNCTUATION = frozenset(".!?。！？")


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
    normalized_config = replace(
        config,
        agent_id=agent_id,
        provider_kind=provider_kind,
        connection_kind=connection_kind,
    )
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
    setup_error = _continuity_structural_setup_error(normalized_config)
    if setup_error:
        result = _safe_setup_failed_result(normalized_config)
        result["approved"] = True
        return result
    code = (code_factory or _continuity_code)()
    suffix = code[-4:]
    first_prompt = _first_prompt(code)
    second_prompt = _second_prompt()
    runner = None
    try:
        with _continuity_proof_cwd(provider_kind, cwd) as proof_cwd:
            runner = _runner_for_config(normalized_config, command_runner=command_runner, cwd=proof_cwd)
            try:
                first_reply = runner([], first_prompt, timeout_seconds=max(1, int(normalized_config.timeout_seconds or 120)))
                second_reply = runner([], second_prompt, timeout_seconds=max(1, int(normalized_config.timeout_seconds or 120)))
            finally:
                _close_runner(runner)
    except Exception as error:
        result = {
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
        error_category = _continuity_error_category(error)
        if error_category:
            result["error_category"] = error_category
            result["error_message"] = clean_lobby_text(str(error), limit=240)
        return result

    first_reply_is_ready = first_reply.strip() == "READY"
    first_reply_ready_normalized = _first_reply_ready_normalized(first_reply)
    first_reply_ready_acknowledged = _first_reply_ready_acknowledged(provider_kind, first_reply)
    first_revealed_code = code in first_reply
    first_revealed_suffix = suffix in first_reply
    second_replayed_code = code in second_prompt
    expected_suffix_matched = second_reply.strip() == suffix
    recall_match_mode = _recall_match_mode(provider_kind, second_reply, suffix)
    expected_suffix_recalled = recall_match_mode in {"exact", "mentioned"}
    second_reply_revealed_code = code in second_reply
    session_id = str(getattr(runner, "session_id", "") or "")
    ok = (
        bool(session_id)
        and first_reply_ready_acknowledged
        and not first_revealed_code
        and not first_revealed_suffix
        and not second_replayed_code
        and not second_reply_revealed_code
        and expected_suffix_recalled
    )
    return {
        "status": "ok" if ok else "failed",
        "reason": "ok" if ok else _failure_reason(
            session_id_captured=bool(session_id),
            first_reply_ready_acknowledged=first_reply_ready_acknowledged,
            first_revealed_code=first_revealed_code,
            first_revealed_suffix=first_revealed_suffix,
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
        "first_reply_is_ready": first_reply_is_ready,
        "first_reply_ready_normalized": first_reply_ready_normalized,
        "first_reply_ready_acknowledged": first_reply_ready_acknowledged,
        "first_reply_revealed_code": first_revealed_code,
        "first_reply_revealed_suffix": first_revealed_suffix,
        "second_prompt_replayed_code": second_replayed_code,
        "expected_suffix_matched": expected_suffix_matched,
        "expected_suffix_recalled": expected_suffix_recalled,
        "recall_match_mode": recall_match_mode,
        "second_reply_revealed_code": second_reply_revealed_code,
        "limitations": CONTINUITY_PROOF_LIMITATIONS,
    }


def run_live_agent_continuity_proof_batch(
    configs: Iterable[ResidentAgentConfig],
    *,
    approve_real_providers: bool,
    command_runner_factory: Callable[[ResidentAgentConfig], Any] | None = None,
    setup_error_checker: Callable[[ResidentAgentConfig], str] | None = None,
    code_factory: Callable[[], str] | None = None,
    cwd: Path | None = None,
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for index, config in enumerate(configs):
        try:
            command_runner = None
            if approve_real_providers and _config_supports_continuity_proof(config):
                setup_error = _continuity_structural_setup_error(config)
                if not setup_error and setup_error_checker is not None:
                    setup_error = setup_error_checker(config)
                if setup_error:
                    item = _safe_setup_failed_result(config)
                    item["index"] = index
                    results.append(item)
                    continue
                command_runner = command_runner_factory(config) if command_runner_factory is not None else None
            item = run_live_agent_continuity_proof(
                config,
                approve_real_providers=approve_real_providers,
                command_runner=command_runner,
                code_factory=code_factory,
                cwd=cwd,
            )
        except Exception as error:
            item = _safe_batch_error(config, error)
        item["index"] = index
        results.append(item)
    counts = _batch_status_counts(results)
    return {
        "status": _batch_status(results, counts),
        "approval_required": bool(approve_real_providers is False and counts.get("approval_required", 0)),
        "approved": bool(approve_real_providers),
        "diagnostic": True,
        "method": "provider_resume_suffix_recall_batch",
        "total_count": len(results),
        "ok_count": counts.get("ok", 0),
        "failed_count": counts.get("failed", 0),
        "unsupported_count": counts.get("unsupported", 0),
        "approval_required_count": counts.get("approval_required", 0),
        "supported_provider_kinds": sorted(SUPPORTED_CONTINUITY_PROVIDER_KINDS),
        "limitations": CONTINUITY_PROOF_LIMITATIONS,
        "results": results,
    }


def fixed_continuity_code_factory(code: str) -> Callable[[], str]:
    return lambda: code


def _runner_for_config(config: ResidentAgentConfig, *, command_runner: Any | None, cwd: Path | None) -> Any:
    if config.provider_kind == "codex_live_session":
        return CodexResidentCommandRunner(config, command_runner=command_runner, cwd=cwd)
    if config.provider_kind == "kiro_live_session":
        return KiroResidentCommandRunner(config, command_runner=command_runner, cwd=cwd)
    if config.provider_kind == "cursor_live_session":
        return CursorResidentCommandRunner(config, command_runner=command_runner, cwd=cwd)
    if config.provider_kind == "grok_live_session":
        return GrokResidentCommandRunner(config, command_runner=command_runner, cwd=cwd)
    if config.provider_kind == "antigravity_live_session":
        return AntigravityResidentCommandRunner(config, command_runner=command_runner, cwd=cwd)
    if config.provider_kind == "hermes_live_session":
        return HermesResidentCommandRunner(config, command_runner=command_runner, cwd=cwd)
    raise ValueError(f"Provider does not support continuity proof: {config.provider_kind}")


def _continuity_error_category(error: Exception) -> str:
    for category_fn in (antigravity_error_category, hermes_error_category, grok_error_category, cursor_error_category):
        category = category_fn(error)
        if category:
            return category
    return ""


@contextmanager
def _continuity_proof_cwd(provider_kind: str, cwd: Path | None) -> Iterator[Path]:
    base_cwd = Path(cwd or Path.cwd())
    if provider_kind not in ISOLATED_CWD_CONTINUITY_PROVIDER_KINDS:
        yield base_cwd
        return
    with tempfile.TemporaryDirectory(prefix="agentsassemble-continuity-proof-") as temp_dir:
        proof_cwd = Path(temp_dir)
        _best_effort_git_init(proof_cwd)
        yield proof_cwd


def _best_effort_git_init(cwd: Path) -> None:
    try:
        subprocess.run(
            ["git", "init", "-q"],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _config_supports_continuity_proof(config: ResidentAgentConfig) -> bool:
    provider_kind = clean_lobby_text(config.provider_kind, limit=64)
    connection_kind = clean_lobby_text(config.connection_kind, limit=64)
    return provider_kind in SUPPORTED_CONTINUITY_PROVIDER_KINDS and connection_kind == "live_session"


def _continuity_structural_setup_error(config: ResidentAgentConfig) -> str:
    provider_kind = clean_lobby_text(config.provider_kind, limit=64)
    command = list(config.command or [])
    executable_name = Path(command[0]).name if command else ""
    if provider_kind == "codex_live_session":
        if len(command) != 1 or executable_name not in {"codex", "codex.exe"}:
            return "resident_setup_failed"
    if provider_kind == "kiro_live_session":
        if executable_name not in {"kiro", "kiro-cli", "kiro-cli-chat"}:
            return "resident_setup_failed"
    if provider_kind == "cursor_live_session":
        if len(command) != 1 or executable_name not in {"cursor-agent", "cursor-agent.exe"}:
            return "resident_setup_failed"
    if provider_kind == "grok_live_session":
        if len(command) != 1 or executable_name not in {"grok", "grok.exe"}:
            return "resident_setup_failed"
    if provider_kind == "antigravity_live_session":
        if len(command) != 1 or executable_name not in {"agy", "agy.exe", "antigravity", "antigravity.exe"}:
            return "resident_setup_failed"
    if provider_kind == "hermes_live_session":
        if len(command) != 1 or executable_name not in {"hermes", "hermes.exe"}:
            return "resident_setup_failed"
    return ""


def _close_runner(runner: Any) -> None:
    close = getattr(runner, "close", None)
    if close is not None:
        close()


def _continuity_code() -> str:
    suffix = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4))
    return f"AAPROOF-{secrets.token_hex(8).upper()}-{suffix}"


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


def _first_reply_ready_normalized(reply: str) -> bool:
    text = reply.strip()
    if not text or len(text) > _READY_MARKER_MAX_LENGTH:
        return False
    if text == "READY":
        return True
    return len(text) == len("READY.") and text.startswith("READY") and text[-1] in _READY_MARKER_TERMINAL_PUNCTUATION


def _first_reply_ready_acknowledged(provider_kind: str, reply: str) -> bool:
    if _first_reply_ready_normalized(reply):
        return True
    if provider_kind not in FORMATTING_TOLERANT_CONTINUITY_PROVIDER_KINDS:
        return False
    return bool(re.search(r"(?<![A-Za-z0-9])READY(?![A-Za-z0-9])", reply))


def _failure_reason(
    *,
    session_id_captured: bool,
    first_reply_ready_acknowledged: bool,
    first_revealed_code: bool,
    first_revealed_suffix: bool,
    second_replayed_code: bool,
    expected_suffix_matched: bool,
) -> str:
    if not session_id_captured:
        return "session_id_not_captured"
    if first_revealed_code:
        return "first_reply_revealed_code"
    if first_revealed_suffix:
        return "first_reply_revealed_suffix"
    if not first_reply_ready_acknowledged:
        return "first_reply_not_ready"
    if second_replayed_code:
        return "second_prompt_replayed_code"
    if not expected_suffix_matched:
        return "suffix_not_recalled"
    return "unknown"


def _recall_match_mode(provider_kind: str, reply: str, suffix: str) -> str:
    if reply.strip() == suffix:
        return "exact"
    if provider_kind in FORMATTING_TOLERANT_CONTINUITY_PROVIDER_KINDS and _reply_mentions_suffix(reply, suffix):
        return "mentioned"
    return "none"


def _reply_mentions_suffix(reply: str, suffix: str) -> bool:
    if not suffix:
        return False
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(suffix)}(?![A-Za-z0-9])")
    return bool(pattern.search(reply))


def _safe_batch_error(config: ResidentAgentConfig, error: Exception) -> dict[str, object]:
    return {
        "status": "failed",
        "reason": "batch_item_failed",
        "error_type": error.__class__.__name__,
        "agent_id": clean_lobby_text(config.agent_id, limit=128) or "continuity-proof",
        "provider_kind": clean_lobby_text(config.provider_kind, limit=64),
        "connection_kind": clean_lobby_text(config.connection_kind, limit=64),
        "approval_required": True,
        "approved": True,
        "diagnostic": True,
        "method": "provider_resume_suffix_recall",
        "limitations": CONTINUITY_PROOF_LIMITATIONS,
    }


def _safe_setup_failed_result(config: ResidentAgentConfig) -> dict[str, object]:
    return {
        "status": "failed",
        "reason": "resident_setup_failed",
        "agent_id": clean_lobby_text(config.agent_id, limit=128) or "continuity-proof",
        "provider_kind": clean_lobby_text(config.provider_kind, limit=64),
        "connection_kind": clean_lobby_text(config.connection_kind, limit=64),
        "approval_required": True,
        "approved": True,
        "diagnostic": True,
        "method": "provider_resume_suffix_recall",
        "limitations": CONTINUITY_PROOF_LIMITATIONS,
    }


def _batch_status_counts(results: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        status = clean_lobby_text(result.get("status"), limit=64) or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _batch_status(results: list[dict[str, object]], counts: dict[str, int]) -> str:
    if not results:
        return "empty"
    if counts.get("failed", 0):
        return "failed"
    if counts.get("approval_required", 0):
        return "approval_required"
    if counts.get("ok", 0) and counts.get("unsupported", 0):
        return "partial"
    if counts.get("ok", 0):
        return "ok"
    if counts.get("unsupported", 0) == len(results):
        return "unsupported"
    return "unknown"
