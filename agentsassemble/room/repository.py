from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import ContextManager, Protocol, runtime_checkable

from agentsassemble.room_attention import AgentAttentionState, AttentionEvaluation
from agentsassemble.room.global_settings import RoomGlobalSettingsRecord


RoomRecord = dict[str, object]
ParticipantRecord = dict[str, object]
SessionRecord = dict[str, object]
EventRecord = dict[str, object]
CommandRecord = dict[str, object]
EventListener = Callable[[EventRecord], None]


def resolve_room_media_source(output_root: Path, source_path: str) -> Path:
    """Resolve an existing server-owned media file without copying its bytes."""

    root = Path(output_root).expanduser().resolve()
    candidate = Path(source_path).expanduser().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("Room media source must be inside the application data root") from error
    if not candidate.is_file():
        raise ValueError("Room media source was not found")
    return candidate


class RoomTransaction(Protocol):
    """Room-local writes that either commit together or leave no visible state."""

    @property
    def room_id(self) -> str: ...

    def participant(self, participant_id: str) -> ParticipantRecord: ...

    def session(self, session_id: str) -> SessionRecord: ...

    def command_record(self, principal_id: str, request_id: str) -> CommandRecord: ...

    def event_by_id(self, event_id: str) -> EventRecord: ...

    def room_settings(self) -> RoomGlobalSettingsRecord: ...

    def update_room_settings(self, updates: dict[str, object]) -> RoomGlobalSettingsRecord: ...

    def update_room_status(self, status: str) -> RoomRecord: ...

    def create_room(
        self,
        *,
        label: str = "",
        status: str = "active",
        room_uid: str = "",
    ) -> tuple[RoomRecord, bool]: ...

    def ensure_room(self, *, label: str = "", status: str = "active") -> tuple[RoomRecord, bool]: ...

    def upsert_participant(
        self,
        participant: ParticipantRecord,
    ) -> tuple[ParticipantRecord, bool]: ...

    def update_participant_fields(
        self,
        participant_id: str,
        **updates: object,
    ) -> ParticipantRecord: ...

    def upsert_session(
        self,
        session: SessionRecord,
    ) -> tuple[SessionRecord, bool]: ...

    def update_session_fields(
        self,
        session_id: str,
        **updates: object,
    ) -> SessionRecord: ...

    def detach_participant_sessions(self, participant_id: str) -> list[SessionRecord]: ...

    def append_event(self, event_type: str, **payload: object) -> EventRecord: ...

    def record_command_result(
        self,
        request_id: str,
        result: CommandRecord,
        *,
        principal_id: str = "",
        action: str = "",
        payload_hash: str = "",
        max_entries: int = 500,
    ) -> CommandRecord: ...

    def advance_attention_state(
        self,
        participant_id: str,
        *,
        observed_seq: int | None = None,
        attention_evaluated_seq: int | None = None,
        provider_sync_seq: int | None = None,
        spoke_seq: int | None = None,
    ) -> AgentAttentionState: ...

    def checkpoint_observed_seq(
        self,
        participant_id: str,
        observed_seq: int,
    ) -> AgentAttentionState: ...

    def attention_state(self, participant_id: str) -> AgentAttentionState: ...

    def record_attention_evaluation(
        self,
        evaluation: AttentionEvaluation,
        *,
        mode: str,
        status: str,
    ) -> dict[str, object]: ...

    def attention_job(self, job_id: str) -> dict[str, object]: ...

    def attention_lease(self, lease_id: str) -> dict[str, object]: ...

    def claim_attention_job(
        self,
        job_id: str,
        *,
        participant_id: str,
        owner_id: str,
        lease_seconds: float,
    ) -> dict[str, object]: ...

    def resolve_attention_lease(
        self,
        lease_id: str,
        *,
        status: str,
    ) -> dict[str, object]: ...

    def cancel_attention_job(self, job_id: str) -> dict[str, object]: ...


