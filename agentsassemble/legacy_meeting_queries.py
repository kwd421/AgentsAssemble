from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy_meeting_records import (
    live_agent_admission_details,
    load_meeting_record,
    read_meeting_record,
    safe_meeting_dir,
)
from agentsassemble.live_agents import read_live_agents
from agentsassemble.live_meeting_memory import projected_live_meeting_memory_artifacts
from agentsassemble.live_transcript import projected_live_transcript_text
from agentsassemble.meeting_events import ROOM_TOPIC_LIMIT, clean_lobby_text, read_live_events
from agentsassemble.meeting_lifecycle import infer_live_status, project_meeting_lifecycle


TAB_LABELS = {"lobby": "로비", "live": "실황", "board": "작전판", "archive": "아카이브"}
TABS = ["lobby", "live", "board", "archive"]

WORKROOM_QUEUE_ARTIFACT_PATHS = (
    "transcript.md",
    "decision.md",
    "shared_memory/rolling-summary.md",
    "shared_memory/action-items.md",
    "shared_memory/open-questions.md",
)
WORKROOM_QUEUE_SCOPE_OVERLAP_LIMIT = 5
WORKROOM_QUEUE_SCOPE_SUMMARIES = {"scope_overlap_evidence", "no_obvious_overlaps"}
WORKROOM_QUEUE_SCOPE_KINDS = {"file", "dir"}
WORKROOM_QUEUE_SCOPE_UNSAFE_SEGMENT_MARKERS = (
    "authorization",
    "auth_ref",
    "api-key",
    "api_key",
    "apikey",
    "x-api-key",
    "bearer",
    "credential",
    "password",
    "secret",
    "session",
    "token",
    "cookie",
)

SAFE_MEETING_STREAM_EVENT_STRING_FIELDS = (
    "id",
    "event_id",
    "created_at",
    "kind",
    "meeting_id",
    "channel",
    "audience",
    "actor_id",
    "target_agent_id",
    "source_event_id",
    "role_id",
    "display_name",
    "artifact_kind",
    "round",
    "turn_id",
    "engagement_mode",
    "confidence",
    "retry_status",
)
SAFE_MEETING_STREAM_TEXT_FIELDS = (
    "content",
    "message",
    "summary",
    "position",
    "change_reason",
    "remaining_resistance",
)
PRIVATE_MEETING_STREAM_CHANNELS = {"review"}
PRIVATE_MEETING_STREAM_KINDS = {"live_agent_turn_request"}


class LegacyMeetingNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class LegacyMeetingQueryService:
    """Read-only authority for retained meeting, lifecycle, and workroom views."""

    output_root: Path

    def list(self, *, now: float | None = None) -> list[dict[str, object]]:
        return list_meetings(self.output_root, now=now)

    def latest(self, *, now: float | None = None) -> dict[str, object] | None:
        meetings = self.list(now=now)
        if not meetings:
            return None
        return build_meeting_payload(
            Path(str(meetings[0]["path"])),
            now=now,
            output_root=self.output_root,
        )

    def detail(self, meeting_id: str, *, now: float | None = None) -> dict[str, object]:
        meeting_dir = self.require_meeting_dir(meeting_id)
        return build_meeting_payload(meeting_dir, now=now, output_root=self.output_root)

    def lifecycle(self, meeting_id: str, *, now: float | None = None) -> dict[str, object]:
        meeting_dir = self.require_meeting_dir(meeting_id)
        try:
            meeting = read_meeting_record(meeting_dir)
        except (OSError, json.JSONDecodeError):
            meeting = {"meeting_id": meeting_id}
        return {
            "meeting_id": meeting_id,
            "lifecycle": project_meeting_lifecycle(
                meeting_dir,
                now=time.time() if now is None else now,
                live_agents=_lifecycle_live_agents_for_meeting(self.output_root, meeting),
            ),
        }

    def workroom_queue(self, meeting_id: str, *, now: float | None = None) -> dict[str, object]:
        meeting_dir = self.require_meeting_dir(meeting_id)
        return build_workroom_queue_payload(
            meeting_dir,
            now=time.time() if now is None else now,
            output_root=self.output_root,
        )

    def require_meeting_dir(self, meeting_id: str) -> Path:
        try:
            meeting_dir = safe_meeting_dir(self.output_root, meeting_id)
        except ValueError as error:
            raise LegacyMeetingNotFoundError(str(error)) from error
        if not meeting_dir.exists():
            raise LegacyMeetingNotFoundError("Meeting not found")
        return meeting_dir


