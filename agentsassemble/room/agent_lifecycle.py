from __future__ import annotations

import threading
from typing import Callable, Iterable, Protocol
from uuid import uuid4

from agentsassemble.room.bridge_stop_confirmation import (
    BridgeStopConfirmationError,
    ExternalBridgeStopCoordinator,
)
from agentsassemble.diagnostics.cleanup import CleanupReport
from agentsassemble.diagnostics.sensitive_text import redact_persisted_diagnostic_text
from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.projection import public_session
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text as clean_lobby_text


class AgentBridgeManager(Protocol):
    def start(
        self,
        room_id: str,
        session: dict[str, object],
        spec: NativeCliProviderSpec,
        *,
        server_url: str = "",
        ticket_issuer: Callable[[dict[str, object]], object] | None = None,
    ) -> dict[str, object]: ...

    def stop(
        self,
        room_id: str,
        session_id: str,
        *,
        timeout_seconds: float = 2.0,
        handle_id: str = "",
    ) -> dict[str, object]: ...

    def close(self) -> CleanupReport | None: ...

    def health(self, room_id: str, session_id: str) -> dict[str, object]: ...

    def redact_diagnostic(
        self,
        room_id: str,
        session_id: str,
        value: object,
        *,
        limit: int = 16000,
    ) -> str: ...

    def release_preserved_security_values(
        self,
        room_id: str,
        session_id: str,
    ) -> None: ...


RecoveryScheduler = Callable[[float, Callable[[], None]], object]
ProviderLookup = Callable[[str, str], NativeCliProviderSpec]
SessionCallback = Callable[[str, dict[str, object]], object]
PendingAssignment = Callable[[str, str], bool]
EnsureProviderSession = Callable[[str, NativeCliProviderSpec], None]
SessionRevoker = Callable[[str, str], int]
PrepareSessionReset = Callable[..., dict[str, object]]


