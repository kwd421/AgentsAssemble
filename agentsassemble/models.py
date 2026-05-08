from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ResearchDepthName = Literal["smoke", "standard", "deep"]
ResearchStance = Literal["open", "user_leaning"]


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


@dataclass(frozen=True)
class ResearchDepth:
    name: ResearchDepthName
    label: str
    min_sources: int
    target_sources: int
    min_queries: int
    min_claims: int
    min_counterclaims: int
    notes_per_source: int
    source_mix: str
    instructions: str


@dataclass(frozen=True)
class ResearchSteering:
    stance: ResearchStance = "open"
    prompt: str | None = None

    @property
    def is_open(self) -> bool:
        return self.stance == "open" or not self.prompt

    def to_dict(self) -> dict[str, str | None]:
        return {"stance": self.stance, "prompt": self.prompt}


@dataclass(frozen=True)
class MeetingRound:
    id: str
    title: str
    report_label: str
    instruction: str
    context_scope: Literal["own_research", "public_debate"]


RESEARCH_DEPTHS: dict[ResearchDepthName, ResearchDepth] = {
    "smoke": ResearchDepth(
        name="smoke",
        label="Smoke",
        min_sources=5,
        target_sources=8,
        min_queries=3,
        min_claims=3,
        min_counterclaims=1,
        notes_per_source=1,
        source_mix="Use enough sources to prove the meeting pipeline works.",
        instructions=(
            "Fast pass. Find a small but varied source set, capture the most important claims, "
            "and clearly mark uncertainty. Prefer speed over exhaustive coverage."
        ),
    ),
    "standard": ResearchDepth(
        name="standard",
        label="Standard",
        min_sources=12,
        target_sources=20,
        min_queries=6,
        min_claims=6,
        min_counterclaims=3,
        notes_per_source=2,
        source_mix=(
            "Use a balanced set of authoritative, role-preferred, contradictory, and context sources. "
            "Avoid relying on one wiki or one community thread cluster."
        ),
        instructions=(
            "Usable council research. Build a claim table, include counterevidence, separate direct evidence "
            "from interpretation, and explain why weaker sources were still useful or rejected."
        ),
    ),
    "deep": ResearchDepth(
        name="deep",
        label="Deep",
        min_sources=30,
        target_sources=45,
        min_queries=12,
        min_claims=12,
        min_counterclaims=6,
        notes_per_source=3,
        source_mix=(
            "Use a dense source mix: primary/official sources where available, chapter or event references, "
            "reputable summaries, role-preferred communities, dissenting takes, and sources that should be rejected. "
            "Act like a long-form Extended Pro research session, not a quick search."
        ),
        instructions=(
            "Deep research. Iterate search queries, follow source trails, collect enough evidence to challenge your "
            "own conclusion, map every major claim to specific URLs, preserve rejected claims, and make uncertainty "
            "auditable. If the target count is not reachable within the tool limit, state exactly what was missing."
        ),
    ),
}


def get_research_depth(name: str) -> ResearchDepth:
    try:
        return RESEARCH_DEPTHS[name]  # type: ignore[index]
    except KeyError as error:
        allowed = ", ".join(RESEARCH_DEPTHS)
        raise ValueError(f"Unknown research depth: {name}. Expected one of: {allowed}") from error
