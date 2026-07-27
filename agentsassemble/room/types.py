"""Shared room event, participant, session, command, and turn shapes."""

from __future__ import annotations

from typing import NotRequired, TypedDict

from agentsassemble.room.public_event_contract import (
    PublicRoomActor as RoomActor,
    PublicRoomEvent as RoomEvent,
)


class RoomParticipant(TypedDict):
    room_id: str
    participant_id: str
    display_name: str
    participant_type: str
    status: str
    role: NotRequired[str]
    owner_id: NotRequired[str]
    provider_kind: NotRequired[str]
    connection_kind: NotRequired[str]
    muted: NotRequired[bool]


class AgentSession(TypedDict):
    room_id: str
    session_id: str
    participant_id: str
    display_name: str
    status: str
    runtime_status: str
    enabled: bool
    provider_kind: str
    runtime_kind: str
    connection_kind: str
    last_provider_sync_event_id: NotRequired[str]
    last_provider_sync_seq: NotRequired[int]
    last_seen_event_id: NotRequired[str]
    last_seen_seq: NotRequired[int]
    pending_event_ids: NotRequired[list[str]]
    pending_event_modes: NotRequired[dict[str, str]]
    pending_event_observation_kinds: NotRequired[dict[str, str]]
    pending_input_mode: NotRequired[str]
    inflight_event_ids: NotRequired[list[str]]
    pending_attention_job_id: NotRequired[str]
    pending_attention_lease_id: NotRequired[str]
    pending_attention_source_event_id: NotRequired[str]
    active_attention_job_id: NotRequired[str]
    active_attention_lease_id: NotRequired[str]
    active_attention_source_event_id: NotRequired[str]
    recovery_required: NotRequired[bool]
    recovery_attempt_count: NotRequired[int]
    provider_session_active: NotRequired[bool]
    provider_session_load_supported: NotRequired[bool]
    provider_session_reused: NotRequired[bool]
    provider_session_resume_failed: NotRequired[bool]
    provider_session_resume_error: NotRequired[str]
    provider_visible_chars: NotRequired[int]
    provider_visible_event_count: NotRequired[int]
    provider_observation_kind: NotRequired[str]
    stderr_byte_count: NotRequired[int]
    stderr_warning_count: NotRequired[int]
    notification_drop_count: NotRequired[int]


class RoomCommand(TypedDict):
    op: str
    request_id: str
    action: str
    payload: dict[str, object]


class TurnAssignment(TypedDict):
    op: str
    room_id: str
    participant_id: str
    session_id: str
    turn_id: str
    source_event_id: str
    input_up_to_event_id: str
    input_up_to_seq: int
    provider_input: str
    provider_visible_chars: int
    provider_context_event_ids: NotRequired[list[str]]
    provider_context_actor_ids: NotRequired[list[str]]
    timeout_seconds: float
    publication_mode: str