@runtime_checkable
class RoomRepository(Protocol):
    """Persistence boundary shared by local SQLite and hosted PostgreSQL.

    A transaction is scoped to one room. Event sequence allocation, participant
    and session mutations, and command-result writes performed through it must
    commit atomically. Event listeners are notified only after that commit. A
    rollback must not publish an event or consume a room sequence number.
    """

    def close(self) -> None: ...

    def public_diagnostics(self) -> dict[str, object]: ...

    def transaction(self, room_id: str) -> ContextManager[RoomTransaction]: ...

    def create_room(
        self,
        room_id: str,
        *,
        label: str = "",
        status: str = "active",
        room_uid: str = "",
    ) -> RoomRecord: ...

    def ensure_room(self, room_id: str, *, label: str = "", status: str = "active") -> RoomRecord: ...

    def delete_room(
        self,
        room_id: str,
        *,
        reason: str = "",
        tombstone: CommandRecord | None = None,
        cleanup_status: str = "complete",
        room_name: str = "",
    ) -> bool: ...

    def room(self, room_id: str) -> RoomRecord: ...

    def room_settings(self, room_id: str) -> RoomGlobalSettingsRecord: ...

    def update_room_settings(
        self,
        room_id: str,
        updates: dict[str, object],
    ) -> RoomGlobalSettingsRecord: ...

    def list_rooms(self, *, include_archived: bool = False) -> list[RoomRecord]: ...

    def room_is_deleted(self, room_id: str) -> bool: ...

    def deleted_room_record(self, room_id: str) -> CommandRecord: ...

    def update_deleted_room_record(
        self,
        room_id: str,
        *,
        result: CommandRecord,
        cleanup_status: str,
    ) -> CommandRecord: ...

    def participants(self, room_id: str) -> list[ParticipantRecord]: ...

    def participant(self, room_id: str, participant_id: str) -> ParticipantRecord: ...

    def active_participants(self, room_id: str) -> list[ParticipantRecord]: ...

    def upsert_participant(
        self,
        room_id: str,
        participant: ParticipantRecord,
    ) -> tuple[ParticipantRecord, bool]: ...

    def set_participant_status(
        self,
        room_id: str,
        participant_id: str,
        status: str,
        *,
        reason: str = "",
    ) -> ParticipantRecord: ...

    def update_participant_fields(
        self,
        room_id: str,
        participant_id: str,
        **updates: object,
    ) -> ParticipantRecord: ...

    def sessions(self, room_id: str) -> list[SessionRecord]: ...

    def session(self, room_id: str, session_id: str) -> SessionRecord: ...

    def upsert_session(
        self,
        room_id: str,
        session: SessionRecord,
    ) -> tuple[SessionRecord, bool]: ...

    def update_session_fields(
        self,
        room_id: str,
        session_id: str,
        **updates: object,
    ) -> SessionRecord: ...

    def detach_participant_sessions(self, room_id: str, participant_id: str) -> list[SessionRecord]: ...

    def append_event(self, room_id: str, event_type: str, **payload: object) -> EventRecord: ...

    def read_events(
        self,
        room_id: str,
        *,
        after: str = "",
        after_seq: int = 0,
        before_seq: int = 0,
        limit: int | None = None,
        newest: bool = False,
        include_hidden: bool = False,
        event_types: Iterable[str] | None = None,
        exclude_actor_id: str = "",
    ) -> list[EventRecord]: ...

    def event_count(
        self,
        room_id: str,
        *,
        include_hidden: bool = False,
        after_seq: int = 0,
        before_seq: int = 0,
        event_types: Iterable[str] | None = None,
        exclude_actor_id: str = "",
    ) -> int: ...

    def event_by_id(self, room_id: str, event_id: str, *, include_hidden: bool = False) -> EventRecord: ...

    def vote_events(self, room_id: str, vote_id: str) -> list[EventRecord]: ...

    def event_sequence(self, room_id: str, event_id: str) -> int: ...

    def latest_event_sequence(self, room_id: str) -> int: ...

    def oldest_event_sequence(self, room_id: str, *, include_hidden: bool = False) -> int: ...

    def command_record(self, room_id: str, principal_id: str, request_id: str) -> CommandRecord: ...

    def command_result(self, room_id: str, request_id: str, *, principal_id: str = "") -> CommandRecord: ...

    def record_command_result(
        self,
        room_id: str,
        request_id: str,
        result: CommandRecord,
        *,
        principal_id: str = "",
        action: str = "",
        payload_hash: str = "",
        max_entries: int = 500,
    ) -> CommandRecord: ...

    def add_event_listener(self, room_id: str, listener: EventListener) -> Callable[[], None]: ...

    def set_room_status(self, room_id: str, status: str) -> RoomRecord: ...

    def attach_media(
        self,
        room_id: str,
        *,
        filename: str,
        content_type: str,
        size: int = 0,
        supported: bool,
        data: bytes = b"",
        source_path: str = "",
    ) -> dict[str, object]: ...

    def export_participant(
        self,
        room_id: str,
        participant_id: str,
        *,
        reason: str = "",
    ) -> dict[str, object]: ...

    def room_payload(self, room_id: str) -> dict[str, object]: ...

    def attention_state(self, room_id: str, participant_id: str) -> AgentAttentionState: ...

    def attention_jobs(
        self,
        room_id: str,
        *,
        mode: str = "",
        status: str = "",
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict[str, object]]: ...

    def attention_job(self, room_id: str, job_id: str) -> dict[str, object]: ...

    def attention_leases(
        self,
        room_id: str,
        *,
        status: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]: ...

    def attention_lease(self, room_id: str, lease_id: str) -> dict[str, object]: ...


__all__ = [
    "CommandRecord",
    "EventListener",
    "EventRecord",
    "ParticipantRecord",
    "RoomRecord",
    "RoomRepository",
    "RoomTransaction",
    "SessionRecord",
]
