from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agentsassemble.adapters import ProviderAdapter, default_provider_registry
from agentsassemble.artifacts import write_public_artifacts, write_research, write_role_files
from agentsassemble.config import load_council_config
from agentsassemble.evidence import apply_evidence_gate, summarize_evidence_gates
from agentsassemble.memory import load_memory_context, write_memory_artifacts
from agentsassemble.models import (
    AgentBinding,
    MeetingResult,
    PermissionProfile,
    ProviderConfig,
    ResearchDepthName,
    ResearchSteering,
    get_research_depth,
)
from agentsassemble.templates import DEMO_MEETING_TEMPLATE


def get_adapter(
    adapter_name: str,
    codex_timeout_seconds: int = 240,
    codex_search_enabled: bool = True,
) -> ProviderAdapter:
    provider = _provider_config_for_adapter(adapter_name, codex_timeout_seconds, codex_search_enabled)
    return default_provider_registry(
        codex_timeout_seconds=codex_timeout_seconds,
        codex_search_enabled=codex_search_enabled,
    ).create(provider)


def _provider_config_for_adapter(
    adapter_name: str,
    codex_timeout_seconds: int,
    codex_search_enabled: bool,
) -> ProviderConfig:
    if adapter_name == "mock":
        return ProviderConfig(id="mock", kind="mock", display_name="Mock Demo", default_model="deterministic")
    if adapter_name == "codex":
        return ProviderConfig(
            id="codex",
            kind="codex",
            display_name="Codex CLI",
            default_model="local-codex-session",
            timeout_seconds=codex_timeout_seconds,
            search_enabled=codex_search_enabled,
        )
    raise ValueError(f"Unknown adapter: {adapter_name}")


def _default_permissions(adapter_name: str, codex_search_enabled: bool) -> dict[str, PermissionProfile]:
    return {
        "meeting_read_only": PermissionProfile(
            id="meeting_read_only",
            web_search=adapter_name == "codex" and codex_search_enabled,
            tool_use=False,
            filesystem_read=False,
            filesystem_write=False,
            git_write=False,
            push=False,
            implementation=False,
        )
    }


def _default_agent_bindings(config_roles: list[Any], provider_id: str) -> list[AgentBinding]:
    return [
        AgentBinding(
            agent_id=f"{role.id}-agent",
            role_id=role.id,
            owner_id="local-user",
            provider_id=provider_id,
            model_id=None,
            permission_profile_id="meeting_read_only",
        )
        for role in config_roles
    ]


