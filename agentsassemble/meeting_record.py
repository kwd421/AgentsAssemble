from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentsassemble.meeting_context import build_diagnostics, public_debate_rounds, public_synthesis
from agentsassemble.models import CouncilConfig, ResearchDepth, ResearchSteering, _public_endpoint


def assemble_meeting_record(
    *,
    meeting_id: str,
    adapter_name: str,
    config: CouncilConfig,
    roles: list[dict[str, Any]],
    setup: Any,
    sessions: dict[str, dict[str, Any]],
    memory_context: dict[str, Any],
    research_records: list[dict[str, Any]],
    debate_rounds: list[dict[str, Any]],
    synthesis: dict[str, Any],
    evidence_gate: dict[str, Any],
    depth: ResearchDepth,
    steering: ResearchSteering,
    event_log: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    memory_input = {
        "research_summaries": [
            {
                "role_id": research["role_id"],
                "display_name": research["display_name"],
                "status": research.get("status", "complete"),
                "summary": research["summary"],
                "confidence": research["confidence"],
                "retry": research.get("retry", {"status": "not_needed", "attempts": 1}),
                "evidence_gate": research.get("evidence_gate", {}),
                "claim_evidence": research.get("claim_evidence", []),
                "weak_claims": research.get("weak_claims", []),
                "unsupported_claims": research.get("unsupported_claims", []),
                "verifier_rejected_claims": research.get("verifier_rejected_claims", []),
            }
            for research in research_records
        ]
    }

    return {
        "meeting_id": meeting_id,
        "command": f"assemble demo --adapter {adapter_name}",
        "question": config.question,
        "display_question": config.display_question,
        "topic": config.topic,
        "display_topic": config.display_topic,
        "meeting_mode": config.meeting_mode,
        "moderator": config.moderator.to_dict(),
        "roles": roles,
        "meeting_template": {
            "id": config.meeting_template_id,
            "display_name": config.meeting_template_name,
            "rounds": [
                {
                    "id": round_definition.id,
                    "title": round_definition.title,
                    "context_scope": round_definition.context_scope,
                    "instruction": round_definition.instruction,
                    "turn_control": round_definition.turn_control.to_dict(),
                }
                for round_definition in config.rounds
            ],
        },
        "adapter_config": {"name": setup.moderator_adapter.name},
        "provider_configs": {
            provider_id: provider_config.public_dict()
            for provider_id, provider_config in setup.providers.items()
        },
        "permission_profiles": {
            profile_id: profile.to_dict() for profile_id, profile in setup.permissions.items()
        },
        "agent_bindings": [binding.to_dict() for binding in setup.agent_bindings],
        "agent_config_source": setup.config_source,
        "incoming_agents": [_public_incoming_agent(agent) for agent in setup.incoming_agents],
        "admission_decisions": setup.admission_decisions,
        "provider_capabilities": {
            provider_id: setup.registry.capabilities_for(provider_config).to_dict()
            for provider_id, provider_config in setup.providers.items()
        },
        "memory_context": memory_context,
        "memory_input": memory_input,
        "research_steering": steering.to_dict(),
        "research_depth": {
            "name": depth.name,
            "label": depth.label,
            "min_sources": depth.min_sources,
            "target_sources": depth.target_sources,
            "min_queries": depth.min_queries,
            "min_claims": depth.min_claims,
            "min_counterclaims": depth.min_counterclaims,
            "notes_per_source": depth.notes_per_source,
            "source_mix": depth.source_mix,
            "instructions": depth.instructions,
        },
        "isolation": {
            role.id: {
                "role_dir": f"roles/{role.id}",
                "private_research_dir": f"private_research/{role.id}",
                "session": sessions[role.id],
                "agent_binding": setup.resolved_agents[role.id].binding.to_dict(),
                "provider": setup.resolved_agents[role.id].provider.public_dict(),
                "permissions": setup.resolved_agents[role.id].permissions.to_dict(),
                "capabilities": setup.resolved_agents[role.id].capabilities.to_dict(),
            }
            for role in config.roles
        },
        "research_artifacts": [
            {
                "role_id": research["role_id"],
                "path": f"private_research/{research['role_id']}/research.json",
            }
            for research in research_records
        ],
        "debate_rounds": public_debate_rounds(debate_rounds),
        "event_log": event_log or [],
        "moderator_synthesis": public_synthesis(synthesis),
        "evidence_gate": evidence_gate,
        "diagnostics": build_diagnostics(debate_rounds, synthesis),
        "artifacts": {
            "agenda": "agenda.md",
            "transcript": "transcript.md",
            "decision": "decision.md",
            "meeting": "meeting.json",
            "tasks": "tasks/",
            "private_research": "private_research/",
        },
        "audit_metadata": {
            "created_at": datetime.now(UTC).isoformat(),
            "adapter": setup.moderator_adapter.name,
            "provider_ids": list(setup.providers),
            "research_depth": depth.name,
            "research_steering": steering.to_dict(),
            "reproducibility": "auditable and resumable, not deterministic replay",
        },
        "failure_state": derive_failure_state(
            synthesis=synthesis,
            evidence_gate=evidence_gate,
            research_records=research_records,
            debate_rounds=debate_rounds,
        ),
    }


def _public_incoming_agent(agent: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _public_incoming_value(str(key), value) for key, value in agent.items()}


def _public_incoming_value(key: str, value: Any) -> Any:
    normalized_key = key.casefold().replace("-", "_")
    if isinstance(value, dict):
        return {str(child_key): _public_incoming_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_public_incoming_value(key, item) for item in value]
    if isinstance(value, str):
        if normalized_key in {"endpoint", "url", "uri"}:
            return _public_endpoint(value)
        if _incoming_key_is_sensitive(normalized_key) or _incoming_text_is_sensitive(value):
            return "<redacted>"
    return value


def _incoming_key_is_sensitive(key: str) -> bool:
    sensitive_keys = {
        "auth_ref",
        "authorization",
        "token",
        "api_key",
        "apikey",
        "key",
        "access_key",
        "secret",
        "client_secret",
        "password",
        "headers",
        "notes",
    }
    return key in sensitive_keys or key.endswith("_token") or key.endswith("_secret")


def _incoming_text_is_sensitive(value: str) -> bool:
    normalized = value.casefold()
    markers = ("authorization", "bearer ", "secret", "token", "api-key", "api_key", "apikey", "x-api-key", "password")
    return any(marker in normalized for marker in markers)


def derive_failure_state(
    *,
    synthesis: dict[str, Any],
    evidence_gate: dict[str, Any] | None = None,
    decision_gate: dict[str, Any] | None = None,
    research_records: list[dict[str, Any]] | None = None,
    debate_rounds: list[dict[str, Any]] | None = None,
    room_chat: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    failures = []
    if synthesis.get("fallback") or synthesis.get("status") == "degraded":
        failures.append("moderator_synthesis_fallback")
    failures.extend(_research_failures(research_records or []))
    failures.extend(_debate_failures(debate_rounds or []))
    failures.extend(_room_chat_failures(room_chat or []))
    evidence_status = str((evidence_gate or {}).get("status") or "unknown")
    gate_status = str((decision_gate or {}).get("status") or "unknown")
    required_action = (decision_gate or {}).get("required_action")
    can_finalize = (decision_gate or {}).get("can_finalize")
    degraded_gate_statuses = {"invalid", "blocked", "needs_more_research"}
    action_gate_statuses = {"needs_user_decision", "no_consensus"}

    if failures or gate_status in degraded_gate_statuses:
        status = "degraded"
    elif gate_status in action_gate_statuses:
        status = "action_required"
    elif gate_status == "no_official_decision":
        status = "not_applicable"
    else:
        status = "none"

    return {
        "status": status,
        "failures": _dedupe(failures),
        "decision_gate_status": gate_status,
        "required_action": required_action,
        "can_finalize": can_finalize,
        "evidence_gate_status": evidence_status,
    }


def _research_failures(research_records: list[dict[str, Any]]) -> list[str]:
    failures = []
    for record in research_records:
        role_id = str(record.get("role_id") or "unknown")
        retry = record.get("retry") if isinstance(record.get("retry"), dict) else {}
        if record.get("status") == "failed":
            failures.append(f"research_failed:{role_id}")
        if retry.get("status") == "failed":
            failures.append(f"retry_failed:{role_id}")
    return failures


def _debate_failures(debate_rounds: list[dict[str, Any]]) -> list[str]:
    failures = []
    for round_record in debate_rounds:
        round_id = str(round_record.get("id") or round_record.get("round") or "unknown")
        for message in round_record.get("messages", []):
            if not isinstance(message, dict):
                continue
            if message.get("status") == "failed" or message.get("stance_status") == "blocked":
                role_id = str(message.get("role_id") or "unknown")
                message_round = str(message.get("round") or round_id)
                failures.append(f"debate_failed:{role_id}:{message_round}")
    return failures


def _room_chat_failures(room_chat: list[dict[str, Any]]) -> list[str]:
    failures = []
    for message in room_chat:
        if not isinstance(message, dict):
            continue
        if message.get("status") == "failed" or message.get("stance_status") == "blocked":
            role_id = str(message.get("role_id") or "unknown")
            failures.append(f"room_chat_failed:{role_id}")
    return failures


def _dedupe(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
