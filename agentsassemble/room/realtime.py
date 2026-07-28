from __future__ import annotations

import base64
import hashlib
import logging
import threading
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from agentsassemble.diagnostics.cleanup import CleanupReport, emit_cleanup_failure
from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    default_native_cli_provider_specs,
    validate_native_cli_provider_spec,
)
from agentsassemble.providers.runtime_contracts import (
    AMBIENT_OBSERVATION,
    ORDERED_FLOOR,
)
from agentsassemble.providers.capabilities import (
    PROVIDER_CAPABILITIES,
    ValidatedProviderSelection,
)
from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
)
from agentsassemble.room.commands import (
    RoomCommandValidationError,
    capabilities_for_identity,
    parse_room_command,
)
from agentsassemble.room.command_uow import (
    RoomCommandIdempotencyConflict,
    RoomCommandUnitOfWork,
    command_payload_hash,
)
from agentsassemble.room.deleted_cleanup import RoomDeletedCleanupService
from agentsassemble.room.deletion import RoomDeletionService
from agentsassemble.room.agent_creation import RoomAgentCreationService
from agentsassemble.room.bridge_reports import RoomBridgeReportService
from agentsassemble.room.connections import RoomConnectionService
from agentsassemble.room.agent_lifecycle import (
    AgentBridgeManager,
    RecoveryScheduler,
    RoomAgentLifecycle,
    schedule_daemon_timer,
)
from agentsassemble.room.agent_profiles import RoomAgentProfileService
from agentsassemble.room.agent_reactivation import RoomAgentReactivationService
from agentsassemble.room.agent_runtime_profiles import (
    RoomAgentRuntimeProfileService,
)
from agentsassemble.room.attachments import AttachmentError, read_attachment_file
from agentsassemble.room_attention_coordinator import RoomAttentionCoordinator
from agentsassemble.room_attention_reconciliation import RoomAttentionReconciler
from agentsassemble.room_attention_policy import (
    normalize_shadow_attention_mode,
    should_record_shadow_attention,
)
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import (
    RoomEventBroker,
    RoomSocketChannel,
)
from agentsassemble.room_floor_policy import (
    AgentFloorEligibility,
    continuous_floor_targets,
    evaluate_agent_floor_eligibility,
    ordered_floor_target,
)
from agentsassemble.room.moderation import (
    is_room_member_muted,
    remove_room_member,
    set_room_member_muted,
)
from agentsassemble.room.attachments import FileAttachmentStore
from agentsassemble.room.messages import RoomMessageService
from agentsassemble.room.member_mute import RoomMemberMuteService
from agentsassemble.room.participant_kick import RoomParticipantKickService
from agentsassemble.room.participant_leave import RoomParticipantLeaveService
from agentsassemble.room.projection import (
    public_event as _public_event,
    public_participant,
    public_session,
)
from agentsassemble.room.provider_registry import RoomProviderRegistry
from agentsassemble.room.provider_sessions import RoomProviderSessionService
from agentsassemble.providers.sync_cursor import (
    ProviderSyncCursorParityError,
    ProviderSyncCursorReconciler,
)
from agentsassemble.room_routing import direct_message_targets, route_message_targets
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.snapshots import (
    ROOM_HISTORY_MAX_LIMIT,
    ROOM_SNAPSHOT_EVENT_LIMIT,
    RoomSnapshotService,
)
from agentsassemble.room.settings_commands import RoomGlobalSettingsCommandService
from agentsassemble.room.startup_reconciliation import RoomStartupSessionReconciler
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.turn_coordinator import RoomTurnCoordinator
from agentsassemble.room.turn_context import build_room_turn_packet
from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.room.types import RoomCommand, RoomEvent
from agentsassemble.room.votes import vote_summary
from agentsassemble.room.voice_presence import leave_all_voice

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
_LOGGER = logging.getLogger("agentsassemble.room_realtime")


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
        self._lock = threading.RLock()
        self._provider_registry = RoomProviderRegistry(
            lock=self._lock,
            default_room_id=self.default_room_id,
        )
        self._event_listener_removers: dict[str, Callable[[], None]] = {}
        self._provider_catalog_remove = self.provider_catalog.subscribe(self._on_provider_catalog_update)
        self._closed = False
        self._provider_sessions = RoomProviderSessionService(
            store=self.store,
            broker=self.broker,
            lock=self._lock,
            registry=self._provider_registry,
            ensure_room=self.ensure_room,
            publish_session_state=self._publish_session_state,
        )
        self._connections = RoomConnectionService(
            store=self.store,
            broker=self.broker,
            ensure_room=self.ensure_room,
            ensure_external_bridge_session=self._provider_sessions.ensure_external_bridge_session,
            publish_session_state=self._publish_session_state,
        )
        self._snapshots = RoomSnapshotService(
            store=self.store,
            provider_catalog=self.provider_catalog,
            ensure_room=self.ensure_room,
            capabilities=self.capabilities,
        )
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
        self._bridge_reports = RoomBridgeReportService(
            store=self.store,
            broker=self.broker,
            bridge_session=self._turn_coordinator.bridge_session,
            assign_pending=self._turn_coordinator.assign_pending,
            publish_session_state=self._publish_session_state,
        )
        self._member_mute = RoomMemberMuteService(
            store=self.store,
            broker=self.broker,
            assign_pending=self._turn_coordinator.assign_pending,
            compatibility_mute_writer=lambda room_id, participant_id, muted: (
                set_room_member_muted(
                    self.output_root,
                    meeting_id=room_id,
                    participant_id=participant_id,
                    muted=muted,
                )
            ),
        )
        self._participant_leave = RoomParticipantLeaveService(
            remove_membership=lambda room_id, participant_id: (
                identity_store_for_output_root(self.output_root).remove_membership(
                    room_id,
                    participant_id,
                )
            ),
            leave_all_voice=lambda room_id, participant_id: leave_all_voice(
                room_id,
                participant_id,
            ),
            revoke_participant_sessions=lambda room_id, participant_id: (
                self._room_sessions.revoke_participant(
                    room_id,
                    participant_id,
                )
            ),
            schedule_cleanup=lambda delay, callback: schedule_daemon_timer(
                delay,
                callback,
            ),
        )
        self._startup_sessions = RoomStartupSessionReconciler(
            store=self.store,
            reconcile_session_attention=self._turn_coordinator.reconcile_session_attention,
        )
        self._agent_profiles = RoomAgentProfileService(
            store=self.store,
            provider_registry=self._provider_registry,
            publish_session_state=self._publish_session_state,
        )
        self._agent_runtime_profiles = RoomAgentRuntimeProfileService(
            store=self.store,
            provider_catalog=self.provider_catalog,
            configure_stopped_profile=self.configure_stopped_provider_profile,
        )
        self._agent_lifecycle = RoomAgentLifecycle(
            store=self.store,
            broker=self.broker,
            bridge_manager=bridge_manager,
            lock=self._lock,
            provider_lookup=self._provider,
            ensure_provider_session=self._provider_sessions.ensure_provider_session,
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
        self._participant_kick = RoomParticipantKickService(
            store=self.store,
            stop_agent=lambda room_id, participant_id, operation_id: (
                self._agent_lifecycle.stop(
                    room_id,
                    participant_id,
                    operation_id=operation_id,
                )
            ),
            revoke_participant_sessions=lambda room_id, participant_id: (
                self._room_sessions.revoke_participant(
                    room_id,
                    participant_id,
                )
            ),
            disconnect_participant=lambda room_id, participant_id: (
                self.broker.disconnect_participant(
                    room_id,
                    participant_id,
                )
            ),
            remove_membership=lambda room_id, participant_id: (
                remove_room_member(
                    self.output_root,
                    room_id,
                    participant_id,
                )
            ),
            leave_all_voice=lambda room_id, participant_id: leave_all_voice(
                room_id,
                participant_id,
            ),
            remove_provider=self._provider_registry.remove,
        )
        self._deleted_room_cleanup = RoomDeletedCleanupService(
            store=self.store,
            broker=self.broker,
            provider_registry=self._provider_registry,
            output_root=self.output_root,
            revoke_room_invites=lambda room_id: (
                self._invite_application.revoke_room(room_id)
            ),
            revoke_room_sessions=lambda room_id: (
                self._room_sessions.revoke_room(room_id)
            ),
            purge_terminal_admission_workflows=lambda room_id: (
                self._invite_application
                .remove_terminal_admission_workflows_for_room(
                    room_id
                )
                .purged_count
            ),
            delete_identity_room=lambda room_id: (
                identity_store_for_output_root(
                    self.output_root
                ).delete_room(room_id)
            ),
            remove_event_listener=self._remove_room_event_listener,
            schedule_cleanup=lambda delay, callback: schedule_daemon_timer(
                delay,
                callback,
            ),
        )
        self._room_deletion = RoomDeletionService(
            store=self.store,
            identity_room=lambda room_id: (
                identity_store_for_output_root(self.output_root).get_room(
                    room_id
                )
                or {}
            ),
            has_bridge=lambda room_id, session_id: self.broker.has_bridge(
                room_id,
                session_id,
            ),
            stop_agent=lambda room_id, session_id, operation_id: (
                self._agent_lifecycle.stop(
                    room_id,
                    session_id,
                    operation_id=operation_id,
                )
            ),
            revoke_participant_sessions=lambda room_id, participant_id: (
                self._room_sessions.revoke_participant(
                    room_id,
                    participant_id,
                )
            ),
            disconnect_participant=lambda room_id, participant_id: (
                self.broker.disconnect_participant(
                    room_id,
                    participant_id,
                )
            ),
            complete_cleanup=lambda room_id, room_name, ack, deduplicated: (
                self._complete_deleted_room_cleanup(
                    room_id,
                    room_name=room_name,
                    ack=ack,
                    deduplicated=deduplicated,
                )
            ),
        )
        self._agent_creation = RoomAgentCreationService(
            store=self.store,
            provider_catalog=self.provider_catalog,
            create_provider_session=self.create_provider_session,
            start_agent=self._agent_lifecycle.start,
        )
        self._agent_reactivation = RoomAgentReactivationService(
            store=self.store,
            broker=self.broker,
            lock=self._lock,
            provider_registry=self._provider_registry,
            ensure_provider_session=self._provider_sessions.ensure_provider_session,
            publish_session_state=self._publish_session_state,
            start_agent=self._agent_lifecycle.start,
        )
        self._messages = RoomMessageService(FileAttachmentStore(self.output_root))
        self._room_settings = RoomGlobalSettingsCommandService()
        self.last_cleanup_report = CleanupReport("room_realtime_controller")
        self.ensure_room(self.default_room_id)
        self._provider_sessions.restore_server_owned_providers()
        for agent_id, spec in default_providers.items():
            if self.store.session(self.default_room_id, agent_id) or self.store.participant(
                self.default_room_id, agent_id
            ):
                continue
            self._provider_registry.register(self.default_room_id, spec)
            self._provider_sessions.ensure_provider_session(self.default_room_id, spec)
        self._reconcile_startup_sessions()
        self._provider_sync_cursor_reconciliation_report = ProviderSyncCursorReconciler(
            self.store
        ).reconcile()
        self._attention_reconciliation_report = RoomAttentionReconciler(self.store).reconcile()

    def create_provider_session(self, room_id: str, spec: NativeCliProviderSpec) -> dict[str, object]:
        return self._provider_sessions.create_provider_session(room_id, spec)

    def configure_stopped_provider_profile(
        self,
        room_id: str,
        spec: NativeCliProviderSpec,
    ) -> dict[str, object]:
        return self._provider_sessions.configure_stopped_provider_profile(room_id, spec)

    def _reconcile_startup_sessions(self) -> None:
        self._startup_sessions.reconcile()

    def ensure_room(self, room_id: str) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        with self._lock:
            room = self.store.ensure_room(
                clean_room_id,
                label="#general" if clean_room_id == "general" else clean_room_id,
            )
            if clean_room_id not in self._event_listener_removers:
                self._event_listener_removers[clean_room_id] = self.store.add_event_listener(
                    clean_room_id,
                    self._on_event_appended,
                )
            return room

    def connect(self, identity: dict[str, object]) -> RoomSocketChannel:
        return self._connections.connect(identity)

    def disconnect(self, channel: RoomSocketChannel) -> None:
        self._connections.disconnect(channel)

    def snapshot(self, identity: dict[str, object], *, after_seq: int = 0) -> dict[str, object]:
        return self._snapshots.snapshot(identity, after_seq=after_seq)

    def history_page(self, room_id: str, *, before_seq: int, limit: int = ROOM_HISTORY_MAX_LIMIT) -> dict[str, object]:
        return self._snapshots.history_page(
            room_id,
            before_seq=before_seq,
            limit=limit,
        )

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
        if action == "room.check":
            self._require_bridge(identity)
            result = self._turn_coordinator.request_room_check(identity, room_id)
            return {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "action": action,
                "result": result,
                "deduplicated": False,
            }
        if action == "room.attachment.read":
            self._require_bridge(identity)
            result = self._read_room_attachment(identity, room_id, payload)
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
        if action == "room.vote.summary":
            if identity.get("client_type") == "agent_bridge":
                raise RoomCommandRejected(
                    "Agent Bridges read votes through their assigned room view.",
                    code="permission_denied",
                )
            try:
                vote_id = clean_lobby_text(payload.get("vote_id"), limit=128)
                result = vote_summary(
                    self.store.vote_events(room_id, vote_id),
                    vote_id,
                )
            except ValueError as error:
                raise RoomCommandRejected(
                    str(error),
                    code="vote_not_found",
                ) from error
            return {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
                "action": action,
                "result": result,
                "deduplicated": False,
            }
        if action == "room.settings.update":
            self._require_capability(identity, "room.manage")
            with self._lock:
                ack = self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._room_settings.update_in_unit(
                        payload,
                        actor_id=clean_lobby_text(identity.get("agent_id"), limit=128),
                        actor_type=clean_lobby_text(
                            identity.get("participant_type"),
                            limit=32,
                        )
                        or "human",
                        unit=unit,
                    ),
                )
                self._turn_coordinator.reconcile_conversation_mode(room_id)
                return ack
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
                self._participant_kick.apply_after_commit(
                    room_id,
                    participant,
                )
                return ack
        if action == "room.result.publish":
            self._require_bridge(identity)
            with self._lock:
                return self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._turn_coordinator.room_result_in_unit(
                        identity,
                        payload,
                        unit=unit,
                    ),
                )
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
            provider_agents = self._provider_registry.provider_agents()
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
        return self._messages.send_in_unit(
            identity,
            payload,
            unit=unit,
            compatibility_muted=compatibility_muted,
        )

    def _create_agent(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        return self._agent_creation.create(
            room_id,
            payload,
            server_url=server_url,
            ticket_issuer=ticket_issuer,
        )

    def _readd_agent(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        return self._agent_reactivation.readd(
            room_id,
            self._payload_agent_id(payload),
            payload,
            server_url=server_url,
            ticket_issuer=ticket_issuer,
        )

    def _configure_agent(self, room_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._agent_runtime_profiles.configure(
            room_id,
            self._payload_agent_id(payload),
            payload,
        )

    def _configure_agent_profile(
        self,
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        return self._agent_profiles.update_in_unit(
            self._payload_agent_id(payload),
            payload,
            unit=unit,
        )

    def _apply_agent_profile_after_commit(
        self,
        room_id: str,
        ack: dict[str, object],
    ) -> None:
        self._agent_profiles.apply_after_commit(room_id, ack)

    def _prepare_kick_intent(
        self,
        room_id: str,
        participant_id: str,
        *,
        operation_id: str,
    ) -> dict[str, object]:
        return self._participant_kick.prepare_intent(
            room_id,
            participant_id,
            operation_id=operation_id,
        )

    def _apply_kick_effects(
        self,
        room_id: str,
        participant: dict[str, object],
        *,
        operation_id: str,
    ) -> dict[str, object]:
        return self._participant_kick.apply_effects(
            room_id,
            participant,
            operation_id=operation_id,
        )

    def _finalize_kick_durable(
        self,
        participant_id: str,
        *,
        operation_id: str,
        cleanup: dict[str, object],
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        return self._participant_kick.finalize_in_unit(
            participant_id,
            operation_id=operation_id,
            cleanup=cleanup,
            unit=unit,
        )

    def _mute_participant_durable(
        self,
        participant_id: str,
        muted: bool,
        compatibility_member: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        return self._member_mute.update_in_unit(
            participant_id,
            muted,
            compatibility_member,
            unit=unit,
        )

    def _apply_mute_after_commit(
        self,
        room_id: str,
        participant_id: str,
        muted: bool,
    ) -> None:
        self._member_mute.apply_after_commit(
            room_id,
            participant_id,
            muted,
        )

    def _leave_participant_durable(
        self,
        participant_id: str,
        *,
        is_owner: bool,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        return self._participant_leave.update_in_unit(
            participant_id,
            is_owner=is_owner,
            unit=unit,
        )

    def _schedule_participant_leave_cleanup(self, room_id: str, participant_id: str) -> None:
        self._participant_leave.apply_after_commit(
            room_id,
            participant_id,
        )

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
        return self._room_deletion.delete(
            room_id,
            str(payload.get("confirmation_name") or ""),
            is_owner=self._is_room_owner(identity, room_id),
            request_id=request_id,
            principal_id=principal_id,
            payload_hash=payload_hash,
            operation_id=operation_id,
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
        return self._room_deletion.resume(
            room_id,
            principal_id=_command_principal(identity),
            request_id=request_id,
            payload_hash=command_payload_hash(payload),
            tombstone=tombstone,
        )

    def _complete_deleted_room_cleanup(
        self,
        room_id: str,
        *,
        room_name: str,
        ack: dict[str, object],
        deduplicated: bool,
    ) -> dict[str, object]:
        return self._deleted_room_cleanup.complete(
            room_id,
            room_name,
            ack,
            deduplicated,
        )

    def _remove_room_event_listener(self, room_id: str) -> None:
        remove_listener = self._event_listener_removers.pop(room_id, None)
        if remove_listener is not None:
            remove_listener()

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
        return self._bridge_reports.ready(identity, room_id, payload)

    def _bridge_health(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._bridge_reports.health(identity, room_id, payload)

    def _read_room_attachment(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        self._turn_coordinator.bridge_session(identity, room_id)
        attachment_id = clean_lobby_text(payload.get("attachment_id"), limit=64)
        referenced = any(
            isinstance(attachment, dict)
            and clean_lobby_text(attachment.get("id"), limit=64) == attachment_id
            for event in self.store.read_events(
                room_id,
                event_types=("message_final",),
            )
            for attachment in (
                event.get("attachments")
                if isinstance(event.get("attachments"), list)
                else []
            )
        )
        if not attachment_id or not referenced:
            raise RoomCommandRejected(
                "Attachment is not part of this room.",
                code="not_found",
            )
        try:
            metadata, path = read_attachment_file(self.output_root, attachment_id)
            content = path.read_bytes()
        except (AttachmentError, OSError) as error:
            raise RoomCommandRejected(str(error), code="not_found") from error
        return {
            "attachment": metadata,
            "data_base64": base64.b64encode(content).decode("ascii"),
        }

    def _on_event_appended(self, event: RoomEvent | dict[str, object]) -> None:
        self.broker.broadcast_event(_public_event(event))
        if event.get("type") != "message_final":
            return
        if (
            event.get("message_source") == "room_tool_result"
            or event.get("message_kind") == "vote_cast"
        ):
            return
        with self._lock:
            self._route_message_event(event)

    def _route_message_event(self, event: RoomEvent | dict[str, object]) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        providers = self._room_providers(room_id)
        settings = self.store.room_settings(room_id)
        conversation_mode = settings.get("conversation_mode")
        if conversation_mode == "ambient":
            self._route_ambient_event(
                dict(event),
                providers,
            )
            return
        if conversation_mode == "ordered":
            self._route_ordered_event(
                dict(event),
                providers,
                exclude_previous_speaker=bool(
                    settings.get("ordered_exclude_previous_speaker")
                ),
            )
            return
        self._record_shadow_attention(dict(event), providers)
        continuous = conversation_mode == "continuous"
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

    def _route_ordered_event(
        self,
        event: dict[str, object],
        providers: dict[str, NativeCliProviderSpec],
        *,
        exclude_previous_speaker: bool,
    ) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
        actor_id = clean_lobby_text(
            actor.get("participant_id") or event.get("participant_id"),
            limit=128,
        )
        direct_targets = direct_message_targets(event, providers)
        eligible_agent_ids: list[str] = []
        for agent_id in providers:
            if agent_id == actor_id:
                continue
            eligibility = self.agent_floor_eligibility(room_id, agent_id)
            if eligibility.eligible or eligibility.reason_code == "runtime_busy":
                eligible_agent_ids.append(agent_id)
        message_counts, previous_speaker_id = self._recent_agent_speaking_state(
            room_id,
            providers,
        )
        target_ids = ordered_floor_target(
            provider_ids=providers,
            actor_id=actor_id,
            direct_targets=direct_targets,
            eligible_agent_ids=eligible_agent_ids,
            message_counts=message_counts,
            previous_speaker_id=previous_speaker_id,
            exclude_previous_speaker=exclude_previous_speaker,
        )
        for agent_id in target_ids:
            participant = self.store.participant(room_id, agent_id)
            if participant.get("status") == "kicked" or participant.get("muted"):
                continue
            self._turn_coordinator.queue_event(
                room_id,
                agent_id,
                event,
                relay_depth=0,
                input_mode="room_observation",
                observation_kind=ORDERED_FLOOR,
            )

    def _recent_agent_speaking_state(
        self,
        room_id: str,
        providers: dict[str, NativeCliProviderSpec],
    ) -> tuple[dict[str, int], str]:
        counts = {agent_id: 0 for agent_id in providers}
        previous_speaker_id = ""
        for message in self.store.read_events(
            room_id,
            event_types=("message_final",),
            limit=100,
            newest=True,
        ):
            actor = message.get("actor") if isinstance(message.get("actor"), dict) else {}
            participant_id = clean_lobby_text(
                message.get("participant_id") or actor.get("participant_id"),
                limit=128,
            )
            if participant_id in counts:
                counts[participant_id] += 1
                previous_speaker_id = participant_id
        return counts, previous_speaker_id

    def _route_ambient_event(
        self,
        event: dict[str, object],
        providers: dict[str, NativeCliProviderSpec],
    ) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        actor_id = clean_lobby_text(
            event.get("participant_id") or event.get("actor_id"),
            limit=128,
        )
        target_ids: list[str] = []
        for agent_id in providers:
            if agent_id == actor_id:
                continue
            eligibility = self.agent_floor_eligibility(room_id, agent_id)
            if eligibility.eligible or eligibility.reason_code == "runtime_busy":
                target_ids.append(agent_id)
        for agent_id in target_ids:
            try:
                self._turn_coordinator.queue_event(
                    room_id,
                    agent_id,
                    event,
                    relay_depth=0,
                    input_mode="room_observation",
                    observation_kind=AMBIENT_OBSERVATION,
                )
            except Exception as error:
                self._attention_active_error_count += 1
                self._attention_active_last_error = str(error)
                _LOGGER.exception(
                    "Autonomous room wake failed",
                    extra={
                        "room_id": room_id,
                        "event_id": str(event.get("id") or ""),
                        "participant_id": agent_id,
                    },
                )
                self.store.append_event(
                    room_id,
                    "error",
                    participant_id=agent_id,
                    content="Autonomous room wake failed.",
                    error_code="ambient_wake_failed",
                )

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

    def _room_providers(self, room_id: str) -> dict[str, NativeCliProviderSpec]:
        return self._provider_registry.providers(room_id)

    def _provider(self, room_id: str, agent_id: str) -> NativeCliProviderSpec:
        return self._provider_registry.provider(room_id, agent_id)

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
