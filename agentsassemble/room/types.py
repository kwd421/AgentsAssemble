"""Shared room event, participant, session, command, and turn shapes."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class RoomActor(TypedDict, total=False):
    participant_id: str
    participant_type: str


class RoomEvent(TypedDict):
    v: int
    id: str
    seq: int
    created_at: str
    room_id: str
    type: str
    actor: RoomActor
    participant_id: NotRequired[str]
    participant_type: NotRequired[str]
    session_id: NotRequired[str]
    turn_id: NotRequired[str]
    source_event_id: NotRequired[str]
    display_name: NotRequired[str]
    content: NotRequired[str]
    visibility: NotRequired[str]
    metadata: NotRequired[dict[str, object]]


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
