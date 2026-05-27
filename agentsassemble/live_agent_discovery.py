from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentsassemble.live_session_transport import terminal_sessions_supported


DEFAULT_DISCOVERY_SERVER = "http://127.0.0.1:8765"
TERMINAL_PROMPT_BRIDGE_CONTRACT = {
    "join_semantics": "terminal_pty_prompt_bridge",
    "context_durability": "process_lifetime",
    "sandbox_enforcement": "advisory",
    "evidence_basis": "path_and_pty_preflight",
}
CODEX_LIVE_SESSION_CONTRACT = {
    "join_semantics": "codex_exec_resume",
    "context_durability": "provider_managed_resume",
    "sandbox_enforcement": "codex_readonly",
    "evidence_basis": "path_and_codex_safety_preflight",
}
KIRO_LIVE_SESSION_CONTRACT = {
    "join_semantics": "kiro_chat_resume",
    "context_durability": "provider_managed_resume",
    "sandbox_enforcement": "advisory",
    "evidence_basis": "path_and_kiro_resume_preflight",
}
GROK_LIVE_SESSION_CONTRACT = {
    "join_semantics": "grok_session_resume",
    "context_durability": "provider_managed_resume",
    "sandbox_enforcement": "advisory",
    "evidence_basis": "path_and_grok_resume_preflight",
}
SELF_SERVICE_CONTRACT = {
    "join_semantics": "self_service_room_loop",
    "context_durability": "provider_managed_room_loop",
    "sandbox_enforcement": "advisory",
    "evidence_basis": "path_and_self_service_preflight",
}