def list_meetings(output_root: Path, now: float | None = None) -> list[dict[str, object]]:
    meetings_dir = output_root / "meetings"
    if not meetings_dir.exists():
        return []

    meetings = []
    for meeting_dir in meetings_dir.iterdir():
        record_path = meeting_dir / "meeting.json"
        live_path = meeting_dir / "live_state.json"
        if not record_path.exists() and not live_path.exists():
            continue
        try:
            meeting, source_path, has_final_record = load_meeting_record(meeting_dir)
        except json.JSONDecodeError:
            continue
        if _payload_bool(meeting.get("diagnostic")):
            continue
        meeting = infer_live_status(
            meeting,
            meeting_dir,
            has_final_record=has_final_record,
            now=now,
        )
        stat = source_path.stat()
        meetings.append(
            {
                "meeting_id": meeting.get("meeting_id", meeting_dir.name),
                "topic": meeting.get("topic", ""),
                "question": meeting.get("question", ""),
                "created_at": meeting.get("audit_metadata", {}).get("created_at", ""),
                "live_status": meeting.get("live_status", "complete" if record_path.exists() else "unknown"),
                "path": str(meeting_dir),
                "mtime": stat.st_mtime,
            }
        )
    return sorted(meetings, key=lambda item: item["mtime"], reverse=True)


