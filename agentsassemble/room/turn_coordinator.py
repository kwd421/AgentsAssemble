from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import threading
from typing import Callable
from uuid import uuid4

from agentsassemble.diagnostics.cleanup import CleanupReport
from agentsassemble.room.text import (
    clean_room_text as clean_lobby_text,
    has_room_visible_text,
)
from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.providers.model_verification import (
    model_observation_matches,
    model_verification_status,
)
from agentsassemble.providers.runtime_contracts import (
    AUTOMATIC_FINAL,
    EXPLICIT_ROOM_PORTAL,
    SUPPORTED_DECLINE_REASONS,
)
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room_attention import AttentionLeaseConflict
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.projection import (
    PUBLIC_ACTIVITY_LABELS,
    merged_latency,
    public_activity,
    public_runtime_diagnostics,
    public_session,
    runtime_diagnostic_fields,
)
from agentsassemble.providers.sync_cursor import (
    ProviderSyncCursorParityError,
    assert_provider_sync_cursor_parity,
    canonical_provider_sync_seq,
    provider_sync_session_fields,
)
from agentsassemble.room.repository import RoomRepository, RoomTransaction
from agentsassemble.room_turn_attention import RoomTurnAttention
from agentsassemble.room.types import RoomEvent, TurnAssignment


RecoveryScheduler = Callable[[float, Callable[[], None]], object]
TurnPacketBuilder = Callable[..., dict[str, object]]
ProviderLookup = Callable[[str, str], NativeCliProviderSpec]
SessionCallback = Callable[[str, dict[str, object]], object]
EnsureRoom = Callable[[str], dict[str, object]]
TurnFinalizationWriter = RoomTransaction | RoomCommandUnitOfWork


@dataclass(frozen=True)
class PendingEventPartition:
    inflight: list[str]
    deferred: list[str]
    already_synced: list[str]
    invalid: list[str]


@dataclass(frozen=True)
class PreparedFinalMessage:
    content: str
    latency: dict[str, object]
    diagnostics: dict[str, object]
    observed_model_id: str