def run_demo_meeting(
    adapter_name: str = "mock",
    output_root: Path | None = None,
    reporter: Callable[[str], None] | None = None,
    codex_timeout_seconds: int = 240,
    codex_search_enabled: bool = True,
    research_depth: ResearchDepthName = "smoke",
    research_steering: str | None = None,
) -> MeetingResult:
    def report(message: str) -> None:
        if reporter is not None:
            reporter(message)

    config = load_council_config()
    depth = get_research_depth(research_depth)
    steering = ResearchSteering(
        stance="user_leaning" if research_steering else "open",
        prompt=research_steering,
    )
    provider = _provider_config_for_adapter(adapter_name, codex_timeout_seconds, codex_search_enabled)
    providers = {provider.id: provider}
    permissions = _default_permissions(adapter_name, codex_search_enabled)
    agent_bindings = _default_agent_bindings(config.roles, provider.id)
    registry = default_provider_registry(
        codex_timeout_seconds=codex_timeout_seconds,
        codex_search_enabled=codex_search_enabled,
    )
    resolved_agents = {
        binding.role_id: registry.resolve(binding, providers, permissions)
        for binding in agent_bindings
    }
    moderator_adapter = registry.create(provider)
    root = output_root or Path(".agentsassemble")
    memory_context = load_memory_context(root, config.roles)
    meeting_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    meeting_dir = root / "meetings" / meeting_id
    meeting_dir.mkdir(parents=True, exist_ok=False)
    report(f"Meeting {meeting_id}")
    report(f"Question: {config.display_question}")
    report(
        "Research depth: "
        f"{depth.name} (target {depth.target_sources} sources, "
        f"{depth.min_claims} claims, {depth.min_counterclaims} counterclaims per role)"
    )
    if not steering.is_open:
        report(f"Research steering: {steering.prompt}")

    context: dict[str, Any] = {
        "meeting_id": meeting_id,
        "question": config.question,
        "topic": config.topic,
        "display_question": config.display_question,
        "display_topic": config.display_topic,
        "meeting_dir": str(meeting_dir),
        "research_depth": depth.name,
        "research_steering": steering.to_dict(),
        "memory_context": memory_context,
    }

    roles = [role.__dict__ for role in config.roles]
    sessions = {}
    for role in config.roles:
        report(f"Preparing role: {role.display_name} ({role.id})")
        write_role_files(meeting_dir, role)
        sessions[role.id] = resolved_agents[role.id].adapter.start_session(role, context)

    research_records = []
    for role in config.roles:
        report(f"Research: {role.display_name}")
        research = resolved_agents[role.id].adapter.run_research(
            role,
            sessions[role.id],
            config.question,
            depth,
            steering,
        )
        research = apply_evidence_gate(research, depth)
        research_records.append(research)
        write_research(meeting_dir, research)
    evidence_gate = summarize_evidence_gates(research_records)

    debate_rounds = []
    rounds_by_id: dict[str, list[dict[str, Any]]] = {}
    for round_definition in DEMO_MEETING_TEMPLATE["rounds"]:
        report(round_definition.report_label)
        messages = []
        if round_definition.context_scope == "own_research":
            for role, research in zip(config.roles, research_records, strict=True):
                messages.append(
                    resolved_agents[role.id].adapter.run_round(
                        role,
                        sessions[role.id],
                        round_definition.id,
                        round_definition.instruction,
                        {
                            "own_research": research,
                            "evidence_gate_rule": "Use supported claim_evidence as grounds. Mention unsupported_claims only as discarded or uncertain material.",
                            "stance_rule": "Keep your role's own position distinct. Do not soften your stance just to agree with the room.",
                        },
                    )
                )
        else:
            public_context = {
                **rounds_by_id,
                "evidence_gate": evidence_gate,
                "evidence_gate_rule": "Do not treat unsupported claims as accepted facts.",
                "stance_rule": (
                    "You may revise your position only when another role gives supported evidence that changes your reasoning. "
                    "Otherwise, hold your position and attack the weakest premise."
                ),
            }
            for role in config.roles:
                messages.append(
                    resolved_agents[role.id].adapter.run_round(
                        role,
                        sessions[role.id],
                        round_definition.id,
                        round_definition.instruction,
                        public_context,
                    )
                )
        rounds_by_id[round_definition.id] = messages
        debate_rounds.append(
            {
                "id": round_definition.id,
                "title": round_definition.title,
                "context_scope": round_definition.context_scope,
                "instruction": round_definition.instruction,
                "messages": messages,
            }
        )

    moderator_session = {
        "adapter": moderator_adapter.name,
        "role_id": "moderator",
        "session_id": f"{moderator_adapter.name}-{meeting_id}-moderator",
        "meeting_dir": str(meeting_dir),
    }
    report("Moderator synthesis")
    synthesis = moderator_adapter.synthesize(
        moderator_session,
        config.question,
        {
            "research_summaries": [
                {
                    "role_id": research["role_id"],
                    "summary": research["summary"],
                    "confidence": research["confidence"],
                    "claim_evidence": research["claim_evidence"],
                    "unsupported_claims": research.get("unsupported_claims", []),
                    "weak_claims": research.get("weak_claims", []),
                    "verifier_rejected_claims": research.get("verifier_rejected_claims", []),
                    "claim_verification": research.get("claim_verification", []),
                    "evidence_gate": research.get("evidence_gate", {}),
                    "counterclaims": research.get("counterclaims", []),
                    "coverage_gaps": research.get("coverage_gaps", []),
                }
                for research in research_records
            ],
            "rounds": {round_record["id"]: round_record["messages"] for round_record in debate_rounds},
            "evidence_gate": evidence_gate,
            "moderator_rule": "Base the decision on supported claim_evidence. Unsupported, weak, verifier-rejected, irrelevant, or contradictory claims may be listed as caveats but must not determine the winner.",
            "stance_rule": "Treat held, revised, and conceded stances as debate state. Do not collapse disagreement into fake consensus.",
        },
    )
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

    meeting = {
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
        "adapter_config": {"name": moderator_adapter.name},
        "provider_configs": {provider_id: config.public_dict() for provider_id, config in providers.items()},
        "permission_profiles": {
            profile_id: profile.to_dict() for profile_id, profile in permissions.items()
        },
        "agent_bindings": [binding.to_dict() for binding in agent_bindings],
        "provider_capabilities": {
            provider_id: registry.capabilities_for(config).to_dict()
            for provider_id, config in providers.items()
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
                "agent_binding": resolved_agents[role.id].binding.to_dict(),
                "provider": resolved_agents[role.id].provider.public_dict(),
                "permissions": resolved_agents[role.id].permissions.to_dict(),
                "capabilities": resolved_agents[role.id].capabilities.to_dict(),
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
            "adapter": moderator_adapter.name,
            "provider_ids": list(providers),
            "research_depth": depth.name,
            "research_steering": steering.to_dict(),
            "reproducibility": "auditable and resumable, not deterministic replay",
        },
        "failure_state": {"status": "none", "failures": []},
    }

    meeting["memory_artifacts"] = write_memory_artifacts(root, meeting)
    meeting["artifacts"]["memory"] = "memory/"
    write_public_artifacts(meeting_dir, meeting)
    report(f"Decision: {synthesis['winner']} ({synthesis['confidence']} confidence)")
    report(f"Artifacts: {meeting_dir}")
    return MeetingResult(meeting_id=meeting_id, meeting_dir=meeting_dir)
