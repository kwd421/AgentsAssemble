from __future__ import annotations

import json
from pathlib import Path

from agentsassemble.live_transcript import official_live_transcript_events
from agentsassemble.meeting_events import clean_lobby_text, read_live_events


ROLLING_SUMMARY_LIMIT = 20
MEMORY_ITEM_LIMIT = 50
SUMMARY_TEXT_LIMIT = 360
MEMORY_TEXT_LIMIT = 280
SHARED_MEMORY_ARTIFACTS = {
    "rolling_summary": "shared_memory/rolling-summary.md",
    "open_questions": "shared_memory/open-questions.md",
    "action_items": "shared_memory/action-items.md",
    "index": "shared_memory/index.json",
}


def build_live_meeting_memory(
    events: list[dict[str, object]],
    meeting: dict[str, object] | None = None,
) -> dict[str, object]:
    meeting = meeting or {}
    official_events = official_live_transcript_events(events)
    rolling_events = official_events[-ROLLING_SUMMARY_LIMIT:]
    return {
        "source": "official_live_events",
        "generated_at": _last_event_created_at(official_events),
        "meeting_id": clean_lobby_text(meeting.get("meeting_id"), limit=128),
        "topic": clean_lobby_text(meeting.get("display_topic") or meeting.get("topic") or meeting.get("question"), limit=240),
        "official_event_count": len(official_events),
        "official_message_count": sum(1 for event in official_events if str(event.get("kind") or "") == "message"),
        "official_synthesis_count": sum(1 for event in official_events if str(event.get("kind") or "") == "synthesis"),
        "last_official_event_id": _last_event_id(official_events),
        "artifact_dir": "shared_memory/",
        "artifacts": dict(SHARED_MEMORY_ARTIFACTS),
        "rolling_summary": [_summary_item(event) for event in rolling_events],
        "decisions": _extract_memory_items(official_events, kind="decision"),
        "open_questions": _extract_memory_items(official_events, kind="question"),
        "action_items": _extract_memory_items(official_events, kind="action"),
    }


def render_rolling_summary(memory: dict[str, object]) -> str:
    lines = [
        "# Rolling Summary",
        "",
        "Derived from official live events only. Lobby, side chat, review checkpoints, status events, and private turn requests are excluded.",
        "",
        f"Meeting id: {memory.get('meeting_id') or 'unknown'}",
        f"Topic: {memory.get('topic') or 'unknown'}",
        f"Official events: {memory.get('official_event_count') or 0}",
        f"Last official event id: {memory.get('last_official_event_id') or 'none'}",
        "",
        "## Recent Official Events",
        "",
    ]
    lines.extend(_render_summary_items(memory.get("rolling_summary")))
    lines.extend(["", "## Decisions", ""])
    lines.extend(_render_memory_items(memory.get("decisions")))
    return "\n".join(lines).rstrip() + "\n"


def render_open_questions(memory: dict[str, object]) -> str:
    lines = [
        "# Open Questions",
        "",
        "Questions are extracted only from explicit official markers or question-form official lines.",
        "",
    ]
    lines.extend(_render_memory_items(memory.get("open_questions")))
    return "\n".join(lines).rstrip() + "\n"


def render_action_items(memory: dict[str, object]) -> str:
    lines = [
        "# Action Items",
        "",
        "Action items are extracted only from explicit official action markers.",
        "",
    ]
    lines.extend(_render_memory_items(memory.get("action_items")))
    return "\n".join(lines).rstrip() + "\n"


def projected_live_meeting_memory_artifacts(
    meeting_dir: Path,
    meeting: dict[str, object] | None = None,
) -> dict[str, str]:
    events = read_live_events(meeting_dir, limit=None)
    if not official_live_transcript_events(events):
        return {}
    return rendered_live_meeting_memory_artifacts(build_live_meeting_memory(events, meeting=meeting))


def rendered_live_meeting_memory_artifacts(memory: dict[str, object]) -> dict[str, str]:
    return {
        "shared_memory/rolling-summary.md": render_rolling_summary(memory),
        "shared_memory/open-questions.md": render_open_questions(memory),
        "shared_memory/action-items.md": render_action_items(memory),
        "shared_memory/index.json": json.dumps(memory, ensure_ascii=False, indent=2) + "\n",
    }


