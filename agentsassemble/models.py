from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Role:
    id: str
    display_name: str
    lens: str
    research_focus: str
    personality: dict[str, object] | None = None
    source_preferences: list[str] | None = None


@dataclass(frozen=True)
class MeetingResult:
    meeting_id: str
    meeting_dir: Path


@dataclass(frozen=True)
class CouncilConfig:
    topic: str
    display_topic: str
    question: str
    display_question: str
    roles: list[Role]
