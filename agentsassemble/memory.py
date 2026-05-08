from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.models import Role


def load_memory_context(output_root: Path, roles: list[Role]) -> dict[str, Any]:
    memory_root = output_root / "memory"
    return {
        "project_memory": _read_optional(memory_root / "project.md"),
        "agent_memories": {
            role.id: _read_optional(memory_root / "agents" / f"{role.id}.md") for role in roles
        },
        "recent_episodes": _read_recent_jsonl(memory_root / "episodes.jsonl", limit=5),
    }


def write_memory_artifacts(output_root: Path, meeting: dict[str, Any]) -> dict[str, str]:
    memory_root = output_root / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    (memory_root / "agents").mkdir(exist_ok=True)
    (memory_root / "reflections").mkdir(exist_ok=True)

    project_path = memory_root / "project.md"
    episode_path = memory_root / "episodes.jsonl"
    reflection_path = memory_root / "reflections" / f"{meeting['meeting_id']}.md"

    _append_section(project_path, _project_memory_section(meeting))
    for role in meeting.get("roles", []):
        _append_section(memory_root / "agents" / f"{role['id']}.md", _agent_memory_section(meeting, role))
    _append_jsonl(episode_path, _episode_record(meeting))
    reflection_path.write_text(_reflection_document(meeting), encoding="utf-8")

    return {
        "project": "memory/project.md",
        "agents": "memory/agents/",
        "episodes": "memory/episodes.jsonl",
        "reflection": f"memory/reflections/{meeting['meeting_id']}.md",
    }


def _project_memory_section(meeting: dict[str, Any]) -> str:
    synthesis = meeting.get("moderator_synthesis", {})
    gate = meeting.get("evidence_gate", {})
    return "\n".join(
        [
            f"## Meeting {meeting['meeting_id']}",
            "",
            f"- Topic: {meeting.get('display_topic') or meeting.get('topic', '')}",
            f"- Question: {meeting.get('display_question') or meeting.get('question', '')}",
            f"- Decision: {synthesis.get('winner', 'Undetermined')}",
            f"- Confidence: {synthesis.get('confidence', 'low')}",
            f"- Evidence gate: {gate.get('status', 'unknown')} "
            f"({gate.get('total_supported_claims', 0)} supported, "
            f"{gate.get('total_unsupported_claims', 0)} unsupported)",
            f"- Research depth: {meeting.get('research_depth', {}).get('name', 'unknown')}",
            f"- Research steering: {meeting.get('research_steering', {}).get('prompt') or 'open'}",
            "",
            "### Rationale",
            "",
            synthesis.get("summary", ""),
            "",
        ]
    )


def _agent_memory_section(meeting: dict[str, Any], role: dict[str, Any]) -> str:
    role_id = role["id"]
    synthesis = meeting.get("moderator_synthesis", {})
    research_summary = _research_summary_for_role(meeting, role_id)
    role_messages = _messages_for_role(meeting, role_id)
    task = synthesis.get("tasks", {}).get(role_id, "No task assigned.")
    return "\n".join(
        [
            f"## Meeting {meeting['meeting_id']}",
            "",
            f"- Role: {role.get('display_name', role_id)}",
            f"- Lens: {role.get('lens', '')}",
            f"- Decision: {synthesis.get('winner', 'Undetermined')}",
            f"- Task: {task}",
            "",
            "### Research Takeaway",
            "",
            research_summary,
            "",
            "### Public Contributions",
            "",
            *[f"- {message}" for message in role_messages],
            "",
        ]
    )


def _reflection_document(meeting: dict[str, Any]) -> str:
    synthesis = meeting.get("moderator_synthesis", {})
    gate = meeting.get("evidence_gate", {})
    caveats = synthesis.get("caveats", [])
    return "\n".join(
        [
            f"# Reflection: {meeting['meeting_id']}",
            "",
            "## Decision",
            "",
            f"- Winner: {synthesis.get('winner', 'Undetermined')}",
            f"- Confidence: {synthesis.get('confidence', 'low')}",
            f"- Evidence gate: {gate.get('status', 'unknown')}",
            "",
            "## Lessons",
            "",
            "- Preserve supported claims separately from unsupported claims.",
            "- Carry caveats and coverage gaps into future meetings.",
            "",
            "## Caveats To Remember",
            "",
            *[f"- {caveat}" for caveat in caveats],
            "",
            "## Next Tasks",
            "",
            *[f"- {role_id}: {task}" for role_id, task in synthesis.get("tasks", {}).items()],
            "",
        ]
    )


def _episode_record(meeting: dict[str, Any]) -> dict[str, Any]:
    synthesis = meeting.get("moderator_synthesis", {})
    return {
        "meeting_id": meeting["meeting_id"],
        "created_at": meeting.get("audit_metadata", {}).get("created_at") or datetime.now(UTC).isoformat(),
        "topic": meeting.get("topic", ""),
        "display_topic": meeting.get("display_topic", ""),
        "question": meeting.get("question", ""),
        "display_question": meeting.get("display_question", ""),
        "decision": synthesis.get("winner", "Undetermined"),
        "confidence": synthesis.get("confidence", "low"),
        "research_depth": meeting.get("research_depth", {}).get("name", "unknown"),
        "evidence_gate": meeting.get("evidence_gate", {}),
        "artifacts": meeting.get("artifacts", {}),
    }


def _research_summary_for_role(meeting: dict[str, Any], role_id: str) -> str:
    for summary in meeting.get("memory_input", {}).get("research_summaries", []):
        if summary.get("role_id") == role_id:
            return summary.get("summary", "")
    return "No research summary recorded."


def _messages_for_role(meeting: dict[str, Any], role_id: str) -> list[str]:
    messages = []
    for round_record in meeting.get("debate_rounds", []):
        for message in round_record.get("messages", []):
            if message.get("role_id") == role_id:
                messages.append(f"{round_record.get('title', message.get('round', 'Round'))}: {message.get('content', '')}")
    return messages


def _append_section(path: Path, section: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8").strip():
        prefix = path.read_text(encoding="utf-8").rstrip() + "\n\n"
    else:
        prefix = f"# {path.stem.replace('-', ' ').title()}\n\n"
    path.write_text(prefix + section.rstrip() + "\n", encoding="utf-8")


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_recent_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records[-limit:]
