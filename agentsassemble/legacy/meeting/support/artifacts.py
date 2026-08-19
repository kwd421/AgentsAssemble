from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import Any

from agentsassemble.legacy.meeting.support.artifact_packets import (
    build_return_packet,
    render_return_packet_markdown,
)
from agentsassemble.legacy.meeting.support.persona_artifact_contract import (
    apply_persona_artifact_contract_report,
)
from agentsassemble.legacy.meeting.support.artifact_public import (
    render_agenda,
    render_decision,
    render_transcript,
)
from agentsassemble.legacy.meeting.support.delegate_packets import (
    build_delegate_packet,
    render_delegate_packet_markdown,
)
from agentsassemble.models import Role
from agentsassemble.legacy.meeting.support.task_scope_report import (
    write_task_scope_report,
)


MAX_ARTIFACT_COMPONENT_LENGTH = 128


def _safe_artifact_component(value: object, *, field: str) -> str:
    """Validate an untrusted identifier before using it as a path component."""

    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field} must not be empty or padded with whitespace")
    if len(value) > MAX_ARTIFACT_COMPONENT_LENGTH:
        raise ValueError(f"{field} exceeds the artifact path component limit")
    if value in {".", ".."}:
        raise ValueError(f"{field} must not be a relative path segment")
    if any(character in value for character in ("/", "\\", "\x00", ":")):
        raise ValueError(f"{field} must be a single safe path component")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} contains control characters")
    if value.endswith((".", " ")) or PureWindowsPath(value).is_reserved():
        raise ValueError(f"{field} is not portable across supported platforms")
    return value


def _safe_artifact_path(root: Path, *parts: str) -> Path:
    """Resolve an artifact path and prove it remains below ``root``."""

    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("artifact path escapes its assigned directory") from error
    return candidate


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_role_files(meeting_dir: Path, role: Role) -> None:
    role_id = _safe_artifact_component(role.id, field="role.id")
    role_dir = _safe_artifact_path(meeting_dir, "roles", role_id)
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "role.md").write_text(
        f"# {role.display_name}\n\nLens: {role.lens}\n\nFocus: {role.research_focus}\n\n"
        f"Source preferences: {role.source_preferences or []}\n",
        encoding="utf-8",
    )
    (role_dir / "persona.md").write_text(
        f"# Persona\n\nMaintain the {role.display_name} perspective without converging into the other roles.\n\n"
        f"{role.personality or {}}\n",
        encoding="utf-8",
    )
    memory_root = meeting_dir.parent.parent / "memory" / "agents"
    memory = _safe_artifact_path(memory_root, f"{role_id}.md")
    (role_dir / "memory.md").write_text(
        (
            memory.read_text(encoding="utf-8")
            if memory.exists()
            else "# Memory\n\nNo prior meetings recorded yet.\n"
        ),
        encoding="utf-8",
    )
    (role_dir / "history.jsonl").write_text("", encoding="utf-8")


def write_research(meeting_dir: Path, research: dict[str, Any]) -> None:
    role_id = _safe_artifact_component(
        research.get("role_id"),
        field="research.role_id",
    )
    research_dir = _safe_artifact_path(
        meeting_dir,
        "private_research",
        role_id,
    )
    research_dir.mkdir(parents=True, exist_ok=True)
    write_json(research_dir / "research.json", research)

    depth = research.get("research_depth", {})
    lines = [
        f"# Research: {research['display_name']}",
        "",
        "## Depth",
        f"- Name: {depth.get('name', 'unknown')}",
        f"- Target sources: {depth.get('target_sources', 'unknown')}",
        f"- Minimum claims: {depth.get('min_claims', 'unknown')}",
        f"- Minimum counterclaims: {depth.get('min_counterclaims', 'unknown')}",
        "",
        "## Queries",
        *[f"- {query}" for query in research["queries"]],
        "",
        "## Sources",
    ]
    for source in research["sources"]:
        lines.extend(
            [
                f"- {source['url']}",
                f"  - Title: {source.get('title', '')}",
                f"  - Type: {source.get('source_type', 'unknown')}",
                f"  - Quality: {source.get('quality', 'unknown')}",
                f"  - Note: {source.get('note', '')}",
                f"  - Snippet: {source.get('snippet', '')}",
            ]
        )
        for note in source.get("extracted_notes", []):
            lines.append(f"  - Extracted: {note}")
    lines.extend(
        [
            "",
            "## Summary",
            research["summary"],
            "",
            "## Confidence",
            research["confidence"],
            "",
            "## Uncertainty",
            research["uncertainty"],
            "",
            "## Coverage Gaps",
            *[f"- {gap}" for gap in research.get("coverage_gaps", [])],
            "",
            "## Evidence Gate",
            f"- Status: {research.get('evidence_gate', {}).get('status', 'unknown')}",
            f"- Supported claims: {research.get('evidence_gate', {}).get('supported_claim_count', 0)}",
            f"- Unsupported claims: {research.get('evidence_gate', {}).get('unsupported_claim_count', 0)}",
            f"- Weak claims: {research.get('evidence_gate', {}).get('weak_claim_count', 0)}",
            f"- Verifier rejected claims: {research.get('evidence_gate', {}).get('verifier_rejected_claim_count', 0)}",
            f"- Confidence after gate: {research.get('evidence_gate', {}).get('confidence_after', research.get('confidence', 'low'))}",
            "",
            "## Claim Evidence",
        ]
    )
    for claim in research.get("claim_evidence", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Confidence: {claim.get('confidence', '')}",
                f"  - Source quality: {claim.get('source_quality', '')}",
                f"  - Interpretation: {claim.get('interpretation', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Claim Verification"])
    for record in research.get("claim_verification", []):
        lines.extend(
            [
                f"- Claim: {record.get('claim', '')}",
                f"  - URL: {record.get('url', '')}",
                f"  - Verdict: {record.get('verdict', '')}",
                f"  - Reason: {record.get('reason', '')}",
                f"  - Source quality: {record.get('source_quality', '')}",
                f"  - Source type: {record.get('source_type', '')}",
            ]
        )
    lines.extend(["", "## Weak Claims"])
    for claim in research.get("weak_claims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Reason: {claim.get('reason', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Verifier Rejected Claims"])
    for claim in research.get("verifier_rejected_claims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Reason: {claim.get('reason', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Unsupported Claims"])
    for claim in research.get("unsupported_claims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Reason: {claim.get('reason', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Counterclaims"])
    for claim in research.get("counterclaims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Confidence: {claim.get('confidence', '')}",
                f"  - Why it matters: {claim.get('why_it_matters', '')}",
            ]
        )
        for url in claim.get("evidence", []):
            lines.append(f"  - Evidence: {url}")
    lines.extend(["", "## Rejected Claims"])
    for claim in research.get("rejected_claims", []):
        lines.extend(
            [
                f"- Claim: {claim.get('claim', '')}",
                f"  - Reason: {claim.get('reason', '')}",
            ]
        )
        for url in claim.get("sources", []):
            lines.append(f"  - Source: {url}")
    (research_dir / "research.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def write_agenda(meeting_dir: Path, meeting: dict[str, Any]) -> None:
    (meeting_dir / "agenda.md").write_text(
        render_agenda(meeting),
        encoding="utf-8",
    )


def write_room_log(meeting_dir: Path, meeting: dict[str, Any]) -> None:
    lines = [
        "# Room Log",
        "",
        "This is an informal free-chat record. It is not an official transcript, decision, evidence record, or task assignment.",
        "",
    ]
    for message in meeting.get("room_chat", []):
        lines.extend(
            [
                f"## {message.get('display_name', message.get('role_id', 'Unknown'))}",
                "",
                str(message.get("content", "")).strip(),
                "",
            ]
        )
    (meeting_dir / "room-log.md").write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )


