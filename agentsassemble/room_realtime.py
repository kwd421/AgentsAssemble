from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import dataclass, replace
import hashlib
import json
import threading
import shutil
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from agentsassemble.agent_sessions import build_room_turn_packet
from agentsassemble.meeting_events import clean_lobby_text, has_room_visible_text
from agentsassemble.native_cli_providers import (
    NativeCliProviderSpec,
    UnsupportedNativeCliProvider,
    default_native_cli_provider_specs,
    native_cli_provider_definition,
    native_cli_provider_spec_from_payload,
    validate_native_cli_provider_spec,
)
from agentsassemble.provider_capabilities import provider_catalog_payload
from agentsassemble.identity_store import identity_store_for_output_root
from agentsassemble.room_invite import revoke_room_access, revoke_sessions_for_participant
from agentsassemble.room_commands import (
    RoomCommandValidationError,
    capabilities_for_identity,
    parse_room_command,
)
from agentsassemble.room_event_broker import ROOM_EVENT_STREAM, RoomEventBroker, RoomSocketChannel
from agentsassemble.room_members import is_room_member_muted, remove_room_member, set_room_member_muted
from agentsassemble.room_routing import route_message_targets
from agentsassemble.room_settings import room_settings_payload
from agentsassemble.room_store import RoomStore
from agentsassemble.room_types import RoomCommand, RoomEvent, TurnAssignment
from agentsassemble.voice_presence import leave_all_voice

ROOM_SNAPSHOT_EVENT_LIMIT = 200
ROOM_HISTORY_MAX_LIMIT = 200


class AgentBridgeManager(Protocol):
    def start(
        self,
        room_id: str,
        session: dict[str, object],
        spec: NativeCliProviderSpec,
        *,
        server_url: str = "",
        ticket_issuer: Callable[[dict[str, object]], str] | None = None,
    ) -> dict[str, object]: ...

    def stop(
        self,
        room_id: str,
        session_id: str,
        *,
        timeout_seconds: float = 2.0,
        handle_id: str = "",
    ) -> dict[str, object]: ...

    def close(self) -> None: ...


RecoveryScheduler = Callable[[float, Callable[[], None]], object]


@dataclass(frozen=True)
class _PendingEventPartition:
    inflight: list[str]
    deferred: list[str]
    already_synced: list[str]
    invalid: list[str]


class RoomCommandRejected(ValueError):
    def __init__(self, message: str, *, code: str = "rejected") -> None:
        super().__init__(message)
        self.code = code


