from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging
import threading
import shutil
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from agentsassemble.agent_sessions import build_room_turn_packet
from agentsassemble.cleanup_report import CleanupReport, emit_cleanup_failure
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.native_cli_providers import (
    NativeCliProviderSpec,
    StoredProviderProfileError,
    UnsupportedNativeCliProvider,
    default_native_cli_provider_specs,
    native_cli_provider_definition,
    native_cli_provider_spec_from_stored_session_strict,
    native_cli_provider_spec_from_payload,
    validate_native_cli_provider_spec,
)
from agentsassemble.provider_capabilities import (
    PROVIDER_CAPABILITIES,
    ProviderCatalogSelectionError,
    ValidatedProviderSelection,
)
from agentsassemble.provider_runtime_contracts import (
    AdapterContractError,
    ProviderRuntimeHealth,
)
from agentsassemble.provider_runtime_config import (
    ProviderRuntimeConfigError,
    ProviderRuntimeProfile,
)
from agentsassemble.identity_store import identity_store_for_output_root
from agentsassemble.room_invite_application import InviteApplicationService
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.room_commands import (
    RoomCommandValidationError,
    capabilities_for_identity,
    parse_room_command,
)
from agentsassemble.room_command_uow import (
    RoomCommandIdempotencyConflict,
    RoomCommandUnitOfWork,
    command_payload_hash,
)
from agentsassemble.room_agent_lifecycle import (
    AgentBridgeManager,
    RecoveryScheduler,
    RoomAgentLifecycle,
    schedule_daemon_timer,
)
from agentsassemble.room_attention_coordinator import RoomAttentionCoordinator
from agentsassemble.room_attention_reconciliation import RoomAttentionReconciler
from agentsassemble.room_attention_policy import (
    normalize_shadow_attention_mode,
    should_record_shadow_attention,
)
from agentsassemble.room_errors import RoomCommandRejected
from agentsassemble.room_event_broker import ROOM_EVENT_STREAM, RoomEventBroker, RoomSocketChannel
from agentsassemble.room_floor_policy import (
    AgentFloorEligibility,
    continuous_floor_targets,
    evaluate_agent_floor_eligibility,
)
from agentsassemble.room_members import is_room_member_muted, remove_room_member, set_room_member_muted
from agentsassemble.provider_model_verification import model_verification_status as _model_verification_status
from agentsassemble.room_projection import (
    public_event as _public_event,
    public_participant,
    public_session,
    runtime_diagnostic_fields as _runtime_diagnostic_fields,
)
from agentsassemble.room_provider_sync_cursor import (
    ProviderSyncCursorParityError,
    ProviderSyncCursorReconciler,
    assert_provider_sync_cursor_parity,
)
from agentsassemble.room_routing import route_message_targets
from agentsassemble.room_repository import RoomRepository
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room_turn_coordinator import (
    RoomTurnCoordinator,
    dedupe_event_ids as _dedupe_text_list,
    room_message_text as _room_message_text,
)
from agentsassemble.room_types import RoomCommand, RoomEvent
from agentsassemble.voice_presence import leave_all_voice

ROOM_SNAPSHOT_EVENT_LIMIT = 200
ROOM_HISTORY_MAX_LIMIT = 200
AMBIENT_AGENT_RELAY_DEPTH = 2
AGENT_RUNTIME_PROFILE_KEYS = frozenset(
    {
        "provider_id",
        "provider_kind",
        "workspace",
        "model",
        "reasoning_effort",
        "service_tier",
        "variant",
        "permission_mode",
        "transport",
    }
)
_LOGGER = logging.getLogger(__name__)


class ProviderCatalog(Protocol):
    def snapshot(self, *, refresh: bool = False) -> dict[str, object]: ...

    def subscribe(self, listener: Callable[[dict[str, object]], None]) -> Callable[[], None]: ...

    def validate_selection(
        self,
        *,
        catalog_revision: str,
        provider_id: str,
        values: dict[str, str],
    ) -> ValidatedProviderSelection: ...