class RoomAgentLifecycle:
    """Owns provider process launch, pause, recovery, and shutdown state."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        bridge_manager: AgentBridgeManager | None,
        lock: threading.RLock,
        provider_lookup: ProviderLookup,
        ensure_provider_session: EnsureProviderSession,
        revoke_participant_sessions: SessionRevoker,
        publish_session_state: SessionCallback,
        assign_pending: PendingAssignment,
        is_closed: Callable[[], bool],
        recovery_delay_seconds: float,
        external_stop_timeout_seconds: float,
        recovery_scheduler: RecoveryScheduler,
        prepare_session_reset: PrepareSessionReset,
    ) -> None:
        self.store = store
        self.broker = broker
        self.bridge_manager = bridge_manager
        self._lock = lock
        self._provider_lookup = provider_lookup
        self._ensure_provider_session = ensure_provider_session
        self._revoke_participant_sessions = revoke_participant_sessions
        self._publish_session_state = publish_session_state
        self._assign_pending = assign_pending
        self._is_closed = is_closed
        self.recovery_delay_seconds = max(0.0, float(recovery_delay_seconds))
        self._recovery_scheduler = recovery_scheduler
        self._prepare_session_reset = prepare_session_reset
        self._external_stop_confirmations = ExternalBridgeStopCoordinator(
            timeout_seconds=external_stop_timeout_seconds,
        )
        self._launch_contexts: dict[
            tuple[str, str],
            tuple[str, Callable[[dict[str, object]], object] | None],
        ] = {}
        self._recovery_handles: dict[tuple[str, str], object] = {}

    def confirm_external_stopped(
        self,
        room_id: str,
        agent_id: str,
        *,
        generation: int,
        payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            return self._external_stop_confirmations.confirm(
                room_id,
                agent_id,
                generation=generation,
                payload=payload,
                before_release=lambda operation_id, _result: self._record_stop_effect(
                    room_id,
                    agent_id,
                    operation_id=operation_id,
                ),
            )
        except BridgeStopConfirmationError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error

    def process_health(self, room_id: str, session_id: str) -> dict[str, object]:
        if self.bridge_manager is None:
            return {}
        return dict(self.bridge_manager.health(room_id, session_id))

    def start(
        self,
        room_id: str,
        agent_id: str,
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], object] | None,
        automatic_recovery: bool = False,
        operation_id: str = "",
    ) -> dict[str, object]:
        spec = self._provider_lookup(room_id, agent_id)
        session = self.store.session(room_id, agent_id)
        participant = self.store.participant(room_id, agent_id)
        if participant.get("status") == "kicked":
            raise RoomCommandRejected(
                "This agent was removed from the room. Add it again before starting it.",
                code="participant_kicked",
            )
        if clean_lobby_text(participant.get("moderation_intent_action"), limit=32) == "kick":
            raise RoomCommandRejected(
                "This agent is being removed from the room.",
                code="participant_kick_in_progress",
            )
        if not session:
            self._ensure_provider_session(room_id, spec)
            session = self.store.session(room_id, agent_id)
        intent_action = clean_lobby_text(session.get("lifecycle_intent_action"), limit=32)
        intent_status = clean_lobby_text(session.get("lifecycle_intent_status"), limit=32)
        recovering_incomplete_start = bool(
            intent_action == "start"
            and intent_status == "prepared"
            and not session.get("bridge_handle_id")
        )
        if (
            session.get("runtime_status") in {"starting", "idle", "busy", "paused"}
            and not recovering_incomplete_start
        ):
            return {"agent_session": public_session(session), "runtime_reused": True}
        current_operation_id = (
            clean_lobby_text(session.get("lifecycle_intent_id"), limit=128)
            if recovering_incomplete_start
            else _lifecycle_operation_id(operation_id)
        )
        if not recovering_incomplete_start:
            self.store.update_session_fields(
                room_id,
                agent_id,
                status="available",
                enabled=True,
                runtime_status="starting",
                last_error="",
                recovery_required=bool(session.get("recovery_required")) if automatic_recovery else False,
                recovery_attempt_count=(
                    int(session.get("recovery_attempt_count") or 0) if automatic_recovery else 0
                ),
                lifecycle_intent_action="start",
                lifecycle_intent_id=current_operation_id,
                lifecycle_intent_status="prepared",
            )
        if self.bridge_manager is None:
            self.store.update_session_fields(
                room_id,
                agent_id,
                status="unavailable",
                enabled=False,
                runtime_status="error",
                last_error="Agent bridge manager is unavailable.",
                lifecycle_intent_action="",
                lifecycle_intent_id="",
                lifecycle_intent_status="",
            )
            raise RoomCommandRejected("Agent bridge manager is unavailable.", code="runtime_unavailable")
        launch_key = (room_id, agent_id)
        self._launch_contexts[launch_key] = (server_url, ticket_issuer)
        try:
            launch = self.bridge_manager.start(
                room_id,
                self.store.session(room_id, agent_id),
                spec,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
            )
        except Exception as error:
            if not automatic_recovery:
                self._launch_contexts.pop(launch_key, None)
            safe_error = redact_persisted_diagnostic_text(error, limit=4000) or "Provider launch failed."
            self.store.update_session_fields(
                room_id,
                agent_id,
                status="unavailable",
                enabled=False,
                runtime_status="error",
                last_error=safe_error,
                lifecycle_intent_action="",
                lifecycle_intent_id="",
                lifecycle_intent_status="",
            )
            self.store.append_event(
                room_id,
                "error",
                participant_id=agent_id,
                session_id=agent_id,
                content=safe_error,
                error_code="runtime_start_failed",
            )
            raise RoomCommandRejected(safe_error, code="runtime_start_failed") from error
        updated = self.store.update_session_fields(
            room_id,
            agent_id,
            bridge_pid=launch.get("bridge_pid"),
            bridge_handle_id=launch.get("bridge_handle_id") or "",
            process_ownership="server",
            resolved_executable=launch.get("resolved_executable") or "",
            lifecycle_intent_action="",
            lifecycle_intent_id="",
            lifecycle_intent_status="",
        )
        self._publish_session_state(room_id, updated)
        return {
            "agent_session": public_session(updated),
            "launch": {
                "runtime_reused": bool(launch.get("runtime_reused")),
                "runtime_profile_key": launch.get("runtime_profile_key") or "",
            },
            "runtime_reused": bool(launch.get("runtime_reused")),
        }

    def resume(
        self,
        room_id: str,
        agent_id: str,
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], object] | None,
    ) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if session and session.get("runtime_status") == "paused":
            if not self.broker.has_bridge(room_id, agent_id):
                raise RoomCommandRejected(
                    "The paused Agent Session bridge is no longer connected.",
                    code="runtime_unavailable",
                )
            self.store.update_session_fields(
                room_id,
                agent_id,
                status="attached",
                enabled=True,
                runtime_status="idle",
                last_error="",
            )
            self.store.append_event(
                room_id,
                "session_resumed",
                participant_id=agent_id,
                session_id=agent_id,
                process_reused=True,
            )
            self._assign_pending(room_id, agent_id)
            current = self.store.session(room_id, agent_id)
            self._publish_session_state(room_id, current)
            return {
                "agent_session": public_session(current),
                "runtime_reused": True,
                "process_reused": True,
            }
        if session and session.get("runtime_status") in {"starting", "idle", "busy"}:
            return {"agent_session": public_session(session), "runtime_reused": True}
        profile_error_code = clean_lobby_text(session.get("last_error_code"), limit=64) if session else ""
        if profile_error_code in {
            "profile_incomplete",
            "profile_migration_required",
            "provider_definition_changed",
        }:
            raise RoomCommandRejected(
                "This Agent Session runtime profile must be saved again before it can resume.",
                code=profile_error_code,
            )
        if session and self._known_stopped_server_runtime(room_id, agent_id, session):
            self._finalize_stop(
                room_id,
                agent_id,
                session,
                disable=False,
                process={
                    "stopped": True,
                    "alive": False,
                    "ownership": "server",
                    "confirmed": True,
                    "already_stopped": True,
                },
                revoked_sessions=0,
            )
            session = self.store.session(room_id, agent_id)
        if session and session.get("runtime_status") not in {"stopped", "available"}:
            self.stop(room_id, agent_id, disable=False)
        return self.start(room_id, agent_id, server_url=server_url, ticket_issuer=ticket_issuer)

    def _known_stopped_server_runtime(
        self,
        room_id: str,
        agent_id: str,
        session: dict[str, object],
    ) -> bool:
        ownership = clean_lobby_text(session.get("process_ownership"), limit=32) or (
            "external" if session.get("external_owned") else "server"
        )
        return bool(
            ownership == "server"
            and session.get("runtime_status") in {"error", "disconnected"}
            and not session.get("bridge_handle_id")
            and not self.broker.has_bridge(room_id, agent_id)
            and self.process_health(room_id, agent_id).get("running") is False
        )

    def pause(self, room_id: str, agent_id: str) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        runtime_status = clean_lobby_text(session.get("runtime_status"), limit=32)
        if runtime_status == "paused":
            return {
                "agent_session": public_session(session),
                "runtime_reused": True,
                "process_preserved": True,
            }
        if runtime_status != "idle" or not self.broker.has_bridge(room_id, agent_id):
            raise RoomCommandRejected(
                "Only an idle, connected Agent Session can be paused.",
                code="invalid_state",
            )
        updated = self.store.update_session_fields(
            room_id,
            agent_id,
            status="attached",
            enabled=False,
            runtime_status="paused",
            last_error="",
        )
        self.store.append_event(
            room_id,
            "session_paused",
            participant_id=agent_id,
            session_id=agent_id,
            process_preserved=True,
        )
        self._publish_session_state(room_id, updated)
        return {
            "agent_session": public_session(updated),
            "runtime_reused": True,
            "process_preserved": True,
        }

    def stop(
        self,
        room_id: str,
        agent_id: str,
        *,
        disable: bool = True,
        operation_id: str = "",
    ) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        ownership = clean_lobby_text(session.get("process_ownership"), limit=32) or (
            "external" if session.get("external_owned") else "server"
        )
        if (
            session.get("runtime_status") in {"stopped", "available"}
            and not self.broker.has_bridge(room_id, agent_id)
            and not session.get("bridge_handle_id")
        ):
            return {
                "agent_session": public_session(session),
                "process": {
                    "stopped": True,
                    "alive": False,
                    "ownership": ownership,
                    "already_stopped": True,
                },
                "revoked_sessions": 0,
            }
        key = (room_id, agent_id)
        recovery_handle = self._recovery_handles.pop(key, None)
        cancel = getattr(recovery_handle, "cancel", None)
        if callable(cancel):
            cancel()
        if disable:
            self._launch_contexts.pop(key, None)
        existing_stop_intent = clean_lobby_text(session.get("lifecycle_intent_action"), limit=32) == "stop"
        current_operation_id = (
            clean_lobby_text(session.get("lifecycle_intent_id"), limit=128)
            if existing_stop_intent
            else _lifecycle_operation_id(operation_id)
        )
        if not existing_stop_intent:
            self.store.update_session_fields(
                room_id,
                agent_id,
                runtime_status="stopping",
                enabled=False if disable else bool(session.get("enabled")),
                lifecycle_intent_action="stop",
                lifecycle_intent_id=current_operation_id,
                lifecycle_intent_status="prepared",
            )
            session = self.store.session(room_id, agent_id)
        effect_already_applied = (
            clean_lobby_text(session.get("lifecycle_intent_status"), limit=32) == "effect_applied"
        )
        if effect_already_applied:
            revoked_sessions = self._revoke_participant_sessions(room_id, agent_id) if disable else 0
            self.broker.disconnect_participant(room_id, agent_id)
            return self._finalize_stop(
                room_id,
                agent_id,
                session,
                disable=disable,
                process={
                    "stopped": True,
                    "alive": False,
                    "ownership": ownership,
                    "confirmed": True,
                    "already_stopped": True,
                },
                revoked_sessions=revoked_sessions,
            )
        manager_health = self.process_health(room_id, agent_id)
        manager_running = manager_health.get("running")
        remotely_owned_stop = ownership == "external" or (
            ownership == "server"
            and manager_running is False
            and self.broker.has_bridge(room_id, agent_id)
        )
        if remotely_owned_stop:
            try:
                stopped = self._external_stop_confirmations.request(
                    room_id,
                    agent_id,
                    generation=int(session.get("bridge_generation") or 0),
                    operation_id=current_operation_id,
                    send=lambda message: self.broker.direct_to_bridge(room_id, agent_id, message),
                )
            except BridgeStopConfirmationError as error:
                revoked_sessions = self._revoke_participant_sessions(room_id, agent_id) if disable else 0
                self.broker.disconnect_participant(room_id, agent_id)
                self._mark_stop_unconfirmed(
                    room_id,
                    agent_id,
                    session,
                    disable=disable,
                    message=str(error),
                    error_code=error.code,
                )
                raise RoomCommandRejected(str(error), code=error.code) from error
            revoked_sessions = self._revoke_participant_sessions(room_id, agent_id) if disable else 0
            self.broker.disconnect_participant(room_id, agent_id)
            return self._finalize_stop(
                room_id,
                agent_id,
                session,
                disable=disable,
                process={
                    **stopped,
                    "alive": False,
                    "ownership": ownership,
                    "confirmed": True,
                    "adopted_after_restart": ownership == "server",
                },
                revoked_sessions=revoked_sessions,
            )

        if (
            existing_stop_intent
            and manager_running is False
            and not self.broker.has_bridge(room_id, agent_id)
        ):
            self._record_stop_effect(
                room_id,
                agent_id,
                operation_id=current_operation_id,
            )
            return self._finalize_stop(
                room_id,
                agent_id,
                self.store.session(room_id, agent_id),
                disable=disable,
                process={
                    "stopped": True,
                    "alive": False,
                    "ownership": "server",
                    "confirmed": True,
                    "already_stopped": True,
                },
                revoked_sessions=0,
            )
        self.broker.direct_to_bridge(room_id, agent_id, {"op": "agent.control", "action": "stop"})
        try:
            if self.bridge_manager is None:
                raise RuntimeError("Agent bridge manager is unavailable.")
            owned_handle_id = clean_lobby_text(
                session.get("bridge_handle_id") or manager_health.get("bridge_handle_id"),
                limit=128,
            )
            stopped = self.bridge_manager.stop(
                room_id,
                agent_id,
                handle_id=owned_handle_id,
            )
        except Exception as error:
            message = (
                redact_persisted_diagnostic_text(error, limit=1000)
                or "Server-owned provider shutdown failed."
            )
            self._mark_stop_unconfirmed(
                room_id,
                agent_id,
                session,
                disable=disable,
                message=message,
                error_code="runtime_stop_failed",
            )
            raise RoomCommandRejected(message, code="runtime_stop_failed") from error
        if stopped.get("stopped") is not True or stopped.get("alive") is True:
            message = "Server-owned bridge handle did not confirm provider shutdown."
            self._mark_stop_unconfirmed(
                room_id,
                agent_id,
                session,
                disable=disable,
                message=message,
                error_code="runtime_stop_unconfirmed",
            )
            raise RoomCommandRejected(message, code="runtime_stop_unconfirmed")
        self._record_stop_effect(
            room_id,
            agent_id,
            operation_id=current_operation_id,
        )
        revoked_sessions = (
            self._revoke_participant_sessions(room_id, agent_id) if disable else 0
        )
        return self._finalize_stop(
            room_id,
            agent_id,
            self.store.session(room_id, agent_id),
            disable=disable,
            process={**stopped, "ownership": "server", "confirmed": True},
            revoked_sessions=revoked_sessions,
        )

    def interrupt(self, room_id: str, agent_id: str) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        if not self.broker.direct_to_bridge(room_id, agent_id, {"op": "agent.control", "action": "interrupt"}):
            raise RoomCommandRejected("Agent bridge is not connected.", code="runtime_unavailable")
        return {"agent_session": public_session(session), "interrupt_sent": True}

    def bridge_process_exited(
        self,
        room_id: str,
        session_id: str,
        returncode: int,
        stderr_tail: str = "",
    ) -> None:
        with self._lock:
            session = self.store.session(room_id, session_id)
            if not session or session.get("runtime_status") == "stopped":
                return
            key = (room_id, session_id)
            pending = _dedupe_text_list(
                [*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])]
            )
            message = f"Agent Bridge exited with return code {returncode}."
            recovery_attempt_count = int(session.get("recovery_attempt_count") or 0)
            automatic_recovery = bool(
                not self._is_closed()
                and session.get("enabled")
                and recovery_attempt_count < 1
                and key in self._launch_contexts
            )
            attention_reset = self._prepare_session_reset(
                room_id,
                session,
                pending_event_ids=pending,
                retry=automatic_recovery,
            )
            self.store.update_session_fields(
                room_id,
                session_id,
                status="unavailable" if automatic_recovery else "error",
                runtime_status="recovering" if automatic_recovery else "error",
                pid=None,
                bridge_pid=None,
                provider_session_active=False,
                active_turn_id="",
                turn_phase="",
                inflight_event_ids=[],
                **attention_reset,
                recovery_required=True,
                recovery_attempt_count=recovery_attempt_count + (1 if automatic_recovery else 0),
                last_error=message,
            stderr_tail=redact_persisted_diagnostic_text(stderr_tail, limit=16000),
            )
            participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
            if participant_id and self.store.participant(room_id, participant_id):
                self.store.update_participant_fields(room_id, participant_id, status="detached")
            self.store.append_event(
                room_id,
                "error",
                participant_id=participant_id,
                session_id=session_id,
                content=message,
                error_code="bridge_process_exited",
                stderr_tail_present=bool(redact_persisted_diagnostic_text(stderr_tail, limit=16000)),
                recovery_required=True,
                automatic_recovery_scheduled=automatic_recovery,
            )
            self._publish_session_state(room_id, self.store.session(room_id, session_id))
            if automatic_recovery:
                self._recovery_handles[key] = self._recovery_scheduler(
                    self.recovery_delay_seconds,
                    lambda: self._recover_bridge(room_id, session_id),
                )

    def close(
        self,
        provider_agents: Iterable[tuple[str, str]],
        *,
        preserve_runtimes: bool = False,
    ) -> CleanupReport:
        self._external_stop_confirmations.cancel_all()
        with self._lock:
            recovery_handles = list(self._recovery_handles.values())
            self._recovery_handles.clear()
            self._launch_contexts.clear()
        cleanup = CleanupReport("room_agent_lifecycle")
        for handle in recovery_handles:
            cancel = getattr(handle, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                    cleanup.record_success()
                except Exception as error:
                    cleanup.record_failure("recovery.cancel", error)
        if preserve_runtimes:
            return cleanup
        if self.bridge_manager is None:
            return cleanup
        for room_id, agent_id in provider_agents:
            session = self.store.session(room_id, agent_id)
            if session and session.get("process_ownership") != "server":
                continue
            owns_live_runtime = bool(
                session
                and (
                    session.get("bridge_handle_id")
                    or self.broker.has_bridge(room_id, agent_id)
                    or _bridge_manager_session_running(self.bridge_manager, room_id, agent_id)
                )
            )
            if session and owns_live_runtime:
                try:
                    self.stop(room_id, agent_id)
                    cleanup.record_success()
                except Exception as error:
                    cleanup.record_failure(
                        "agent.stop",
                        error,
                        handle_id=clean_lobby_text(session.get("bridge_handle_id"), limit=128),
                        orphaned=_bridge_manager_session_running(
                            self.bridge_manager,
                            room_id,
                            agent_id,
                        ),
                    )
        try:
            manager_cleanup = self.bridge_manager.close()
            if isinstance(manager_cleanup, CleanupReport):
                cleanup.merge(manager_cleanup)
            else:
                cleanup.record_success()
        except Exception as error:
            cleanup.record_failure("bridge_manager.close", error)
        return cleanup

    def _mark_stop_unconfirmed(
        self,
        room_id: str,
        agent_id: str,
        session: dict[str, object],
        *,
        disable: bool,
        message: str,
        error_code: str,
    ) -> dict[str, object]:
        pending = _dedupe_text_list(
            [*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])]
        )
        attention_reset = self._prepare_session_reset(
            room_id,
            session,
            pending_event_ids=pending,
            retry=False,
        )
        updated = self.store.update_session_fields(
            room_id,
            agent_id,
            status="unavailable",
            enabled=False if disable else bool(session.get("enabled")),
            runtime_status="disconnected",
            pid=None,
            bridge_pid=None,
            bridge_handle_id="",
            provider_session_active=False,
            active_turn_id="",
            turn_phase="",
            inflight_event_ids=[],
            **attention_reset,
            last_error=message,
            recovery_required=True,
            recovery_attempt_count=0,
            lifecycle_intent_action="",
            lifecycle_intent_id="",
            lifecycle_intent_status="",
        )
        participant = self.store.participant(room_id, agent_id)
        if participant:
            self.store.update_participant_fields(room_id, agent_id, status="detached")
        self.store.append_event(
            room_id,
            "error",
            participant_id=agent_id,
            session_id=agent_id,
            content=message,
            error_code=error_code,
            recovery_required=True,
        )
        self._publish_session_state(room_id, updated)
        return updated

    def _finalize_stop(
        self,
        room_id: str,
        agent_id: str,
        session: dict[str, object],
        *,
        disable: bool,
        process: dict[str, object],
        revoked_sessions: int,
    ) -> dict[str, object]:
        if self.bridge_manager is not None:
            self.bridge_manager.release_preserved_security_values(room_id, agent_id)
        pending = _dedupe_text_list(
            [*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])]
        )
        attention_reset = self._prepare_session_reset(
            room_id,
            session,
            pending_event_ids=pending,
            retry=False,
        )
        updated = self.store.update_session_fields(
            room_id,
            agent_id,
            status="detached",
            enabled=False if disable else bool(session.get("enabled")),
            runtime_status="stopped",
            pid=None,
            bridge_pid=None,
            bridge_handle_id="",
            provider_session_active=False,
            active_turn_id="",
            turn_phase="",
            inflight_event_ids=[],
            **attention_reset,
            last_error="",
            recovery_required=False,
            recovery_attempt_count=0,
            lifecycle_intent_action="",
            lifecycle_intent_id="",
            lifecycle_intent_status="",
        )
        participant = self.store.participant(room_id, agent_id)
        if participant:
            self.store.update_participant_fields(room_id, agent_id, status="detached")
        self.store.append_event(
            room_id,
            "session_detached",
            participant_id=agent_id,
            session_id=agent_id,
            reason="operator stop",
        )
        self._publish_session_state(room_id, updated)
        return {
            "agent_session": public_session(updated),
            "process": process,
            "revoked_sessions": revoked_sessions,
        }

    def _record_stop_effect(
        self,
        room_id: str,
        agent_id: str,
        *,
        operation_id: str,
    ) -> None:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        if (
            clean_lobby_text(session.get("lifecycle_intent_action"), limit=32) != "stop"
            or clean_lobby_text(session.get("lifecycle_intent_id"), limit=128) != operation_id
        ):
            raise RoomCommandRejected(
                "The provider stop confirmation does not match the active lifecycle operation.",
                code="stale_stop_confirmation",
            )
        self.store.update_session_fields(
            room_id,
            agent_id,
            lifecycle_intent_status="effect_applied",
        )

    def _recover_bridge(self, room_id: str, session_id: str) -> None:
        key = (room_id, session_id)
        with self._lock:
            self._recovery_handles.pop(key, None)
            session = self.store.session(room_id, session_id)
            launch_context = self._launch_contexts.get(key)
            if (
                self._is_closed()
                or not session
                or not session.get("enabled")
                or session.get("runtime_status") != "recovering"
                or launch_context is None
            ):
                return
            server_url, ticket_issuer = launch_context
            try:
                self.start(
                    room_id,
                    session_id,
                    server_url=server_url,
                    ticket_issuer=ticket_issuer,
                    automatic_recovery=True,
                )
            except Exception as error:
                current = self.store.session(room_id, session_id)
                if current:
                    updated = self.store.update_session_fields(
                        room_id,
                        session_id,
                        status="error",
                        runtime_status="error",
                        recovery_required=True,
                        last_error=(
                            redact_persisted_diagnostic_text(error, limit=4000)
                            or "Automatic recovery failed."
                        ),
                    )
                    self._publish_session_state(room_id, updated)


def schedule_daemon_timer(delay_seconds: float, callback: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(max(0.0, float(delay_seconds)), callback)
    timer.daemon = True
    timer.start()
    return timer


def _dedupe_text_list(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = clean_lobby_text(value, limit=128)
        if text and text not in result:
            result.append(text)
    return result


def _bridge_manager_session_running(
    manager: AgentBridgeManager,
    room_id: str,
    session_id: str,
) -> bool:
    health = getattr(manager, "health", None)
    if not callable(health):
        return True
    try:
        return bool(health(room_id, session_id).get("running", True))
    except Exception:
        return True


def _lifecycle_operation_id(value: object) -> str:
    return clean_lobby_text(value, limit=128) or uuid4().hex


__all__ = [
    "AgentBridgeManager",
    "EnsureProviderSession",
    "PendingAssignment",
    "PrepareSessionReset",
    "ProviderLookup",
    "RecoveryScheduler",
    "RoomAgentLifecycle",
    "SessionCallback",
    "SessionRevoker",
    "schedule_daemon_timer",
]