class RoomRealtimeController:
    """Canonical room command, event, session, and provider-turn coordinator."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        providers: list[NativeCliProviderSpec] | None = None,
        bridge_manager: AgentBridgeManager | None = None,
        broker: RoomEventBroker | None = None,
        default_room_id: str = "general",
        max_agent_relay_depth: int = 2,
        recovery_delay_seconds: float = 1.0,
        recovery_scheduler: RecoveryScheduler | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.store = RoomStore(self.output_root)
        self.broker = broker or RoomEventBroker()
        self.bridge_manager = bridge_manager
        self.default_room_id = clean_lobby_text(default_room_id, limit=128) or "general"
        self.max_agent_relay_depth = max(0, int(max_agent_relay_depth))
        self.recovery_delay_seconds = max(0.0, float(recovery_delay_seconds))
        self._recovery_scheduler = recovery_scheduler or _schedule_daemon_timer
        default_providers = {
            clean_lobby_text(spec.agent_id, limit=128): spec
            for spec in list(providers or [])
            if clean_lobby_text(spec.agent_id, limit=128)
        }
        self._providers_by_room: dict[str, dict[str, NativeCliProviderSpec]] = {
            self.default_room_id: default_providers,
        }
        self._lock = threading.RLock()
        self._event_listener_removers: dict[str, Callable[[], None]] = {}
        self._launch_contexts: dict[
            tuple[str, str],
            tuple[str, Callable[[dict[str, object]], str] | None],
        ] = {}
        self._recovery_handles: dict[tuple[str, str], object] = {}
        self._closed = False
        self.ensure_room(self.default_room_id)
        for spec in default_providers.values():
            self._ensure_provider_session(self.default_room_id, spec)
        self._restore_server_owned_providers()
        self._reconcile_startup_sessions()

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
                if session.get("process_ownership") != "server":
                    continue
                definition = native_cli_provider_definition(session.get("provider_kind"))
                if definition is None:
                    continue
                spec = definition.make_spec(
                    agent_id=agent_id,
                    display_name=clean_lobby_text(session.get("display_name"), limit=128) or agent_id,
                    cwd=clean_lobby_text(session.get("workspace"), limit=500) or ".",
                    model=clean_lobby_text(session.get("model"), limit=128),
                    reasoning_effort=clean_lobby_text(session.get("reasoning_effort"), limit=32),
                    service_tier=clean_lobby_text(session.get("service_tier"), limit=32),
                    variant=clean_lobby_text(session.get("variant"), limit=64),
                    permission_mode=clean_lobby_text(session.get("permission_mode"), limit=64),
                )
                with self._lock:
                    self._providers_by_room.setdefault(room_id, {})[agent_id] = spec

    def register_provider(self, room_id: str, spec: NativeCliProviderSpec) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        validate_native_cli_provider_spec(spec)
        self.ensure_room(clean_room_id)
        with self._lock:
            previous_session = self.store.session(clean_room_id, spec.agent_id)
            previous_participant = self.store.participant(clean_room_id, spec.agent_id)
            requested_profile_key = spec.runtime_profile_key()
            if (
                previous_session.get("runtime_status") in {"starting", "idle", "busy", "paused", "recovering"}
                and previous_session.get("runtime_profile_key") != requested_profile_key
            ):
                raise RoomCommandRejected(
                    "This Agent Session is running with a different runtime profile; stop it before changing settings.",
                    code="runtime_profile_conflict",
                )
            providers = self._providers_by_room.setdefault(clean_room_id, {})
            providers[clean_lobby_text(spec.agent_id, limit=128)] = spec
            self._ensure_provider_session(clean_room_id, spec)
            if previous_participant.get("status") == "kicked":
                self.store.update_participant_fields(clean_room_id, spec.agent_id, status="detached")
            current = self.store.session(clean_room_id, spec.agent_id)
            if not previous_session or previous_participant.get("status") == "kicked":
                self.store.append_event(
                    clean_room_id,
                    "participant_joined",
                    participant_id=spec.agent_id,
                    session_id=spec.agent_id,
                )
                self.store.append_event(
                    clean_room_id,
                    "agent_session_created",
                    participant_id=spec.agent_id,
                    session_id=spec.agent_id,
                    provider_kind=spec.normalized_provider_kind(),
                )
            self._publish_session_state(clean_room_id, current)
        return self._public_session(current)

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
                    pending_event_ids=pending,
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
        spec = NativeCliProviderSpec(
            agent_id=participant_id,
            display_name=display_name,
            command=("external-attendee",),
            cwd=".",
            provider_kind=provider_kind,
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
                "status": "joined",
            },
        )
        self.store.upsert_session(
            room_id,
            {
                "session_id": participant_id,
                "participant_id": participant_id,
                "display_name": display_name,
                "status": "available",
                "provider_kind": provider_kind,
                "runtime_kind": "external_bridge",
                "connection_kind": "native_cli_bridge",
                "runtime_profile_key": spec.runtime_profile_key(),
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
        self.store.append_event(
            room_id,
            "participant_joined",
            participant_id=participant_id,
            session_id=participant_id,
        )
        self.store.append_event(
            room_id,
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
        if not session or session.get("runtime_status") == "stopped":
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
        sessions = [self._public_session(session) for session in stored_sessions]
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
        return {
            "op": "snapshot",
            "stream": ROOM_EVENT_STREAM,
            "room": self.store.room(room_id),
            "participants": (
                [
                    participant
                    for participant in self.store.participants(room_id)
                    if participant.get("participant_id") == identity.get("agent_id")
                ]
                if bridge
                else self.store.participants(room_id)
            ),
            "agent_sessions": sessions,
            "active_turns": active_turns,
            "events": events,
            "oldest_seq": oldest_seq,
            "last_seq": latest_seq,
            "has_more_before": has_more_before,
            "resume_gap": resume_gap,
            "snapshot_mode": snapshot_mode,
            "available_providers": [] if bridge else provider_catalog_payload(),
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

        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_agent_id = clean_lobby_text(agent_id, limit=128)
        self.ensure_room(clean_room_id)
        with self._lock:
            self._provider(clean_room_id, clean_agent_id)
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
                raise RoomCommandRejected("A public room message is required to assign a turn.", code="no_room_message")
            source_seq = int(source.get("seq") or 0)
            last_sync_seq = int(session.get("last_provider_sync_seq") or 0)
            if session.get("bootstrap_done") and source_seq <= last_sync_seq:
                raise RoomCommandRejected(
                    "The Agent Session has no unseen public room message to answer.",
                    code="no_new_room_message",
                )
            self._queue_event(clean_room_id, clean_agent_id, source, relay_depth=0)
            current = self.store.session(clean_room_id, clean_agent_id)
            return {
                "source_event_id": source.get("id"),
                "source_event_seq": source_seq,
                "queued": bool(source.get("id")),
                "assigned": current.get("active_source_event_id") == source.get("id"),
                "agent_session": self._public_session(current),
            }

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
        self.ensure_room(room_id)
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
        with self._lock:
            principal_id = _command_principal(identity)
            payload_hash = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            prior = self.store.command_record(room_id, principal_id, request_id)
            if prior:
                if prior.get("action") != action or prior.get("payload_hash") != payload_hash:
                    raise RoomCommandRejected(
                        "request_id was already used for a different command.",
                        code="idempotency_conflict",
                    )
                return {**dict(prior.get("result") or {}), "deduplicated": True}
            result = self._execute_action(
                identity,
                room_id,
                action,
                payload,
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
            if action == "room.delete":
                return ack
            return self.store.record_command_result(
                room_id,
                request_id,
                ack,
                principal_id=principal_id,
                action=action,
                payload_hash=payload_hash,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            removers = list(self._event_listener_removers.values())
            self._event_listener_removers.clear()
            recovery_handles = list(self._recovery_handles.values())
            self._recovery_handles.clear()
            self._launch_contexts.clear()
        for handle in recovery_handles:
            cancel = getattr(handle, "cancel", None)
            if callable(cancel):
                cancel()
        for remove in removers:
            remove()
        if self.bridge_manager is not None:
            for room_id, providers in list(self._providers_by_room.items()):
                for agent_id in list(providers):
                    session = self.store.session(room_id, agent_id)
                    if session and session.get("runtime_status") not in {"stopped", "available"}:
                        try:
                            self._stop_agent(room_id, agent_id)
                        except Exception:
                            continue
            self.bridge_manager.close()
        self.broker.close()

    def bridge_process_exited(
        self,
        room_id: str,
        session_id: str,
        returncode: int,
        stderr_tail: str = "",
    ) -> None:
        """Preserve crash evidence and schedule one bounded in-process recovery."""
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
                not self._closed
                and session.get("enabled")
                and recovery_attempt_count < 1
                and key in self._launch_contexts
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
                pending_event_ids=pending,
                recovery_required=True,
                recovery_attempt_count=recovery_attempt_count + (1 if automatic_recovery else 0),
                last_error=message,
                stderr_tail=clean_lobby_text(stderr_tail, limit=16000),
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
                stderr_tail_present=bool(clean_lobby_text(stderr_tail, limit=16000)),
                recovery_required=True,
                automatic_recovery_scheduled=automatic_recovery,
            )
            self._publish_session_state(room_id, self.store.session(room_id, session_id))
            if automatic_recovery:
                handle = self._recovery_scheduler(
                    self.recovery_delay_seconds,
                    lambda: self._recover_bridge(room_id, session_id),
                )
                self._recovery_handles[key] = handle

    def _execute_action(
        self,
        identity: dict[str, object],
        room_id: str,
        action: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        if action == "message.send":
            self._require_capability(identity, "message.send")
            return self._send_message(identity, room_id, payload)
        if action == "participant.leave":
            self._require_capability(identity, "participant.leave")
            return self._leave_participant(identity, room_id)
        if action == "room.delete":
            self._require_capability(identity, "room.delete")
            return self._delete_room(identity, room_id, payload)
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
                return self._start_agent(room_id, agent_id, server_url=server_url, ticket_issuer=ticket_issuer)
            if action == "agent.pause":
                return self._pause_agent(room_id, agent_id)
            if action == "agent.resume":
                return self._resume_agent(room_id, agent_id, server_url=server_url, ticket_issuer=ticket_issuer)
            if action == "agent.stop":
                return self._stop_agent(room_id, agent_id)
            return self._interrupt_agent(room_id, agent_id)
        if action == "participant.kick":
            self._require_capability(identity, "participant.kick")
            return self._kick_participant(room_id, self._payload_agent_id(payload))
        if action == "participant.mute":
            self._require_capability(identity, "participant.mute")
            return self._mute_participant(room_id, self._payload_agent_id(payload), bool(payload.get("muted", True)))
        self._require_bridge(identity)
        if action == "bridge.ready":
            return self._bridge_ready(identity, room_id, payload)
        if action == "bridge.health":
            return self._bridge_health(identity, room_id, payload)
        if action == "turn.state":
            return self._turn_state(identity, room_id, payload)
        if action == "activity.update":
            return self._activity_update(identity, room_id, payload)
        if action == "message.delta":
            return self._message_delta(identity, room_id, payload)
        if action == "message.final":
            return self._message_final(identity, room_id, payload)
        if action == "turn.failed":
            return self._turn_failed(identity, room_id, payload)
        raise RoomCommandRejected(f"Unsupported room command: {action}", code="unknown_action")

    def _send_message(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        content = _room_message_text(payload.get("content") or payload.get("message"), limit=12000)
        kind = clean_lobby_text(payload.get("kind"), limit=64) or "message"
        if kind not in {"vote", "vote_cast"} and not content:
            raise RoomCommandRejected("Message content is required.", code="empty")
        participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        participant = self.store.participant(room_id, participant_id)
        if participant.get("status") in {"kicked", "left"}:
            raise RoomCommandRejected("This participant is no longer in the room.", code="session_revoked")
        if participant.get("muted") or is_room_member_muted(self.output_root, room_id, participant_id):
            raise RoomCommandRejected("You are muted by the room host.", code="muted")
        event = self.store.append_event(
            room_id,
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

    def _start_agent(
        self,
        room_id: str,
        agent_id: str,
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
        automatic_recovery: bool = False,
    ) -> dict[str, object]:
        spec = self._provider(room_id, agent_id)
        session = self.store.session(room_id, agent_id)
        participant = self.store.participant(room_id, agent_id)
        if participant.get("status") == "kicked":
            raise RoomCommandRejected("This agent was removed from the room. Add it again before starting it.", code="participant_kicked")
        if not session:
            self._ensure_provider_session(room_id, spec)
            session = self.store.session(room_id, agent_id)
        if session.get("runtime_status") in {"starting", "idle", "busy", "paused"}:
            return {"agent_session": self._public_session(session), "runtime_reused": True}
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
        )
        if self.bridge_manager is None:
            self.store.update_session_fields(
                room_id,
                agent_id,
                status="unavailable",
                enabled=False,
                runtime_status="error",
                last_error="Agent bridge manager is unavailable.",
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
            self.store.update_session_fields(
                room_id,
                agent_id,
                status="unavailable",
                enabled=False,
                runtime_status="error",
                last_error=str(error),
            )
            self.store.append_event(
                room_id,
                "error",
                participant_id=agent_id,
                session_id=agent_id,
                content=str(error),
                error_code="runtime_start_failed",
            )
            raise RoomCommandRejected(str(error), code="runtime_start_failed") from error
        updated = self.store.update_session_fields(
            room_id,
            agent_id,
            bridge_pid=launch.get("bridge_pid"),
            bridge_handle_id=launch.get("bridge_handle_id") or "",
            process_ownership="server",
            resolved_executable=launch.get("resolved_executable") or "",
        )
        self._publish_session_state(room_id, updated)
        return {
            "agent_session": self._public_session(updated),
            "launch": {
                "runtime_reused": bool(launch.get("runtime_reused")),
                "runtime_profile_key": launch.get("runtime_profile_key") or "",
            },
            "runtime_reused": False,
        }

    def _create_agent(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        try:
            spec = native_cli_provider_spec_from_payload(payload)
        except UnsupportedNativeCliProvider as error:
            raise RoomCommandRejected(str(error), code="unsupported_provider") from error
        session = self.register_provider(room_id, spec)
        result: dict[str, object] = {
            "status": "created",
            "agent_session": session,
            "participant": self.store.participant(room_id, spec.agent_id),
        }
        if bool(payload.get("start") or payload.get("start_now")):
            result["start"] = self._start_agent(
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
        definition = native_cli_provider_definition(current.get("provider_kind"))
        if definition is None:
            raise RoomCommandRejected(
                "This Agent Session provider is no longer available.",
                code="unsupported_provider",
            )
        spec = definition.make_spec(
            agent_id=agent_id,
            display_name=clean_lobby_text(current.get("display_name"), limit=128) or agent_id,
            cwd=clean_lobby_text(current.get("workspace"), limit=500) or ".",
            model=clean_lobby_text(current.get("model"), limit=128),
            reasoning_effort=clean_lobby_text(current.get("reasoning_effort"), limit=32),
            service_tier=clean_lobby_text(current.get("service_tier"), limit=32),
            variant=clean_lobby_text(current.get("variant"), limit=64),
            permission_mode=clean_lobby_text(current.get("permission_mode"), limit=64),
        )
        session = self.register_provider(room_id, spec)
        result: dict[str, object] = {
            "status": "readded",
            "agent_session": session,
            "participant": self.store.participant(room_id, agent_id),
        }
        if bool(payload.get("start") or payload.get("start_now")):
            result["start"] = self._start_agent(
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
        runtime_keys = {
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
        if not any(key in payload for key in runtime_keys):
            display_name = clean_lobby_text(
                payload.get("display_name") or current.get("display_name") or agent_id,
                limit=80,
            )
            avatar_image_url = clean_lobby_text(payload.get("avatar_image_url"), limit=4096)
            participant = self.store.participant(room_id, agent_id)
            if not participant:
                raise RoomCommandRejected(f"Participant {agent_id} was not found.", code="not_found")
            updated_participant = self.store.update_participant_fields(
                room_id,
                agent_id,
                display_name=display_name,
                avatar_image_url=avatar_image_url,
            )
            updated_session = self.store.update_session_fields(
                room_id,
                agent_id,
                display_name=display_name,
                avatar_image_url=avatar_image_url,
            )
            with self._lock:
                current_spec = self._providers_by_room.get(room_id, {}).get(agent_id)
                if current_spec is not None:
                    self._providers_by_room[room_id][agent_id] = replace(current_spec, display_name=display_name)
            self.store.append_event(
                room_id,
                "participant_updated",
                participant_id=agent_id,
                display_name=display_name,
                avatar_image_url=avatar_image_url,
            )
            self._publish_session_state(room_id, updated_session)
            return {
                "status": "profile_updated",
                "agent_session": self._public_session(updated_session),
                "participant": updated_participant,
            }
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
            "workspace": payload.get("workspace") or current.get("workspace") or ".",
        }
        try:
            spec = native_cli_provider_spec_from_payload(merged)
        except (UnsupportedNativeCliProvider, ValueError) as error:
            raise RoomCommandRejected(str(error), code="invalid_runtime_profile") from error
        session = self.register_provider(room_id, spec)
        return {"status": "configured", "agent_session": session}

    def _resume_agent(
        self,
        room_id: str,
        agent_id: str,
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if session and session.get("runtime_status") == "paused":
            if not self.broker.has_bridge(room_id, agent_id):
                raise RoomCommandRejected(
                    "The paused Agent Session bridge is no longer connected.",
                    code="runtime_unavailable",
                )
            updated = self.store.update_session_fields(
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
                "agent_session": self._public_session(current),
                "runtime_reused": True,
                "process_reused": True,
            }
        if session and session.get("runtime_status") in {"starting", "idle", "busy"}:
            return {"agent_session": self._public_session(session), "runtime_reused": True}
        if session and session.get("runtime_status") not in {"stopped", "available"}:
            self._stop_agent(room_id, agent_id, disable=False)
        return self._start_agent(room_id, agent_id, server_url=server_url, ticket_issuer=ticket_issuer)

    def _pause_agent(self, room_id: str, agent_id: str) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        runtime_status = clean_lobby_text(session.get("runtime_status"), limit=32)
        if runtime_status == "paused":
            return {
                "agent_session": self._public_session(session),
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
            "agent_session": self._public_session(updated),
            "runtime_reused": True,
            "process_preserved": True,
        }

    def _stop_agent(self, room_id: str, agent_id: str, *, disable: bool = True) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        key = (room_id, agent_id)
        recovery_handle = self._recovery_handles.pop(key, None)
        cancel = getattr(recovery_handle, "cancel", None)
        if callable(cancel):
            cancel()
        if disable:
            self._launch_contexts.pop(key, None)
        self.store.update_session_fields(room_id, agent_id, runtime_status="stopping")
        self.broker.direct_to_bridge(room_id, agent_id, {"op": "agent.control", "action": "stop"})
        ownership = clean_lobby_text(session.get("process_ownership"), limit=32) or (
            "external" if session.get("external_owned") else "server"
        )
        stopped = {"stopped": ownership == "external", "alive": False, "ownership": ownership}
        if self.bridge_manager is not None and ownership == "server":
            stopped = self.bridge_manager.stop(
                room_id,
                agent_id,
                handle_id=clean_lobby_text(session.get("bridge_handle_id"), limit=128),
            )
        pending = _dedupe_text_list([*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])])
        handle_lost = ownership == "server" and not bool(stopped.get("stopped"))
        updated = self.store.update_session_fields(
            room_id,
            agent_id,
            status="detached",
            enabled=False if disable else bool(session.get("enabled")),
            runtime_status="disconnected" if handle_lost else "stopped",
            pid=None,
            bridge_pid=None,
            bridge_handle_id="",
            provider_session_active=False,
            active_turn_id="",
            turn_phase="",
            inflight_event_ids=[],
            pending_event_ids=pending,
            last_error="Server-owned bridge handle was lost; no PID fallback was attempted." if handle_lost else "",
            recovery_required=handle_lost,
            recovery_attempt_count=0,
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
        return {"agent_session": self._public_session(updated), "process": stopped}

    def _recover_bridge(self, room_id: str, session_id: str) -> None:
        key = (room_id, session_id)
        with self._lock:
            self._recovery_handles.pop(key, None)
            session = self.store.session(room_id, session_id)
            launch_context = self._launch_contexts.get(key)
            if (
                self._closed
                or not session
                or not session.get("enabled")
                or session.get("runtime_status") != "recovering"
                or launch_context is None
            ):
                return
            server_url, ticket_issuer = launch_context
            try:
                self._start_agent(
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
                        last_error=clean_lobby_text(error, limit=4000) or "Automatic recovery failed.",
                    )
                    self._publish_session_state(room_id, updated)

    def _interrupt_agent(self, room_id: str, agent_id: str) -> dict[str, object]:
        session = self.store.session(room_id, agent_id)
        if not session:
            raise RoomCommandRejected(f"Agent session {agent_id} was not found.", code="not_found")
        if not self.broker.direct_to_bridge(room_id, agent_id, {"op": "agent.control", "action": "interrupt"}):
            raise RoomCommandRejected("Agent bridge is not connected.", code="runtime_unavailable")
        return {"agent_session": self._public_session(session), "interrupt_sent": True}

    def _kick_participant(self, room_id: str, participant_id: str) -> dict[str, object]:
        if participant_id == "operator-local":
            raise RoomCommandRejected("The room host cannot be removed.", code="permission_denied")
        participant = self.store.participant(room_id, participant_id)
        if not participant:
            raise RoomCommandRejected(f"Participant {participant_id} was not found.", code="not_found")
        if participant.get("role") == "agent":
            self._stop_agent(room_id, participant_id)
            self._providers_by_room.get(room_id, {}).pop(participant_id, None)
        revoked_sessions = revoke_sessions_for_participant(room_id, participant_id)
        removed_member = remove_room_member(self.output_root, room_id, participant_id)
        leave_all_voice(room_id, participant_id)
        updated = self.store.update_participant_fields(room_id, participant_id, status="kicked")
        self.store.append_event(room_id, "participant_kicked", participant_id=participant_id)
        return {
            "participant": updated,
            "revoked_sessions": revoked_sessions,
            "removed_member": bool(removed_member),
        }

    def _mute_participant(self, room_id: str, participant_id: str, muted: bool) -> dict[str, object]:
        participant = self.store.participant(room_id, participant_id)
        if not participant:
            raise RoomCommandRejected(f"Participant {participant_id} was not found.", code="not_found")
        member = set_room_member_muted(self.output_root, meeting_id=room_id, participant_id=participant_id, muted=muted)
        updated = self.store.update_participant_fields(room_id, participant_id, muted=muted)
        session = self.store.session(room_id, participant_id)
        if participant.get("role") == "agent" and session:
            if muted and session.get("runtime_status") == "busy":
                self.broker.direct_to_bridge(room_id, participant_id, {"op": "agent.control", "action": "interrupt"})
            elif not muted:
                self._assign_pending(room_id, participant_id)
        self.store.append_event(
            room_id,
            "participant_muted",
            participant_id=participant_id,
            muted=muted,
        )
        return {"participant": updated, "member": member}

    def _leave_participant(self, identity: dict[str, object], room_id: str) -> dict[str, object]:
        participant_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        participant = self.store.participant(room_id, participant_id)
        if not participant:
            raise RoomCommandRejected("Participant was not found in this room.", code="not_found")
        if self._is_room_owner(identity, room_id):
            raise RoomCommandRejected(
                "The room owner must transfer ownership or delete the server.",
                code="owner_must_transfer_or_delete",
            )
        updated = self.store.update_participant_fields(room_id, participant_id, status="left")
        identity_store_for_output_root(self.output_root).remove_membership(room_id, participant_id)
        leave_all_voice(room_id, participant_id)
        event = self.store.append_event(room_id, "participant_left", participant_id=participant_id)
        timer = threading.Timer(
            0.1,
            revoke_sessions_for_participant,
            args=(room_id, participant_id),
        )
        timer.daemon = True
        timer.start()
        return {"participant": updated, "event": event, "revocation_scheduled": True}

    def _delete_room(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
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
        for session in list(self.store.sessions(room_id)):
            session_id = clean_lobby_text(session.get("session_id"), limit=128)
            if not session_id:
                continue
            try:
                self._stop_agent(room_id, session_id)
            except (RoomCommandRejected, ValueError):
                continue
        self.broker.broadcast_control(
            room_id,
            {"op": "room_deleted", "room_id": room_id, "room_name": room_name},
        )
        revoked = revoke_room_access(room_id)
        identity_store.delete_room(room_id)
        remove_listener = self._event_listener_removers.pop(room_id, None)
        if remove_listener is not None:
            remove_listener()
        self.store.delete_room(room_id, reason="owner deleted server")
        self._providers_by_room.pop(room_id, None)
        for path in (self.output_root / "rooms" / room_id, self.output_root / "meetings" / room_id):
            if path.exists() and path.is_dir():
                shutil.rmtree(path)
        threading.Timer(0.1, lambda: self.broker.disconnect_room(room_id)).start()
        return {"room_id": room_id, "deleted": True, **revoked}

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
        agent_id, session = self._bridge_session(identity, room_id, allow_unleased=True)
        connection_id = clean_lobby_text(identity.get("connection_id"), limit=128)
        channel = self.broker.channel(connection_id)
        if channel is None:
            raise RoomCommandRejected("Agent bridge connection is no longer active.", code="bridge_disconnected")
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
            pty=bool(payload.get("pty", True)),
            transport=clean_lobby_text(payload.get("transport"), limit=64) or "pty",
            is_one_shot=bool(payload.get("is_one_shot", False)),
            model=clean_lobby_text(payload.get("model"), limit=128) or session.get("model") or "",
            started_at=clean_lobby_text(payload.get("started_at"), limit=128) or _now(),
            last_error="",
            **_runtime_profile_fields(payload, include_model=False),
            **_runtime_diagnostic_fields(payload),
        )
        if previous_participant.get("status") != "joined":
            self.store.append_event(room_id, "participant_joined", participant_id=agent_id, session_id=session["session_id"])
        self.store.append_event(room_id, "session_attached", participant_id=agent_id, session_id=session["session_id"])
        self._assign_pending(room_id, agent_id)
        current = self.store.session(room_id, str(session["session_id"]))
        self._publish_session_state(room_id, current)
        return {"agent_session": self._public_session(current)}

    def _bridge_health(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        _agent_id, session = self._bridge_session(identity, room_id)
        fields: dict[str, object] = {
            key: payload[key]
            for key in ("running", "resolved_executable", "started_at", "last_error", "returncode")
            if key in payload
        }
        if "pid" in payload:
            fields["reported_provider_pid"] = _safe_int_or_none(payload.get("pid"))
        fields.update(_runtime_diagnostic_fields(payload))
        fields.update(_runtime_profile_fields(payload))
        updated = self.store.update_session_fields(room_id, str(session["session_id"]), **fields)
        self._publish_session_state(room_id, updated)
        return {"agent_session": self._public_session(updated)}

    def _turn_state(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        phase = clean_lobby_text(payload.get("phase"), limit=32) or "thinking"
        latency = _merged_latency(session.get("latency"), payload.get("latency"))
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
        return {"event": event, "agent_session": self._public_session(updated)}

    def _message_delta(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        content = _message_delta_text(payload.get("content"), limit=12000)
        if not has_room_visible_text(content):
            raise RoomCommandRejected("Delta content is required.", code="empty")
        if session.get("turn_phase") != "streaming":
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

    def _activity_update(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        category = clean_lobby_text(payload.get("category"), limit=32)
        status = clean_lobby_text(payload.get("status"), limit=32)
        content, activity_kind = _public_activity(category, status)
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
            category=category if category in _PUBLIC_ACTIVITY_LABELS else "tool",
            status=status if status in {"started", "running", "completed"} else "running",
            content=content,
        )
        return {"event": event, "event_seq": event["seq"]}

    def _message_final(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        content = _room_message_text(payload.get("content"), limit=12000)
        active_turn_id = str(session["active_turn_id"])
        input_up_to_event_id = clean_lobby_text(session.get("input_up_to_event_id"), limit=128)
        input_up_to_seq = _safe_bounded_int(session.get("input_up_to_seq"), default=0, minimum=0)
        relay_depth = int(session.get("active_relay_depth") or 0)
        latency = _merged_latency(session.get("latency"), payload.get("latency"))
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        if not has_room_visible_text(content):
            finished, current = self._complete_active_turn(
                room_id,
                session,
                input_up_to_event_id=input_up_to_event_id,
                input_up_to_seq=input_up_to_seq,
                latency=latency,
                diagnostics=diagnostics,
                finish_status="silent",
            )
            return {
                "silent": True,
                "turn_finished": finished,
                "agent_session": self._public_session(current),
            }
        event = self.store.append_event(
            room_id,
            "message_final",
            participant_id=agent_id,
            participant_type="agent",
            actor_id=agent_id,
            actor_type="agent",
            display_name=session.get("display_name") or agent_id,
            avatar_image_url=session.get("avatar_image_url") or "",
            session_id=session["session_id"],
            turn_id=active_turn_id,
            content=content,
            source_event_id=session.get("active_source_event_id"),
            relay_depth=relay_depth,
            message_source=payload.get("message_source"),
        )
        finished, current = self._complete_active_turn(
            room_id,
            session,
            input_up_to_event_id=input_up_to_event_id,
            input_up_to_seq=input_up_to_seq,
            latency=latency,
            diagnostics=diagnostics,
            finish_status="completed",
            last_spoke_event_id=event["id"],
        )
        return {"event": event, "turn_finished": finished, "agent_session": self._public_session(current)}

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
        active_turn_id = str(session["active_turn_id"])
        finished = self.store.append_event(
            room_id,
            "turn_finished",
            participant_id=session["participant_id"],
            session_id=session["session_id"],
            turn_id=active_turn_id,
            status=finish_status,
            latency=latency,
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
            "last_provider_sync_event_id": input_up_to_event_id or session.get("last_provider_sync_event_id") or "",
            "last_provider_sync_seq": input_up_to_seq or session.get("last_provider_sync_seq") or 0,
            "last_seen_event_id": input_up_to_event_id or session.get("last_seen_event_id") or "",
            "last_seen_seq": input_up_to_seq or session.get("last_seen_seq") or 0,
            "bootstrap_done": True,
            "recovery_required": False,
            "recovery_attempt_count": 0,
            "turn_count": int(session.get("turn_count") or 0) + 1,
            "latency": latency,
            "last_error": "",
            **_runtime_diagnostic_fields(diagnostics),
        }
        if last_spoke_event_id:
            updates["last_spoke_event_id"] = last_spoke_event_id
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            **updates,
        )
        self._assign_pending(room_id, str(session["participant_id"]))
        current = self.store.session(room_id, str(session["session_id"]))
        self._publish_session_state(room_id, current)
        return finished, current

    def _turn_failed(self, identity: dict[str, object], room_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent_id, session = self._active_bridge_turn(identity, room_id, payload)
        interrupted = clean_lobby_text(payload.get("status"), limit=32) == "interrupted"
        content = clean_lobby_text(payload.get("message") or payload.get("content"), limit=4000) or "Provider turn failed."
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        error = self.store.append_event(
            room_id,
            "error",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            content=content,
            error_code="interrupted" if interrupted else "provider_turn_failed",
            diagnostics=_public_runtime_diagnostics(diagnostics),
        )
        self.store.append_event(
            room_id,
            "turn_finished",
            participant_id=agent_id,
            session_id=session["session_id"],
            turn_id=session["active_turn_id"],
            status="interrupted" if interrupted else "error",
        )
        pending = _dedupe_text_list([*list(session.get("inflight_event_ids") or []), *list(session.get("pending_event_ids") or [])])
        recovery_attempt_count = int(session.get("recovery_attempt_count") or 0)
        automatic_recovery = bool(
            not interrupted
            and recovery_attempt_count < 1
            and session.get("enabled")
            and self.broker.has_bridge(room_id, agent_id)
            and _provider_process_exited(content, diagnostics)
        )
        updated = self.store.update_session_fields(
            room_id,
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
            recovery_required=not interrupted,
            recovery_attempt_count=recovery_attempt_count + (1 if automatic_recovery else 0),
            last_error=content,
            **_runtime_diagnostic_fields(diagnostics),
        )
        self._publish_session_state(room_id, updated)
        if automatic_recovery:
            key = (room_id, str(session["session_id"]))
            handle = self._recovery_scheduler(
                self.recovery_delay_seconds,
                lambda: self._retry_pending_turn(room_id, str(session["session_id"])),
            )
            self._recovery_handles[key] = handle
        return {"event": error, "agent_session": self._public_session(updated)}

    def _retry_pending_turn(self, room_id: str, session_id: str) -> None:
        key = (room_id, session_id)
        with self._lock:
            self._recovery_handles.pop(key, None)
            session = self.store.session(room_id, session_id)
            if (
                self._closed
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
            if not self._assign_pending(room_id, participant_id):
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

    def _on_event_appended(self, event: RoomEvent | dict[str, object]) -> None:
        self.broker.broadcast_event(_public_event(event))
        if event.get("type") != "message_final":
            return
        with self._lock:
            self._route_message_event(event)

    def _route_message_event(self, event: RoomEvent | dict[str, object]) -> None:
        room_id = clean_lobby_text(event.get("room_id"), limit=128)
        providers = self._room_providers(room_id)
        room_settings = room_settings_payload(self.output_root, room_id=room_id).get("settings")
        settings = room_settings if isinstance(room_settings, dict) else {}
        continuous = settings.get("conversation_mode") == "continuous"
        max_relay_turns = int(settings.get("max_relay_turns") or self.max_agent_relay_depth)
        decision = route_message_targets(
            dict(event),
            providers,
            max_agent_relay_depth=max_relay_turns if continuous else self.max_agent_relay_depth,
            relay_agent_messages=continuous,
        )
        targets = decision.targets
        content = clean_lobby_text(event.get("content"), limit=12000).casefold()
        explicitly_routed = "@all" in content or any(f"@{agent_id.casefold()}" in content for agent_id in providers)
        if continuous and not explicitly_routed and targets:
            targets = tuple(
                agent_id
                for agent_id in targets
                if self._continuous_target_is_available(room_id, agent_id)
            )
        if continuous and not explicitly_routed and targets:
            ordered = sorted(providers)
            if decision.actor_id in ordered:
                start = (ordered.index(decision.actor_id) + 1) % len(ordered)
                candidates = (ordered[(start + offset) % len(ordered)] for offset in range(len(ordered)))
                next_agent = next((candidate for candidate in candidates if candidate in targets), targets[0])
            else:
                next_agent = targets[0]
            targets = (next_agent,)
        for agent_id in targets:
            participant = self.store.participant(room_id, agent_id)
            if participant.get("status") == "kicked" or participant.get("muted"):
                continue
            self._queue_event(
                room_id,
                agent_id,
                event,
                relay_depth=decision.relay_depth + (1 if continuous or decision.actor_type == "agent" else 0),
            )

    def _continuous_target_is_available(self, room_id: str, agent_id: str) -> bool:
        participant = self.store.participant(room_id, agent_id)
        session = self.store.session(room_id, agent_id)
        return bool(
            session
            and session.get("enabled")
            and participant.get("status") not in {"kicked", "left"}
            and not participant.get("muted")
        )

    def _queue_event(
        self,
        room_id: str,
        agent_id: str,
        event: RoomEvent | dict[str, object],
        *,
        relay_depth: int,
    ) -> None:
        session = self.store.session(room_id, agent_id)
        if not session:
            return
        event_id = clean_lobby_text(event.get("id"), limit=128)
        pending = _dedupe_text_list([*list(session.get("pending_event_ids") or []), event_id])
        session = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            pending_event_ids=pending,
            pending_relay_depth=max(int(session.get("pending_relay_depth") or 0), relay_depth),
        )
        if (
            session.get("enabled")
            and session.get("runtime_status") == "idle"
            and self.broker.has_bridge(room_id, agent_id)
        ):
            self._assign_pending(room_id, agent_id)

    def _assign_pending(self, room_id: str, agent_id: str) -> bool:
        session = self.store.session(room_id, agent_id)
        participant = self.store.participant(room_id, agent_id)
        pending = _dedupe_text_list(list(session.get("pending_event_ids") or [])) if session else []
        if (
            not session
            or participant.get("status") == "kicked"
            or bool(participant.get("muted"))
            or not pending
            or not session.get("enabled")
            or session.get("runtime_status") != "idle"
            or not self.broker.has_bridge(room_id, agent_id)
        ):
            return False
        turn_id = f"turn-{uuid4().hex[:12]}"
        packet = build_room_turn_packet(
            self.output_root,
            room_id=room_id,
            participant_id=agent_id,
            session_id=str(session["session_id"]),
            instruction="Reply naturally to the new room messages. Return only the text that should appear in the room.",
            max_recent_events=50 if session.get("external_owned") else None,
            max_prompt_chars=64_000 if session.get("external_owned") else None,
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
        input_up_to_seq = _safe_bounded_int(
            packet.get("last_provider_sync_seq_after"),
            default=0,
            minimum=0,
        )
        partition = self._partition_pending_events(
            room_id,
            pending,
            included_event_ids=set(provider_context_event_ids),
            last_provider_sync_seq=(
                _safe_bounded_int(
                    packet.get("last_provider_sync_seq_before", session.get("last_provider_sync_seq")),
                    default=0,
                    minimum=0,
                )
                if session.get("bootstrap_done")
                else 0
            ),
        )
        if not partition.inflight:
            cleaned_pending = partition.deferred
            if cleaned_pending != pending:
                self.store.update_session_fields(
                    room_id,
                    str(session["session_id"]),
                    pending_event_ids=cleaned_pending,
                    pending_relay_depth=int(session.get("pending_relay_depth") or 0) if cleaned_pending else 0,
                )
            return False
        active_source_event_id = partition.inflight[-1]
        source_event = self.store.event_by_id(room_id, active_source_event_id)
        input_up_to_event_id = input_up_to_event_id or active_source_event_id
        relay_depth = int(session.get("pending_relay_depth") or 0)
        dispatched_at = _now()
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
            pending_event_ids=partition.deferred,
            pending_relay_depth=relay_depth if partition.deferred else 0,
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
            "timeout_seconds": self._provider(room_id, agent_id).turn_timeout_seconds,
        }
        if self.broker.direct_to_bridge(room_id, agent_id, assignment):
            return True
        self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="unavailable",
            runtime_status="disconnected",
            active_turn_id="",
            turn_phase="",
            input_up_to_seq=0,
            inflight_event_ids=[],
            pending_event_ids=[*partition.inflight, *partition.deferred],
            last_error="Agent bridge disconnected before turn assignment.",
        )
        return False

    def _partition_pending_events(
        self,
        room_id: str,
        pending: list[str],
        *,
        included_event_ids: set[str],
        last_provider_sync_seq: int,
    ) -> _PendingEventPartition:
        partition = _PendingEventPartition(inflight=[], deferred=[], already_synced=[], invalid=[])
        for event_id in pending:
            event = self.store.event_by_id(room_id, event_id)
            event_seq = _safe_bounded_int(event.get("seq"), default=0, minimum=0)
            if not event or not event_seq:
                partition.invalid.append(event_id)
            elif event_seq <= last_provider_sync_seq:
                partition.already_synced.append(event_id)
            elif event_id in included_event_ids:
                partition.inflight.append(event_id)
            else:
                partition.deferred.append(event_id)
        return partition

    def _publish_session_state(self, room_id: str, session: dict[str, object]) -> dict[str, object]:
        if not session:
            return {}
        return self.store.append_event(
            room_id,
            "agent_session_state",
            participant_id=session.get("participant_id"),
            session_id=session.get("session_id"),
            runtime_status=session.get("runtime_status"),
            agent_session=self._public_session(session),
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
        self.store.upsert_session(
            room_id,
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

    def _bridge_session(
        self,
        identity: dict[str, object],
        room_id: str,
        *,
        allow_unleased: bool = False,
    ) -> tuple[str, dict[str, object]]:
        agent_id = clean_lobby_text(identity.get("agent_id"), limit=128)
        session_id = clean_lobby_text(identity.get("session_id") or agent_id, limit=128)
        session = self.store.session(room_id, session_id)
        if not session or session.get("participant_id") != agent_id:
            raise RoomCommandRejected("Agent bridge session does not match its ticket identity.", code="permission_denied")
        identity_generation = int(identity.get("bridge_generation") or 0)
        session_generation = int(session.get("bridge_generation") or 0)
        if not allow_unleased and session_generation and identity_generation != session_generation:
            raise RoomCommandRejected("Agent bridge lease is stale.", code="stale_bridge_generation")
        return agent_id, session

    def _active_bridge_turn(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        agent_id, session = self._bridge_session(identity, room_id)
        turn_id = clean_lobby_text(payload.get("turn_id"), limit=128)
        if not turn_id or turn_id != session.get("active_turn_id"):
            raise RoomCommandRejected("Turn does not match the active assignment.", code="turn_conflict")
        return agent_id, session

    def _require_capability(self, identity: dict[str, object], capability: str) -> None:
        if not self.capabilities(identity).get(capability):
            raise RoomCommandRejected(f"{capability} permission is required.", code="permission_denied")

    @staticmethod
    def _require_bridge(identity: dict[str, object]) -> None:
        if identity.get("client_type") != "agent_bridge":
            raise RoomCommandRejected("This command is reserved for an Agent Bridge.", code="permission_denied")

    @staticmethod
    def _public_session(session: dict[str, object]) -> dict[str, object]:
        hidden = {
            "env",
            "token",
            "ticket",
            "credentials",
            "stderr_tail",
            "terminal_tail",
            "provider_session_id",
            "pid",
            "reported_provider_pid",
            "bridge_pid",
            "bridge_handle_id",
            "command_configured",
            "resolved_executable",
            "workspace",
            "config_path",
            "stdout_path",
            "stderr_path",
            "provider_endpoint",
        }
        return {key: value for key, value in session.items() if key not in hidden}


def _dedupe_text_list(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = clean_lobby_text(value, limit=128)
        if text and text not in result:
            result.append(text)
    return result


_PUBLIC_ACTIVITY_LABELS = {
    "reasoning": {"running": "생각 정리 중", "completed": "생각 정리 완료"},
    "file_read": {"running": "파일 읽는 중", "completed": "파일 확인 완료"},
    "search": {"running": "정보 검색 중", "completed": "정보 검색 완료"},
    "command": {"running": "명령 실행 중", "completed": "명령 실행 완료"},
    "web": {"running": "웹 확인 중", "completed": "웹 확인 완료"},
    "tool": {"running": "도구 사용 중", "completed": "도구 사용 완료"},
}


def _public_activity(category: str, status: str) -> tuple[str, str]:
    safe_category = category if category in _PUBLIC_ACTIVITY_LABELS else "tool"
    safe_status = "completed" if status == "completed" else "running"
    return (
        _PUBLIC_ACTIVITY_LABELS[safe_category][safe_status],
        "reasoning" if safe_category == "reasoning" else "tool",
    )


def _command_principal(identity: dict[str, object]) -> str:
    client_type = clean_lobby_text(identity.get("client_type"), limit=64) or "unknown"
    principal = clean_lobby_text(
        identity.get("session_id") or identity.get("user_id") or identity.get("agent_id"),
        limit=128,
    )
    return f"{client_type}:{principal or 'anonymous'}"


def _public_event(event: RoomEvent | dict[str, object]) -> dict[str, object]:
    hidden = {
        "legacy_source_path",
        "path",
        "file_path",
        "absolute_path",
        "workspace",
        "executable",
        "argv",
        "pid",
        "bridge_pid",
        "reported_provider_pid",
        "provider_session_id",
    }

    def project(value: object) -> object:
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items() if key not in hidden}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    return dict(project(dict(event)))


def _merged_latency(existing: object, incoming: object) -> dict[str, object]:
    base = dict(existing) if isinstance(existing, dict) else {}
    if isinstance(incoming, dict):
        base.update({key: value for key, value in incoming.items() if value not in (None, "")})
    return base


def _runtime_diagnostic_fields(diagnostics: object) -> dict[str, object]:
    values = diagnostics if isinstance(diagnostics, dict) else {}
    return {
        "terminal_byte_count": int(values.get("terminal_byte_count") or 0),
        "terminal_tail": str(values.get("terminal_tail") or "")[-16000:],
        "stderr_drained": bool(values.get("stderr_drained", False)),
        "stderr_byte_count": int(values.get("stderr_byte_count") or 0),
        "stderr_line_count": int(values.get("stderr_line_count") or 0),
        "stderr_warning_count": int(values.get("stderr_warning_count") or 0),
        "stderr_tail": str(values.get("stderr_tail") or "")[-16000:],
        "stderr_tail_truncated": bool(values.get("stderr_tail_truncated", False)),
        "stderr_last_line_at": clean_lobby_text(values.get("stderr_last_line_at"), limit=128),
        "provider_session_active": bool(values.get("provider_session_active", False)),
        "provider_session_load_supported": bool(values.get("provider_session_load_supported", False)),
        "provider_session_reused": bool(values.get("provider_session_reused", False)),
        "provider_session_resume_failed": bool(values.get("provider_session_resume_failed", False)),
        "provider_session_resume_error": clean_lobby_text(
            values.get("provider_session_resume_error"),
            limit=1000,
        ),
        "approval_policy": clean_lobby_text(values.get("approval_policy"), limit=64),
        "yolo_mode": values.get("yolo_mode") if isinstance(values.get("yolo_mode"), bool) else None,
        "permission_request_count": int(values.get("permission_request_count") or 0),
        "permission_denied_count": int(values.get("permission_denied_count") or 0),
        "empty_turn_recovery_count": int(values.get("empty_turn_recovery_count") or 0),
        "notification_drop_count": int(values.get("notification_drop_count") or 0),
        "message_source": clean_lobby_text(values.get("message_source"), limit=128),
        "message_source_strict": bool(values.get("message_source_strict", False)),
    }


def _runtime_profile_fields(values: object, *, include_model: bool = True) -> dict[str, object]:
    payload = values if isinstance(values, dict) else {}
    limits = {
        "model": 128,
        "reasoning_effort": 32,
        "service_tier": 32,
        "variant": 64,
        "permission_mode": 64,
    }
    fields: dict[str, object] = {}
    for key, limit in limits.items():
        if key == "model" and not include_model:
            continue
        value = clean_lobby_text(payload.get(key), limit=limit)
        if value:
            fields[key] = value
    return fields


def _public_runtime_diagnostics(diagnostics: object) -> dict[str, object]:
    return {
        key: value
        for key, value in _runtime_diagnostic_fields(diagnostics).items()
        if key not in {"stderr_tail", "terminal_tail"}
    }


def _message_delta_text(value: object, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")[:limit]


def _room_message_text(value: object, *, limit: int) -> str:
    return _message_delta_text(value, limit=limit).strip()


def _provider_process_exited(message: str, diagnostics: dict[str, object]) -> bool:
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


def _schedule_daemon_timer(delay_seconds: float, callback: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(max(0.0, float(delay_seconds)), callback)
    timer.daemon = True
    timer.start()
    return timer


def _now() -> str:
    return datetime.now(UTC).isoformat()