def write_room_artifacts(meeting_dir: Path, meeting: dict[str, Any]) -> None:
    write_agenda(meeting_dir, meeting)
    write_room_log(meeting_dir, meeting)
    write_json(meeting_dir / "meeting.json", meeting)


def write_public_artifacts(
    meeting_dir: Path,
    meeting: dict[str, Any],
    *,
    transcript_text: str | None = None,
) -> None:
    write_agenda(meeting_dir, meeting)
    (meeting_dir / "transcript.md").write_text(
        (
            transcript_text
            if transcript_text is not None
            else render_transcript(meeting)
        ),
        encoding="utf-8",
    )
    (meeting_dir / "decision.md").write_text(
        render_decision(meeting),
        encoding="utf-8",
    )

    tasks_dir = _safe_artifact_path(meeting_dir, "tasks")
    tasks_dir.mkdir(exist_ok=True)
    synthesis = meeting["moderator_synthesis"]
    for raw_role_id, task in synthesis["tasks"].items():
        role_id = _safe_artifact_component(
            raw_role_id,
            field="moderator_synthesis.tasks role_id",
        )
        task_path = _safe_artifact_path(tasks_dir, f"{role_id}.md")
        task_path.write_text(f"# Task\n\n{task}\n", encoding="utf-8")

    write_task_scope_report(meeting_dir, meeting)

    delegate_packet_dir = _safe_artifact_path(meeting_dir, "delegate_packets")
    delegate_packet_dir.mkdir(exist_ok=True)
    meeting["artifacts"]["delegate_packets"] = "delegate_packets/"
    meeting["delegate_packets"] = {}
    for role in meeting.get("roles", []):
        packet = build_delegate_packet(meeting, role)
        role_id = _safe_artifact_component(
            role.get("id"),
            field="role.id",
        )
        meeting["delegate_packets"][role_id] = {
            "json": f"delegate_packets/{role_id}.json",
            "markdown": f"delegate_packets/{role_id}.md",
        }
        write_json(
            _safe_artifact_path(delegate_packet_dir, f"{role_id}.json"),
            packet,
        )
        _safe_artifact_path(
            delegate_packet_dir,
            f"{role_id}.md",
        ).write_text(
            render_delegate_packet_markdown(packet),
            encoding="utf-8",
        )

    return_packet_dir = _safe_artifact_path(meeting_dir, "return_packets")
    return_packet_dir.mkdir(exist_ok=True)
    meeting["artifacts"]["return_packets"] = "return_packets/"
    meeting["return_packets"] = {}
    for role in meeting.get("roles", []):
        packet = build_return_packet(meeting, role)
        role_id = _safe_artifact_component(
            role.get("id"),
            field="role.id",
        )
        meeting["return_packets"][role_id] = {
            "json": f"return_packets/{role_id}.json",
            "markdown": f"return_packets/{role_id}.md",
        }
        write_json(
            _safe_artifact_path(return_packet_dir, f"{role_id}.json"),
            packet,
        )
        _safe_artifact_path(
            return_packet_dir,
            f"{role_id}.md",
        ).write_text(
            render_return_packet_markdown(packet),
            encoding="utf-8",
        )

    apply_persona_artifact_contract_report(meeting_dir, meeting)
    write_json(meeting_dir / "meeting.json", meeting)
