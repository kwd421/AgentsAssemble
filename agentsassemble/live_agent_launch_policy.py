from __future__ import annotations

from pathlib import Path

from agentsassemble.live_agent_runner import ResidentAgentConfig, load_group_configs
from agentsassemble.meeting_events import clean_lobby_text


APPROVAL_REQUIRED_MESSAGE = "Live-agent session run requires current operator approval before relaunching real providers."

_REAL_PROVIDER_KINDS = {
    "antigravity_cli",
    "claude_code",
    "codex_live_session",
    "cursor",
    "gemini_cli_legacy",
    "grok_build_cli",
    "hermes_cli",
    "openclaw_cli",
    "remote_http_bridge",
}
_REAL_PROVIDER_MARKERS = (
    "antigravity",
    "bridge",
    "claude",
    "codex",
    "cursor",
    "gemini",
    "grok",
    "hermes",
    "openclaw",
    "remote",
)
_APPROVAL_REQUIRED_CONNECTION_KINDS = {"remote_bridge", "terminal_session"}


class LiveAgentLaunchApprovalRequired(ValueError):
    pass


def resident_launch_approval_report(
    config_path: str | Path | None,
    *,
    request: dict[str, object] | None = None,
    server: str = "",
    approved: bool = False,
) -> dict[str, object]:
    """Classify whether a durable resident launch/reconcile needs current approval."""
    safe_request = request if isinstance(request, dict) else {}
    if _truthy_request_flag(safe_request.get("diagnostic")):
        return _approval_report("ok", [], approval_required=False, approved=False, reason="diagnostic")

    clean_config_path = str(config_path or "").strip()
    if not clean_config_path:
        return _approval_report("ok", [], approval_required=False, approved=False, reason="no_config")

    path = Path(clean_config_path)
    if not path.exists():
        return _approval_report("ok", [], approval_required=False, approved=False, reason="config_unavailable")

    configs = load_group_configs(path, server_override=server or None)
    agents = [_agent_approval_entry(config) for config in configs]
    required_count = sum(1 for agent in agents if agent["approval_required"] is True)
    approval_required = required_count > 0
    if approval_required and not approved:
        return _approval_report(
            "approval_required",
            agents,
            approval_required=True,
            approved=False,
            reason="current_operator_approval_required",
        )
    reason = "current_operator_approved" if approval_required and approved else "credential_free"
    return _approval_report(
        "ok",
        agents,
        approval_required=approval_required,
        approved=bool(approved and approval_required),
        reason=reason,
    )


def assert_resident_launch_approved(
    config_path: str | Path | None,
    *,
    request: dict[str, object] | None = None,
    server: str = "",
    approved: bool = False,
) -> dict[str, object]:
    report = resident_launch_approval_report(config_path, request=request, server=server, approved=approved)
    if report.get("status") == "approval_required":
        raise LiveAgentLaunchApprovalRequired(APPROVAL_REQUIRED_MESSAGE)
    return report


def _agent_approval_entry(config: ResidentAgentConfig) -> dict[str, object]:
    provider_kind = _safe_kind(config.provider_kind, default="local_cli")
    connection_kind = _safe_kind(config.connection_kind, default="local_cli")
    reason = _approval_reason(provider_kind, connection_kind)
    return {
        "agent_id": clean_lobby_text(config.agent_id, limit=128),
        "provider_kind": provider_kind,
        "connection_kind": connection_kind,
        "approval_required": bool(reason),
        "reason": reason or "credential_free",
    }


def _approval_reason(provider_kind: str, connection_kind: str) -> str:
    if connection_kind in _APPROVAL_REQUIRED_CONNECTION_KINDS:
        return connection_kind
    if provider_kind in _REAL_PROVIDER_KINDS:
        return "real_provider"
    if connection_kind == "self_service" and provider_kind not in {"local_cli", "jsonl"}:
        return "self_service_real_provider"
    if any(marker in provider_kind for marker in _REAL_PROVIDER_MARKERS):
        return "real_provider"
    return ""


def _approval_report(
    status: str,
    agents: list[dict[str, object]],
    *,
    approval_required: bool,
    approved: bool,
    reason: str,
) -> dict[str, object]:
    return {
        "status": status,
        "approval_required": approval_required,
        "approved": approved,
        "reason": reason,
        "agent_count": len(agents),
        "approval_required_count": sum(1 for agent in agents if agent.get("approval_required") is True),
        "agents": agents,
    }


def _safe_kind(value: object, *, default: str) -> str:
    text = clean_lobby_text(value, limit=128).casefold()
    return text or default


def _truthy_request_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return False