def build_discovered_live_agent_config(
    *,
    server: str = DEFAULT_DISCOVERY_SERVER,
    meeting_id: str = "",
    engagement_mode: str = "mentioned",
    include_legacy_gemini: bool = False,
    command_resolver: Callable[[str], str | None] | None = None,
    terminal_session_supported: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    resolver = command_resolver or shutil.which
    terminal_supported = (terminal_session_supported or terminal_sessions_supported)()
    discoveries = []
    agents = []
    for spec in _candidate_specs():
        path = resolver(spec["command"])
        available = bool(path)
        supported = _candidate_supported(spec, terminal_supported=terminal_supported)
        legacy = bool(spec.get("legacy"))
        included = available and supported and (not legacy or include_legacy_gemini)
        reason = "included" if included else _discovery_skip_reason(available=available, supported=supported, legacy=legacy)
        entry_status = _entry_status(available=available, supported=supported, included=included, legacy=legacy)
        discoveries.append(
            {
                "command": spec["command"],
                "agent_id": spec["agent_id"],
                "provider_kind": spec["provider_kind"],
                "connection_kind": spec["connection_kind"],
                "entry_mode": _entry_mode(spec),
                "entry_status": entry_status,
                "join_semantics": spec["join_semantics"],
                "context_durability": spec["context_durability"],
                "sandbox_enforcement": spec["sandbox_enforcement"],
                "evidence_basis": spec["evidence_basis"],
                "operator_action": _operator_action(entry_status),
                "requires_approval": _requires_approval(entry_status),
                "safety_note": _safety_note(spec, entry_status),
                "available": available,
                "included": included,
                "reason": reason,
            }
        )
        if included:
            agents.append(_agent_entry(spec, meeting_id=meeting_id, engagement_mode=engagement_mode))
    config = {
        "server": server,
        "poll_interval": 2,
        "heartbeat_interval": 30,
        "cooldown": 5,
        "max_chain_depth": 1,
        "agents": agents,
    }
    return {
        "status": "ok" if agents else "empty",
        "config": config,
        "discoveries": discoveries,
        "next_commands": _next_commands(server=server),
    }


def apply_discovery_approval_filter(
    report: dict[str, Any],
    *,
    approved_agents: list[object],
    approved_commands: list[object],
) -> None:
    approved_agent_ids = {str(value or "").strip() for value in approved_agents if str(value or "").strip()}
    approved_command_names = {str(value or "").strip() for value in approved_commands if str(value or "").strip()}
    discoveries = report.get("discoveries") if isinstance(report.get("discoveries"), list) else []
    excluded_agent_ids: set[str] = set()
    matched_agent_ids: set[str] = set()
    matched_command_names: set[str] = set()
    approved_count = 0
    excluded_agents: list[str] = []
    excluded_commands: list[str] = []
    for item in discoveries:
        if not isinstance(item, dict) or not item.get("included") or not item.get("requires_approval"):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        command = str(item.get("command") or "").strip()
        approved = bool(agent_id and agent_id in approved_agent_ids) or bool(command and command in approved_command_names)
        if approved:
            item["approval_status"] = "approved"
            item["operator_action"] = "approved_auto_join"
            if agent_id and agent_id in approved_agent_ids:
                matched_agent_ids.add(agent_id)
            if command and command in approved_command_names:
                matched_command_names.add(command)
            approved_count += 1
            continue
        item["approval_status"] = "not_approved"
        item["included"] = False
        item["reason"] = "not_approved"
        item["entry_status"] = "approval_required"
        item["operator_action"] = "approve_agent"
        item["safety_note"] = "Candidate was discovered but excluded from this auto-join because it was not explicitly approved."
        if agent_id:
            excluded_agent_ids.add(agent_id)
            excluded_agents.append(agent_id)
        if command:
            excluded_commands.append(command)
    config = report.get("config") if isinstance(report.get("config"), dict) else {}
    agents = config.get("agents") if isinstance(config.get("agents"), list) else []
    if excluded_agent_ids and isinstance(config, dict):
        config["agents"] = [
            agent
            for agent in agents
            if not (isinstance(agent, dict) and str(agent.get("agent_id") or "").strip() in excluded_agent_ids)
        ]
    kept_agents = config.get("agents") if isinstance(config.get("agents"), list) else []
    report["status"] = "ok" if kept_agents else "approval_required"
    report["approval_filter"] = {
        "approved_agents": sorted(matched_agent_ids),
        "approved_commands": sorted(matched_command_names),
        "approved_count": approved_count,
        "excluded_agents": sorted(excluded_agents),
        "excluded_commands": sorted(excluded_commands),
        "unmatched_approval_count": len(approved_agent_ids - matched_agent_ids) + len(approved_command_names - matched_command_names),
    }


def discovery_has_exact_approval(report: dict[str, Any]) -> bool:
    approval_filter = report.get("approval_filter") if isinstance(report.get("approval_filter"), dict) else {}
    try:
        return int(approval_filter.get("approved_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def _candidate_specs() -> list[dict[str, Any]]:
    return [
        {
            "command": "claude",
            "agent_id": "claude-code-live",
            "display_name": "Claude Code",
            "provider_kind": "claude_code",
            "connection_kind": "terminal_session",
            "terminal_idle_timeout": 0.75,
            "timeout_seconds": 120,
            **TERMINAL_PROMPT_BRIDGE_CONTRACT,
        },
        {
            "command": "codex",
            "agent_id": "codex-live",
            "display_name": "Codex",
            "provider_kind": "codex_live_session",
            "connection_kind": "live_session",
            "timeout_seconds": 240,
            "omit_command": True,
            **CODEX_LIVE_SESSION_CONTRACT,
        },
        {
            "command": "kiro",
            "agent_id": "kiro-live",
            "display_name": "Kiro",
            "provider_kind": "kiro_live_session",
            "connection_kind": "live_session",
            "timeout_seconds": 180,
            "omit_command": True,
            **KIRO_LIVE_SESSION_CONTRACT,
        },
        {
            "command": "antigravity",
            "agent_id": "antigravity-cli-live",
            "display_name": "Antigravity CLI",
            "provider_kind": "antigravity_cli",
            "connection_kind": "self_service",
            "timeout_seconds": 120,
            **SELF_SERVICE_CONTRACT,
        },
        {
            "command": "cursor-agent",
            "agent_id": "cursor-agent-live",
            "display_name": "Cursor Agent",
            "provider_kind": "cursor",
            "connection_kind": "terminal_session",
            "terminal_idle_timeout": 0.75,
            "timeout_seconds": 120,
            **TERMINAL_PROMPT_BRIDGE_CONTRACT,
        },
        {
            "command": "grok",
            "agent_id": "grok-live",
            "display_name": "Grok",
            "provider_kind": "grok_live_session",
            "connection_kind": "live_session",
            "timeout_seconds": 240,
            "omit_command": True,
            **GROK_LIVE_SESSION_CONTRACT,
        },
        {
            "command": "hermes",
            "agent_id": "hermes-cli-live",
            "display_name": "Hermes CLI",
            "provider_kind": "hermes_cli",
            "connection_kind": "terminal_session",
            "terminal_idle_timeout": 0.75,
            "timeout_seconds": 120,
            **TERMINAL_PROMPT_BRIDGE_CONTRACT,
        },
        {
            "command": "openclaw",
            "agent_id": "openclaw-cli-live",
            "display_name": "OpenClaw CLI",
            "provider_kind": "openclaw_cli",
            "connection_kind": "terminal_session",
            "terminal_idle_timeout": 0.75,
            "timeout_seconds": 120,
            **TERMINAL_PROMPT_BRIDGE_CONTRACT,
        },
        {
            "command": "gemini",
            "agent_id": "gemini-cli-legacy-live",
            "display_name": "Gemini CLI Legacy",
            "provider_kind": "gemini_cli_legacy",
            "connection_kind": "terminal_session",
            "terminal_idle_timeout": 0.75,
            "timeout_seconds": 120,
            "legacy": True,
            **TERMINAL_PROMPT_BRIDGE_CONTRACT,
        },
    ]


def _agent_entry(spec: dict[str, Any], *, meeting_id: str, engagement_mode: str) -> dict[str, Any]:
    entry = {
        "agent_id": spec["agent_id"],
        "display_name": spec["display_name"],
        "provider_kind": spec["provider_kind"],
        "connection_kind": spec["connection_kind"],
        "meeting_id": meeting_id,
        "engagement_mode": engagement_mode,
        "timeout_seconds": spec["timeout_seconds"],
        "join_semantics": spec["join_semantics"],
        "context_durability": spec["context_durability"],
        "sandbox_enforcement": spec["sandbox_enforcement"],
        "evidence_basis": spec["evidence_basis"],
    }
    if not spec.get("omit_command"):
        entry["command"] = [spec["command"]]
    if "terminal_idle_timeout" in spec:
        entry["terminal_idle_timeout"] = spec["terminal_idle_timeout"]
    return entry


def _discovery_skip_reason(*, available: bool, supported: bool, legacy: bool) -> str:
    if not available:
        return "not_found"
    if not supported:
        return "terminal_unsupported"
    if legacy:
        return "legacy"
    return "not_included"


def _entry_status(*, available: bool, supported: bool, included: bool, legacy: bool) -> str:
    if included:
        return "ready"
    if available and not supported:
        return "unsupported"
    if available and legacy:
        return "legacy"
    if available:
        return "skipped"
    return "missing"


def _candidate_supported(spec: dict[str, Any], *, terminal_supported: bool) -> bool:
    if spec.get("connection_kind") == "terminal_session":
        return terminal_supported
    return True


def _entry_mode(spec: dict[str, Any]) -> str:
    if spec["provider_kind"] == "codex_live_session":
        return "codex_live_session"
    if spec["provider_kind"] == "kiro_live_session":
        return "kiro_live_session"
    if spec["provider_kind"] == "grok_live_session":
        return "grok_live_session"
    return str(spec["connection_kind"])


def _operator_action(entry_status: str) -> str:
    if entry_status == "ready":
        return "auto_join"
    if entry_status == "legacy":
        return "include_legacy_gemini"
    if entry_status == "missing":
        return "install_cli"
    if entry_status == "unsupported":
        return "unsupported_terminal"
    return "preflight"


def _requires_approval(entry_status: str) -> bool:
    return entry_status == "ready"


def _safety_note(spec: dict[str, Any], entry_status: str) -> str:
    if entry_status == "missing":
        return "CLI executable was not found on PATH."
    if entry_status == "unsupported":
        return "PTY terminal sessions are not available on this host."
    if entry_status == "legacy":
        return "legacy Gemini is skipped unless explicitly included."
    if spec["provider_kind"] == "codex_live_session":
        return "Codex defaults and safety checks stay centralized in preflight."
    if spec["provider_kind"] == "kiro_live_session":
        return "Kiro uses kiro chat --resume-id; run preflight before auto join starts the resident."
    if spec["provider_kind"] == "grok_live_session":
        return "Grok uses JSON stdout plus --resume; run preflight before auto join starts the resident."
    if spec["connection_kind"] == "self_service":
        return "Self-service process is supervised; it owns its own room loop after preflight."
    if spec["connection_kind"] == "terminal_session":
        return "PATH only; run preflight before auto join starts the terminal session."
    return "PATH only; run preflight before auto join starts the resident."


def _next_commands(*, server: str) -> dict[str, list[str]]:
    return {
        "preflight": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "preflight",
            "--config",
            "<output>",
        ],
        "run_group": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "run-group",
            "--server",
            server,
            "--config",
            "<output>",
        ],
    }


def build_discovered_session_bundle(config: dict[str, Any]) -> dict[str, Any]:
    agents = [agent for agent in config.get("agents", []) if isinstance(agent, dict)]
    roles = []
    providers = []
    bindings = []
    for agent in agents:
        agent_id = str(agent.get("agent_id") or "").strip()
        if not agent_id:
            continue
        display_name = str(agent.get("display_name") or agent_id)
        provider_kind = str(agent.get("provider_kind") or "local_cli")
        join_semantics = str(agent.get("join_semantics") or "unknown")
        context_durability = str(agent.get("context_durability") or "unknown")
        sandbox_enforcement = str(agent.get("sandbox_enforcement") or "unknown")
        evidence_basis = str(agent.get("evidence_basis") or "unknown")
        role_id = _session_role_id(agent_id)
        provider_id = f"{agent_id}-provider"
        roles.append(
            {
                "id": role_id,
                "display_name": display_name,
                "lens": f"Live resident perspective from {display_name}.",
                "research_focus": (
                    f"Join the resident session through {join_semantics}. "
                    f"Context durability is {context_durability}; "
                    f"sandbox enforcement is {sandbox_enforcement}; discovery evidence is {evidence_basis}."
                ),
                "join_semantics": join_semantics,
                "context_durability": context_durability,
                "sandbox_enforcement": sandbox_enforcement,
                "evidence_basis": evidence_basis,
            }
        )
        provider: dict[str, Any] = {
            "id": provider_id,
            "kind": provider_kind,
            "display_name": display_name,
            "default_model": agent_id,
            "join_semantics": join_semantics,
            "context_durability": context_durability,
            "sandbox_enforcement": sandbox_enforcement,
            "evidence_basis": evidence_basis,
        }
        if agent.get("timeout_seconds"):
            provider["timeout_seconds"] = agent.get("timeout_seconds")
        providers.append(provider)
        bindings.append(
            {
                "agent_id": agent_id,
                "role_id": role_id,
                "owner_id": "discovery",
                "provider_id": provider_id,
                "model_id": agent_id,
                "permission_profile_id": "discovered_meeting_readonly",
                "join_mode": "fresh",
                "join_semantics": join_semantics,
                "context_durability": context_durability,
                "sandbox_enforcement": sandbox_enforcement,
                "evidence_basis": evidence_basis,
            }
        )
    return {
        "council_config": {
            "topic": "Discovered Live Agent Session",
            "question": "What should the discovered resident agents contribute from their own local CLI sessions?",
            "roles": roles,
            "meeting_template": {
                "id": "discovered_live_agents",
                "display_name": "Discovered Live Agents",
                "rounds": [
                    {
                        "id": "discovered_intro",
                        "title": "Discovered Agent Check-in",
                        "instruction": "Reply with one concise status update from your resident session.",
                        "turn_control": {"selection": "all_roles"},
                    }
                ],
            },
        },
        "agent_config": {
            "providers": providers,
            "permission_profiles": [
                {
                    "id": "discovered_meeting_readonly",
                    "meeting_read": True,
                    "lobby_chat": True,
                    "official_turn": True,
                    "web_search": False,
                    "tool_use": False,
                    "filesystem_read": False,
                    "filesystem_write": False,
                    "git_write": False,
                    "push": False,
                    "secrets": False,
                    "implementation": False,
                }
            ],
            "agent_bindings": bindings,
        },
    }


def discovered_session_bundle_paths(
    live_agent_output: Path,
    *,
    council_output: str = "",
    agent_output: str = "",
) -> tuple[Path, Path]:
    return (
        Path(council_output) if council_output else live_agent_output.with_name(_discovered_companion_name(live_agent_output, "council")),
        Path(agent_output) if agent_output else live_agent_output.with_name(_discovered_companion_name(live_agent_output, "agents")),
    )


def validate_distinct_session_bundle_paths(*paths: Path) -> None:
    seen: dict[Path, Path] = {}
    for path in paths:
        normalized = Path(path).expanduser().resolve(strict=False)
        if normalized in seen:
            raise ValueError("session bundle output paths must be distinct.")
        seen[normalized] = path


def add_session_bundle_outputs(
    report: dict[str, Any],
    *,
    live_agent_output: str,
    council_output: str,
    agent_output: str,
    server: str,
    meeting_id: str,
    group_id: str,
) -> None:
    report["session_bundle"] = {
        "live_agent_config_path": live_agent_output,
        "council_config_path": council_output,
        "agent_config_path": agent_output,
        "group_id": group_id,
    }
    next_commands = report.setdefault("next_commands", {})
    if isinstance(next_commands, dict):
        command = [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "ensure-session",
            "--server",
            server,
        ]
        if meeting_id:
            command.extend(["--meeting-id", meeting_id])
        command.extend(
            [
                "--group-id",
                group_id,
                "--council-config",
                council_output,
                "--agent-config",
                agent_output,
                "--live-agent-config",
                live_agent_output,
            ]
        )
        next_commands["ensure_session"] = command


def _discovered_companion_name(path: Path, prefix: str) -> str:
    name = path.name
    if name.startswith("live-agents."):
        return f"{prefix}.{name[len('live-agents.'):]}"
    if name == "live-agents.json":
        return f"{prefix}.json"
    return f"{path.stem}.{prefix}{path.suffix}"


def _session_role_id(agent_id: str) -> str:
    cleaned = "".join(character if character.isalnum() else "_" for character in agent_id).strip("_").lower()
    if not cleaned:
        return "discovered_agent"
    if cleaned[0].isdigit():
        return f"agent_{cleaned}"
    return cleaned


def fill_discovery_next_command_output(report: dict[str, Any], output: str) -> None:
    next_commands = report.get("next_commands")
    if not isinstance(next_commands, dict):
        return
    for command in next_commands.values():
        if not isinstance(command, list):
            continue
        for index, part in enumerate(command):
            if part == "<output>":
                command[index] = output
