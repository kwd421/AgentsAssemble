from __future__ import annotations

from typing import Any


def build_delegate_packet(meeting: dict[str, Any], role: dict[str, Any]) -> dict[str, Any]:
    role_id = role["id"]
    isolation = meeting.get("isolation", {}).get(role_id, {})
    binding = isolation.get("agent_binding", {})
    provider = isolation.get("provider", {})
    permissions = isolation.get("permissions", {})
    return {
        "meeting_id": meeting["meeting_id"],
        "question": meeting.get("display_question") or meeting.get("question", ""),
        "topic": meeting.get("display_topic") or meeting.get("topic", ""),
        "role": {
            "id": role_id,
            "display_name": role.get("display_name", role_id),
            "lens": role.get("lens", ""),
            "research_focus": role.get("research_focus", ""),
        },
        "persona": {
            "display_name": role.get("display_name", role_id),
            "personality": role.get("personality") or {},
            "source_preferences": role.get("source_preferences") or [],
            "continuity_rule": "Keep the role identity stable, but let conclusions change only when evidence justifies it.",
        },
        "memory": {
            "memory_summary": _memory_summary(meeting, role_id),
            "recent_episodes": meeting.get("memory_context", {}).get("recent_episodes", []),
        },
        "stance": {
            "current_stance": "uncommitted_before_meeting",
            "change_rule": "Do not concede for social harmony. Revise only for specific stronger evidence.",
            "must_explain": ["what you argued", "what changed your mind", "what remains unresolved"],
        },
        "meeting_template": meeting.get("meeting_template", {}),
        "decision_gate": meeting.get("decision_gate", {"status": "unknown", "reasons": []}),
        "provider": provider,
        "agent_binding": binding,
        "permissions": _meeting_permissions(permissions),
        "return_schema": {
            "artifact": f"return_packets/{role_id}.json",
            "required_fields": [
                "decision",
                "decision_status",
                "stance",
                "research_status",
                "next_task",
                "handoff_checklist",
            ],
        },
        "provenance": {
            "source": "AgentsAssemble delegate packet v0",
            "agent_config_source": meeting.get("agent_config_source"),
            "follow_up": meeting.get("follow_up", {"parent_meeting_id": None, "note": None}),
        },
    }


def render_delegate_packet_markdown(packet: dict[str, Any]) -> str:
    role = packet["role"]
    lines = [
        f"# Delegate Packet: {role['display_name']}",
        "",
        f"- Meeting: {packet['meeting_id']}",
        f"- Topic: {packet['topic']}",
        f"- Question: {packet['question']}",
        f"- Lens: {role.get('lens', '')}",
        f"- Focus: {role.get('research_focus', '')}",
        f"- Provider: {packet.get('provider', {}).get('display_name', 'unknown')}",
        f"- Permissions: {packet.get('permissions', {}).get('mode', 'unknown')}",
        "",
        "## Persona",
        "",
        f"- Continuity: {packet['persona']['continuity_rule']}",
        f"- Personality: {packet['persona'].get('personality', {})}",
        f"- Source preferences: {packet['persona'].get('source_preferences', [])}",
        "",
        "## Memory",
        "",
        packet["memory"]["memory_summary"],
        "",
        "## Stance Rule",
        "",
        f"- Current stance: {packet['stance']['current_stance']}",
        f"- Change rule: {packet['stance']['change_rule']}",
        "",
        "## Return",
        "",
        f"- Return artifact: {packet['return_schema']['artifact']}",
    ]
    return "\n".join(lines) + "\n"


def _memory_summary(meeting: dict[str, Any], role_id: str) -> str:
    agent_memories = meeting.get("memory_context", {}).get("agent_memories", {})
    memory = agent_memories.get(role_id)
    if memory:
        return str(memory)
    return "No prior agent memory was loaded for this role."


def _meeting_permissions(permissions: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "meeting_read_only",
        "meeting_read": bool(permissions.get("meeting_read", True)),
        "lobby_chat": bool(permissions.get("lobby_chat", True)),
        "official_turn": bool(permissions.get("official_turn", True)),
        "filesystem_read": False,
        "filesystem_write": False,
        "git_write": False,
        "push": False,
        "secrets": False,
        "implementation": False,
    }
