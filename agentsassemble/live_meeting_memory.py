from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from threading import Lock

from agentsassemble.live_transcript import official_live_transcript_events
from agentsassemble.meeting_events import clean_lobby_text, read_live_events


ROLLING_SUMMARY_LIMIT = 20
MEMORY_ITEM_LIMIT = 50
SUMMARY_TEXT_LIMIT = 360
MEMORY_TEXT_LIMIT = 280
ROOM_MEMORY_ROLLING_LIMIT = 6
ROOM_MEMORY_ITEM_LIMIT = 8
ROOM_MEMORY_TEXT_LIMIT = 220
SHARED_MEMORY_ARTIFACTS = {
    "rolling_summary": "shared_memory/rolling-summary.md",
    "open_questions": "shared_memory/open-questions.md",
    "action_items": "shared_memory/action-items.md",
    "index": "shared_memory/index.json",
}
LIVE_MEMORY_CONTEXT_CACHE_LIMIT = 32
_LIVE_MEMORY_CONTEXT_CACHE: dict[tuple[str, int, int, str, str], dict[str, object]] = {}
_LIVE_MEMORY_CONTEXT_CACHE_LOCK = Lock()


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


def load_live_meeting_memory_context(
    meeting_dir: Path,
    meeting: dict[str, object] | None = None,
) -> dict[str, object]:
    index_memory: dict[str, object] = {}
    index_path = meeting_dir / "shared_memory" / "index.json"
    if index_path.exists():
        try:
            memory = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            memory = {}
        if isinstance(memory, dict):
            index_memory = compact_live_meeting_memory(memory)
    current_memory = _current_live_meeting_memory_context(meeting_dir, meeting=meeting)
    if current_memory:
        return current_memory
    if index_memory:
        return index_memory
    if isinstance(meeting, dict):
        embedded = meeting.get("shared_memory")
        if isinstance(embedded, dict):
            return compact_live_meeting_memory(embedded)
    return {}


def compact_live_meeting_memory(memory: dict[str, object]) -> dict[str, object]:
    official_count = _nonnegative_int(memory.get("official_event_count"))
    if official_count <= 0:
        return {}
    return {
        "source": clean_lobby_text(memory.get("source") or "official_live_events", limit=64),
        "meeting_id": clean_lobby_text(memory.get("meeting_id"), limit=128),
        "topic": clean_lobby_text(memory.get("topic"), limit=240),
        "official_event_count": official_count,
        "official_message_count": _nonnegative_int(memory.get("official_message_count")),
        "official_synthesis_count": _nonnegative_int(memory.get("official_synthesis_count")),
        "last_official_event_id": clean_lobby_text(memory.get("last_official_event_id"), limit=128),
        "rolling_summary": _compact_summary_items(memory.get("rolling_summary")),
        "decisions": _compact_memory_items(memory.get("decisions")),
        "open_questions": _compact_memory_items(memory.get("open_questions")),
        "action_items": _compact_memory_items(memory.get("action_items")),
    }


def _current_live_meeting_memory_context(
    meeting_dir: Path,
    meeting: dict[str, object] | None = None,
) -> dict[str, object]:
    live_events_path = meeting_dir / "live_events.jsonl"
    try:
        stat = live_events_path.stat()
    except OSError:
        return {}
    cache_key = (
        str(live_events_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        *_meeting_memory_signature(meeting),
    )
    with _LIVE_MEMORY_CONTEXT_CACHE_LOCK:
        cached = _LIVE_MEMORY_CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return _copy_memory(cached)
    try:
        events = read_live_events(meeting_dir, limit=None)
    except OSError:
        return {}
    if not official_live_transcript_events(events):
        _cache_live_memory_context(cache_key, {})
        return {}
    memory = compact_live_meeting_memory(build_live_meeting_memory(events, meeting=meeting))
    _cache_live_memory_context(cache_key, memory)
    return _copy_memory(memory)


def _cache_live_memory_context(cache_key: tuple[str, int, int, str, str], memory: dict[str, object]) -> None:
    with _LIVE_MEMORY_CONTEXT_CACHE_LOCK:
        _LIVE_MEMORY_CONTEXT_CACHE[cache_key] = _copy_memory(memory)
        while len(_LIVE_MEMORY_CONTEXT_CACHE) > LIVE_MEMORY_CONTEXT_CACHE_LIMIT:
            oldest_key = next(iter(_LIVE_MEMORY_CONTEXT_CACHE))
            _LIVE_MEMORY_CONTEXT_CACHE.pop(oldest_key, None)


def _copy_memory(memory: dict[str, object]) -> dict[str, object]:
    return deepcopy(memory)


def _meeting_memory_signature(meeting: dict[str, object] | None) -> tuple[str, str]:
    if not isinstance(meeting, dict):
        return ("", "")
    return (
        clean_lobby_text(meeting.get("meeting_id"), limit=128),
        clean_lobby_text(meeting.get("display_topic") or meeting.get("topic") or meeting.get("question"), limit=240),
    )


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


def _compact_summary_items(value: object) -> list[dict[str, object]]:
    items = value if isinstance(value, list) else []
    compacted = []
    for item in items[-ROOM_MEMORY_ROLLING_LIMIT:]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "event_id": clean_lobby_text(item.get("event_id"), limit=128),
                "speaker": clean_lobby_text(item.get("speaker"), limit=128) or "Unknown Speaker",
                "summary": _compact_text(item.get("summary"), limit=ROOM_MEMORY_TEXT_LIMIT),
            }
        )
    return compacted


def _compact_memory_items(value: object) -> list[dict[str, object]]:
    items = value if isinstance(value, list) else []
    compacted = []
    for item in items[-ROOM_MEMORY_ITEM_LIMIT:]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "event_id": clean_lobby_text(item.get("event_id"), limit=128),
                "speaker": clean_lobby_text(item.get("speaker"), limit=128) or "Unknown Speaker",
                "text": _compact_text(item.get("text"), limit=ROOM_MEMORY_TEXT_LIMIT),
            }
        )
    return compacted


def _nonnegative_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


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
