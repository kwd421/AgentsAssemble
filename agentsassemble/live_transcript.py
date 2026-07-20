from __future__ import annotations

from pathlib import Path
from typing import Any

from agentsassemble.legacy.meeting.core.events import read_live_events


OFFICIAL_TRANSCRIPT_KINDS = {"message", "synthesis", "promoted_context"}


def official_live_transcript_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        event
        for event in events
        if event.get("official_record") is True
        and event.get("channel") == "official"
        and str(event.get("kind") or "") in OFFICIAL_TRANSCRIPT_KINDS
        and str(event.get("content") or "").strip()
    ]


def render_live_transcript(events: list[dict[str, object]], meeting: dict[str, Any] | None = None) -> str:
    official_events = official_live_transcript_events(events)
    meeting = meeting or {}
    lines = [
        "# Transcript",
        "",
        "Informal lobby and side chat are excluded from this official transcript unless explicitly promoted.",
        "",
        "Projected from official live events for a running meeting. Decision artifacts are not synthesized by this projection.",
        "",
    ]
    meeting_id = str(meeting.get("meeting_id") or "").strip()
    topic = str(meeting.get("topic") or meeting.get("question") or "").strip()
    if meeting_id:
        lines.append(f"Meeting id: {meeting_id}")
    if topic:
        lines.append(f"Topic: {topic}")
    if meeting_id or topic:
        lines.append("")
    if not official_events:
        lines.extend(["No official live events have been recorded yet.", ""])
        return "\n".join(lines)
    for event in official_events:
        lines.extend([f"## {_event_heading(event)}", ""])
        lines.extend(_event_metadata_lines(event))
        content = str(event.get("content") or "").strip()
        if content:
            lines.extend(["", content, ""])
        else:
            lines.extend(["", "_No content recorded._", ""])
    return "\n".join(lines)


def projected_live_transcript_text(meeting_dir: Path, meeting: dict[str, Any] | None = None) -> str:
    events = read_live_events(meeting_dir, limit=None)
    if not official_live_transcript_events(events):
        return ""
    return render_live_transcript(events, meeting=meeting)


def _event_heading(event: dict[str, object]) -> str:
    kind = str(event.get("kind") or "")
    display_name = str(event.get("display_name") or "").strip()
    if display_name:
        return display_name
    if kind == "synthesis":
        return "Moderator Synthesis"
    if kind == "promoted_context":
        return "Promoted Lobby Context"
    return (
        str(event.get("role_id") or "").strip()
        or str(event.get("actor_id") or "").strip()
        or "Unknown Speaker"
    )


def _event_metadata_lines(event: dict[str, object]) -> list[str]:
    fields = [
        ("Event id", "id"),
        ("Created at", "created_at"),
        ("Actor id", "actor_id"),
        ("Role id", "role_id"),
        ("Turn id", "turn_id"),
        ("Turn index", "turn_index"),
        ("Source event id", "source_event_id"),
        ("Promoted from", "promoted_from"),
        ("Promoted reason", "promoted_reason"),
    ]
    lines = []
    for label, key in fields:
        value = event.get(key)
        if value is None or value == "":
            continue
        lines.append(f"- {label}: {value}")
    return lines
