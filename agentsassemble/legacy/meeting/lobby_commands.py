"""Retained lobby promotion and remote-bridge command policy.

These commands belong to the legacy meeting/lobby compatibility surface. They
must not become a second authority for the canonical shared room.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.providers.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.config import load_agent_runtime_config, providers_from_config
from agentsassemble.legacy.meeting.queries import list_meetings
from agentsassemble.legacy.meeting.records import read_meeting_record
from agentsassemble.legacy.live_agent.runtime.operations import append_live_agent_operation
from agentsassemble.legacy.meeting.support.lobby_promotion import LOBBY_PROMOTION_OPERATION, promote_lobby_events_to_official
from agentsassemble.legacy.meeting.core.events import LOBBY_KINDS, clean_lobby_text
from agentsassemble.models import ProviderConfig, Role
from agentsassemble.room.speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_lobby_say,
)


LobbyAppender = Callable[..., dict[str, object]]
RoomScopePolicy = Callable[[dict[str, object]], bool]
MutedPolicy = Callable[..., bool]
RequesterProvider = Callable[[], object | None]


@dataclass(frozen=True)
class LegacyLobbyCommandService:
    """Execute retained lobby commands without owning HTTP transport."""

    output_root: Path
    append_lobby_event: LobbyAppender
    public_lobby_allows_room_scope: RoomScopePolicy
    is_muted: MutedPolicy
    requester: RequesterProvider

    def promote(self, payload: dict[str, object]) -> dict[str, object]:
        raw_event_ids = payload.get("lobby_event_ids") or payload.get("lobby_event_id") or []
        event_ids = raw_event_ids if isinstance(raw_event_ids, list) else [raw_event_ids]
        meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
        try:
            return promote_lobby_events_to_official(
                self.output_root,
                meeting_id,
                event_ids,
                reason=clean_lobby_text(payload.get("reason"), limit=240),
            )
        except ValueError as error:
            self.record_promotion_failure(
                meeting_id=meeting_id,
                source_event_count=len(event_ids),
                error=str(error),
            )
            raise

    def record_promotion_failure(
        self,
        *,
        meeting_id: str,
        source_event_count: int,
        error: str,
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation=LOBBY_PROMOTION_OPERATION,
            status="failed",
            target_id=meeting_id,
            error=error,
            details={"source_event_count": source_event_count},
        )

    def send_remote(self, payload: dict[str, object]) -> dict[str, object]:
        return send_lobby_message_to_remote_bridge(
            self.output_root,
            str(payload.get("message") or ""),
            meeting_id=_optional_str(payload.get("meeting_id")),
            target_agent_id=_optional_str(payload.get("target_agent_id")),
            speaker_name=str(payload.get("speaker_name") or "나"),
            requester=self.requester(),
            append_lobby_event=self.append_lobby_event,
            public_lobby_allows_room_scope=self.public_lobby_allows_room_scope,
            is_muted=self.is_muted,
        )


def send_lobby_message_to_remote_bridge(
    output_root: Path,
    message: str,
    meeting_id: str | None = None,
    target_agent_id: str | None = None,
    speaker_name: str = "나",
    *,
    requester: object | None,
    append_lobby_event: LobbyAppender,
    public_lobby_allows_room_scope: RoomScopePolicy,
    is_muted: MutedPolicy,
) -> dict[str, object]:
    """Send one governed lobby message through a configured remote bridge."""

    if not message.strip():
        raise ValueError("Message is required.")
    meeting_dir = _resolve_lobby_meeting_dir(output_root, meeting_id)
    meeting = read_meeting_record(meeting_dir)
    role_data, binding, provider_data = _select_remote_bridge_binding(meeting, target_agent_id)
    role = _role_from_payload(role_data)
    provider = _runtime_provider_for_binding(meeting, binding, provider_data)
    session = {
        "meeting_id": meeting.get("meeting_id", meeting_dir.name),
        "agent_id": binding.get("agent_id"),
        "owner_id": binding.get("owner_id"),
        "join_mode": binding.get("join_mode"),
        "session_id": binding.get("session_id"),
    }
    remote_agent_id = clean_lobby_text(binding.get("agent_id"), limit=64) or role.id
    identity = ActorIdentity(
        agent_id=remote_agent_id,
        display_name=role.display_name,
        participant_type="remote_http_bridge",
        meeting_id=clean_lobby_text(session.get("meeting_id"), limit=128),
    )
    try:
        ensure_lobby_say_allowed(output_root, identity, is_muted=is_muted)
    except GovernedLobbySayRejected as rejected:
        if rejected.category == "muted":
            raise ValueError("This participant is muted by the room host.") from rejected
        raise ValueError(str(rejected)) from rejected

    adapter = RemoteBridgeAdapter(provider, requester=requester)
    remote_event = adapter.run_lobby_message(role, session, speaker_name=speaker_name, message=message.strip())
    try:
        return governed_lobby_say(
            output_root,
            identity=identity,
            payload={
                "kind": remote_event.get("kind") or "message",
                "message": remote_event.get("message") or "",
            },
            append_lobby_event=append_lobby_event,
            public_lobby_allows_room_scope=public_lobby_allows_room_scope,
            is_muted=is_muted,
            policy_already_checked=True,
            side="other-agent",
            allowed_kinds=LOBBY_KINDS,
        )
    except GovernedLobbySayRejected as rejected:
        raise ValueError(str(rejected)) from rejected


def _resolve_lobby_meeting_dir(output_root: Path, meeting_id: str | None) -> Path:
    if meeting_id:
        meeting_dir = output_root / "meetings" / meeting_id
        if meeting_dir.exists():
            return meeting_dir
        raise ValueError(f"Meeting {meeting_id} was not found.")
    meetings = list_meetings(output_root)
    if not meetings:
        raise ValueError("No meeting is available for remote lobby chat.")
    return Path(str(meetings[0]["path"]))


def _select_remote_bridge_binding(
    meeting: dict[str, object],
    target_agent_id: str | None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    roles = _index_by_id(meeting.get("roles", []))
    providers = _index_by_id(meeting.get("provider_configs", []))
    for binding in _as_dict_list(meeting.get("agent_bindings", [])):
        if target_agent_id and binding.get("agent_id") != target_agent_id:
            continue
        provider = providers.get(str(binding.get("provider_id")))
        if not provider or provider.get("kind") != "remote_http_bridge":
            continue
        role = roles.get(str(binding.get("role_id")))
        if role:
            return role, binding, provider
    raise ValueError("No remote bridge lobby participant is available.")


def _index_by_id(items: object) -> dict[str, dict[str, object]]:
    return {str(item["id"]): item for item in _as_dict_list(items) if item.get("id")}


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _role_from_payload(payload: dict[str, object]) -> Role:
    return Role(
        id=str(payload.get("id") or "remote"),
        display_name=str(payload.get("display_name") or payload.get("id") or "원격 에이전트"),
        lens=str(payload.get("lens") or "Remote participant"),
        research_focus=str(payload.get("research_focus") or "Lobby participation"),
        personality=payload.get("personality") if isinstance(payload.get("personality"), dict) else None,
        source_preferences=payload.get("source_preferences") if isinstance(payload.get("source_preferences"), list) else None,
    )


def _provider_from_payload(payload: dict[str, object]) -> ProviderConfig:
    return ProviderConfig(
        id=str(payload.get("id") or "remote"),
        kind="remote_http_bridge",
        display_name=str(payload.get("display_name") or payload.get("id") or "Remote bridge"),
        default_model=_optional_str(payload.get("default_model")),
        endpoint=_optional_str(payload.get("endpoint")),
        auth_ref=_optional_str(payload.get("auth_ref")),
        timeout_seconds=payload.get("timeout_seconds") if isinstance(payload.get("timeout_seconds"), int) else None,
        search_enabled=bool(payload.get("search_enabled")),
        notes=_optional_str(payload.get("notes")),
    )


def _runtime_provider_for_binding(
    meeting: dict[str, object],
    binding: dict[str, object],
    public_provider: dict[str, object],
) -> ProviderConfig:
    provider_id = str(binding.get("provider_id") or public_provider.get("id") or "remote")
    runtime_provider = _provider_from_agent_config(meeting.get("agent_config_source"), provider_id)
    if runtime_provider is not None:
        return runtime_provider
    auth_ref = _optional_str(public_provider.get("auth_ref"))
    if auth_ref == "literal:<redacted>" or auth_ref == "<redacted>":
        raise ValueError(
            "Remote bridge credential is not available from the public meeting artifact. "
            "Use an env: auth_ref or rerun with the original agent config available."
        )
    return _provider_from_payload(public_provider)


def _provider_from_agent_config(source: object, provider_id: str) -> ProviderConfig | None:
    if not isinstance(source, str) or not source or source == "default":
        return None
    config_path = Path(source)
    if not config_path.exists():
        return None
    runtime_config = load_agent_runtime_config(config_path)
    if runtime_config is None:
        return None
    return providers_from_config(runtime_config).get(provider_id)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