def build_meeting_payload(
    meeting_dir: Path,
    now: float | None = None,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    meeting, _, has_final_record = load_meeting_record(meeting_dir)
    meeting = infer_live_status(
        meeting,
        meeting_dir,
        has_final_record=has_final_record,
        now=now,
    )
    lifecycle_live_agents = _lifecycle_live_agents_for_meeting(
        output_root or _output_root_for_meeting_dir(meeting_dir),
        meeting,
    )
    artifacts = {
        name: _read_optional(meeting_dir / name)
        for name in ("agenda.md", "transcript.md", "decision.md", "room-log.md", "meeting.json")
    }
    if not (meeting_dir / "transcript.md").exists() and not has_final_record:
        artifacts["transcript.md"] = projected_live_transcript_text(meeting_dir, meeting=meeting)
    artifacts.update(
        _shared_memory_artifacts(
            meeting_dir,
            meeting=meeting,
            has_final_record=has_final_record,
        )
    )
    tasks = {
        task_path.name: task_path.read_text(encoding="utf-8")
        for task_path in sorted((meeting_dir / "tasks").glob("*.md"))
    }
    return_packets = {
        packet_path.name: packet_path.read_text(encoding="utf-8")
        for packet_path in sorted((meeting_dir / "return_packets").glob("*.md"))
    }
    review_checkpoints = {
        checkpoint_path.name: checkpoint_path.read_text(encoding="utf-8")
        for checkpoint_path in sorted((meeting_dir / "review_checkpoints").glob("*.*"))
        if checkpoint_path.suffix in {".md", ".json"}
    }
    research: dict[str, str] = {}
    research_json: dict[str, object] = {}
    research_root = meeting_dir / "private_research"
    if research_root.exists():
        for research_path in sorted(research_root.glob("*/research.md")):
            research[f"{research_path.parent.name}/research.md"] = research_path.read_text(encoding="utf-8")
        for research_path in sorted(research_root.glob("*/research.json")):
            try:
                research_json[research_path.parent.name] = json.loads(research_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                research_json[research_path.parent.name] = {"error": "Research JSON could not be parsed."}
    return {
        "tabs": TABS,
        "tab_labels": TAB_LABELS,
        "meeting": meeting,
        "lifecycle": project_meeting_lifecycle(
            meeting_dir,
            now=now,
            live_agents=lifecycle_live_agents,
        ),
        "artifacts": artifacts,
        "tasks": tasks,
        "return_packets": return_packets,
        "review_checkpoints": review_checkpoints,
        "research": research,
        "research_json": research_json,
        "live_events": read_live_events(meeting_dir),
    }


def build_workroom_queue_payload(
    meeting_dir: Path,
    now: float | None = None,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    meeting, _, has_final_record = load_meeting_record(meeting_dir)
    meeting = infer_live_status(
        meeting,
        meeting_dir,
        has_final_record=has_final_record,
        now=now,
    )
    lifecycle_live_agents = _lifecycle_live_agents_for_meeting(
        output_root or _output_root_for_meeting_dir(meeting_dir),
        meeting,
    )
    return {
        "meeting_id": clean_lobby_text(meeting.get("meeting_id") or meeting_dir.name, limit=128),
        "lifecycle": project_meeting_lifecycle(
            meeting_dir,
            now=now,
            live_agents=lifecycle_live_agents,
        ),
        "artifacts": {
            path: {"available": _workroom_artifact_available(meeting_dir, path)}
            for path in WORKROOM_QUEUE_ARTIFACT_PATHS
        },
        "return_packets": {
            "count": _count_existing_files(meeting_dir / "return_packets", {".md"}),
        },
        "review_checkpoints": {
            "count": _count_existing_stems(meeting_dir / "review_checkpoints", {".md", ".json"}),
        },
        "task_scope": _workroom_task_scope_payload(meeting_dir, meeting),
    }


def project_meeting_stream_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    for event in events:
        safe_event = _project_meeting_stream_event(event)
        if safe_event is not None:
            projected.append(safe_event)
    return projected


def build_meeting_stream_payload(
    meeting_dir: Path,
    now: float | None = None,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    meeting, _, has_final_record = load_meeting_record(meeting_dir)
    meeting = infer_live_status(
        meeting,
        meeting_dir,
        has_final_record=has_final_record,
        now=now,
    )
    meeting_id = clean_lobby_text(meeting.get("meeting_id") or meeting_dir.name, limit=128)
    lifecycle_live_agents = _lifecycle_live_agents_for_meeting(
        output_root or _output_root_for_meeting_dir(meeting_dir),
        meeting,
    )
    return {
        "meeting": {
            "meeting_id": meeting_id,
            "topic": clean_lobby_text(meeting.get("topic"), limit=ROOM_TOPIC_LIMIT),
            "question": clean_lobby_text(meeting.get("question"), limit=ROOM_TOPIC_LIMIT),
            "live_status": clean_lobby_text(meeting.get("live_status"), limit=64),
        },
        "lifecycle": project_meeting_lifecycle(
            meeting_dir,
            now=now,
            live_agents=lifecycle_live_agents,
        ),
        "live_events": project_meeting_stream_events(read_live_events(meeting_dir)),
    }


def _workroom_artifact_available(meeting_dir: Path, artifact_path: str) -> bool:
    path = meeting_dir / artifact_path
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _count_existing_files(root: Path, suffixes: set[str]) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.iterdir() if path.is_file() and path.suffix in suffixes)


def _count_existing_stems(root: Path, suffixes: set[str]) -> int:
    if not root.exists():
        return 0
    return len(
        {
            path.stem
            for path in root.iterdir()
            if path.is_file() and path.suffix in suffixes
        }
    )


def _workroom_task_scope_payload(meeting_dir: Path, meeting: dict[str, object]) -> dict[str, object]:
    report = _read_workroom_task_scope_report(meeting_dir)
    summary_source = report if report is not None else meeting.get("task_scope_report")
    if not isinstance(summary_source, dict):
        summary_source = {}
    overlaps = _safe_workroom_task_scope_overlaps(
        report.get("overlaps") if isinstance(report, dict) else []
    )
    overlap_count = max(
        _safe_nonnegative_int(summary_source.get("overlap_count")),
        len(overlaps),
    )
    return {
        "available": bool(report or summary_source),
        "summary": _safe_workroom_task_scope_summary(summary_source.get("summary")),
        "overlap_count": overlap_count,
        "candidate_count_total": _safe_nonnegative_int(summary_source.get("candidate_count_total")),
        "overlaps": overlaps,
        "overlaps_truncated": bool(
            summary_source.get("overlaps_truncated")
            or (
                report
                and len(report.get("overlaps") if isinstance(report.get("overlaps"), list) else [])
                > len(overlaps)
            )
        ),
    }


def _read_workroom_task_scope_report(meeting_dir: Path) -> dict[str, object] | None:
    try:
        payload = json.loads((meeting_dir / "task_scope_report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_workroom_task_scope_summary(value: object) -> str:
    summary = str(value or "").strip()
    return summary if summary in WORKROOM_QUEUE_SCOPE_SUMMARIES else "unknown"


def _safe_workroom_task_scope_overlaps(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    overlaps: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        token = _safe_workroom_scope_token(item.get("token"))
        if kind not in WORKROOM_QUEUE_SCOPE_KINDS or not token:
            continue
        overlaps.append({"kind": kind, "token": token})
        if len(overlaps) >= WORKROOM_QUEUE_SCOPE_OVERLAP_LIMIT:
            break
    return overlaps


def _safe_workroom_scope_token(value: object) -> str:
    token = str(value or "").strip().strip("`'\"")
    if not token or len(token) > 160:
        return ""
    if token.startswith(("/", "~")) or "://" in token or "\\" in token:
        return ""
    segments = [segment for segment in token.split("/") if segment]
    if len(segments) < 2 or any(segment in {".", ".."} for segment in segments):
        return ""
    if any(_workroom_scope_segment_looks_sensitive(segment) for segment in segments):
        return ""
    first = segments[0].rstrip(".")
    if "." in first or ":" in token:
        return ""
    if token.endswith("/"):
        return token if all(re.fullmatch(r"[A-Za-z0-9._-]+", segment) for segment in segments) else ""
    if not re.fullmatch(r"(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,8}", token):
        return ""
    return token


def _workroom_scope_segment_looks_sensitive(segment: str) -> bool:
    lowered = segment.casefold()
    if segment.startswith((".", "-")) or "=" in segment:
        return True
    return any(marker in lowered for marker in WORKROOM_QUEUE_SCOPE_UNSAFE_SEGMENT_MARKERS)


def _safe_nonnegative_int(value: object) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(number):
        return 0
    try:
        return max(0, int(number))
    except (TypeError, ValueError, OverflowError):
        return 0


def _project_meeting_stream_event(event: dict[str, object]) -> dict[str, object] | None:
    kind = clean_lobby_text(event.get("kind"), limit=64)
    channel = clean_lobby_text(event.get("channel"), limit=32)
    audience = clean_lobby_text(event.get("audience"), limit=64)
    if channel in PRIVATE_MEETING_STREAM_CHANNELS:
        return None
    if kind in PRIVATE_MEETING_STREAM_KINDS or audience.startswith("agent:"):
        return None
    safe: dict[str, object] = {}
    for field in SAFE_MEETING_STREAM_EVENT_STRING_FIELDS:
        value = clean_lobby_text(event.get(field), limit=256)
        if value:
            safe[field] = value
    if isinstance(event.get("official_record"), bool):
        safe["official_record"] = event["official_record"]
    for field in ("turn_index", "retry_attempts"):
        value = event.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            safe[field] = value
    for field in ("artifact_path", "artifact_json_path"):
        value = _safe_meeting_stream_relative_path(event.get(field))
        if value:
            safe[field] = value
    for field in SAFE_MEETING_STREAM_TEXT_FIELDS:
        value = clean_lobby_text(event.get(field), limit=2000)
        if value:
            safe[field] = value
    return safe if safe.get("id") else None


def _safe_meeting_stream_relative_path(value: object) -> str:
    text = clean_lobby_text(value, limit=256)
    if not text:
        return ""
    if text.startswith(("/", "\\", "~")) or "\\" in text or ":" in text:
        return ""
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def _output_root_for_meeting_dir(meeting_dir: Path) -> Path | None:
    parent = meeting_dir.parent
    return parent.parent if parent.name == "meetings" else None


def _lifecycle_live_agents_for_meeting(
    output_root: Path | None,
    meeting: dict[str, object],
) -> list[dict[str, object]]:
    if output_root is None:
        return []
    meeting_id = clean_lobby_text(meeting.get("meeting_id"), limit=128)
    agents = []
    for agent in read_live_agents(output_root):
        agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
        agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
        if not meeting_id or agent_meeting_id != meeting_id:
            continue
        agents.append(
            {
                **agent,
                **live_agent_admission_details(meeting, agent, agent_id=agent_id),
            }
        )
    return agents


def _shared_memory_artifacts(
    meeting_dir: Path,
    *,
    meeting: dict[str, object],
    has_final_record: bool,
) -> dict[str, str]:
    shared_dir = meeting_dir / "shared_memory"
    artifact_paths = {
        "shared_memory/rolling-summary.md": shared_dir / "rolling-summary.md",
        "shared_memory/open-questions.md": shared_dir / "open-questions.md",
        "shared_memory/action-items.md": shared_dir / "action-items.md",
        "shared_memory/index.json": shared_dir / "index.json",
    }
    existing = {
        key: path.read_text(encoding="utf-8")
        for key, path in artifact_paths.items()
        if path.exists()
    }
    if existing or has_final_record:
        return existing
    return projected_live_meeting_memory_artifacts(meeting_dir, meeting=meeting)


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False
