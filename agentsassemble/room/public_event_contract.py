"""Authoritative public room event contract shared with the frontend."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class PublicRoomActor(TypedDict, total=False):
    participant_id: str
    participant_type: str


class PublicRoomAttachment(TypedDict):
    id: str
    filename: str
    content_type: str
    size: int
    is_image: bool
    url: str
    download_url: str


class PublicRoomGlobalAppearance(TypedDict):
    banner_preset: str
    banner_image_url: str
    icon_image_url: str
    icon_label: str
    invite_scope: str


class PublicRoomGlobalChannel(TypedDict):
    id: str
    name: str
    type: str
    position: int
    created_at: str


class PublicRoomGlobalSettings(TypedDict):
    label: str
    topic: str
    appearance: PublicRoomGlobalAppearance
    conversation_mode: str
    ordered_exclude_previous_speaker: bool
    max_relay_turns: int
    channels: list[PublicRoomGlobalChannel]


class PublicRoomEvent(TypedDict):
    v: int
    id: str
    seq: int
    created_at: str
    room_id: str
    type: str
    actor: PublicRoomActor
    participant_id: NotRequired[str]
    participant_type: NotRequired[str]
    actor_id: NotRequired[str]
    actor_type: NotRequired[str]
    session_id: NotRequired[str]
    turn_id: NotRequired[str]
    source_event_id: NotRequired[str]
    display_name: NotRequired[str]
    avatar_image_url: NotRequired[str]
    provider_kind: NotRequired[str]
    role: NotRequired[str]
    content: NotRequired[str]
    message_kind: NotRequired[str]
    target_agent_id: NotRequired[str]
    vote_id: NotRequired[str]
    vote_question: NotRequired[str]
    vote_options: NotRequired[list[str]]
    vote_duration_seconds: NotRequired[int]
    vote_deadline_at: NotRequired[str]
    vote_choice: NotRequired[str]
    visibility: NotRequired[str]
    phase: NotRequired[str]
    status: NotRequired[str]
    activity_kind: NotRequired[str]
    category: NotRequired[str]
    activity_id: NotRequired[str]
    activity_title: NotRequired[str]
    activity_detail: NotRequired[str]
    attachments: NotRequired[list[PublicRoomAttachment]]
    latency: NotRequired[dict[str, object]]
    metadata: NotRequired[dict[str, object]]
    diagnostics: NotRequired[dict[str, object]]
    agent_session: NotRequired[dict[str, object]]
    message_source: NotRequired[str]
    relay_depth: NotRequired[int]
    reason_code: NotRequired[str]
    room_settings: NotRequired[PublicRoomGlobalSettings]


__all__ = [
    "PublicRoomActor",
    "PublicRoomAttachment",
    "PublicRoomGlobalAppearance",
    "PublicRoomGlobalChannel",
    "PublicRoomGlobalSettings",
    "PublicRoomEvent",
]
