from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from agentsassemble.artifacts import write_research, write_role_files
from agentsassemble.evidence import apply_evidence_gate, summarize_evidence_gates
from agentsassemble.meeting_context import build_decision_context
from agentsassemble.models import CouncilConfig, ResearchDepth, ResearchSteering
from agentsassemble.templates import DEMO_MEETING_TEMPLATE


def start_role_sessions(
    config: CouncilConfig,
    meeting_dir: Path,
    context: dict[str, Any],
    resolved_agents: dict[str, Any],
    report: Callable[[str], None],
    live_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any]]:
    sessions = {}
    for role in config.roles:
        report(f"Preparing role: {role.display_name} ({role.id})")
        if live_event is not None:
            live_event({"kind": "status", "role_id": role.id, "display_name": role.display_name, "content": f"{role.display_name} 세션 준비 중"})
        write_role_files(meeting_dir, role)
        session = resolved_agents[role.id].adapter.start_session(role, context)
        binding = resolved_agents[role.id].binding
        session.setdefault("meeting_id", context.get("meeting_id"))
        session.setdefault("agent_id", binding.agent_id)
        session.setdefault("owner_id", binding.owner_id)
        session.setdefault("join_mode", binding.join_mode)
        sessions[role.id] = session
        if live_event is not None:
            live_event({"kind": "status", "role_id": role.id, "display_name": role.display_name, "content": f"{role.display_name} 세션 준비 완료"})
    return sessions


def run_research_phase(
    config: CouncilConfig,
    meeting_dir: Path,
    sessions: dict[str, dict[str, Any]],
    resolved_agents: dict[str, Any],
    depth: ResearchDepth,
    steering: ResearchSteering,
    report: Callable[[str], None],
    live_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    research_by_role = {}
    max_attempts = 2

    def run_role_research(role: Any) -> dict[str, Any]:
        errors = []
        for attempt in range(1, max_attempts + 1):
            try:
                research = resolved_agents[role.id].adapter.run_research(
                    role,
                    sessions[role.id],
                    config.question,
                    depth,
                    steering,
                )
                research["retry"] = retry_metadata(attempt, max_attempts, errors)
                research = apply_evidence_gate(research, depth)
                write_research(meeting_dir, research)
                return research
            except Exception as error:
                errors.append(public_adapter_error(error))
                if attempt < max_attempts and live_event is not None:
                    live_event(
                        {
                            "kind": "status",
                            "role_id": role.id,
                            "display_name": role.display_name,
                            "content": "리서치 실패, 재조사 시도",
                        }
                    )
        raise ResearchPhaseError(errors)

    futures = {}
    max_workers = max(1, len(config.roles))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    if live_event is not None:
        live_event({"kind": "status", "content": f"{len(config.roles)}명 병렬 독립 리서치 시작"})
    for role in config.roles:
        report(f"Research: {role.display_name}")
        if live_event is not None:
            live_event({"kind": "status", "role_id": role.id, "display_name": role.display_name, "content": f"{role.display_name} 독립 리서치 시작"})
        futures[executor.submit(run_role_research, role)] = role

    with executor:
        for future in as_completed(futures):
            role = futures[future]
            try:
                research = future.result()
            except Exception as error:
                research = failed_research_record(role, error, depth, steering)
                write_research(meeting_dir, research)
            research_by_role[role.id] = research
            if live_event is not None:
                live_event(
                    {
                        "kind": "research",
                        "role_id": role.id,
                        "display_name": role.display_name,
                        "content": compact_live_research_summary(research),
                        "confidence": research.get("confidence"),
                        "retry_status": retry_metadata_from_record(research).get("status"),
                        "retry_attempts": retry_metadata_from_record(research).get("attempts"),
                    }
                )
    research_records = [research_by_role[role.id] for role in config.roles]
    return research_records, summarize_evidence_gates(research_records)


class ResearchPhaseError(RuntimeError):
    def __init__(self, errors: list[str]):
        super().__init__(errors[-1] if errors else "research failed")
        self.errors = errors


def retry_metadata(attempts: int, max_attempts: int, errors: list[str]) -> dict[str, Any]:
    return {
        "attempts": attempts,
        "max_attempts": max_attempts,
        "status": "recovered" if errors else "not_needed",
        "errors": list(errors),
    }


def retry_metadata_from_record(research: dict[str, Any]) -> dict[str, Any]:
    retry = research.get("retry")
    if isinstance(retry, dict):
        return retry
    return {"attempts": 1, "max_attempts": 1, "status": "unknown", "errors": []}


def failed_research_record(
    role: Any,
    error: Exception,
    depth: ResearchDepth,
    steering: ResearchSteering,
) -> dict[str, Any]:
    errors = error.errors if isinstance(error, ResearchPhaseError) else [public_adapter_error(error)]
    message = f"Research failed for {role.display_name}: {errors[-1] if errors else 'adapter failed'}"
    return {
        "role_id": role.id,
        "display_name": role.display_name,
        "status": "failed",
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
        },
        "queries": [],
        "sources": [],
        "summary": message,
        "retry": {
            "attempts": len(errors),
            "max_attempts": 2,
            "status": "failed",
            "errors": errors,
        },
        "confidence": "low",
        "uncertainty": "Provider or adapter failed before producing research.",
        "claim_evidence": [],
        "counterclaims": [],
        "rejected_claims": [],
        "evidence_gate": {
            "status": "warn",
            "supported_claim_count": 0,
            "unsupported_claim_count": 0,
            "weak_claim_count": 0,
            "verifier_rejected_claim_count": 0,
            "claim_verification_count": 0,
            "source_count": 0,
            "failures": ["research_failed"],
            "confidence_before": "low",
            "confidence_after": "low",
        },
    }