class RoomTurnCoordinator:
    """Owns pending room input, active provider turns, and provider sync cursors."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        lock: threading.RLock,
        provider_lookup: ProviderLookup,
        ensure_room: EnsureRoom,
        publish_session_state: SessionCallback,
        is_closed: Callable[[], bool],
        recovery_delay_seconds: float,
        recovery_scheduler: RecoveryScheduler,
        packet_builder: TurnPacketBuilder,
        attention_owner_id: str = "",
    ) -> None:
        self.output_root = Path(output_root)
        self.store = store
        self.broker = broker
        self._lock = lock
        self._provider_lookup = provider_lookup
        self._ensure_room = ensure_room
        self._publish_session_state = publish_session_state
        self._is_closed = is_closed
        self.recovery_delay_seconds = max(0.0, float(recovery_delay_seconds))
        self._recovery_scheduler = recovery_scheduler
        self._packet_builder = packet_builder
        self._turn_attention = RoomTurnAttention(
            store,
            provider_lookup=provider_lookup,
            owner_id=attention_owner_id,
        )
        self._recovery_handles: dict[tuple[str, str], object] = {}

    def close(self) -> CleanupReport:
        with self._lock:
            handles = list(self._recovery_handles.values())
            self._recovery_handles.clear()
        cleanup = CleanupReport("room_turn_coordinator")
        for handle in handles:
            cancel = getattr(handle, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                    cleanup.record_success()
                except Exception as error:
                    cleanup.record_failure("recovery.cancel", error)
        return cleanup

    def request_turn(
        self,
        room_id: str,
        agent_id: str,
        *,
        source_event_id: str = "",
    ) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_agent_id = clean_lobby_text(agent_id, limit=128)
        self._ensure_room(clean_room_id)
        with self._lock:
            self._provider_lookup(clean_room_id, clean_agent_id)
            session = self.store.session(clean_room_id, clean_agent_id)
            if not session:
                raise RoomCommandRejected(f"Agent session {clean_agent_id} was not found.", code="not_found")
            source = self.store.event_by_id(clean_room_id, source_event_id) if source_event_id else {}
            if not source:
                latest = self.store.read_events(
                    clean_room_id,
                    event_types=("message_final",),
                    exclude_actor_id=clean_agent_id,
                    limit=1,
                    newest=True,
                )
                source = latest[-1] if latest else {}
            if source.get("type") != "message_final":
                raise RoomCommandRejected(
                    "A public room message is required to assign a turn.",
                    code="no_room_message",
                )
            source_seq = int(source.get("seq") or 0)
            try:
                last_sync_seq = canonical_provider_sync_seq(
                    self.store,
                    clean_room_id,
                    clean_agent_id,
                    session,
                )
            except ProviderSyncCursorParityError as error:
                raise RoomCommandRejected(str(error), code="provider_sync_cursor_mismatch") from error
            if session.get("bootstrap_done") and source_seq <= last_sync_seq:
                raise RoomCommandRejected(
                    "The Agent Session has no unseen public room message to answer.",
                    code="no_new_room_message",
                )
            self.queue_event(clean_room_id, clean_agent_id, source, relay_depth=0)
            current = self.store.session(clean_room_id, clean_agent_id)
            return {
                "source_event_id": source.get("id"),
                "source_event_seq": source_seq,
                "queued": bool(source.get("id")),
                "assigned": current.get("active_source_event_id") == source.get("id"),
                "agent_session": public_session(current),
            }

    def observe_room(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        agent_id, _session = self.bridge_session(identity, room_id)
        through_seq = safe_bounded_int(payload.get("through_seq"), default=0, minimum=0)
        if through_seq <= 0:
            raise RoomCommandRejected("Observed room sequence is required.", code="observed_seq_invalid")
        latest_seq = self.store.latest_event_sequence(room_id)
        if through_seq > latest_seq:
            raise RoomCommandRejected(
                "Observed room sequence is ahead of the canonical event stream.",
                code="observed_seq_invalid",
            )
        with self.store.transaction(room_id) as transaction:
            state = transaction.checkpoint_observed_seq(agent_id, through_seq)
        return {"observed_through_seq": state.last_observed_seq}

    def queue_event(
        self,
        room_id: str,
        agent_id: str,
        event: RoomEvent | dict[str, object],
        *,
        relay_depth: int,
        attention_job_id: str = "",
        attention_lease_id: str = "",
        input_mode: str = "transcript",
    ) -> bool:
        session = self.store.session(room_id, agent_id)
        if not session:
            return False
        event_id = clean_lobby_text(event.get("id"), limit=128)
        pending = dedupe_event_ids([*list(session.get("pending_event_ids") or []), event_id])
        event_modes = pending_event_modes(session, pending)
        event_modes[event_id] = (
            "room_observation"
            if clean_lobby_text(input_mode, limit=32) == "room_observation"
            else "transcript"
        )
        updates: dict[str, object] = {
            "pending_event_ids": pending,
            "pending_event_modes": event_modes,
            "pending_relay_depth": max(int(session.get("pending_relay_depth") or 0), relay_depth),
            "pending_input_mode": event_modes.get(pending[0], "") if pending else "",
        }
        updates.update(
            self._turn_attention.queue_fields(
                event_id,
                job_id=attention_job_id,
                lease_id=attention_lease_id,
            )
        )
        session = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            **updates,
        )
        if (
            session.get("enabled")
            and session.get("runtime_status") == "idle"
            and self.broker.has_bridge(room_id, agent_id)
        ):
            if self.store.room_settings(room_id).get("conversation_mode") == "ordered":
                return self.assign_next_ordered_pending(room_id)
            return self.assign_pending(room_id, agent_id)
        return False

    def assign_next_ordered_pending(self, room_id: str) -> bool:
        """Assign the oldest selected observation while preserving one room-wide turn."""
        if self.store.room_settings(room_id).get("conversation_mode") != "ordered":
            return False
        sessions = self.store.sessions(room_id)
        if self._ordered_turn_is_active(room_id, sessions=sessions):
            return False

        candidates: list[tuple[int, str]] = []
        for session in sessions:
            agent_id = clean_lobby_text(session.get("participant_id"), limit=128)
            pending = dedupe_event_ids(list(session.get("pending_event_ids") or []))
            if (
                not agent_id
                or not pending
                or not session.get("enabled")
                or session.get("status") != "attached"
                or session.get("runtime_status") != "idle"
                or not self.broker.has_bridge(room_id, agent_id)
            ):
                continue
            first_event = self.store.event_by_id(room_id, pending[0])
            candidates.append(
                (
                    safe_bounded_int(first_event.get("seq"), default=0, minimum=0),
                    agent_id,
                )
            )
        for _event_seq, agent_id in sorted(candidates):
            if self.assign_pending(room_id, agent_id):
                return True
        return False

    def _ordered_turn_is_active(
        self,
        room_id: str,
        *,
        sessions: list[dict[str, object]] | None = None,
    ) -> bool:
        return any(
            session.get("enabled")
            and session.get("status") == "attached"
            and session.get("runtime_status") == "busy"
            and self.broker.has_bridge(
                room_id,
                clean_lobby_text(session.get("participant_id"), limit=128),
            )
            for session in (sessions if sessions is not None else self.store.sessions(room_id))
        )

    def assign_pending(self, room_id: str, agent_id: str) -> bool:
        session = self.store.session(room_id, agent_id)
        participant = self.store.participant(room_id, agent_id)
        all_pending = dedupe_event_ids(list(session.get("pending_event_ids") or [])) if session else []
        if (
            not session
            or participant.get("status") == "kicked"
            or bool(participant.get("muted"))
            or not session.get("enabled")
            or session.get("runtime_status") != "idle"
            or not self.broker.has_bridge(room_id, agent_id)
        ):
            return False
        if (
            self.store.room_settings(room_id).get("conversation_mode") == "ordered"
            and self._ordered_turn_is_active(room_id)
        ):
            return False
        event_modes = pending_event_modes(session, all_pending)
        if not all_pending:
            return False
        if self.store.room_settings(room_id).get("conversation_mode") not in {
            "ambient",
            "ordered",
        }:
            retained = [
                event_id
                for event_id in all_pending
                if event_modes.get(event_id) != "room_observation"
            ]
            if retained != all_pending:
                attention_fields = self._turn_attention.deferred_fields(
                    room_id,
                    session,
                    retained,
                )
                session = self.store.update_session_fields(
                    room_id,
                    str(session["session_id"]),
                    pending_event_ids=retained,
                    pending_input_mode=event_modes.get(retained[0], "") if retained else "",
                    **attention_fields,
                )
                all_pending = retained
            if not all_pending:
                return False
        selected_mode = event_modes.get(all_pending[0], "transcript")
        pending = [
            event_id
            for event_id in all_pending
            if event_modes.get(event_id, "transcript") == selected_mode
        ]
        preserved_pending = [
            event_id for event_id in all_pending if event_id not in set(pending)
        ]
        if selected_mode == "room_observation":
            return self._assign_room_observation(
                room_id,
                agent_id,
                session=session,
                pending=pending,
                preserved_pending=preserved_pending,
                event_modes=event_modes,
            )
        if not pending:
            return False
        try:
            canonical_sync_seq = canonical_provider_sync_seq(
                self.store,
                room_id,
                agent_id,
                session,
            )
        except ProviderSyncCursorParityError as error:
            raise RoomCommandRejected(str(error), code="provider_sync_cursor_mismatch") from error
        turn_id = f"turn-{uuid4().hex[:12]}"
        packet = self._packet_builder(
            self.output_root,
            room_id=room_id,
            participant_id=agent_id,
            session_id=str(session["session_id"]),
            instruction="Room context update.",
            include_instruction=False,
            max_recent_events=50 if session.get("external_owned") else None,
            max_prompt_chars=64_000 if session.get("external_owned") else None,
            up_to_seq=max(
                (
                    safe_bounded_int(
                        self.store.event_by_id(room_id, event_id).get("seq"),
                        default=0,
                        minimum=0,
                    )
                    for event_id in pending
                ),
                default=0,
            ),
        )
        provider_events = [event for event in list(packet.get("events") or []) if isinstance(event, dict)]
        provider_context_event_ids = [
            clean_lobby_text(event.get("id"), limit=128)
            for event in provider_events
            if clean_lobby_text(event.get("id"), limit=128)
        ]
        provider_context_actor_ids = [
            clean_lobby_text(event.get("participant_id") or event.get("actor_id"), limit=128)
            for event in provider_events
            if clean_lobby_text(event.get("participant_id") or event.get("actor_id"), limit=128)
        ]
        input_up_to_event_id = clean_lobby_text(packet.get("last_provider_sync_event_id_after"), limit=128)
        input_up_to_seq = safe_bounded_int(
            packet.get("last_provider_sync_seq_after"),
            default=0,
            minimum=0,
        )
        partition = self.partition_pending_events(
            room_id,
            pending,
            included_event_ids=set(provider_context_event_ids),
            last_provider_sync_seq=(
                safe_bounded_int(
                    packet.get("last_provider_sync_seq_before", canonical_sync_seq),
                    default=0,
                    minimum=0,
                )
                if session.get("bootstrap_done")
                else 0
            ),
        )
        if not partition.inflight:
            cleaned_pending = ordered_pending_subset(
                all_pending,
                [*partition.deferred, *preserved_pending],
            )
            attention_fields = self._turn_attention.deferred_fields(
                room_id,
                session,
                [*partition.deferred, *preserved_pending],
            )
            attention_changed = any(session.get(key) != value for key, value in attention_fields.items())
            if cleaned_pending != pending or attention_changed:
                self.store.update_session_fields(
                    room_id,
                    str(session["session_id"]),
                    pending_event_ids=cleaned_pending,
                    pending_input_mode=event_modes.get(cleaned_pending[0], "") if cleaned_pending else "",
                    pending_relay_depth=int(session.get("pending_relay_depth") or 0) if cleaned_pending else 0,
                    **attention_fields,
                )
            return False
        active_source_event_id = partition.inflight[-1]
        source_event = self.store.event_by_id(room_id, active_source_event_id)
        input_up_to_event_id = input_up_to_event_id or active_source_event_id
        relay_depth = int(session.get("pending_relay_depth") or 0)
        try:
            attention_fields = self._turn_attention.assignment_fields(
                room_id,
                agent_id,
                session,
                inflight_event_ids=partition.inflight,
                deferred_event_ids=[*partition.deferred, *preserved_pending],
            )
        except AttentionLeaseConflict as error:
            failed = self.store.update_session_fields(
                room_id,
                str(session["session_id"]),
                last_error=str(error),
            )
            self._publish_session_state(room_id, failed)
            raise RoomCommandRejected(str(error), code="attention_lease_conflict") from error
        dispatched_at = now()
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            runtime_status="busy",
            turn_phase="thinking",
            active_turn_id=turn_id,
            active_source_event_id=active_source_event_id,
            active_relay_depth=relay_depth,
            input_up_to_event_id=input_up_to_event_id,
            input_up_to_seq=input_up_to_seq,
            inflight_event_ids=partition.inflight,
            pending_event_ids=ordered_pending_subset(
                all_pending,
                [*partition.deferred, *preserved_pending],
            ),
            pending_relay_depth=relay_depth if partition.deferred or preserved_pending else 0,
            pending_input_mode=(
                event_modes.get(
                    ordered_pending_subset(
                        all_pending,
                        [*partition.deferred, *preserved_pending],
                    )[0],
                    "",
                )
                if partition.deferred or preserved_pending
                else ""
            ),
            **attention_fields,
            latency={
                "queued_at": source_event.get("created_at") or dispatched_at,
                "dispatch_started_at": dispatched_at,
            },
            provider_visible_chars=int(packet.get("provider_visible_chars") or 0),
            provider_visible_event_count=int(packet.get("provider_visible_event_count") or 0),
            provider_input_mode=clean_lobby_text(packet.get("input_mode"), limit=32),
            context_error_detected=False,
        )
        self._publish_session_state(room_id, updated)
        self.store.append_event(
            room_id,
            "turn_started",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=turn_id,
            source_event_id=active_source_event_id,
            provider_visible_chars=packet.get("provider_visible_chars"),
            provider_visible_event_count=packet.get("provider_visible_event_count"),
            provider_context_event_ids=provider_context_event_ids,
            provider_context_actor_ids=provider_context_actor_ids,
            provider_context_after_seq=packet.get("provider_context_after_seq"),
            provider_context_up_to_seq=packet.get("last_provider_sync_seq_after"),
        )
        self.store.append_event(
            room_id,
            "turn_state",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=turn_id,
            phase="thinking",
        )
        assignment: TurnAssignment = {
            "op": "turn.assign",
            "room_id": room_id,
            "participant_id": agent_id,
            "session_id": session["session_id"],
            "turn_id": turn_id,
            "source_event_id": active_source_event_id,
            "input_up_to_event_id": input_up_to_event_id,
            "input_up_to_seq": input_up_to_seq,
            "provider_input": packet.get("provider_input") or "",
            "provider_visible_chars": packet.get("provider_visible_chars") or 0,
            "provider_context_event_ids": provider_context_event_ids,
            "provider_context_actor_ids": provider_context_actor_ids,
            "timeout_seconds": self._provider_lookup(room_id, agent_id).turn_timeout_seconds,
            "publication_mode": AUTOMATIC_FINAL,
        }
        if self.broker.direct_to_bridge(room_id, agent_id, assignment):
            return True
        self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="unavailable",
            runtime_status="disconnected",
            active_turn_id="",
            active_relay_depth=0,
            turn_phase="",
            input_up_to_seq=0,
            inflight_event_ids=[],
            pending_event_ids=ordered_pending_subset(
                all_pending,
                [*partition.inflight, *partition.deferred, *preserved_pending],
            ),
            pending_input_mode=event_modes.get(all_pending[0], "") if all_pending else "",
            **self._turn_attention.delivery_failed_fields(updated),
            last_error="Agent bridge disconnected before turn assignment.",
        )
        return False

    def request_room_check(
        self,
        identity: dict[str, object],
        room_id: str,
    ) -> dict[str, object]:
        agent_id, session = self.bridge_session(identity, room_id)
        if self.store.room_settings(room_id).get("conversation_mode") != "ambient":
            return {"assigned": False, "reason": "ambient_disabled"}
        assigned = self._assign_room_observation(
            room_id,
            agent_id,
            session=session,
            pending=[],
            allow_empty=True,
        )
        return {"assigned": assigned}

    def _assign_room_observation(
        self,
        room_id: str,
        agent_id: str,
        *,
        session: dict[str, object],
        pending: list[str],
        preserved_pending: list[str] | None = None,
        event_modes: dict[str, str] | None = None,
        allow_empty: bool = False,
    ) -> bool:
        preserved_pending = dedupe_event_ids(list(preserved_pending or []))
        event_modes = event_modes or pending_event_modes(
            session,
            [*pending, *preserved_pending],
        )
        all_pending = dedupe_event_ids([*pending, *preserved_pending])
        participant = self.store.participant(room_id, agent_id)
        if (
            participant.get("status") == "kicked"
            or bool(participant.get("muted"))
            or not session.get("enabled")
            or session.get("runtime_status") != "idle"
            or not self.broker.has_bridge(room_id, agent_id)
        ):
            return False
        try:
            canonical_sync_seq = canonical_provider_sync_seq(
                self.store,
                room_id,
                agent_id,
                session,
            )
        except ProviderSyncCursorParityError as error:
            raise RoomCommandRejected(str(error), code="provider_sync_cursor_mismatch") from error
        partition = self.partition_pending_events(
            room_id,
            pending,
            included_event_ids=set(pending),
            last_provider_sync_seq=canonical_sync_seq,
        )
        if not partition.inflight and not allow_empty:
            remaining_pending = ordered_pending_subset(
                all_pending,
                [*partition.deferred, *preserved_pending],
            )
            self.store.update_session_fields(
                room_id,
                str(session["session_id"]),
                pending_event_ids=remaining_pending,
                pending_relay_depth=0,
                pending_input_mode=(
                    event_modes.get(remaining_pending[0], "")
                    if remaining_pending
                    else ""
                ),
                **self._turn_attention.deferred_fields(
                    room_id,
                    session,
                    partition.deferred,
                ),
            )
            return False
        latest_message = {}
        if partition.inflight:
            latest_message = self.store.event_by_id(room_id, partition.inflight[-1])
        elif allow_empty:
            messages = self.store.read_events(
                room_id,
                event_types=("message_final",),
                limit=1,
                newest=True,
            )
            latest_message = messages[-1] if messages else {}
        active_source_event_id = clean_lobby_text(latest_message.get("id"), limit=128)
        input_up_to_event_id = active_source_event_id or clean_lobby_text(
            session.get("last_provider_sync_event_id"),
            limit=128,
        )
        input_up_to_seq = max(
            canonical_sync_seq,
            safe_bounded_int(latest_message.get("seq"), default=0, minimum=0),
        )
        relay_depth = int(session.get("pending_relay_depth") or 0)
        try:
            attention_fields = self._turn_attention.assignment_fields(
                room_id,
                agent_id,
                session,
                inflight_event_ids=partition.inflight,
                deferred_event_ids=[*partition.deferred, *preserved_pending],
            )
        except AttentionLeaseConflict as error:
            failed = self.store.update_session_fields(
                room_id,
                str(session["session_id"]),
                last_error=str(error),
            )
            self._publish_session_state(room_id, failed)
            raise RoomCommandRejected(str(error), code="attention_lease_conflict") from error
        turn_id = f"turn-{uuid4().hex[:12]}"
        dispatched_at = now()
        remaining_pending = ordered_pending_subset(
            all_pending,
            [*partition.deferred, *preserved_pending],
        )
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            runtime_status="busy",
            turn_phase="thinking",
            active_turn_id=turn_id,
            active_source_event_id=active_source_event_id,
            active_relay_depth=relay_depth,
            input_up_to_event_id=input_up_to_event_id,
            input_up_to_seq=input_up_to_seq,
            inflight_event_ids=partition.inflight,
            pending_event_ids=remaining_pending,
            pending_relay_depth=relay_depth if remaining_pending else 0,
            pending_input_mode=(
                event_modes.get(remaining_pending[0], "")
                if remaining_pending
                else ""
            ),
            **attention_fields,
            latency={
                "queued_at": latest_message.get("created_at") or dispatched_at,
                "dispatch_started_at": dispatched_at,
            },
            provider_visible_chars=0,
            provider_visible_event_count=0,
            provider_input_mode="room_observation",
            context_error_detected=False,
        )
        self._publish_session_state(room_id, updated)
        self.store.append_event(
            room_id,
            "turn_started",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=turn_id,
            source_event_id=active_source_event_id,
            provider_visible_chars=0,
            provider_visible_event_count=0,
            provider_context_event_ids=[],
            provider_context_actor_ids=[],
            provider_context_after_seq=canonical_sync_seq,
            provider_context_up_to_seq=input_up_to_seq,
            provider_input_mode="room_observation",
        )
        self.store.append_event(
            room_id,
            "turn_state",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=turn_id,
            phase="thinking",
        )
        wake = {
            "op": "room.wake",
            "room_id": room_id,
            "participant_id": agent_id,
            "session_id": session["session_id"],
            "turn_id": turn_id,
            "source_event_id": active_source_event_id,
            "input_up_to_event_id": input_up_to_event_id,
            "input_up_to_seq": input_up_to_seq,
            "attachment_ids": _attachment_ids(
                self.store,
                room_id,
                partition.inflight or ([active_source_event_id] if active_source_event_id else []),
            ),
            "timeout_seconds": self._provider_lookup(
                room_id,
                agent_id,
            ).turn_timeout_seconds,
            "publication_mode": EXPLICIT_ROOM_PORTAL,
        }
        if self.broker.direct_to_bridge(room_id, agent_id, wake):
            return True
        self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="unavailable",
            runtime_status="disconnected",
            active_turn_id="",
            active_relay_depth=0,
            turn_phase="",
            input_up_to_seq=0,
            inflight_event_ids=[],
            pending_event_ids=ordered_pending_subset(
                all_pending,
                [*partition.inflight, *partition.deferred, *preserved_pending],
            ),
            pending_input_mode=event_modes.get(all_pending[0], "") if all_pending else "",
            **self._turn_attention.delivery_failed_fields(updated),
            last_error="Agent bridge disconnected before room wake.",
        )
        return False

    def cancel_queued_attention(
        self,
        room_id: str,
        agent_id: str,
        *,
        source_event_id: str,
        lease_id: str,
    ) -> None:
        self._turn_attention.cancel_queued(
            room_id,
            agent_id,
            source_event_id=source_event_id,
            lease_id=lease_id,
        )

    def prepare_session_reset(
        self,
        room_id: str,
        session: dict[str, object],
        *,
        pending_event_ids: list[str],
        retry: bool,
    ) -> dict[str, object]:
        fields = self._turn_attention.prepare_session_reset(
            room_id,
            session,
            pending_event_ids=pending_event_ids,
            retry=retry,
        )
        fields.update(
            pending_mode_fields(
                session,
                list(fields.get("pending_event_ids") or []),
            )
        )
        return fields

    def reconcile_session_attention(
        self,
        room_id: str,
        session: dict[str, object],
        *,
        pending_event_ids: list[str],
    ) -> dict[str, object]:
        fields = self._turn_attention.reconcile_session(
            room_id,
            session,
            pending_event_ids=pending_event_ids,
        )
        fields.update(
            pending_mode_fields(
                session,
                list(fields.get("pending_event_ids") or []),
            )
        )
        return fields

    def partition_pending_events(
        self,
        room_id: str,
        pending: list[str],
        *,
        included_event_ids: set[str],
        last_provider_sync_seq: int,
    ) -> PendingEventPartition:
        partition = PendingEventPartition(inflight=[], deferred=[], already_synced=[], invalid=[])
        for event_id in pending:
            event = self.store.event_by_id(room_id, event_id)
            event_seq = safe_bounded_int(event.get("seq"), default=0, minimum=0)
            if not event or not event_seq:
                partition.invalid.append(event_id)
            elif event_seq <= last_provider_sync_seq:
                partition.already_synced.append(event_id)
            elif event_id in included_event_ids:
                partition.inflight.append(event_id)
            else:
                partition.deferred.append(event_id)
        return partition

    def bridge_session(
        self,
        identity: dict[str, object],
        room_id: str,
        *,
        allow_unleased: bool = False,
    ) -> tuple[str, dict[str, object]]:
        agent_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        session_id = clean_lobby_text(identity.get("session_id") or agent_id, limit=128)
        session = self.store.session(room_id, session_id)
        self._validate_bridge_session(
            identity,
            agent_id=agent_id,
            session=session,
            allow_unleased=allow_unleased,
        )
        return agent_id, session

    @staticmethod
    def _validate_bridge_session(
        identity: dict[str, object],
        *,
        agent_id: str,
        session: dict[str, object],
        allow_unleased: bool,
    ) -> None:
        if not session or session.get("participant_id") != agent_id:
            raise RoomCommandRejected(
                "Agent bridge session does not match its ticket identity.",
                code="permission_denied",
            )
        identity_generation = int(identity.get("bridge_generation") or 0)
        session_generation = int(session.get("bridge_generation") or 0)
        if not allow_unleased and session_generation and identity_generation != session_generation:
            raise RoomCommandRejected("Agent bridge lease is stale.", code="stale_bridge_generation")

    def active_bridge_turn_in_writer(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        writer: TurnFinalizationWriter,
    ) -> tuple[str, dict[str, object]]:
        agent_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        session_id = clean_lobby_text(identity.get("session_id") or agent_id, limit=128)
        session = writer.session(session_id)
        self._validate_bridge_session(
            identity,
            agent_id=agent_id,
            session=session,
            allow_unleased=False,
        )
        self._validate_active_turn(payload, session)
        return agent_id, session

    def active_bridge_turn(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        agent_id, session = self.bridge_session(identity, room_id)
        self._validate_active_turn(payload, session)
        return agent_id, session

    @staticmethod
    def _validate_active_turn(
        payload: dict[str, object],
        session: dict[str, object],
    ) -> None:
        turn_id = clean_lobby_text(payload.get("turn_id"), limit=128)
        if not turn_id or turn_id != session.get("active_turn_id"):
            raise RoomCommandRejected("Turn does not match the active assignment.", code="turn_conflict")

    def turn_state(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        agent_id, session = self.active_bridge_turn(identity, room_id, payload)
        phase = clean_lobby_text(payload.get("phase"), limit=32)
        validate_turn_phase_transition(session, phase)
        latency = merged_latency(session.get("latency"), payload.get("latency"))
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            turn_phase=phase,
            latency=latency,
        )
        event = self.store.append_event(
            room_id,
            "turn_state",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            phase=phase,
            latency=latency,
        )
        self._publish_session_state(room_id, updated)
        return {"event": event, "agent_session": public_session(updated)}

    def message_delta(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        agent_id, session = self.active_bridge_turn(identity, room_id, payload)
        current_phase = require_active_turn_phase(session)
        content = message_delta_text(payload.get("content"), limit=12000)
        if not has_room_visible_text(content):
            raise RoomCommandRejected("Delta content is required.", code="empty")
        if current_phase != "streaming":
            self.store.update_session_fields(room_id, str(session["session_id"]), turn_phase="streaming")
            self.store.append_event(
                room_id,
                "turn_state",
                participant_id=agent_id,
                session_id=session["session_id"],
                turn_id=session["active_turn_id"],
                phase="streaming",
            )
        event = self.store.append_event(
            room_id,
            "message_delta",
            participant_id=agent_id,
            participant_type="agent",
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            content=content,
        )
        return {"event": event, "event_seq": event["seq"]}

    def activity_update(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        agent_id, session = self.active_bridge_turn(identity, room_id, payload)
        require_active_turn_phase(session)
        category = clean_lobby_text(payload.get("category"), limit=32)
        status = clean_lobby_text(payload.get("status"), limit=32)
        if category not in PUBLIC_ACTIVITY_LABELS or status not in {"started", "running", "completed"}:
            raise RoomCommandRejected(
                "Agent activity category or status is invalid.",
                code="adapter_activity_invalid",
            )
        content, activity_kind = public_activity(
            category,
            status,
            detail=payload.get("content"),
        )
        event = self.store.append_event(
            room_id,
            "activity_delta",
            participant_id=agent_id,
            participant_type="agent",
            actor_id=agent_id,
            actor_type="agent",
            display_name=session.get("display_name") or agent_id,
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            activity_kind=activity_kind,
            category=category,
            status=status,
            content=content,
        )
        return {"event": event, "event_seq": event["seq"]}

    def message_final(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        prepared = self.prepare_message_final(identity, room_id, payload)
        with self.store.transaction(room_id) as transaction:
            result = self._commit_final_message(
                identity,
                payload,
                prepared=prepared,
                writer=transaction,
            )
        self.after_message_final(room_id, result, deduplicated=False)
        return result

    def prepare_message_final(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> PreparedFinalMessage:
        agent_id, session = self.active_bridge_turn(identity, room_id, payload)
        require_active_turn_phase(session)
        self._require_observation_receipt(
            identity,
            room_id,
            payload,
            session=session,
        )
        content = room_message_text(payload.get("content"), limit=12000)
        active_turn_id = str(session["active_turn_id"])
        latency = merged_latency(session.get("latency"), payload.get("latency"))
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        observed_model_id = clean_lobby_text(payload.get("observed_model_id"), limit=128)
        requested_model_id = clean_lobby_text(
            session.get("requested_model_id") or session.get("model"),
            limit=128,
        )
        selection_kind = clean_lobby_text(session.get("model_selection_kind"), limit=16) or "exact"
        observation_policy = clean_lobby_text(session.get("model_observation_policy"), limit=32) or "unavailable"
        provider_kind = clean_lobby_text(session.get("provider_kind"), limit=64)
        if observation_policy == "required" and not observed_model_id:
            self.turn_failed(
                identity,
                room_id,
                {
                    "turn_id": active_turn_id,
                    "message": "Provider did not report the model used for this turn.",
                    "error_code": "provider_model_unobserved",
                    "diagnostics": diagnostics,
                },
            )
            raise RoomCommandRejected(
                "Provider did not report the model used for this turn.",
                code="provider_model_unobserved",
            )
        if (
            observed_model_id
            and requested_model_id
            and selection_kind == "exact"
            and not model_observation_matches(
                requested_model_id=requested_model_id,
                observed_model_id=observed_model_id,
                selection_kind=selection_kind,
                provider_kind=provider_kind,
            )
        ):
            self.turn_failed(
                identity,
                room_id,
                {
                    "turn_id": active_turn_id,
                    "message": (
                        f"Provider reported model {observed_model_id}, but the session requested {requested_model_id}."
                    ),
                    "error_code": "provider_model_mismatch",
                    "diagnostics": diagnostics,
                },
            )
            raise RoomCommandRejected(
                "Provider used a different model than the exact session selection.",
                code="provider_model_mismatch",
            )
        if not has_room_visible_text(content):
            self.turn_failed(
                identity,
                room_id,
                {
                    "turn_id": active_turn_id,
                    "message": "Provider completed without a room-visible final message.",
                    "error_code": "empty_provider_final",
                    "diagnostics": diagnostics,
                },
            )
            raise RoomCommandRejected("Provider final message was empty.", code="empty_provider_final")
        return PreparedFinalMessage(
            content=content,
            latency=latency,
            diagnostics=diagnostics,
            observed_model_id=observed_model_id,
        )

    def message_final_in_unit(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        prepared: PreparedFinalMessage,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        return self._commit_final_message(
            identity,
            payload,
            prepared=prepared,
            writer=unit,
        )

    def _commit_final_message(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        prepared: PreparedFinalMessage,
        writer: TurnFinalizationWriter,
    ) -> dict[str, object]:
        agent_id, session = self.active_bridge_turn_in_writer(
            identity,
            payload,
            writer=writer,
        )
        require_active_turn_phase(session)
        active_turn_id = str(session["active_turn_id"])
        input_up_to_event_id = clean_lobby_text(session.get("input_up_to_event_id"), limit=128)
        input_up_to_seq = safe_bounded_int(session.get("input_up_to_seq"), default=0, minimum=0)
        event = writer.append_event(
            "message_final",
            participant_id=agent_id,
            participant_type="agent",
            actor_id=agent_id,
            actor_type="agent",
            display_name=session.get("display_name") or agent_id,
            avatar_image_url=session.get("avatar_image_url") or "",
            session_id=session["session_id"],
            turn_id=active_turn_id,
            content=prepared.content,
            source_event_id=session.get("active_source_event_id"),
            relay_depth=int(session.get("active_relay_depth") or 0),
            message_source=payload.get("message_source"),
        )
        writer.advance_attention_state(agent_id, spoke_seq=int(event["seq"]))
        verification_updates: dict[str, object] = {}
        if prepared.observed_model_id:
            requested_model_id = clean_lobby_text(
                session.get("requested_model_id") or session.get("model"),
                limit=128,
            )
            selection_kind = clean_lobby_text(session.get("model_selection_kind"), limit=16) or "exact"
            observation_policy = (
                clean_lobby_text(session.get("model_observation_policy"), limit=32) or "unavailable"
            )
            provider_kind = clean_lobby_text(session.get("provider_kind"), limit=64)
            verification_updates = {
                "observed_model_id": prepared.observed_model_id,
                "model_verification_status": model_verification_status(
                    requested_model_id=requested_model_id,
                    observed_model_id=prepared.observed_model_id,
                    selection_kind=selection_kind,
                    observation_policy=observation_policy,
                    provider_kind=provider_kind,
                ),
            }
        finished, updated = self._complete_active_turn_durable(
            writer,
            session,
            input_up_to_event_id=input_up_to_event_id,
            input_up_to_seq=input_up_to_seq,
            latency=prepared.latency,
            diagnostics=prepared.diagnostics,
            finish_status="completed",
            last_spoke_event_id=str(event["id"]),
            extra_session_updates=verification_updates,
        )
        return {
            "event": event,
            "turn_finished": finished,
            "agent_session": public_session(updated),
        }

    def turn_decline(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        _agent_id, session = self.active_bridge_turn(identity, room_id, payload)
        require_active_turn_phase(session)
        self._require_observation_receipt(
            identity,
            room_id,
            payload,
            session=session,
        )
        reason_code = clean_lobby_text(payload.get("reason_code"), limit=64)
        if reason_code not in SUPPORTED_DECLINE_REASONS:
            raise RoomCommandRejected("A supported decline reason is required.", code="invalid_decline_reason")
        latency = merged_latency(session.get("latency"), payload.get("latency"))
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        finished, current = self._complete_active_turn(
            room_id,
            session,
            input_up_to_event_id=clean_lobby_text(session.get("input_up_to_event_id"), limit=128),
            input_up_to_seq=safe_bounded_int(session.get("input_up_to_seq"), default=0, minimum=0),
            latency=latency,
            diagnostics=diagnostics,
            finish_status="declined",
        )
        return {
            "declined": True,
            "reason_code": reason_code,
            "turn_finished": finished,
            "agent_session": public_session(current),
        }

    def _require_observation_receipt(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
        *,
        session: dict[str, object],
    ) -> None:
        if clean_lobby_text(session.get("provider_input_mode"), limit=32) != "room_observation":
            return
        assigned_seq = safe_bounded_int(
            session.get("input_up_to_seq"),
            default=0,
            minimum=0,
        )
        observed_value = payload.get("observed_through_seq")
        observed_seq = (
            observed_value
            if isinstance(observed_value, int) and not isinstance(observed_value, bool)
            else -1
        )
        if observed_seq >= assigned_seq:
            return
        turn_id = clean_lobby_text(session.get("active_turn_id"), limit=128)
        self.turn_failed(
            identity,
            room_id,
            {
                "turn_id": turn_id,
                "message": "Provider did not confirm reading the assigned room observation.",
                "error_code": "room_observation_unconfirmed",
                "diagnostics": (
                    payload.get("diagnostics")
                    if isinstance(payload.get("diagnostics"), dict)
                    else {}
                ),
            },
        )
        raise RoomCommandRejected(
            "Provider did not confirm reading the assigned room observation.",
            code="room_observation_unconfirmed",
        )

    def turn_failed(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        agent_id, session = self.active_bridge_turn(identity, room_id, payload)
        require_active_turn_phase(session)
        interrupted = clean_lobby_text(payload.get("status"), limit=32) == "interrupted"
        requested_error_code = clean_lobby_text(payload.get("error_code"), limit=64)
        error_code = (
            requested_error_code
            if requested_error_code
            in {
                "adapter_contract_error",
                "empty_provider_final",
                "provider_model_mismatch",
                "provider_model_unobserved",
                "room_observation_unconfirmed",
            }
            else ("interrupted" if interrupted else "provider_turn_failed")
        )
        content = (
            clean_lobby_text(payload.get("message") or payload.get("content"), limit=4000)
            or "Provider turn failed."
        )
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        pending = dedupe_event_ids(
            [*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])]
        )
        pending_modes = pending_event_modes(session, list(session.get("pending_event_ids") or []))
        active_input_mode = clean_lobby_text(session.get("provider_input_mode"), limit=32)
        for event_id in list(session.get("inflight_event_ids") or []):
            pending_modes[clean_lobby_text(event_id, limit=128)] = (
                "room_observation"
                if active_input_mode == "room_observation"
                else "transcript"
            )
        recovery_attempt_count = int(session.get("recovery_attempt_count") or 0)
        automatic_recovery = bool(
            not interrupted
            and recovery_attempt_count < 1
            and session.get("enabled")
            and self.broker.has_bridge(room_id, agent_id)
            and provider_process_exited(content, diagnostics)
        )
        active_attention_source = clean_lobby_text(
            session.get("active_attention_source_event_id"),
            limit=128,
        )
        if active_attention_source and not automatic_recovery:
            pending = [event_id for event_id in pending if event_id != active_attention_source]
        with self.store.transaction(room_id) as transaction:
            error = transaction.append_event(
                "error",
                participant_id=agent_id,
                session_id=session["session_id"],
                turn_id=session["active_turn_id"],
                content=content,
                error_code=error_code,
                diagnostics=public_runtime_diagnostics(diagnostics),
            )
            transaction.append_event(
                "turn_finished",
                participant_id=agent_id,
                session_id=session["session_id"],
                turn_id=session["active_turn_id"],
                status="interrupted" if interrupted else "error",
            )
            if not automatic_recovery:
                self._turn_attention.resolve_active(
                    transaction,
                    session,
                    status="cancelled",
                )
            attention_fields = (
                self._turn_attention.delivery_failed_fields(session)
                if automatic_recovery
                else self._turn_attention.empty_fields()
            )
            updated = transaction.update_session_fields(
                str(session["session_id"]),
                status="attached" if interrupted or automatic_recovery else "error",
                runtime_status="idle" if interrupted else ("recovering" if automatic_recovery else "error"),
                turn_phase="",
                active_turn_id="",
                active_source_event_id="",
                active_relay_depth=0,
                input_up_to_event_id="",
                input_up_to_seq=0,
                inflight_event_ids=[],
                pending_event_ids=pending,
                pending_event_modes={
                    event_id: pending_modes.get(event_id, "transcript")
                    for event_id in pending
                },
                pending_input_mode=(
                    pending_modes.get(pending[0], "transcript")
                    if pending
                    else ""
                ),
                **attention_fields,
                recovery_required=not interrupted,
                recovery_attempt_count=recovery_attempt_count + (1 if automatic_recovery else 0),
                last_error=content,
                **runtime_diagnostic_fields(diagnostics),
            )
        self._publish_session_state(room_id, updated)
        if automatic_recovery:
            key = (room_id, str(session["session_id"]))
            self._recovery_handles[key] = self._recovery_scheduler(
                self.recovery_delay_seconds,
                lambda: self._retry_pending_turn(room_id, str(session["session_id"])),
            )
        return {"event": error, "agent_session": public_session(updated)}

    def _complete_active_turn(
        self,
        room_id: str,
        session: dict[str, object],
        *,
        input_up_to_event_id: str,
        input_up_to_seq: int,
        latency: dict[str, object],
        diagnostics: dict[str, object],
        finish_status: str,
        last_spoke_event_id: str = "",
    ) -> tuple[dict[str, object], dict[str, object]]:
        with self.store.transaction(room_id) as transaction:
            finished, updated = self._complete_active_turn_durable(
                transaction,
                session,
                input_up_to_event_id=input_up_to_event_id,
                input_up_to_seq=input_up_to_seq,
                latency=latency,
                diagnostics=diagnostics,
                finish_status=finish_status,
                last_spoke_event_id=last_spoke_event_id,
            )
        current = self._after_completed_turn(
            room_id,
            participant_id=str(session["participant_id"]),
            session_id=str(session["session_id"]),
            publish_state=True,
        )
        return finished, current

    def _complete_active_turn_durable(
        self,
        writer: TurnFinalizationWriter,
        session: dict[str, object],
        *,
        input_up_to_event_id: str,
        input_up_to_seq: int,
        latency: dict[str, object],
        diagnostics: dict[str, object],
        finish_status: str,
        last_spoke_event_id: str = "",
        extra_session_updates: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        active_turn_id = str(session["active_turn_id"])
        state_before = writer.attention_state(str(session["participant_id"]))
        canonical_before = assert_provider_sync_cursor_parity(session, state_before)
        provider_sync_target = input_up_to_seq or canonical_before
        if provider_sync_target < canonical_before:
            raise ProviderSyncCursorParityError(
                "Provider turn completion cannot move the canonical sync cursor backward."
            )
        provider_sync_event_id = (
            input_up_to_event_id or session.get("last_provider_sync_event_id") or ""
        )
        updates: dict[str, object] = {
            "status": "attached",
            "runtime_status": "idle",
            "turn_phase": "",
            "active_turn_id": "",
            "active_source_event_id": "",
            "active_relay_depth": 0,
            "input_up_to_event_id": "",
            "input_up_to_seq": 0,
            "inflight_event_ids": [],
            "active_attention_job_id": "",
            "active_attention_lease_id": "",
            "active_attention_source_event_id": "",
            "last_seen_event_id": provider_sync_event_id or session.get("last_seen_event_id") or "",
            "last_seen_seq": provider_sync_target,
            "bootstrap_done": True,
            "recovery_required": False,
            "recovery_attempt_count": 0,
            "turn_count": int(session.get("turn_count") or 0) + 1,
            "latency": latency,
            "last_error": "",
            **runtime_diagnostic_fields(diagnostics),
            **dict(extra_session_updates or {}),
        }
        if last_spoke_event_id:
            updates["last_spoke_event_id"] = last_spoke_event_id
        finished = writer.append_event(
            "turn_finished",
            participant_id=session["participant_id"],
            session_id=session["session_id"],
            turn_id=active_turn_id,
            status=finish_status,
            latency=latency,
        )
        self._turn_attention.resolve_active(
            writer,
            session,
            status="released",
        )
        state = writer.advance_attention_state(
            str(session["participant_id"]),
            provider_sync_seq=provider_sync_target,
        )
        updates.update(
            provider_sync_session_fields(
                state,
                event_id=clean_lobby_text(provider_sync_event_id, limit=128),
            )
        )
        updated = writer.update_session_fields(
            str(session["session_id"]),
            **updates,
        )
        assert_provider_sync_cursor_parity(updated, state)
        return finished, updated

    def after_message_final(
        self,
        room_id: str,
        result: dict[str, object],
        *,
        deduplicated: bool,
    ) -> dict[str, object]:
        session = result.get("agent_session") if isinstance(result.get("agent_session"), dict) else {}
        participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
        session_id = clean_lobby_text(session.get("session_id"), limit=128)
        if not participant_id or not session_id:
            return {}
        return self._after_completed_turn(
            room_id,
            participant_id=participant_id,
            session_id=session_id,
            publish_state=not deduplicated,
        )

    def _after_completed_turn(
        self,
        room_id: str,
        *,
        participant_id: str,
        session_id: str,
        publish_state: bool,
    ) -> dict[str, object]:
        if self.store.room_settings(room_id).get("conversation_mode") == "ordered":
            self.assign_next_ordered_pending(room_id)
        else:
            self.assign_pending(room_id, participant_id)
        current = self.store.session(room_id, session_id)
        if publish_state:
            self._publish_session_state(room_id, current)
        return current

    def _retry_pending_turn(self, room_id: str, session_id: str) -> None:
        key = (room_id, session_id)
        with self._lock:
            self._recovery_handles.pop(key, None)
            session = self.store.session(room_id, session_id)
            if (
                self._is_closed()
                or not session
                or not session.get("enabled")
                or session.get("runtime_status") != "recovering"
                or not self.broker.has_bridge(room_id, str(session.get("participant_id") or session_id))
            ):
                return
            participant_id = str(session.get("participant_id") or session_id)
            updated = self.store.update_session_fields(
                room_id,
                session_id,
                status="attached",
                runtime_status="idle",
            )
            self._publish_session_state(room_id, updated)
            if not self.assign_pending(room_id, participant_id):
                current = self.store.session(room_id, session_id)
                if current and current.get("pending_event_ids"):
                    failed = self.store.update_session_fields(
                        room_id,
                        session_id,
                        status="error",
                        runtime_status="error",
                        recovery_required=True,
                        last_error="Automatic provider recovery could not reassign the pending turn.",
                    )
                    self._publish_session_state(room_id, failed)


def dedupe_event_ids(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = clean_lobby_text(value, limit=128)
        if text and text not in result:
            result.append(text)
    return result


def pending_event_modes(
    session: dict[str, object],
    event_ids: list[str],
) -> dict[str, str]:
    raw_modes = (
        session.get("pending_event_modes")
        if isinstance(session.get("pending_event_modes"), dict)
        else {}
    )
    legacy_mode = (
        "room_observation"
        if clean_lobby_text(session.get("pending_input_mode"), limit=32)
        == "room_observation"
        else "transcript"
    )
    modes: dict[str, str] = {}
    for event_id in dedupe_event_ids(list(event_ids)):
        raw_mode = clean_lobby_text(raw_modes.get(event_id), limit=32)
        modes[event_id] = (
            "room_observation"
            if raw_mode == "room_observation"
            else ("transcript" if raw_mode == "transcript" else legacy_mode)
        )
    return modes


def ordered_pending_subset(
    original: list[str],
    retained: list[str],
) -> list[str]:
    retained_ids = set(dedupe_event_ids(list(retained)))
    result = [
        event_id
        for event_id in dedupe_event_ids(list(original))
        if event_id in retained_ids
    ]
    return dedupe_event_ids([*result, *retained])


def pending_mode_fields(
    session: dict[str, object],
    pending_event_ids: list[str],
) -> dict[str, object]:
    pending = dedupe_event_ids(list(pending_event_ids))
    modes = pending_event_modes(session, list(session.get("pending_event_ids") or []))
    active_mode = (
        "room_observation"
        if clean_lobby_text(session.get("provider_input_mode"), limit=32)
        == "room_observation"
        else "transcript"
    )
    for event_id in list(session.get("inflight_event_ids") or []):
        clean_event_id = clean_lobby_text(event_id, limit=128)
        if clean_event_id:
            modes[clean_event_id] = active_mode
    retained_modes = {
        event_id: modes.get(event_id, "transcript")
        for event_id in pending
    }
    return {
        "pending_event_modes": retained_modes,
        "pending_input_mode": (
            retained_modes.get(pending[0], "transcript")
            if pending
            else ""
        ),
    }


def _attachment_ids(
    store: RoomRepository,
    room_id: str,
    event_ids: list[str],
) -> list[str]:
    attachment_ids: list[str] = []
    for event_id in event_ids:
        event = store.event_by_id(room_id, event_id)
        attachments = event.get("attachments") if isinstance(event.get("attachments"), list) else []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            attachment_id = clean_lobby_text(attachment.get("id"), limit=64)
            if attachment_id and attachment_id not in attachment_ids:
                attachment_ids.append(attachment_id)
    return attachment_ids


_ACTIVE_TURN_PHASES = frozenset({"thinking", "streaming"})
_TURN_PHASE_TRANSITIONS = {
    "thinking": frozenset({"thinking", "streaming"}),
    "streaming": frozenset({"streaming"}),
}


def require_active_turn_phase(session: dict[str, object]) -> str:
    phase = clean_lobby_text(session.get("turn_phase"), limit=32)
    if phase not in _ACTIVE_TURN_PHASES:
        raise RoomCommandRejected("The active turn has an invalid phase.", code="turn_phase_invalid")
    return phase


def validate_turn_phase_transition(session: dict[str, object], phase: str) -> None:
    current = require_active_turn_phase(session)
    if phase not in _ACTIVE_TURN_PHASES or phase not in _TURN_PHASE_TRANSITIONS[current]:
        raise RoomCommandRejected(
            f"Turn phase cannot transition from {current} to {phase or 'empty'}.",
            code="turn_phase_invalid",
        )


def message_delta_text(value: object, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")[:limit]


def room_message_text(value: object, *, limit: int) -> str:
    return message_delta_text(value, limit=limit).strip()


def provider_process_exited(message: str, diagnostics: dict[str, object]) -> bool:
    lower = str(message or "").casefold()
    if any(
        marker in lower
        for marker in (
            "401",
            "unauthorized",
            "authentication",
            "authenticate",
            "login required",
            "configured command missing",
            "permission denied",
        )
    ):
        return False
    running = diagnostics.get("running")
    returncode = diagnostics.get("returncode")
    if running is False and returncode not in (None, ""):
        return True
    return "runtime exited with return code" in lower or "runtime stopped while reading" in lower


def safe_bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    parsed = max(int(minimum), parsed)
    if maximum is not None:
        parsed = min(int(maximum), parsed)
    return parsed


def now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "EnsureRoom",
    "PendingEventPartition",
    "PreparedFinalMessage",
    "ProviderLookup",
    "RecoveryScheduler",
    "RoomTurnCoordinator",
    "SessionCallback",
    "TurnFinalizationWriter",
    "TurnPacketBuilder",
    "dedupe_event_ids",
    "ordered_pending_subset",
    "pending_mode_fields",
    "pending_event_modes",
    "message_delta_text",
    "now",
    "provider_process_exited",
    "require_active_turn_phase",
    "room_message_text",
    "safe_bounded_int",
    "validate_turn_phase_transition",
]