class RoomRealtimeController:
    """Canonical room command, event, session, and provider-turn coordinator."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        invite_application: InviteApplicationService,
        room_sessions: RoomSessionService,
        providers: list[NativeCliProviderSpec] | None = None,
        bridge_manager: AgentBridgeManager | None = None,
        broker: RoomEventBroker | None = None,
        default_room_id: str = "general",
        max_agent_relay_depth: int = 2,
        recovery_delay_seconds: float = 1.0,
        external_stop_timeout_seconds: float = 2.0,
        recovery_scheduler: RecoveryScheduler | None = None,
        provider_catalog: ProviderCatalog | None = None,
        repository: RoomRepository | None = None,
        attention_shadow_mode: str = "off",
    ) -> None:
        self.output_root = Path(output_root)
        self.store = repository or RoomStore(self.output_root)
        self._invite_application = invite_application
        self._room_sessions = room_sessions
        self.broker = broker or RoomEventBroker()
        self.default_room_id = clean_lobby_text(default_room_id, limit=128) or "general"
        self.max_agent_relay_depth = max(0, int(max_agent_relay_depth))
        self.attention_shadow_mode = normalize_shadow_attention_mode(attention_shadow_mode)
        self.recovery_delay_seconds = max(0.0, float(recovery_delay_seconds))
        recovery_scheduler_impl = recovery_scheduler or schedule_daemon_timer
        self.provider_catalog = provider_catalog or PROVIDER_CAPABILITIES
        default_providers = {
            clean_lobby_text(spec.agent_id, limit=128): spec
            for spec in list(providers or [])
            if clean_lobby_text(spec.agent_id, limit=128)
        }
        self._providers_by_room: dict[str, dict[str, NativeCliProviderSpec]] = {
            self.default_room_id: {},
        }
        self._lock = threading.RLock()
        self._event_listener_removers: dict[str, Callable[[], None]] = {}
        self._provider_catalog_remove = self.provider_catalog.subscribe(self._on_provider_catalog_update)
        self._closed = False
        self._attention_coordinator = RoomAttentionCoordinator(self.store)
        self._attention_owner_id = f"room-realtime-{uuid4().hex}"
        self._attention_shadow_error_count = 0
        self._attention_shadow_last_error = ""
        self._attention_shadow_recorded_count = 0
        self._attention_shadow_skipped_count = 0
        self._attention_active_error_count = 0
        self._attention_active_last_error = ""
        self._turn_coordinator = RoomTurnCoordinator(
            self.output_root,
            store=self.store,
            broker=self.broker,
            lock=self._lock,
            provider_lookup=self._provider,
            ensure_room=self.ensure_room,
            publish_session_state=self._publish_session_state,
            is_closed=lambda: self._closed,
            recovery_delay_seconds=self.recovery_delay_seconds,
            recovery_scheduler=recovery_scheduler_impl,
            packet_builder=lambda *args, **kwargs: build_room_turn_packet(
                *args,
                repository=self.store,
                **kwargs,
            ),
            attention_owner_id=self._attention_owner_id,
        )
        self._agent_lifecycle = RoomAgentLifecycle(
            store=self.store,
            broker=self.broker,
            bridge_manager=bridge_manager,
            lock=self._lock,
            provider_lookup=self._provider,
            ensure_provider_session=self._ensure_provider_session,
            revoke_participant_sessions=lambda room_id, participant_id: self._room_sessions.revoke_participant(
                room_id, participant_id
            ),
            publish_session_state=self._publish_session_state,
            assign_pending=self._turn_coordinator.assign_pending,
            is_closed=lambda: self._closed,
            recovery_delay_seconds=self.recovery_delay_seconds,
            external_stop_timeout_seconds=external_stop_timeout_seconds,
            recovery_scheduler=recovery_scheduler_impl,
            prepare_session_reset=self._turn_coordinator.prepare_session_reset,
        )
        self.last_cleanup_report = CleanupReport("room_realtime_controller")
        self.ensure_room(self.default_room_id)
        self._restore_server_owned_providers()
        for agent_id, spec in default_providers.items():
            if self.store.session(self.default_room_id, agent_id) or self.store.participant(
                self.default_room_id, agent_id
            ):
                continue
            self._providers_by_room[self.default_room_id][agent_id] = spec
            self._ensure_provider_session(self.default_room_id, spec)
        self._reconcile_startup_sessions()
        self._provider_sync_cursor_reconciliation_report = ProviderSyncCursorReconciler(
            self.store
        ).reconcile()
        self._attention_reconciliation_report = RoomAttentionReconciler(self.store).reconcile()

    def _restore_server_owned_providers(self) -> None:
        """Rebuild startable provider specs from durable Agent Sessions."""
        for room in self.store.list_rooms(include_archived=True):
            room_id = clean_lobby_text(room.get("room_id"), limit=128)
            if not room_id:
                continue
            self.ensure_room(room_id)
            for session in self.store.sessions(room_id):
                agent_id = clean_lobby_text(session.get("participant_id"), limit=128)
                if not agent_id or agent_id in self._room_providers(room_id):
                    continue
                if self.store.participant(room_id, agent_id).get("status") == "kicked":
                    continue
                process_ownership = _restorable_process_ownership(session)
                if process_ownership != "server":
                    continue
                try:
                    spec = native_cli_provider_spec_from_stored_session_strict(session)
                except StoredProviderProfileError as error:
                    self.store.update_session_fields(
                        room_id,
                        agent_id,
                        status="error",
                        runtime_status="error",
                        enabled=False,
                        recovery_required=True,
                        last_error=str(error),
                        last_error_code=error.code,
                    )
                    continue
                profile_changed = (
                    session.get("runtime_profile_key") != spec.runtime_profile_key()
                    or session.get("transport") != spec.transport
                )
                migration_blocked = session.get("last_error_code") == "profile_migration_required" or (
                    not session.get("last_error_code")
                    and session.get("last_error")
                    == "Stored Agent Session profile must be migrated before it can be reused."
                )
                if profile_changed or migration_blocked:
                    updates: dict[str, object] = {}
                    if profile_changed:
                        updates.update(
                            runtime_profile_key=spec.runtime_profile_key(),
                            transport=spec.transport,
                        )
                    if migration_blocked:
                        updates.update(
                            status="available",
                            runtime_status="stopped",
                            enabled=False,
                            recovery_required=False,
                            last_error="",
                            last_error_code="",
                        )
                    self.store.update_session_fields(
                        room_id,
                        agent_id,
                        **updates,
                    )
                with self._lock:
                    self._providers_by_room.setdefault(room_id, {})[agent_id] = spec

    def create_provider_session(self, room_id: str, spec: NativeCliProviderSpec) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        validate_native_cli_provider_spec(spec)
        self.ensure_room(clean_room_id)
        with self._lock:
            if self.store.session(clean_room_id, spec.agent_id) or self.store.participant(
                clean_room_id, spec.agent_id
            ):
                raise RoomCommandRejected(
                    "An Agent Session with this identity already exists; re-add or configure the existing session instead.",
                    code="session_exists",
                )
            providers = self._providers_by_room.setdefault(clean_room_id, {})
            providers[clean_lobby_text(spec.agent_id, limit=128)] = spec
            self._ensure_provider_session(clean_room_id, spec)
            current = self.store.session(clean_room_id, spec.agent_id)
            self.store.append_event(
                clean_room_id,
                "agent_session_created",
                participant_id=spec.agent_id,
                session_id=spec.agent_id,
                provider_kind=spec.normalized_provider_kind(),
            )
            self._publish_session_state(clean_room_id, current)
        return public_session(current)

    def configure_stopped_provider_profile(
        self,
        room_id: str,
        spec: NativeCliProviderSpec,
    ) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        validate_native_cli_provider_spec(spec)
        with self._lock:
            current = self.store.session(clean_room_id, spec.agent_id)
            participant = self.store.participant(clean_room_id, spec.agent_id)
            if not current or not participant:
                raise RoomCommandRejected(
                    f"Agent session {spec.agent_id} was not found.",
                    code="not_found",
                )
            if (
                current.get("enabled")
                or current.get("runtime_status") in {"starting", "idle", "busy", "paused", "recovering", "stopping"}
                or current.get("active_turn_id")
                or current.get("bridge_handle_id")
                or self.broker.has_bridge(clean_room_id, spec.agent_id)
            ):
                raise RoomCommandRejected(
                    "Stop this Agent Session before changing its runtime settings.",
                    code="runtime_profile_conflict",
                )
            self._providers_by_room.setdefault(clean_room_id, {})[spec.agent_id] = spec
            self._ensure_provider_session(clean_room_id, spec)
            self.store.update_session_fields(
                clean_room_id,
                spec.agent_id,
                observed_model_id="",
                model_verification_status=_model_verification_status(
                    requested_model_id=spec.requested_model_id or spec.model,
                    observed_model_id="",
                    selection_kind=spec.model_selection_kind,
                    observation_policy=spec.model_observation_policy,
                    provider_kind=spec.normalized_provider_kind(),
                ),
            )
            updated = self.store.session(clean_room_id, spec.agent_id)
            self.store.append_event(
                clean_room_id,
                "agent_session_profile_updated",
                participant_id=spec.agent_id,
                session_id=spec.agent_id,
                runtime_profile_key=spec.runtime_profile_key(),
            )
            self._publish_session_state(clean_room_id, updated)
        return public_session(updated)

    def _reconcile_startup_sessions(self) -> None:
        active_states = {"starting", "idle", "busy", "paused", "recovering", "stopping"}
        for room in self.store.list_rooms(include_archived=True):
            room_id = clean_lobby_text(room.get("room_id"), limit=128)
            if not room_id:
                continue
            for session in self.store.sessions(room_id):
                if session.get("runtime_status") not in active_states:
                    continue
                pending = _dedupe_text_list(
                    [
                        *list(session.get("inflight_event_ids") or []),
                        *list(session.get("pending_event_ids") or []),
                    ]
                )
                attention_reset = self._turn_coordinator.reconcile_session_attention(
                    room_id,
                    session,
                    pending_event_ids=pending,
                )
                session_id = clean_lobby_text(session.get("session_id"), limit=128)
                updated = self.store.update_session_fields(
                    room_id,
                    session_id,
                    status="unavailable",
                    runtime_status="disconnected",
                    pid=None,
                    reported_provider_pid=None,
                    bridge_pid=None,
                    bridge_handle_id="",
                    active_turn_id="",
                    turn_phase="",
                    inflight_event_ids=[],
                    **attention_reset,
                    recovery_required=True,
                    last_error="Server restarted without a current bridge lease or owned process handle.",
                )
                participant_id = clean_lobby_text(updated.get("participant_id"), limit=128)
                if participant_id and self.store.participant(room_id, participant_id):
                    self.store.update_participant_fields(room_id, participant_id, status="detached")

    def ensure_room(self, room_id: str) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        with self._lock:
            room = self.store.create_room(clean_room_id, label="#general" if clean_room_id == "general" else clean_room_id)
            if clean_room_id not in self._event_listener_removers:
                self._event_listener_removers[clean_room_id] = self.store.add_event_listener(
                    clean_room_id,
                    self._on_event_appended,
                )
            return room

    def connect(self, identity: dict[str, object]) -> RoomSocketChannel:
        room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        self.ensure_room(room_id)
        if identity.get("client_type") == "agent_bridge":
            self._ensure_external_bridge_session(room_id, identity)
        else:
            participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
            if participant_id:
                existing = self.store.participant(room_id, participant_id)
                if not existing:
                    self.store.upsert_participant(
                        room_id,
                        {
                            "participant_id": participant_id,
                            "display_name": clean_lobby_text(identity.get("display_name"), limit=64) or participant_id,
                            "participant_type": "human",
                            "role": "host" if identity.get("operator") else "member",
                            "status": "joined",
                        },
                    )
                elif existing.get("status") not in {"left", "kicked"}:
                    self.store.update_participant_fields(
                        room_id,
                        participant_id,
                        display_name=clean_lobby_text(identity.get("display_name"), limit=64) or participant_id,
                    )
        return self.broker.connect(identity)

    def _ensure_external_bridge_session(self, room_id: str, identity: dict[str, object]) -> None:
        participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        if not participant_id:
            return
        existing_session = self.store.session(room_id, participant_id)
        if existing_session.get("process_ownership") == "server":
            return
        display_name = clean_lobby_text(identity.get("display_name"), limit=64) or participant_id
        provider_kind = clean_lobby_text(identity.get("provider_kind"), limit=64) or "external_agent"
        definition = native_cli_provider_definition(provider_kind)
        if definition is not None:
            provider_kind = definition.provider_kind
        spec = NativeCliProviderSpec(
            agent_id=participant_id,
            display_name=display_name,
            command=("external-attendee",),
            cwd=".",
            provider_kind=provider_kind,
            model_observation_policy=(
                definition.model_observation_policy if definition is not None else "unavailable"
            ),
            runtime_kind="external_bridge",
            transport="websocket",
            default_responder=True,
        )
        with self._lock:
            self._providers_by_room.setdefault(room_id, {})[participant_id] = spec
        if existing_session:
            return
        self.store.upsert_participant(
            room_id,
            {
                "participant_id": participant_id,
                "display_name": display_name,
                "participant_type": "agent",
                "role": "agent",
                "owner_id": clean_lobby_text(identity.get("owner_id"), limit=128),
                "provider_kind": provider_kind,
                "connection_kind": "native_cli_bridge",
                "status": "detached",
            },
        )
        with self.store.transaction(room_id) as transaction:
            created_session, _ = transaction.upsert_session(
                {
                    "session_id": participant_id,
                    "participant_id": participant_id,
                    "display_name": display_name,
                    "status": "available",
                    "provider_kind": provider_kind,
                    "runtime_kind": "external_bridge",
                    "connection_kind": "native_cli_bridge",
                    "runtime_profile_key": "",
                    "model_observation_policy": spec.model_observation_policy,
                    "model_verification_status": (
                        "pending"
                        if spec.model_observation_policy == "required"
                        else "unavailable"
                    ),
                    "enabled": True,
                    "runtime_status": "starting",
                    "pending_event_ids": [],
                    "inflight_event_ids": [],
                    "turn_count": 0,
                    "last_provider_sync_event_id": "",
                    "last_provider_sync_seq": 0,
                    "last_seen_event_id": "",
                    "last_seen_seq": 0,
                    "bootstrap_cutoff_seq": 0,
                    "external_owned": True,
                    "process_ownership": "external",
                    "reported_provider_pid": None,
                    "bridge_handle_id": "",
                    "bridge_generation": 0,
                    "pty": False,
                    "transport": "websocket",
                    "is_one_shot": False,
                },
            )
            state = transaction.advance_attention_state(
                participant_id,
                provider_sync_seq=0,
            )
            assert_provider_sync_cursor_parity(created_session, state)
            transaction.append_event(
                "agent_session_created",
                participant_id=participant_id,
                session_id=participant_id,
                provider_kind=provider_kind,
                external_owned=True,
            )

    def disconnect(self, channel: RoomSocketChannel) -> None:
        identity = channel.identity
        was_active = self.broker.disconnect(channel)
        if identity.get("client_type") != "agent_bridge":
            return
        if not was_active:
            return
        room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        session_id = clean_lobby_text(identity.get("session_id") or identity.get("agent_id"), limit=128)
        session = self.store.session(room_id, session_id)
        if (
            not session
            or session.get("runtime_status") in {"stopping", "stopped"}
            or not session.get("enabled")
        ):
            return
        self.store.update_session_fields(
            room_id,
            session_id,
            status="unavailable",
            runtime_status="disconnected",
            pid=None,
            last_error="Agent bridge disconnected.",
        )
        participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
        if participant_id and self.store.participant(room_id, participant_id):
            self.store.update_participant_fields(room_id, participant_id, status="detached")
        self.store.append_event(
            room_id,
            "session_detached",
            participant_id=participant_id,
            session_id=session_id,
            reason="agent bridge disconnected",
        )
        self._publish_session_state(room_id, self.store.session(room_id, session_id))

    def snapshot(self, identity: dict[str, object], *, after_seq: int = 0) -> dict[str, object]:
        room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        self.ensure_room(room_id)
        latest_seq = self.store.latest_event_sequence(room_id)
        requested_after_seq = max(0, int(after_seq or 0))
        bridge = identity.get("client_type") == "agent_bridge"
        resume_gap = False
        if bridge:
            events: list[dict[str, object]] = []
            snapshot_mode = "bridge"
        elif requested_after_seq:
            resume_gap = latest_seq - requested_after_seq > ROOM_SNAPSHOT_EVENT_LIMIT
            if resume_gap:
                events = self.store.read_events(room_id, limit=ROOM_SNAPSHOT_EVENT_LIMIT, newest=True)
                snapshot_mode = "gap"
            else:
                events = self.store.read_events(
                    room_id,
                    after_seq=requested_after_seq,
                    limit=ROOM_SNAPSHOT_EVENT_LIMIT,
                )
                snapshot_mode = "resume"
        else:
            events = self.store.read_events(room_id, limit=ROOM_SNAPSHOT_EVENT_LIMIT, newest=True)
            snapshot_mode = "initial"
        oldest_seq = int(events[0].get("seq") or 0) if events else 0
        has_more_before = bool(
            not bridge
            and oldest_seq
            and self.store.oldest_event_sequence(room_id) < oldest_seq
        )
        stored_sessions = self.store.sessions(room_id)
        if bridge:
            own_session_id = clean_lobby_text(identity.get("session_id") or identity.get("agent_id"), limit=128)
            stored_sessions = [session for session in stored_sessions if session.get("session_id") == own_session_id]
        sessions = [public_session(session) for session in stored_sessions]
        events = [_public_event(event) for event in events]
        active_turns = [
            {
                "turn_id": session.get("active_turn_id"),
                "participant_id": session.get("participant_id"),
                "phase": session.get("turn_phase") or session.get("runtime_status"),
            }
            for session in sessions
            if session.get("active_turn_id")
        ]
        provider_catalog = {"status": "ready", "catalog_revision": "", "providers": []}
        if not bridge:
            provider_catalog = self.provider_catalog.snapshot()
        return {
            "op": "snapshot",
            "stream": ROOM_EVENT_STREAM,
            "room": self.store.room(room_id),
            "participants": (
                [
                    public_participant(participant)
                    for participant in self.store.participants(room_id)
                    if participant.get("participant_id") == identity.get("agent_id")
                ]
                if bridge
                else [
                    public_participant(participant)
                    for participant in self.store.participants(room_id)
                ]
            ),
            "agent_sessions": sessions,
            "active_turns": active_turns,
            "events": events,
            "oldest_seq": oldest_seq,
            "last_seq": latest_seq,
            "has_more_before": has_more_before,
            "resume_gap": resume_gap,
            "snapshot_mode": snapshot_mode,
            "provider_catalog": provider_catalog,
            "available_providers": list(provider_catalog.get("providers") or []),
            "capabilities": self.capabilities(identity),
        }

    def history_page(self, room_id: str, *, before_seq: int, limit: int = ROOM_HISTORY_MAX_LIMIT) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        self.ensure_room(clean_room_id)
        clean_before_seq = max(0, int(before_seq or 0))
        clean_limit = min(ROOM_HISTORY_MAX_LIMIT, max(1, int(limit or ROOM_HISTORY_MAX_LIMIT)))
        events = self.store.read_events(
            clean_room_id,
            before_seq=clean_before_seq,
            limit=clean_limit,
            newest=True,
        )
        oldest_seq = int(events[0].get("seq") or 0) if events else 0
        return {
            "events": events,
            "oldest_seq": oldest_seq,
            "has_more_before": bool(
                oldest_seq and self.store.oldest_event_sequence(clean_room_id) < oldest_seq
            ),
            "last_seq": self.store.latest_event_sequence(clean_room_id),
        }

    def capabilities(self, identity: dict[str, object]) -> dict[str, bool]:
        return capabilities_for_identity(identity)

    def request_agent_turn(
        self,
        room_id: str,
        agent_id: str,
        *,
        source_event_id: str = "",
    ) -> dict[str, object]:
        """Let a server-owned floor scheduler assign a turn without a visible mention."""
        return self._turn_coordinator.request_turn(
            room_id,
            agent_id,
            source_event_id=source_event_id,
        )

    def handle_command(
        self,
        identity: dict[str, object],
        message: RoomCommand | dict[str, object],
        *,
        server_url: str = "",
        ticket_issuer: Callable[[dict[str, object]], str] | None = None,
    ) -> dict[str, object]:
        room_id = clean_lobby_text(identity.get("meeting_id"), limit=128)
        try:
            command = parse_room_command(dict(message))
        except RoomCommandValidationError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        request_id = command.request_id
        action = command.action
        payload = command.payload
        if action == "bridge.stopped":
            self._require_bridge(identity)
            result = self._agent_lifecycle.confirm_external_stopped(
                room_id,
                clean_lobby_text(identity.get("agent_id"), limit=128),
                generation=int(identity.get("bridge_generation") or 0),
                payload=payload,
            )
            return {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "action": action,
                "result": result,
                "deduplicated": False,
            }
        if action == "room.observed":
            self._require_bridge(identity)
            # This checkpoint is repository-atomic and generation-validated.
            # It must also bypass ensure_room's lifecycle lock so a bridge can
            # flush while a remote stop waits for that bridge's confirmation.
            result = self._turn_coordinator.observe_room(identity, room_id, payload)
            return {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "action": action,
                "result": result,
                "deduplicated": False,
            }
        if action == "room.delete":
            self._require_capability(identity, "room.delete")
            with self._lock:
                deleted = self.store.deleted_room_record(room_id)
                if deleted:
                    return self._resume_deleted_room_command(
                        identity,
                        room_id,
                        request_id=request_id,
                        payload=payload,
                        tombstone=deleted,
                    )
        self.ensure_room(room_id)
        if action == "room.delete":
            with self._lock:
                prior_ack = self._prior_command_ack(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                )
                if prior_ack:
                    return prior_ack
                principal_id = _command_principal(identity)
                return self._delete_room(
                    identity,
                    room_id,
                    payload,
                    request_id=request_id,
                    principal_id=principal_id,
                    payload_hash=command_payload_hash(payload),
                    operation_id=_external_effect_operation_id(
                        room_id,
                        principal_id,
                        request_id,
                        action,
                    ),
                )
        if action == "room.history":
            if identity.get("client_type") == "agent_bridge":
                raise RoomCommandRejected("Agent Bridges receive assigned context, not browser history pages.", code="permission_denied")
            result = self.history_page(
                room_id,
                before_seq=_safe_bounded_int(payload.get("before_seq"), default=0, minimum=0),
                limit=_safe_bounded_int(
                    payload.get("limit"),
                    default=ROOM_HISTORY_MAX_LIMIT,
                    minimum=1,
                    maximum=ROOM_HISTORY_MAX_LIMIT,
                ),
            )
            return {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "action": action,
                "result": result,
                "deduplicated": False,
            }
        if action == "message.send":
            self._require_capability(identity, "message.send")
            with self._lock:
                participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
                compatibility_muted = is_room_member_muted(
                    self.output_root,
                    room_id,
                    participant_id,
                )
                return self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._send_message(
                        identity,
                        payload,
                        unit=unit,
                        compatibility_muted=compatibility_muted,
                    ),
                )
        if action == "agent.configure" and not AGENT_RUNTIME_PROFILE_KEYS.intersection(payload):
            self._require_capability(identity, "agent.control")
            with self._lock:
                ack = self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._configure_agent_profile(payload, unit=unit),
                )
                self._apply_agent_profile_after_commit(room_id, ack)
                return ack
        if action == "participant.mute":
            self._require_capability(identity, "participant.mute")
            with self._lock:
                participant_id = self._payload_agent_id(payload)
                muted = bool(payload.get("muted", True))
                compatibility_member = _planned_muted_member(
                    identity_store_for_output_root(self.output_root).get_membership(room_id, participant_id),
                    room_id=room_id,
                    participant_id=participant_id,
                    muted=muted,
                )
                ack = self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._mute_participant_durable(
                        participant_id,
                        muted,
                        compatibility_member,
                        unit=unit,
                    ),
                )
                self._apply_mute_after_commit(room_id, participant_id, muted)
                return ack
        if action == "participant.leave":
            self._require_capability(identity, "participant.leave")
            with self._lock:
                participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
                is_owner = self._is_room_owner(identity, room_id)
                ack = self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._leave_participant_durable(
                        participant_id,
                        is_owner=is_owner,
                        unit=unit,
                    ),
                )
                self._schedule_participant_leave_cleanup(room_id, participant_id)
                return ack
        if action == "participant.kick":
            self._require_capability(identity, "participant.kick")
            with self._lock:
                prior_ack = self._prior_command_ack(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                )
                if prior_ack:
                    return prior_ack
                participant_id = self._payload_agent_id(payload)
                operation_id = _external_effect_operation_id(
                    room_id,
                    _command_principal(identity),
                    request_id,
                    action,
                )
                participant = self._prepare_kick_intent(
                    room_id,
                    participant_id,
                    operation_id=operation_id,
                )
                cleanup = self._apply_kick_effects(
                    room_id,
                    participant,
                    operation_id=operation_id,
                )
                ack = self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._finalize_kick_durable(
                        participant_id,
                        operation_id=operation_id,
                        cleanup=cleanup,
                        unit=unit,
                    ),
                )
                if participant.get("role") == "agent":
                    self._providers_by_room.get(room_id, {}).pop(participant_id, None)
                return ack
        if action == "message.final":
            self._require_bridge(identity)
            with self._lock:
                prior_ack = self._prior_command_ack(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                )
                if prior_ack:
                    result = (
                        prior_ack.get("result")
                        if isinstance(prior_ack.get("result"), dict)
                        else {}
                    )
                    self._turn_coordinator.after_message_final(
                        room_id,
                        result,
                        deduplicated=True,
                    )
                    return prior_ack
                prepared = self._turn_coordinator.prepare_message_final(identity, room_id, payload)
                ack = self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._turn_coordinator.message_final_in_unit(
                        identity,
                        payload,
                        prepared=prepared,
                        unit=unit,
                    ),
                )
                result = ack.get("result") if isinstance(ack.get("result"), dict) else {}
                self._turn_coordinator.after_message_final(
                    room_id,
                    result,
                    deduplicated=bool(ack.get("deduplicated")),
                )
                return ack
        with self._lock:
            prior_ack = self._prior_command_ack(
                identity,
                room_id,
                request_id,
                action,
                payload,
            )
            if prior_ack:
                return prior_ack
            principal_id = _command_principal(identity)
            payload_hash = command_payload_hash(payload)
            result = self._execute_action(
                identity,
                room_id,
                action,
                payload,
                operation_id=_external_effect_operation_id(
                    room_id,
                    principal_id,
                    request_id,
                    action,
                ),
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
            ack = {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "action": action,
                "result": result,
                "deduplicated": False,
            }
            return self.store.record_command_result(
                room_id,
                request_id,
                ack,
                principal_id=principal_id,
                action=action,
                payload_hash=payload_hash,
            )

    def _execute_durable_command(
        self,
        identity: dict[str, object],
        room_id: str,
        request_id: str,
        action: str,
        payload: dict[str, object],
        operation: Callable[[RoomCommandUnitOfWork], dict[str, object]],
    ) -> dict[str, object]:
        try:
            with RoomCommandUnitOfWork(
                self.store,
                room_id=room_id,
                principal_id=_command_principal(identity),
                request_id=request_id,
                action=action,
                payload=payload,
            ) as unit:
                if unit.deduplicated:
                    return unit.resolved_ack()
                result = operation(unit)
                unit.build_ack(result)
                unit.record_ack()
            return unit.resolved_ack()
        except RoomCommandIdempotencyConflict as error:
            raise RoomCommandRejected(str(error), code="idempotency_conflict") from error
        except ProviderSyncCursorParityError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error

    def _prior_command_ack(
        self,
        identity: dict[str, object],
        room_id: str,
        request_id: str,
        action: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        prior = self.store.command_record(
            room_id,
            _command_principal(identity),
            request_id,
        )
        if not prior:
            return {}
        if (
            prior.get("action") != action
            or prior.get("payload_hash") != command_payload_hash(payload)
        ):
            raise RoomCommandRejected(
                "request_id was already used for a different command.",
                code="idempotency_conflict",
            )
        return {**dict(prior.get("result") or {}), "deduplicated": True}

    def close(self) -> CleanupReport:
        with self._lock:
            if self._closed:
                return self.last_cleanup_report
            self._closed = True
            removers = list(self._event_listener_removers.values())
            self._event_listener_removers.clear()
            provider_agents = [
                (room_id, agent_id)
                for room_id, providers in self._providers_by_room.items()
                for agent_id in providers
            ]
            remove_provider_catalog_listener = self._provider_catalog_remove
            self._provider_catalog_remove = lambda: None
        cleanup = CleanupReport("room_realtime_controller")
        for remove in removers:
            try:
                remove()
                cleanup.record_success()
            except Exception as error:
                cleanup.record_failure("event_listener.remove", error)
        try:
            remove_provider_catalog_listener()
            cleanup.record_success()
        except Exception as error:
            cleanup.record_failure("provider_catalog_listener.remove", error)
        cleanup.merge(self._turn_coordinator.close())
        cleanup.merge(self._agent_lifecycle.close(provider_agents))
        try:
            self.broker.close()
            cleanup.record_success()
        except Exception as error:
            cleanup.record_failure("event_broker.close", error)
        self.last_cleanup_report = cleanup
        emit_cleanup_failure(cleanup)
        return cleanup

    def _on_provider_catalog_update(self, catalog: dict[str, object]) -> None:
        if self._closed:
            return
        message = {"op": "provider_catalog_updated", "catalog": dict(catalog)}
        for room in self.store.list_rooms(include_archived=True):
            room_id = clean_lobby_text(room.get("room_id"), limit=128)
            if room_id:
                self.broker.broadcast_control(room_id, message, client_type="browser")

    def bridge_process_exited(
        self,
        room_id: str,
        session_id: str,
        returncode: int,
        stderr_tail: str = "",
    ) -> None:
        """Preserve crash evidence and schedule one bounded in-process recovery."""
        self._agent_lifecycle.bridge_process_exited(
            room_id,
            session_id,
            returncode,
            stderr_tail,
        )

    def provider_process_health(self, room_id: str, session_id: str) -> dict[str, object]:
        return self._agent_lifecycle.process_health(room_id, session_id)

    def _execute_action(
        self,
        identity: dict[str, object],
        room_id: str,
        action: str,
        payload: dict[str, object],
        *,
        operation_id: str,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        if action == "agent.create":
            self._require_capability(identity, "agent.control")
            return self._create_agent(
                room_id,
                payload,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        if action == "agent.readd":
            self._require_capability(identity, "agent.control")
            return self._readd_agent(
                room_id,
                payload,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        if action == "agent.configure":
            self._require_capability(identity, "agent.control")
            return self._configure_agent(room_id, payload)
        if action in {"agent.start", "agent.pause", "agent.resume", "agent.stop", "agent.interrupt"}:
            self._require_capability(identity, "agent.control")
            agent_id = self._payload_agent_id(payload)
            if action == "agent.start":
                return self._agent_lifecycle.start(
                    room_id,
                    agent_id,
                    server_url=server_url,
                    ticket_issuer=ticket_issuer,
                    operation_id=operation_id,
                )
            if action == "agent.pause":
                return self._agent_lifecycle.pause(room_id, agent_id)
            if action == "agent.resume":
                return self._agent_lifecycle.resume(room_id, agent_id, server_url=server_url, ticket_issuer=ticket_issuer)
            if action == "agent.stop":
                return self._agent_lifecycle.stop(
                    room_id,
                    agent_id,
                    operation_id=operation_id,
                )
            return self._agent_lifecycle.interrupt(room_id, agent_id)
        self._require_bridge(identity)
        if action == "bridge.ready":
            return self._bridge_ready(identity, room_id, payload)
        if action == "bridge.health":
            return self._bridge_health(identity, room_id, payload)
        if action == "turn.state":
            return self._turn_coordinator.turn_state(identity, room_id, payload)
        if action == "turn.decline":
            return self._turn_coordinator.turn_decline(identity, room_id, payload)
        if action == "activity.update":
            return self._turn_coordinator.activity_update(identity, room_id, payload)
        if action == "message.delta":
            return self._turn_coordinator.message_delta(identity, room_id, payload)
        if action == "turn.failed":
            return self._turn_coordinator.turn_failed(identity, room_id, payload)
        raise RoomCommandRejected(f"Unsupported room command: {action}", code="unknown_action")

    def _send_message(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
        compatibility_muted: bool,
    ) -> dict[str, object]:
        content = _room_message_text(payload.get("content") or payload.get("message"), limit=12000)
        kind = clean_lobby_text(payload.get("kind"), limit=64) or "message"
        if kind not in {"vote", "vote_cast"} and not content:
            raise RoomCommandRejected("Message content is required.", code="empty")
        participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        participant = unit.participant(participant_id)
        if participant.get("status") in {"kicked", "left"}:
            raise RoomCommandRejected("This participant is no longer in the room.", code="session_revoked")
        canonical_muted = bool(participant.get("muted")) if "muted" in participant else compatibility_muted
        if canonical_muted:
            raise RoomCommandRejected("You are muted by the room host.", code="muted")
        event = unit.append_event(
            "message_final",
            participant_id=participant_id,
            participant_type="human",
            actor_id=participant_id,
            actor_type="human",
            display_name=clean_lobby_text(identity.get("display_name"), limit=64) or participant_id,
            content=content,
            message_kind=kind,
            attachments=payload.get("attachments") if isinstance(payload.get("attachments"), list) else [],
            vote_id=payload.get("vote_id"),
            vote_question=payload.get("vote_question"),
            vote_options=payload.get("vote_options"),
            vote_choice=payload.get("vote_choice"),
            target_agent_id=payload.get("target_agent_id"),
            relay_depth=0,
        )
        return {"event": event, "event_seq": event["seq"]}

    def _create_agent(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        provider_id = clean_lobby_text(
            payload.get("provider_id") or payload.get("provider_kind") or payload.get("provider"),
            limit=64,
        )
        catalog_revision = clean_lobby_text(payload.get("catalog_revision"), limit=128)
        try:
            selection = self.provider_catalog.validate_selection(
                catalog_revision=catalog_revision,
                provider_id=provider_id,
                values={
                    "model": clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128),
                    "reasoning_effort": clean_lobby_text(
                        payload.get("reasoning_effort") or payload.get("effort"), limit=32
                    ),
                    "service_tier": clean_lobby_text(payload.get("service_tier"), limit=32),
                    "variant": clean_lobby_text(payload.get("variant"), limit=64),
                    "permission_mode": clean_lobby_text(
                        payload.get("permission_mode") or payload.get("permission_option"), limit=64
                    ),
                },
            )
        except ProviderCatalogSelectionError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        try:
            spec = native_cli_provider_spec_from_payload(
                {
                    "provider_id": selection.provider_id,
                    "agent_id": payload.get("agent_id") or payload.get("participant_id"),
                    "display_name": payload.get("display_name"),
                    "workspace": payload.get("workspace") or payload.get("workspace_path") or payload.get("cwd"),
                    "model": selection.model,
                    "model_selection_kind": selection.model_selection_kind,
                    "catalog_revision": selection.catalog_revision,
                    "reasoning_effort": selection.reasoning_effort,
                    "service_tier": selection.service_tier,
                    "variant": selection.variant,
                    "permission_mode": selection.permission_mode,
                }
            )
        except UnsupportedNativeCliProvider as error:
            raise RoomCommandRejected(str(error), code="unsupported_provider") from error
        except ValueError as error:
            raise RoomCommandRejected(str(error), code="invalid_runtime_profile") from error
        session = self.create_provider_session(room_id, spec)
        result: dict[str, object] = {
            "status": "created",
            "agent_session": session,
            "participant": self.store.participant(room_id, spec.agent_id),
        }
        if bool(payload.get("start") or payload.get("start_now")):
            result["start"] = self._agent_lifecycle.start(
                room_id,
                spec.agent_id,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        return result

    def _readd_agent(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        agent_id = self._payload_agent_id(payload)
        current = self.store.session(room_id, agent_id)
        if not current:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        if current.get("process_ownership") != "server":
            raise RoomCommandRejected(
                "External Agent Sessions must reconnect with their original invite.",
                code="runtime_unavailable",
            )
        participant = self.store.participant(room_id, agent_id)
        if current.get("runtime_status") not in {"stopped", "available"}:
            raise RoomCommandRejected(
                "Only stopped Agent Sessions can be added back to the room.",
                code="readd_invalid_state",
            )
        if current.get("enabled"):
            raise RoomCommandRejected("The Agent Session is still enabled.", code="readd_invalid_state")
        if participant.get("status") not in {"detached", "kicked"}:
            raise RoomCommandRejected("The Agent Session participant is still active.", code="readd_invalid_state")
        if current.get("active_turn_id") or current.get("bridge_handle_id") or self.broker.has_bridge(room_id, agent_id):
            raise RoomCommandRejected("The Agent Session still owns an active runtime.", code="readd_invalid_state")
        try:
            spec = native_cli_provider_spec_from_stored_session_strict(current)
        except StoredProviderProfileError as error:
            raise RoomCommandRejected(
                str(error),
                code=error.code,
            ) from error
        if (
            current.get("runtime_profile_key") != spec.runtime_profile_key()
            or current.get("transport") != spec.transport
        ):
            self.store.update_session_fields(
                room_id,
                agent_id,
                runtime_profile_key=spec.runtime_profile_key(),
                transport=spec.transport,
            )
        with self._lock:
            self._providers_by_room.setdefault(room_id, {})[agent_id] = spec
            self._ensure_provider_session(room_id, spec)
            self.store.update_participant_fields(room_id, agent_id, status="detached")
            session = self.store.session(room_id, agent_id)
            self.store.append_event(
                room_id,
                "agent_session_reactivated",
                participant_id=agent_id,
                session_id=agent_id,
            )
            self._publish_session_state(room_id, session)
        result: dict[str, object] = {
            "status": "readded",
            "agent_session": public_session(session),
            "participant": self.store.participant(room_id, agent_id),
        }
        if bool(payload.get("start") or payload.get("start_now")):
            result["start"] = self._agent_lifecycle.start(
                room_id,
                agent_id,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        return result

    def _configure_agent(self, room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id = self._payload_agent_id(payload)
        current = self.store.session(room_id, agent_id)
        if not current:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        if current.get("runtime_status") in {"starting", "idle", "busy", "paused", "recovering", "stopping"}:
            raise RoomCommandRejected(
                "Stop this Agent Session before changing its runtime settings.",
                code="runtime_profile_conflict",
            )
        requested_provider = clean_lobby_text(
            payload.get("provider_id") or payload.get("provider_kind") or current.get("provider_kind"),
            limit=64,
        )
        existing_provider = clean_lobby_text(current.get("provider_kind"), limit=64)
        definition = native_cli_provider_definition(requested_provider)
        if definition is None or definition.provider_kind != existing_provider:
            raise RoomCommandRejected(
                "An existing Agent Session cannot change provider kind; remove it and create a new session.",
                code="provider_mismatch",
            )
        merged = {
            **payload,
            "agent_id": agent_id,
            "provider_id": definition.provider_id,
            "display_name": payload.get("display_name") or current.get("display_name") or agent_id,
            "workspace": payload["workspace"] if "workspace" in payload else current.get("workspace"),
        }
        selected_values = {
            key: payload[key] if key in payload else current.get(key)
            for key in ("model", "reasoning_effort", "service_tier", "variant", "permission_mode")
        }
        try:
            selection = self.provider_catalog.validate_selection(
                catalog_revision=clean_lobby_text(payload.get("catalog_revision"), limit=128),
                provider_id=definition.provider_id,
                values={
                    "model": clean_lobby_text(selected_values["model"], limit=128),
                    "reasoning_effort": clean_lobby_text(selected_values["reasoning_effort"], limit=32),
                    "service_tier": clean_lobby_text(selected_values["service_tier"], limit=32),
                    "variant": clean_lobby_text(selected_values["variant"], limit=64),
                    "permission_mode": clean_lobby_text(selected_values["permission_mode"], limit=64),
                },
            )
            spec = native_cli_provider_spec_from_payload(
                {
                    **merged,
                    "model": selection.model,
                    "model_selection_kind": selection.model_selection_kind,
                    "catalog_revision": selection.catalog_revision,
                    "reasoning_effort": selection.reasoning_effort,
                    "service_tier": selection.service_tier,
                    "variant": selection.variant,
                    "permission_mode": selection.permission_mode,
                }
            )
        except ProviderCatalogSelectionError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        except (UnsupportedNativeCliProvider, ValueError) as error:
            raise RoomCommandRejected(str(error), code="invalid_runtime_profile") from error
        session = self.configure_stopped_provider_profile(room_id, spec)
        return {"status": "configured", "agent_session": session}

    def _configure_agent_profile(
        self,
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        agent_id = self._payload_agent_id(payload)
        current = unit.session(agent_id)
        if not current:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        participant = unit.participant(agent_id)
        if not participant:
            raise RoomCommandRejected(f"Participant {agent_id} was not found.", code="not_found")
        display_name = clean_lobby_text(
            payload.get("display_name") or current.get("display_name") or agent_id,
            limit=80,
        )
        avatar_image_url = clean_lobby_text(payload.get("avatar_image_url"), limit=4096)
        updated_participant = unit.update_participant_fields(
            agent_id,
            display_name=display_name,
            avatar_image_url=avatar_image_url,
        )
        updated_session = unit.update_session_fields(
            agent_id,
            display_name=display_name,
            avatar_image_url=avatar_image_url,
        )
        unit.append_event(
            "participant_updated",
            participant_id=agent_id,
            display_name=display_name,
            avatar_image_url=avatar_image_url,
        )
        return {
            "status": "profile_updated",
            "agent_session": public_session(updated_session),
            "participant": updated_participant,
        }

    def _apply_agent_profile_after_commit(
        self,
        room_id: str,
        ack: dict[str, object],
    ) -> None:
        if ack.get("deduplicated"):
            return
        result = ack.get("result") if isinstance(ack.get("result"), dict) else {}
        session = result.get("agent_session") if isinstance(result.get("agent_session"), dict) else {}
        agent_id = clean_lobby_text(
            session.get("session_id") or session.get("participant_id"),
            limit=128,
        )
        display_name = clean_lobby_text(session.get("display_name"), limit=80)
        if not agent_id:
            return
        current_spec = self._providers_by_room.get(room_id, {}).get(agent_id)
        if current_spec is not None and display_name:
            self._providers_by_room[room_id][agent_id] = replace(
                current_spec,
                display_name=display_name,
            )
        self._publish_session_state(room_id, self.store.session(room_id, agent_id))

    def _prepare_kick_intent(
        self,
        room_id: str,
        participant_id: str,
        *,
        operation_id: str,
    ) -> dict[str, object]:
        if participant_id == "operator-local":
            raise RoomCommandRejected("The room host cannot be removed.", code="permission_denied")
        participant = self.store.participant(room_id, participant_id)
        if not participant:
            raise RoomCommandRejected(f"Participant {participant_id} was not found.", code="not_found")
        if participant.get("status") == "kicked":
            raise RoomCommandRejected("This participant was already removed.", code="already_kicked")
        intent_action = clean_lobby_text(participant.get("moderation_intent_action"), limit=32)
        intent_id = clean_lobby_text(participant.get("moderation_intent_id"), limit=128)
        if intent_action:
            if intent_action != "kick" or intent_id != operation_id:
                raise RoomCommandRejected(
                    "Another moderation operation is already in progress for this participant.",
                    code="operation_in_progress",
                )
            return participant
        return self.store.update_participant_fields(
            room_id,
            participant_id,
            moderation_intent_action="kick",
            moderation_intent_id=operation_id,
            moderation_intent_status="prepared",
            moderation_intent_cleanup_warning="",
            moderation_intent_removed_member=False,
            moderation_intent_revoked_sessions=0,
        )

    def _apply_kick_effects(
        self,
        room_id: str,
        participant: dict[str, object],
        *,
        operation_id: str,
    ) -> dict[str, object]:
        participant_id = clean_lobby_text(participant.get("participant_id"), limit=128)
        if clean_lobby_text(participant.get("moderation_intent_status"), limit=32) == "effect_applied":
            return _kick_cleanup_from_participant(participant)
        stop_warning = ""
        if participant.get("role") == "agent":
            session = self.store.session(room_id, participant_id)
            if session and session.get("runtime_status") not in {"stopped", "available"}:
                try:
                    self._agent_lifecycle.stop(
                        room_id,
                        participant_id,
                        operation_id=f"{operation_id}:stop",
                    )
                except RoomCommandRejected as error:
                    # Moderation must still revoke room access even when an
                    # external process cannot prove its local cleanup.
                    stop_warning = f"{error.code}: {error}"
        revoked_sessions = self._room_sessions.revoke_participant(room_id, participant_id)
        self.broker.disconnect_participant(room_id, participant_id)
        removed_member = remove_room_member(self.output_root, room_id, participant_id)
        leave_all_voice(room_id, participant_id)
        updated = self.store.update_participant_fields(
            room_id,
            participant_id,
            moderation_intent_status="effect_applied",
            moderation_intent_cleanup_warning=stop_warning,
            moderation_intent_removed_member=bool(removed_member),
            moderation_intent_revoked_sessions=int(revoked_sessions),
        )
        return _kick_cleanup_from_participant(updated)

    def _finalize_kick_durable(
        self,
        participant_id: str,
        *,
        operation_id: str,
        cleanup: dict[str, object],
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        participant = unit.participant(participant_id)
        if not participant:
            raise RoomCommandRejected(f"Participant {participant_id} was not found.", code="not_found")
        if (
            clean_lobby_text(participant.get("moderation_intent_action"), limit=32) != "kick"
            or clean_lobby_text(participant.get("moderation_intent_id"), limit=128) != operation_id
            or clean_lobby_text(participant.get("moderation_intent_status"), limit=32)
            != "effect_applied"
        ):
            raise RoomCommandRejected(
                "The participant kick cleanup has not completed.",
                code="moderation_cleanup_incomplete",
            )
        updated = unit.update_participant_fields(
            participant_id,
            status="kicked",
            moderation_intent_action="",
            moderation_intent_id="",
            moderation_intent_status="",
            moderation_intent_cleanup_warning="",
            moderation_intent_removed_member=False,
            moderation_intent_revoked_sessions=0,
        )
        unit.append_event("participant_kicked", participant_id=participant_id)
        return {
            "participant": public_participant(updated),
            **cleanup,
        }

    def _mute_participant_durable(
        self,
        participant_id: str,
        muted: bool,
        compatibility_member: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        participant = unit.participant(participant_id)
        if not participant:
            raise RoomCommandRejected(f"Participant {participant_id} was not found.", code="not_found")
        updated = unit.update_participant_fields(participant_id, muted=muted)
        unit.append_event(
            "participant_muted",
            participant_id=participant_id,
            muted=muted,
        )
        return {"participant": updated, "member": compatibility_member}

    def _apply_mute_after_commit(
        self,
        room_id: str,
        participant_id: str,
        muted: bool,
    ) -> None:
        try:
            set_room_member_muted(
                self.output_root,
                meeting_id=room_id,
                participant_id=participant_id,
                muted=muted,
            )
        except Exception as error:
            raise RoomCommandRejected(
                "The room mute state was saved, but the compatibility roster did not synchronize; retry the command.",
                code="compatibility_sync_failed",
            ) from error
        participant = self.store.participant(room_id, participant_id)
        session = self.store.session(room_id, participant_id)
        if participant.get("role") == "agent" and session:
            if muted and session.get("runtime_status") == "busy":
                self.broker.direct_to_bridge(room_id, participant_id, {"op": "agent.control", "action": "interrupt"})
            elif not muted:
                self._turn_coordinator.assign_pending(room_id, participant_id)

    def _leave_participant_durable(
        self,
        participant_id: str,
        *,
        is_owner: bool,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        participant = unit.participant(participant_id)
        if not participant:
            raise RoomCommandRejected("Participant was not found in this room.", code="not_found")
        if is_owner:
            raise RoomCommandRejected(
                "The room owner must transfer ownership or delete the server.",
                code="owner_must_transfer_or_delete",
            )
        updated = unit.update_participant_fields(participant_id, status="left")
        event = unit.append_event("participant_left", participant_id=participant_id)
        return {"participant": updated, "event": event, "revocation_scheduled": True}

    def _schedule_participant_leave_cleanup(self, room_id: str, participant_id: str) -> None:
        identity_store_for_output_root(self.output_root).remove_membership(room_id, participant_id)
        leave_all_voice(room_id, participant_id)
        timer = threading.Timer(
            0.1,
            self._room_sessions.revoke_participant,
            args=(room_id, participant_id),
        )
        timer.daemon = True
        timer.start()

    def _delete_room(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
        *,
        request_id: str,
        principal_id: str,
        payload_hash: str,
        operation_id: str,
    ) -> dict[str, object]:
        if not self._is_room_owner(identity, room_id):
            raise RoomCommandRejected("Only the room owner can delete this server.", code="permission_denied")
        identity_store = identity_store_for_output_root(self.output_root)
        identity_room = identity_store.get_room(room_id) or {}
        canonical_room = self.store.room(room_id)
        room_name = clean_lobby_text(
            identity_room.get("label") or canonical_room.get("label") or room_id,
            limit=128,
        )
        confirmation = str(payload.get("confirmation_name") or "")
        if confirmation != room_name:
            raise RoomCommandRejected("The confirmation name does not match the server name.", code="confirmation_mismatch")
        cleanup_failures: list[str] = []
        cleanup_warnings: list[str] = []
        for session in list(self.store.sessions(room_id)):
            session_id = clean_lobby_text(session.get("session_id"), limit=128)
            if not session_id or session.get("runtime_status") in {"stopped", "available"}:
                continue
            ownership = clean_lobby_text(session.get("process_ownership"), limit=32) or (
                "external" if session.get("external_owned") else "server"
            )
            if ownership == "external" and not self.broker.has_bridge(room_id, session_id):
                self._room_sessions.revoke_participant(room_id, session_id)
                self.broker.disconnect_participant(room_id, session_id)
                cleanup_warnings.append(
                    f"{session_id}: external bridge was disconnected; room access was revoked without "
                    "claiming provider shutdown"
                )
                continue
            try:
                self._agent_lifecycle.stop(
                    room_id,
                    session_id,
                    operation_id=_nested_effect_operation_id(operation_id, session_id),
                )
            except (RoomCommandRejected, ValueError) as error:
                if ownership == "external":
                    cleanup_warnings.append(f"{session_id}: {error}")
                else:
                    cleanup_failures.append(f"{session_id}: {error}")
        if cleanup_failures:
            raise RoomCommandRejected(
                "Room deletion stopped because Agent Session cleanup failed: "
                + "; ".join(cleanup_failures),
                code="room_cleanup_failed",
            )
        result = {
            "room_id": room_id,
            "deleted": True,
            "cleanup_warnings": cleanup_warnings,
        }
        ack = {
            "op": "ack",
            "request_id": request_id,
            "accepted": True,
            "action": "room.delete",
            "result": result,
            "deduplicated": False,
        }
        deleted = self.store.delete_room(
            room_id,
            reason="owner deleted server",
            tombstone={
                "principal_id": principal_id,
                "request_id": request_id,
                "action": "room.delete",
                "payload_hash": payload_hash,
                "result": ack,
            },
            cleanup_status="pending",
            room_name=room_name,
        )
        if not deleted:
            raise RoomCommandRejected("The room no longer exists.", code="room_deleted")
        return self._complete_deleted_room_cleanup(
            room_id,
            room_name=room_name,
            ack=ack,
            deduplicated=False,
        )

    def _resume_deleted_room_command(
        self,
        identity: dict[str, object],
        room_id: str,
        *,
        request_id: str,
        payload: dict[str, object],
        tombstone: dict[str, object],
    ) -> dict[str, object]:
        principal_id = _command_principal(identity)
        if (
            tombstone.get("principal_id") != principal_id
            or tombstone.get("request_id") != request_id
            or tombstone.get("action") != "room.delete"
        ):
            raise RoomCommandRejected("The room was deleted.", code="room_deleted")
        if tombstone.get("payload_hash") != command_payload_hash(payload):
            raise RoomCommandRejected(
                "request_id was already used for a different command.",
                code="idempotency_conflict",
            )
        ack = dict(tombstone.get("result") or {})
        if not ack:
            raise RoomCommandRejected("The room was deleted.", code="room_deleted")
        if tombstone.get("cleanup_status") != "complete":
            ack = self._complete_deleted_room_cleanup(
                room_id,
                room_name=clean_lobby_text(tombstone.get("room_name"), limit=128) or room_id,
                ack=ack,
                deduplicated=True,
            )
        return {**ack, "deduplicated": True}

    def _complete_deleted_room_cleanup(
        self,
        room_id: str,
        *,
        room_name: str,
        ack: dict[str, object],
        deduplicated: bool,
    ) -> dict[str, object]:
        self.broker.broadcast_control(
            room_id,
            {"op": "room_deleted", "room_id": room_id, "room_name": room_name},
        )
        revoked = {
            "revoked_invites": self._invite_application.revoke_room(room_id),
            "revoked_sessions": self._room_sessions.revoke_room(room_id),
            "purged_admission_workflows": (
                self._invite_application.remove_terminal_admission_workflows_for_room(
                    room_id
                ).purged_count
            ),
        }
        identity_store_for_output_root(self.output_root).delete_room(room_id)
        remove_listener = self._event_listener_removers.pop(room_id, None)
        if remove_listener is not None:
            remove_listener()
        self._providers_by_room.pop(room_id, None)
        for path in (self.output_root / "rooms" / room_id, self.output_root / "meetings" / room_id):
            if path.exists() and path.is_dir():
                shutil.rmtree(path)
        disconnect = threading.Timer(0.1, lambda: self.broker.disconnect_room(room_id))
        disconnect.daemon = True
        disconnect.start()
        result = dict(ack.get("result") or {})
        completed_ack = {
            **ack,
            "result": {**result, **revoked},
            "deduplicated": deduplicated,
        }
        self.store.update_deleted_room_record(
            room_id,
            result={**completed_ack, "deduplicated": False},
            cleanup_status="complete",
        )
        return completed_ack

    def _is_room_owner(self, identity: dict[str, object], room_id: str) -> bool:
        store = identity_store_for_output_root(self.output_root)
        room = store.get_room(room_id) or {}
        owner_id = clean_lobby_text(room.get("owner_id"), limit=128)
        participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        user = store.user_for_participant(participant_id) or {}
        user_id = clean_lobby_text(user.get("user_id"), limit=128)
        if owner_id:
            return owner_id in {participant_id, user_id}
        return bool(identity.get("operator"))

    def _bridge_ready(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._turn_coordinator.bridge_session(identity, room_id, allow_unleased=True)
        try:
            health = ProviderRuntimeHealth.parse(payload)
        except AdapterContractError as error:
            raise RoomCommandRejected(str(error), code="adapter_health_invalid") from error
        if not health.running:
            raise RoomCommandRejected("A stopped provider cannot become ready.", code="adapter_health_invalid")
        connection_id = clean_lobby_text(identity.get("connection_id"), limit=128)
        channel = self.broker.channel(connection_id)
        if channel is None:
            raise RoomCommandRejected("Agent bridge connection is no longer active.", code="bridge_disconnected")
        external_profile: dict[str, object] = {}
        if session.get("process_ownership") == "external":
            definition = native_cli_provider_definition(session.get("provider_kind"))
            if definition is None:
                raise RoomCommandRejected(
                    "The external provider kind is not supported.",
                    code="provider_profile_invalid",
                )
            try:
                profile = ProviderRuntimeProfile.parse_strict(payload)
            except ProviderRuntimeConfigError as error:
                raise RoomCommandRejected(str(error), code="provider_profile_invalid") from error
            if profile.provider_kind != clean_lobby_text(session.get("provider_kind"), limit=64):
                raise RoomCommandRejected(
                    "The external provider profile does not match its invite.",
                    code="provider_profile_invalid",
                )
            if profile.runtime_kind != definition.runtime_kind:
                raise RoomCommandRejected(
                    "The external provider runtime kind is not supported for this provider.",
                    code="provider_profile_invalid",
                )
            if profile.transport not in definition.reported_transports:
                raise RoomCommandRejected(
                    "The external provider transport is not supported for this provider.",
                    code="provider_profile_invalid",
                )
            for field, required_default in (
                ("reasoning_effort", definition.default_reasoning_effort),
                ("service_tier", definition.default_service_tier),
                ("variant", definition.default_variant),
            ):
                if required_default and not getattr(profile, field):
                    raise RoomCommandRejected(
                        f"The external provider profile is missing required {field}.",
                        code="provider_profile_invalid",
                    )
            observation_policy = definition.model_observation_policy
            external_profile = {
                "model": profile.model,
                "requested_model_id": profile.model,
                "observed_model_id": "",
                "model_selection_kind": "exact",
                "model_observation_policy": observation_policy,
                "model_verification_status": _model_verification_status(
                    requested_model_id=profile.model,
                    observed_model_id="",
                    selection_kind="exact",
                    observation_policy=observation_policy,
                ),
                "reasoning_effort": profile.reasoning_effort,
                "service_tier": profile.service_tier,
                "variant": profile.variant,
                "permission_mode": profile.permission_mode,
                "runtime_kind": profile.runtime_kind,
                "runtime_profile_key": _external_runtime_profile_key(profile),
            }
        generation = self.broker.activate_bridge(channel)
        identity["bridge_generation"] = generation
        previous_participant = self.store.participant(room_id, agent_id)
        self.store.update_participant_fields(room_id, agent_id, status="joined")
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="attached",
            enabled=True,
            runtime_status="idle",
            reported_provider_pid=_safe_int_or_none(payload.get("pid")),
            bridge_generation=generation,
            pty=health.pty,
            transport=health.transport,
            is_one_shot=bool(payload.get("is_one_shot", False)),
            started_at=health.started_at,
            last_error="",
            **external_profile,
            **_runtime_diagnostic_fields(payload),
        )
        if previous_participant.get("status") != "joined":
            self.store.append_event(room_id, "participant_joined", participant_id=agent_id, session_id=session["session_id"])
        self.store.append_event(room_id, "session_attached", participant_id=agent_id, session_id=session["session_id"])
        self._turn_coordinator.assign_pending(room_id, agent_id)
        current = self.store.session(room_id, str(session["session_id"]))
        self._publish_session_state(room_id, current)
        return {"agent_session": public_session(current)}

    def _bridge_health(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        _agent_id, session = self._turn_coordinator.bridge_session(identity, room_id)
        try:
            health = ProviderRuntimeHealth.parse(payload)
        except AdapterContractError as error:
            raise RoomCommandRejected(str(error), code="adapter_health_invalid") from error
        fields: dict[str, object] = {
            key: payload[key]
            for key in ("resolved_executable", "last_error", "returncode")
            if key in payload
        }
        fields.update(
            running=health.running,
            pty=health.pty,
            transport=health.transport,
            started_at=health.started_at,
        )
        if "pid" in payload:
            fields["reported_provider_pid"] = _safe_int_or_none(payload.get("pid"))
        fields.update(_runtime_diagnostic_fields(payload))
        updated = self.store.update_session_fields(room_id, str(session["session_id"]), **fields)
        self._publish_session_state(room_id, updated)
        return {"agent_session": public_session(updated)}

    def _on_event_appended(self, event: RoomEvent | dict[str, object]) -> None:
        self.broker.broadcast_event(_public_event(event))
        if event.get("type") != "message_final":
            return
        with self._lock:
            self._route_message_event(event)

    def _route_message_event(self, event: RoomEvent | dict[str, object]) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        providers = self._room_providers(room_id)
        settings = self.store.room_settings(room_id)
        if settings.get("conversation_mode") == "ambient":
            self._route_ambient_event(
                dict(event),
                providers,
                max_relay_turns=AMBIENT_AGENT_RELAY_DEPTH,
            )
            return
        self._record_shadow_attention(dict(event), providers)
        continuous = settings.get("conversation_mode") == "continuous"
        max_relay_turns = int(settings.get("max_relay_turns") or self.max_agent_relay_depth)
        decision = route_message_targets(
            dict(event),
            providers,
            max_agent_relay_depth=max_relay_turns if continuous else self.max_agent_relay_depth,
            relay_agent_messages=continuous,
        )
        targets = decision.targets
        if continuous:
            targets = continuous_floor_targets(
                provider_ids=providers,
                actor_id=decision.actor_id,
                routed_targets=targets,
                eligible_agent_ids=(
                    agent_id
                    for agent_id in providers
                    if self.agent_floor_eligibility(room_id, agent_id).eligible
                ),
                content=clean_lobby_text(event.get("content"), limit=12000),
            )
        for agent_id in targets:
            participant = self.store.participant(room_id, agent_id)
            if participant.get("status") == "kicked" or participant.get("muted"):
                continue
            self._turn_coordinator.queue_event(
                room_id,
                agent_id,
                event,
                relay_depth=decision.relay_depth + (1 if continuous or decision.actor_type == "agent" else 0),
            )

    def _route_ambient_event(
        self,
        event: dict[str, object],
        providers: dict[str, NativeCliProviderSpec],
        *,
        max_relay_turns: int,
    ) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        eligible_ids = tuple(
            agent_id
            for agent_id in providers
            if self.agent_floor_eligibility(room_id, agent_id).eligible
        )
        try:
            result = self._attention_coordinator.evaluate_and_queue_active(
                event,
                candidate_ids=providers,
                eligible_ids=eligible_ids,
                last_spoke_sequences={
                    agent_id: self.store.attention_state(room_id, agent_id).last_spoke_seq
                    for agent_id in providers
                },
                max_agent_relay_depth=max_relay_turns,
                owner_id=self._attention_owner_id,
                lease_seconds=self._ambient_lease_seconds(providers, eligible_ids),
                relay_depth=max(0, int(event.get("relay_depth") or 0)) + 1,
            )
            job = result.get("job") if isinstance(result.get("job"), dict) else {}
            lease = result.get("lease") if isinstance(result.get("lease"), dict) else {}
            selected = clean_lobby_text(job.get("selected_participant_id"), limit=128)
            if not selected:
                return
            assigned = self._turn_coordinator.assign_pending(room_id, selected)
            if assigned:
                return
            self._turn_coordinator.cancel_queued_attention(
                room_id,
                selected,
                source_event_id=clean_lobby_text(event.get("id"), limit=128),
                lease_id=clean_lobby_text(lease.get("lease_id"), limit=128),
            )
            raise RuntimeError("Selected ambient speaker became unavailable before assignment.")
        except Exception as error:
            self._attention_active_error_count += 1
            self._attention_active_last_error = str(error)
            _LOGGER.exception(
                "Active room attention evaluation failed",
                extra={
                    "room_id": room_id,
                    "event_id": str(event.get("id") or ""),
                },
            )
            self.store.append_event(
                room_id,
                "error",
                content="Autonomous speaker selection failed.",
                error_code="ambient_attention_failed",
            )

    @staticmethod
    def _ambient_lease_seconds(
        providers: dict[str, NativeCliProviderSpec],
        eligible_ids: tuple[str, ...],
    ) -> float:
        timeout = max(
            (float(providers[agent_id].turn_timeout_seconds) for agent_id in eligible_ids),
            default=30.0,
        )
        return min(3600.0, max(60.0, timeout + 30.0))

    def _record_shadow_attention(
        self,
        event: dict[str, object],
        providers: dict[str, NativeCliProviderSpec],
    ) -> None:
        if not should_record_shadow_attention(event, self.attention_shadow_mode):
            self._attention_shadow_skipped_count += 1
            return
        try:
            self._attention_coordinator.evaluate_shadow(
                event,
                candidate_ids=providers,
                eligible_ids=(
                    agent_id
                    for agent_id in providers
                    if self.agent_floor_eligibility(str(event.get("room_id") or ""), agent_id).eligible
                ),
            )
            self._attention_shadow_recorded_count += 1
        except Exception as error:
            self._attention_shadow_error_count += 1
            self._attention_shadow_last_error = str(error)
            _LOGGER.exception(
                "Room attention shadow evaluation failed",
                extra={
                    "room_id": str(event.get("room_id") or ""),
                    "event_id": str(event.get("id") or ""),
                },
            )

    def attention_shadow_diagnostics(self) -> dict[str, object]:
        return {
            "mode": self.attention_shadow_mode,
            "recorded_count": self._attention_shadow_recorded_count,
            "skipped_count": self._attention_shadow_skipped_count,
            "error_count": self._attention_shadow_error_count,
            "last_error": self._attention_shadow_last_error,
        }

    def attention_active_diagnostics(self) -> dict[str, object]:
        return {
            "mode": "active",
            "error_count": self._attention_active_error_count,
            "last_error": self._attention_active_last_error,
            "provider_sync_cursor_reconciliation": (
                self._provider_sync_cursor_reconciliation_report.as_dict()
            ),
            "startup_reconciliation": self._attention_reconciliation_report.as_dict(),
        }

    def agent_floor_eligibility(self, room_id: str, agent_id: str) -> AgentFloorEligibility:
        participant = self.store.participant(room_id, agent_id)
        session = self.store.session(room_id, agent_id)
        compatibility_muted = (
            is_room_member_muted(self.output_root, room_id, agent_id)
            if "muted" not in participant
            else False
        )
        return evaluate_agent_floor_eligibility(
            participant,
            session,
            member_muted=compatibility_muted,
            bridge_connected=self.broker.has_bridge(room_id, agent_id),
        )

    def _publish_session_state(self, room_id: str, session: dict[str, object]) -> dict[str, object]:
        if not session:
            return {}
        return self.store.append_event(
            room_id,
            "agent_session_state",
            participant_id=session.get("participant_id"),
            session_id=session.get("session_id"),
            runtime_status=session.get("runtime_status"),
            agent_session=public_session(session),
        )

    def _ensure_provider_session(self, room_id: str, spec: NativeCliProviderSpec) -> None:
        agent_id = clean_lobby_text(spec.agent_id, limit=128)
        participant = self.store.participant(room_id, agent_id)
        if not participant:
            self.store.upsert_participant(
                room_id,
                {
                    "participant_id": agent_id,
                    "display_name": spec.display_name,
                    "role": "agent",
                    "participant_type": "agent",
                    "owner_id": "operator-local",
                    "created_by": "operator-local",
                    "provider_kind": spec.normalized_provider_kind(),
                    "connection_kind": "native_cli_bridge",
                    "status": "detached",
                },
            )
        session = self.store.session(room_id, agent_id)
        if session:
            self.store.update_participant_fields(
                room_id,
                agent_id,
                display_name=spec.display_name,
                provider_kind=spec.normalized_provider_kind(),
                connection_kind="native_cli_bridge",
            )
            cursor_updates: dict[str, object] = {}
            if "last_provider_sync_seq" not in session:
                cursor_updates["last_provider_sync_seq"] = self.store.event_sequence(
                    room_id,
                    clean_lobby_text(session.get("last_provider_sync_event_id"), limit=128),
                )
            if "last_seen_seq" not in session:
                cursor_updates["last_seen_seq"] = self.store.event_sequence(
                    room_id,
                    clean_lobby_text(session.get("last_seen_event_id"), limit=128),
                )
            if "process_ownership" not in session:
                cursor_updates["process_ownership"] = "external" if session.get("external_owned") else "server"
            if "bridge_generation" not in session:
                cursor_updates["bridge_generation"] = 0
            self.store.update_session_fields(
                room_id,
                agent_id,
                display_name=spec.display_name,
                provider_kind=spec.normalized_provider_kind(),
                runtime_kind=spec.runtime_kind,
                connection_kind="native_cli_bridge",
                command_configured=list(spec.command),
                workspace=str(Path(spec.cwd).expanduser().resolve()),
                model=spec.model,
                requested_model_id=spec.requested_model_id or spec.model,
                model_selection_kind=spec.model_selection_kind,
                model_observation_policy=spec.model_observation_policy,
                model_verification_status=_model_verification_status(
                    requested_model_id=spec.requested_model_id or spec.model,
                    observed_model_id=clean_lobby_text(session.get("observed_model_id"), limit=128),
                    selection_kind=spec.model_selection_kind,
                    observation_policy=spec.model_observation_policy,
                ),
                catalog_revision=spec.catalog_revision,
                reasoning_effort=spec.reasoning_effort,
                service_tier=spec.service_tier,
                variant=spec.variant,
                permission_mode=spec.permission_mode,
                runtime_profile_key=spec.runtime_profile_key(),
                pty=spec.transport in {"pty", "conpty"},
                transport=spec.transport,
                is_one_shot=False,
                **cursor_updates,
            )
            return
        latest_events = self.store.read_events(
            room_id,
            event_types=("message_final",),
            limit=1,
            newest=True,
        )
        latest_public_event = latest_events[-1] if latest_events else {}
        latest_public = clean_lobby_text(latest_public_event.get("id"), limit=128)
        latest_public_seq = int(latest_public_event.get("seq") or 0)
        with self.store.transaction(room_id) as transaction:
            created_session, _ = transaction.upsert_session(
                {
                    "session_id": agent_id,
                    "participant_id": agent_id,
                    "display_name": spec.display_name,
                    "status": "available",
                    "provider_kind": spec.normalized_provider_kind(),
                    "runtime_kind": spec.runtime_kind,
                    "connection_kind": "native_cli_bridge",
                    "command_configured": list(spec.command),
                    "workspace": str(Path(spec.cwd).expanduser().resolve()),
                    "model": spec.model,
                    "requested_model_id": spec.requested_model_id or spec.model,
                    "observed_model_id": "",
                    "model_selection_kind": spec.model_selection_kind,
                    "model_observation_policy": spec.model_observation_policy,
                    "model_verification_status": _model_verification_status(
                        requested_model_id=spec.requested_model_id or spec.model,
                        observed_model_id="",
                        selection_kind=spec.model_selection_kind,
                        observation_policy=spec.model_observation_policy,
                    ),
                    "catalog_revision": spec.catalog_revision,
                    "reasoning_effort": spec.reasoning_effort,
                    "service_tier": spec.service_tier,
                    "variant": spec.variant,
                    "permission_mode": spec.permission_mode,
                    "runtime_profile_key": spec.runtime_profile_key(),
                    "enabled": False,
                    "runtime_status": "stopped",
                    "pending_event_ids": [],
                    "inflight_event_ids": [],
                    "turn_count": 0,
                    "last_provider_sync_event_id": latest_public,
                    "last_provider_sync_seq": latest_public_seq,
                    "last_seen_event_id": latest_public,
                    "last_seen_seq": latest_public_seq,
                    "bootstrap_cutoff_seq": latest_public_seq,
                    "recovery_attempt_count": 0,
                    "pty": spec.transport in {"pty", "conpty"},
                    "transport": spec.transport,
                    "is_one_shot": False,
                    "process_ownership": "server",
                    "reported_provider_pid": None,
                    "bridge_handle_id": "",
                    "bridge_generation": 0,
                },
            )
            state = transaction.advance_attention_state(
                agent_id,
                provider_sync_seq=latest_public_seq,
            )
            assert_provider_sync_cursor_parity(created_session, state)

    def _room_providers(self, room_id: str) -> dict[str, NativeCliProviderSpec]:
        with self._lock:
            return dict(self._providers_by_room.get(clean_lobby_text(room_id, limit=128), {}))

    def _provider(self, room_id: str, agent_id: str) -> NativeCliProviderSpec:
        spec = self._room_providers(room_id).get(clean_lobby_text(agent_id, limit=128))
        if spec is None:
            raise RoomCommandRejected(f"Unknown configured agent: {agent_id}", code="not_found")
        return spec

    def _payload_agent_id(self, payload: dict[str, object]) -> str:
        agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("participant_id") or payload.get("session_id"), limit=128)
        if not agent_id:
            raise RoomCommandRejected("agent_id is required.", code="bad_request")
        return agent_id

    def _require_capability(self, identity: dict[str, object], capability: str) -> None:
        if not self.capabilities(identity).get(capability):
            raise RoomCommandRejected(f"{capability} permission is required.", code="permission_denied")

    @staticmethod
    def _require_bridge(identity: dict[str, object]) -> None:
        if identity.get("client_type") != "agent_bridge":
            raise RoomCommandRejected("This command is reserved for an Agent Bridge.", code="permission_denied")


def _restorable_process_ownership(session: dict[str, object]) -> str:
    explicit = clean_lobby_text(session.get("process_ownership"), limit=32)
    if explicit:
        return explicit
    if session.get("external_owned"):
        return "external"
    has_native_runtime_profile = bool(
        session.get("runtime_kind")
        and session.get("runtime_profile_key")
        and session.get("command_configured")
    )
    return "server" if has_native_runtime_profile else ""


def _planned_muted_member(
    current: dict[str, object] | None,
    *,
    room_id: str,
    participant_id: str,
    muted: bool,
) -> dict[str, object]:
    if current:
        return {**current, "muted": muted}
    return {
        "meeting_id": room_id,
        "participant_id": participant_id,
        "display_name": participant_id,
        "role": "agent",
        "participant_type": "unknown",
        "provider_kind": "",
        "connection_kind": "",
        "status": "",
        "muted": muted,
        "is_host": False,
        "source": "moderation",
        "created_at": "",
        "updated_at": "",
        "last_seen_at": "",
    }


def _kick_cleanup_from_participant(participant: dict[str, object]) -> dict[str, object]:
    return {
        "revoked_sessions": int(participant.get("moderation_intent_revoked_sessions") or 0),
        "removed_member": bool(participant.get("moderation_intent_removed_member")),
        "cleanup_warning": clean_lobby_text(
            participant.get("moderation_intent_cleanup_warning"),
            limit=1200,
        ),
    }


def _external_runtime_profile_key(profile: ProviderRuntimeProfile) -> str:
    serialized = json.dumps(
        {
            **profile.report_fields(),
            "transport": profile.transport,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]


def _command_principal(identity: dict[str, object]) -> str:
    client_type = clean_lobby_text(identity.get("client_type"), limit=64) or "unknown"
    principal = clean_lobby_text(
        identity.get("session_id") or identity.get("user_id") or identity.get("agent_id"),
        limit=128,
    )
    return f"{client_type}:{principal or 'anonymous'}"


def _external_effect_operation_id(
    room_id: str,
    principal_id: str,
    request_id: str,
    action: str,
) -> str:
    serialized = "\0".join((room_id, principal_id, request_id, action))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _nested_effect_operation_id(parent_operation_id: str, subject_id: str) -> str:
    serialized = "\0".join((parent_operation_id, subject_id))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


_ACTIVE_TURN_PHASES = frozenset({"thinking", "streaming"})
_TURN_PHASE_TRANSITIONS = {
    "thinking": frozenset({"thinking", "streaming"}),
    "streaming": frozenset({"streaming"}),
}


def _require_active_turn_phase(session: dict[str, object]) -> str:
    phase = clean_lobby_text(session.get("turn_phase"), limit=32)
    if phase not in _ACTIVE_TURN_PHASES:
        raise RoomCommandRejected(
            "The active turn has an invalid phase.",
            code="turn_phase_invalid",
        )
    return phase


def _validate_turn_phase_transition(session: dict[str, object], phase: str) -> None:
    current = _require_active_turn_phase(session)
    if phase not in _ACTIVE_TURN_PHASES or phase not in _TURN_PHASE_TRANSITIONS[current]:
        raise RoomCommandRejected(
            f"Turn phase cannot transition from {current} to {phase or 'empty'}.",
            code="turn_phase_invalid",
        )


def _safe_int_or_none(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _safe_bounded_int(
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
