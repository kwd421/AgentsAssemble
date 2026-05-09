from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentsassemble.models import CouncilConfig, ResearchDepth, ResearchSteering
from agentsassemble.templates import DEMO_MEETING_TEMPLATE


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
                "summary": research["summary"],
                "confidence": research["confidence"],
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
        "roles": roles,
        "meeting_template": {
            "id": DEMO_MEETING_TEMPLATE["id"],
            "display_name": DEMO_MEETING_TEMPLATE["display_name"],
            "rounds": [
                {
                    "id": round_definition.id,
                    "title": round_definition.title,
                    "context_scope": round_definition.context_scope,
                    "instruction": round_definition.instruction,
                }
                for round_definition in DEMO_MEETING_TEMPLATE["rounds"]
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
        "debate_rounds": debate_rounds,
        "event_log": event_log or [],
        "moderator_synthesis": synthesis,
        "evidence_gate": evidence_gate,
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
        "failure_state": {"status": "none", "failures": []},
    }
