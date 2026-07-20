"""Local CLI discovery and generated-config ownership for legacy residents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentsassemble.codex_sessions import write_agent_config
from agentsassemble.live_agent_discovery import (
    add_session_bundle_outputs,
    apply_discovery_approval_filter,
    build_discovered_live_agent_config,
    build_discovered_session_bundle,
    discovered_session_bundle_paths,
    fill_discovery_next_command_output,
    validate_distinct_session_bundle_paths,
)
from agentsassemble.live_agent_processes import clean_live_agent_group_id
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


@dataclass(frozen=True)
class LegacyLiveAgentDiscoveryService:
    output_root: Path

    def run(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        return live_agent_discovery_payload(
            self.output_root,
            payload,
            default_server=default_server,
        )


def live_agent_discovery_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    report = build_discovered_live_agent_config(
        server=str(payload.get("server") or default_server),
        meeting_id=str(payload.get("meeting_id") or ""),
        engagement_mode=str(payload.get("engagement_mode") or "mentioned"),
        include_legacy_gemini=_payload_bool(payload.get("include_legacy_gemini")),
    )
    approved_agents = _safe_payload_strings(payload.get("approved_agents"), limit=64)
    approved_commands = _safe_payload_strings(payload.get("approved_commands"), limit=64)
    if approved_agents or approved_commands:
        apply_discovery_approval_filter(
            report,
            approved_agents=approved_agents,
            approved_commands=approved_commands,
        )
    output_path = output_root / "live-agents.discovered.local.json"
    should_write = not ("write_config" in payload and not _payload_bool(payload.get("write_config")))
    if report.get("status") == "ok" and should_write:
        write_agent_config(output_path, report["config"])
        fill_discovery_next_command_output(report, str(output_path))
        report["output"] = str(output_path)
        report["written"] = True
        if _payload_bool(payload.get("session_bundle")):
            council_output, agent_output = discovered_session_bundle_paths(output_path)
            validate_distinct_session_bundle_paths(output_path, council_output, agent_output)
            bundle = build_discovered_session_bundle(report["config"])
            write_agent_config(council_output, bundle["council_config"])
            write_agent_config(agent_output, bundle["agent_config"])
            add_session_bundle_outputs(
                report,
                live_agent_output=str(output_path),
                council_output=str(council_output),
                agent_output=str(agent_output),
                server=str(payload.get("server") or default_server),
                meeting_id=str(payload.get("meeting_id") or ""),
                group_id=clean_live_agent_group_id(output_path.stem),
            )
    else:
        report["output"] = ""
        report["written"] = False
    return report


def discovery_operation_details(
    discoveries: list[object],
    approval_filter: object = None,
) -> dict[str, object]:
    return {
        "join_semantics": _discovery_operation_values(discoveries, "join_semantics"),
        "context_durability": _discovery_operation_values(discoveries, "context_durability"),
        "sandbox_enforcement": _discovery_operation_values(discoveries, "sandbox_enforcement"),
        "evidence_basis": _discovery_operation_values(discoveries, "evidence_basis"),
        "approval_required": sum(
            1
            for item in discoveries
            if isinstance(item, dict)
            and item.get("available")
            and item.get("included")
            and item.get("requires_approval")
        ),
        **_discovery_approval_operation_details(approval_filter),
    }


def _discovery_approval_operation_details(approval_filter: object) -> dict[str, object]:
    if not isinstance(approval_filter, dict):
        return {}
    approved_agents = _safe_payload_strings(approval_filter.get("approved_agents"), limit=64)
    excluded_agents = _safe_payload_strings(approval_filter.get("excluded_agents"), limit=64)
    approved_clis = _safe_payload_strings(approval_filter.get("approved_commands"), limit=64)
    excluded_clis = _safe_payload_strings(approval_filter.get("excluded_commands"), limit=64)
    approved_count = _nonnegative_int(approval_filter.get("approved_count"), 0)
    unmatched_count = _nonnegative_int(approval_filter.get("unmatched_approval_count"), 0)
    if not (approved_agents or excluded_agents or approved_clis or excluded_clis or approved_count or unmatched_count):
        return {}
    details: dict[str, object] = {
        "approved_count": approved_count,
        "excluded_agent_count": len(excluded_agents),
        "unmatched_approval_count": unmatched_count,
    }
    if approved_agents:
        details["approved_agent_ids"] = approved_agents[:10]
    if approved_clis:
        details["approved_cli_count"] = len(approved_clis)
    if excluded_clis:
        details["excluded_cli_count"] = len(excluded_clis)
    return details


def _discovery_operation_values(discoveries: list[object], field_name: str) -> list[str]:
    values = set()
    for item in discoveries:
        if not isinstance(item, dict) or not item.get("available"):
            continue
        value = clean_lobby_text(item.get(field_name), limit=128)
        if value:
            values.add(value)
    return sorted(values)


def _safe_payload_strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    strings = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = clean_lobby_text(item, limit=limit)
        if text:
            strings.append(text)
    return strings


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)
