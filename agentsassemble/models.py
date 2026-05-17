from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ResearchDepthName = Literal["smoke", "standard", "deep"]
ResearchStance = Literal["open", "user_leaning"]
EngagementMode = Literal["manual", "mentioned", "moderator_called", "human_only", "always", "watch"]
MeetingMode = Literal["debate", "free_chat"]
TurnSelection = Literal["all_roles", "selected_roles"]
ProviderKind = Literal[
    "mock",
    "codex",
    "codex_live_session",
    "anthropic",
    "gemini",
    "grok",
    "remote_http_bridge",
    "local_cli",
    "cursor",
    "claude_code",
    "local_openai_compatible",
    "hermes_memory",
    "openclaw_memory",
    "memory_pack",
]


@dataclass(frozen=True)
class Role:
    id: str
    display_name: str
    lens: str
    research_focus: str
    personality: dict[str, object] | None = None
    source_preferences: list[str] | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_research: bool
    supports_web_search: bool
    supports_tools: bool
    supports_filesystem: bool
    supports_session_resume: bool
    supports_structured_output: bool
    context_window: int | None = None
    cost_class: str = "unknown"

    def to_dict(self) -> dict[str, object]:
        return {
            "supports_research": self.supports_research,
            "supports_web_search": self.supports_web_search,
            "supports_tools": self.supports_tools,
            "supports_filesystem": self.supports_filesystem,
            "supports_session_resume": self.supports_session_resume,
            "supports_structured_output": self.supports_structured_output,
            "context_window": self.context_window,
            "cost_class": self.cost_class,
        }


@dataclass(frozen=True)
class PermissionProfile:
    id: str
    meeting_read: bool = True
    lobby_chat: bool = True
    official_turn: bool = True
    web_search: bool = False
    tool_use: bool = False
    filesystem_read: bool = False
    filesystem_write: bool = False
    git_write: bool = False
    push: bool = False
    secrets: bool = False
    implementation: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "meeting_read": self.meeting_read,
            "lobby_chat": self.lobby_chat,
            "official_turn": self.official_turn,
            "web_search": self.web_search,
            "tool_use": self.tool_use,
            "filesystem_read": self.filesystem_read,
            "filesystem_write": self.filesystem_write,
            "git_write": self.git_write,
            "push": self.push,
            "secrets": self.secrets,
            "implementation": self.implementation,
        }


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    kind: ProviderKind
    display_name: str
    default_model: str | None = None
    endpoint: str | None = None
    auth_ref: str | None = None
    timeout_seconds: int | None = None
    search_enabled: bool = False
    notes: str | None = None
    command: list[str] | None = None

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "display_name": self.display_name,
            "default_model": self.default_model,
            "endpoint": _public_endpoint(self.endpoint),
            "auth_ref": _public_auth_ref(self.auth_ref),
            "timeout_seconds": self.timeout_seconds,
            "search_enabled": self.search_enabled,
            "notes": _public_notes(self.notes),
            "command": ["<redacted>"] if self.command else None,
            "command_configured": bool(self.command),
        }


@dataclass(frozen=True)
class AgentBinding:
    agent_id: str
    role_id: str
    owner_id: str
    provider_id: str
    model_id: str | None
    permission_profile_id: str
    memory_profile_id: str | None = None
    join_mode: Literal["fresh", "current_session", "imported_pack"] = "fresh"
    engagement_mode: EngagementMode = "moderator_called"
    session_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "role_id": self.role_id,
            "owner_id": self.owner_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "permission_profile_id": self.permission_profile_id,
            "memory_profile_id": self.memory_profile_id,
            "join_mode": self.join_mode,
            "engagement_mode": self.engagement_mode,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class MeetingResult:
    meeting_id: str
    meeting_dir: Path


@dataclass(frozen=True)
class ModeratorConfig:
    enabled: bool = True

    def to_dict(self) -> dict[str, object]:
        return {"enabled": self.enabled}


@dataclass(frozen=True)
class CouncilConfig:
    topic: str
    display_topic: str
    question: str
    display_question: str
    roles: list[Role]
    meeting_template_id: str = "default"
    meeting_template_name: str = "Default"
    rounds: list["MeetingRound"] = field(default_factory=list)
    meeting_mode: MeetingMode = "debate"
    moderator: ModeratorConfig = field(default_factory=ModeratorConfig)


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
class RoundTurnControl:
    selection: TurnSelection = "all_roles"
    speaker_role_ids: list[str] = field(default_factory=list)
    non_speaker_mode: EngagementMode = "watch"
    moderator_instruction: str | None = None

    def to_dict(self, skipped_role_ids: list[str] | None = None) -> dict[str, object]:
        return {
            "selection": self.selection,
            "speaker_role_ids": self.speaker_role_ids,
            "non_speaker_mode": self.non_speaker_mode,
            "moderator_instruction": self.moderator_instruction,
            "skipped_role_ids": skipped_role_ids or [],
        }


@dataclass(frozen=True)
class MeetingRound:
    id: str
    title: str
    report_label: str
    instruction: str
    context_scope: Literal["own_research", "public_debate"]
    turn_control: RoundTurnControl = field(default_factory=RoundTurnControl)


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


ENGAGEMENT_MODES: set[str] = {"manual", "mentioned", "moderator_called", "human_only", "always", "watch"}
MEETING_MODES: set[str] = {"debate", "free_chat"}


def normalize_engagement_mode(value: object, default: EngagementMode = "manual") -> EngagementMode:
    if value in ENGAGEMENT_MODES:
        return value  # type: ignore[return-value]
    return default


def normalize_meeting_mode(value: object, default: MeetingMode = "debate") -> MeetingMode:
    if value == "free-chat":
        return "free_chat"
    if value in MEETING_MODES:
        return value  # type: ignore[return-value]
    return default


def _public_auth_ref(auth_ref: str | None) -> str | None:
    if auth_ref is None:
        return None
    if auth_ref.startswith("env:"):
        return auth_ref
    if auth_ref.startswith("literal:"):
        return "literal:<redacted>"
    return "<redacted>"


def _public_endpoint(endpoint: str | None) -> str | None:
    if endpoint is None:
        return None
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return "<redacted>" if _looks_sensitive(endpoint) else endpoint
    if not parsed.scheme or not parsed.netloc:
        return "<redacted>" if _looks_sensitive(endpoint) else endpoint
    netloc = parsed.hostname or ""
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    query_pairs = []
    has_sensitive_component = bool(parsed.username or parsed.password)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        public_key, public_value, was_sensitive = _public_query_pair(key, value)
        query_pairs.append((public_key, public_value))
        has_sensitive_component = has_sensitive_component or was_sensitive
    query = urlencode(query_pairs)
    sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))
    return "<redacted>" if has_sensitive_component or _looks_sensitive(sanitized) else sanitized


def _public_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    return "<redacted>" if _looks_sensitive(notes) else notes


def _public_query_pair(key: str, value: str) -> tuple[str, str, bool]:
    if _is_sensitive_query_key(key) or _looks_sensitive(value):
        return key, "<redacted>", True
    return key, value, False


def _is_sensitive_query_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    sensitive_keys = {
        "key",
        "api_key",
        "apikey",
        "access_key",
        "secret",
        "client_secret",
        "token",
        "auth",
        "authorization",
        "password",
    }
    return normalized in sensitive_keys or normalized.endswith("_token") or normalized.endswith("_secret")


def _looks_sensitive(value: str) -> bool:
    normalized = value.casefold()
    markers = ("authorization", "bearer ", "secret", "token", "api-key", "api_key", "apikey", "x-api-key", "password")
    return any(marker in normalized for marker in markers)