def run_debate_phase(
    config: CouncilConfig,
    sessions: dict[str, dict[str, Any]],
    resolved_agents: dict[str, Any],
    research_records: list[dict[str, Any]],
    evidence_gate: dict[str, Any],
    report: Callable[[str], None],
    live_event: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    debate_rounds = []
    rounds_by_id: dict[str, list[dict[str, Any]]] = {}
    for round_definition in config.rounds or DEMO_MEETING_TEMPLATE["rounds"]:
        report(round_definition.report_label)
        if live_event is not None:
            live_event({"kind": "status", "round": round_definition.id, "content": round_definition.report_label})
        messages = []
        speaker_roles = _speaker_roles_for_round(config.roles, round_definition)
        skipped_role_ids = [role.id for role in config.roles if role.id not in {speaker.id for speaker in speaker_roles}]
        turn_control = round_definition.turn_control.to_dict(skipped_role_ids=skipped_role_ids)
        if round_definition.context_scope == "own_research":
            research_by_role = {research["role_id"]: research for research in research_records}
            for turn_index, role in enumerate(speaker_roles):
                research = research_by_role[role.id]
                try:
                    message = resolved_agents[role.id].adapter.run_round(
                        role,
                        sessions[role.id],
                        round_definition.id,
                        round_definition.instruction,
                        {
                            "own_research": research,
                            "turn_control": turn_control,
                            "current_turn": _current_turn(round_definition.id, turn_index, role.id),
                            "evidence_gate_rule": "Use supported claim_evidence as grounds. Mention unsupported_claims only as discarded or uncertain material.",
                            "stance_rule": "Keep your role's own position distinct. Do not soften your stance just to agree with the room.",
                        },
                    )
                except Exception as error:
                    message = failed_round_message(role, round_definition.id, error)
                message = _with_turn_metadata(message, round_definition.id, turn_index)
                messages.append(message)
                if live_event is not None:
                    live_event({"kind": "message", **message})
        else:
            public_context = {
                **rounds_by_id,
                "evidence_gate": evidence_gate,
                "turn_control": turn_control,
                "evidence_gate_rule": "Do not treat unsupported claims as accepted facts.",
                "stance_rule": (
                    "You may revise your position only when another role gives supported evidence that changes your reasoning. "
                    "Otherwise, hold your position and attack the weakest premise."
                ),
            }
            for turn_index, role in enumerate(speaker_roles):
                try:
                    message = resolved_agents[role.id].adapter.run_round(
                        role,
                        sessions[role.id],
                        round_definition.id,
                        round_definition.instruction,
                        {**public_context, "current_turn": _current_turn(round_definition.id, turn_index, role.id)},
                    )
                except Exception as error:
                    message = failed_round_message(role, round_definition.id, error)
                message = _with_turn_metadata(message, round_definition.id, turn_index)
                messages.append(message)
                if live_event is not None:
                    live_event({"kind": "message", **message})
        rounds_by_id[round_definition.id] = messages
        debate_rounds.append(
            {
                "id": round_definition.id,
                "title": round_definition.title,
                "context_scope": round_definition.context_scope,
                "instruction": round_definition.instruction,
                "turn_control": turn_control,
                "messages": messages,
            }
        )
    return debate_rounds


def _speaker_roles_for_round(roles: list[Any], round_definition: Any) -> list[Any]:
    control = round_definition.turn_control
    if control.selection != "selected_roles":
        return roles
    roles_by_id = {role.id: role for role in roles}
    return [roles_by_id[role_id] for role_id in control.speaker_role_ids if role_id in roles_by_id]


def _current_turn(round_id: str, turn_index: int, role_id: str) -> dict[str, object]:
    return {
        "turn_id": f"{round_id}:{turn_index}:{role_id}",
        "turn_index": turn_index,
        "role_id": role_id,
        "engagement_mode": "moderator_called",
    }


def _with_turn_metadata(message: dict[str, Any], round_id: str, turn_index: int) -> dict[str, Any]:
    role_id = str(message.get("role_id") or "unknown")
    return {
        **message,
        "content": compact_spoken_message(str(message.get("content") or "")),
        "turn_id": f"{round_id}:{turn_index}:{role_id}",
        "turn_index": turn_index,
        "engagement_mode": "moderator_called",
    }


def failed_round_message(role: Any, round_name: str, error: Exception) -> dict[str, Any]:
    return {
        "role_id": role.id,
        "display_name": role.display_name,
        "round": round_name,
        "status": "failed",
        "content": f"Adapter failed during {round_name}: {public_adapter_error(error)}",
        "position": "",
        "stance_status": "blocked",
        "stance_delta": "none",
        "changed_by": [],
        "change_reason": "",
        "remaining_resistance": "Adapter failure prevented this turn.",
        "emotion": {},
        "change_conditions": [],
        "confidence": "low",
    }


def compact_live_research_summary(research: dict[str, Any]) -> str:
    summary = str(research.get("summary") or "리서치 완료").strip()
    sentences = [part.strip() for part in summary.replace("?", "?.").replace("!", "!.").split(".") if part.strip()]
    if len(sentences) <= 2 and len(summary) <= 160:
        return summary
    compact = ". ".join(sentences[:2]).strip()
    if compact and not compact.endswith((".", "?", "!")):
        compact += "."
    if compact and len(compact) <= 160:
        return compact
    return summary[:157].rstrip() + "..."


def compact_spoken_message(content: str, max_sentences: int = 6, max_chars: int = 560) -> str:
    normalized = " ".join(str(content or "").split()).strip()
    if not normalized:
        return ""
    sentences = _split_spoken_sentences(normalized)
    if len(sentences) > max_sentences:
        normalized = " ".join(sentences[:max_sentences]).strip()
    if len(normalized) <= max_chars:
        return normalized
    compact = ""
    for sentence in _split_spoken_sentences(normalized):
        candidate = f"{compact} {sentence}".strip()
        if len(candidate) > max_chars:
            break
        compact = candidate
    if compact:
        return compact
    return normalized[: max_chars - 1].rstrip() + "…"


def _split_spoken_sentences(content: str) -> list[str]:
    sentences: list[str] = []
    current = ""
    endings = {".", "?", "!", "。", "？", "！"}
    for char in content:
        current += char
        if char in endings:
            sentence = current.strip()
            if sentence:
                sentences.append(sentence)
            current = ""
    remainder = current.strip()
    if remainder:
        sentences.extend(_split_long_spoken_remainder(remainder))
    return [sentence for sentence in sentences if sentence]


def _split_long_spoken_remainder(content: str, chunk_size: int = 130) -> list[str]:
    if len(content) <= chunk_size:
        return [content]
    chunks = []
    current = ""
    for part in content.split(","):
        candidate = f"{current}, {part}".strip(", ").strip()
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            current = part.strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    if len(chunks) == 1 and len(chunks[0]) > chunk_size:
        return [content[index : index + chunk_size].strip() for index in range(0, len(content), chunk_size)]
    return chunks


def synthesize_meeting(
    moderator_adapter: Any,
    meeting_id: str,
    meeting_dir: Path,
    question: str,
    research_records: list[dict[str, Any]],
    debate_rounds: list[dict[str, Any]],
    evidence_gate: dict[str, Any],
    report: Callable[[str], None],
    live_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    moderator_session = {
        "adapter": moderator_adapter.name,
        "role_id": "moderator",
        "session_id": f"{moderator_adapter.name}-{meeting_id}-moderator",
        "meeting_dir": str(meeting_dir),
    }
    report("Moderator synthesis")
    if live_event is not None:
        live_event({"kind": "status", "role_id": "moderator", "display_name": "Moderator", "content": "종합 시작"})
    try:
        synthesis = moderator_adapter.synthesize(
            moderator_session,
            question,
            build_decision_context(research_records, debate_rounds, evidence_gate),
        )
    except Exception as error:
        synthesis = failed_synthesis_record(error)
    if live_event is not None:
        live_event(
            {
                "kind": "synthesis",
                "role_id": "moderator",
                "display_name": "Moderator",
                "content": synthesis.get("summary", "종합 완료"),
                "position": synthesis.get("winner", ""),
                "confidence": synthesis.get("confidence"),
                "status": synthesis.get("status"),
            }
        )
    return synthesis


def failed_synthesis_record(error: Exception) -> dict[str, Any]:
    return {
        "winner": "Undetermined",
        "ranking": [],
        "confidence": "low",
        "caveats": ["Moderator adapter failed; decision requires rerun or user review."],
        "summary": f"Moderator adapter failed: {public_adapter_error(error)}",
        "tasks": {},
        "status": "degraded",
        "fallback": "moderator_adapter_error",
    }


def public_adapter_error(error: Exception) -> str:
    returncode = getattr(error, "returncode", None)
    timed_out = bool(getattr(error, "timed_out", False))
    if returncode is not None:
        status = "timed out" if timed_out else f"returned {returncode}"
        return f"{error.__class__.__name__} {status}"
    return error.__class__.__name__