def write_live_meeting_memory_artifacts(
    meeting_dir: Path,
    meeting: dict[str, object] | None = None,
) -> dict[str, object]:
    events = read_live_events(meeting_dir, limit=None)
    memory = build_live_meeting_memory(events, meeting=meeting)
    shared_dir = meeting_dir / "shared_memory"
    shared_dir.mkdir(parents=True, exist_ok=True)
    artifacts = rendered_live_meeting_memory_artifacts(memory)
    for relative_path, content in artifacts.items():
        output_path = meeting_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    return memory


def _summary_item(event: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": clean_lobby_text(event.get("id"), limit=128),
        "created_at": clean_lobby_text(event.get("created_at"), limit=128),
        "speaker": _speaker(event),
        "role_id": clean_lobby_text(event.get("role_id"), limit=128),
        "summary": _compact_text(event.get("content"), limit=SUMMARY_TEXT_LIMIT),
    }


def _extract_memory_items(events: list[dict[str, object]], *, kind: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for event in events:
        for line in _content_lines(event):
            text = _line_memory_text(line, kind=kind)
            if not text:
                continue
            items.append(
                {
                    "event_id": clean_lobby_text(event.get("id"), limit=128),
                    "speaker": _speaker(event),
                    "text": text,
                }
            )
            if len(items) >= MEMORY_ITEM_LIMIT:
                return items
    return items


def _line_memory_text(line: str, *, kind: str) -> str:
    stripped = line.strip()
    lowered = stripped.casefold()
    prefix_groups = {
        "decision": ("decision:", "decision -", "decided:", "결정:", "결정 -"),
        "question": ("question:", "question -", "open question:", "open question -", "q:", "q -", "질문:", "질문 -"),
        "action": ("action:", "action -", "action item:", "action item -", "todo:", "todo -", "next action:", "next action -", "작업:", "작업 -", "액션:", "액션 -"),
    }
    if kind == "action" and _is_checkbox_action(stripped):
        return _compact_text(stripped.split("]", 1)[1], limit=MEMORY_TEXT_LIMIT)
    for prefix in prefix_groups[kind]:
        if lowered.startswith(prefix):
            return _compact_text(stripped[len(prefix) :], limit=MEMORY_TEXT_LIMIT)
        marker_index = lowered.find(prefix)
        if marker_index > 0:
            return _compact_text(stripped[marker_index + len(prefix) :], limit=MEMORY_TEXT_LIMIT)
    if kind == "question" and stripped.endswith("?"):
        return _compact_text(stripped, limit=MEMORY_TEXT_LIMIT)
    return ""


def _content_lines(event: dict[str, object]) -> list[str]:
    text = str(event.get("content") or "")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _speaker(event: dict[str, object]) -> str:
    return (
        clean_lobby_text(event.get("display_name"), limit=128)
        or clean_lobby_text(event.get("role_id"), limit=128)
        or clean_lobby_text(event.get("actor_id"), limit=128)
        or "Unknown Speaker"
    )


def _last_event_id(events: list[dict[str, object]]) -> str:
    if not events:
        return ""
    return clean_lobby_text(events[-1].get("id"), limit=128)


def _last_event_created_at(events: list[dict[str, object]]) -> str:
    if not events:
        return ""
    return clean_lobby_text(events[-1].get("created_at"), limit=128)


def _compact_text(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return clean_lobby_text(text, limit=limit)


def _is_checkbox_action(value: str) -> bool:
    normalized = value.strip()
    return normalized.startswith("- [ ]") or normalized.startswith("* [ ]")


def _render_summary_items(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    if not items:
        return ["No official summary items yet."]
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        speaker = item.get("speaker") or "Unknown Speaker"
        event_id = item.get("event_id") or "unknown"
        summary = item.get("summary") or ""
        lines.append(f"- {speaker} ({event_id}): {summary}")
    return lines or ["No official summary items yet."]


def _render_memory_items(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    if not items:
        return ["- None recorded."]
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        speaker = item.get("speaker") or "Unknown Speaker"
        event_id = item.get("event_id") or "unknown"
        text = item.get("text") or ""
        lines.append(f"- {text} ({speaker}, {event_id})")
    return lines or ["- None recorded."]
