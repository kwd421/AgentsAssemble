from __future__ import annotations

from pathlib import Path
import threading
from typing import Callable

from agentsassemble.providers.launch_specs import (
    EXTERNAL_AGENT_PROVIDER_KIND,
    NativeCliProviderSpec,
    StoredProviderProfileError,
    native_cli_provider_definition,
    native_cli_provider_spec_from_stored_session_strict,
    validate_native_cli_provider_spec,
)
from agentsassemble.providers.model_verification import model_verification_status
from agentsassemble.providers.sync_cursor import assert_provider_sync_cursor_parity
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.projection import public_session
from agentsassemble.room.provider_registry import RoomProviderRegistry
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


EnsureRoom = Callable[[str], dict[str, object]]
SessionCallback = Callable[[str, dict[str, object]], object]


class RoomProviderSessionService:
    """Own configured provider participant and Agent Session persistence."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        lock: threading.RLock,
        registry: RoomProviderRegistry,
        ensure_room: EnsureRoom,
        publish_session_state: SessionCallback,
    ) -> None:
        self.store = store
        self.broker = broker
        self._lock = lock
        self.registry = registry
        self._ensure_room = ensure_room
        self._publish_session_state = publish_session_state

    def restore_server_owned_providers(self) -> None:
        """Rebuild startable provider specs from durable Agent Sessions."""
        for room in self.store.list_rooms(include_archived=True):
            room_id = clean_room_text(room.get("room_id"), 128)
            if not room_id:
                continue
            self._ensure_room(room_id)
            for session in self.store.sessions(room_id):
                agent_id = clean_room_text(session.get("participant_id"), 128)
                if not agent_id or self.registry.contains(room_id, agent_id):
                    continue
                if self.store.participant(room_id, agent_id).get("status") == "kicked":
                    continue
                process_ownership = restorable_process_ownership(session)
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
                migration_blocked = session.get("last_error_code") in {
                    "profile_migration_required",
                    "provider_definition_changed",
                } or (
                    not session.get("last_error_code")
                    and session.get("last_error")
                    in {
                        "Stored Agent Session profile must be migrated before it can be reused.",
                        "Stored Agent Session provider definition changed.",
                    }
                )
                if profile_changed or migration_blocked:
                    updates: dict[str, object] = {}
                    if profile_changed:
                        updates.update(
                            runtime_profile_key=spec.runtime_profile_key(),
                            transport=spec.transport,
                            command_configured=list(spec.command),
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
                self.registry.register(room_id, spec)

    def create_provider_session(
        self,
        room_id: str,
        spec: NativeCliProviderSpec,
    ) -> dict[str, object]:
        clean_room_id = clean_room_text(room_id, 128)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        validate_native_cli_provider_spec(spec)
        self._ensure_room(clean_room_id)
        with self._lock:
            current = self._create_provider_records(
                clean_room_id,
                spec,
                require_absent=True,
                append_created_event=True,
            )
            self.registry.register(clean_room_id, spec)
            self._publish_session_state(clean_room_id, current)
        return public_session(current)

    def configure_stopped_provider_profile(
        self,
        room_id: str,
        spec: NativeCliProviderSpec,
    ) -> dict[str, object]:
        clean_room_id = clean_room_text(room_id, 128)
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
                or current.get("runtime_status")
                in {"starting", "idle", "busy", "paused", "recovering", "stopping"}
                or current.get("active_turn_id")
                or current.get("bridge_handle_id")
                or self.broker.has_bridge(clean_room_id, spec.agent_id)
            ):
                raise RoomCommandRejected(
                    "Stop this Agent Session before changing its runtime settings.",
                    code="runtime_profile_conflict",
                )
            self.registry.register(clean_room_id, spec)
            self.ensure_provider_session(clean_room_id, spec)
            current = self.store.session(clean_room_id, spec.agent_id)
            if (
                current.get("runtime_status") in {"error", "disconnected"}
                and not current.get("bridge_handle_id")
                and not self.broker.has_bridge(clean_room_id, spec.agent_id)
            ):
                self.store.update_session_fields(
                    clean_room_id,
                    spec.agent_id,
                    status="available",
                    runtime_status="stopped",
                    enabled=False,
                    last_error="",
                    last_error_code="",
                    recovery_required=False,
                    recovery_attempt_count=0,
                    lifecycle_intent_action="",
                    lifecycle_intent_id="",
                    lifecycle_intent_status="",
                )
            self.store.update_session_fields(
                clean_room_id,
                spec.agent_id,
                observed_model_id="",
                model_verification_status=model_verification_status(
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

    def ensure_external_bridge_session(
        self,
        room_id: str,
        identity: dict[str, object],
    ) -> None:
        participant_id = clean_room_text(identity.get("agent_id"), 128)
        if not participant_id:
            return
        existing_session = self.store.session(room_id, participant_id)
        if existing_session.get("process_ownership") == "server":
            return
        display_name = clean_room_text(identity.get("display_name"), 64) or participant_id
        provider_kind = (
            clean_room_text(identity.get("provider_kind"), 64) or EXTERNAL_AGENT_PROVIDER_KIND
        )
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
        self.registry.register(room_id, spec)
        if existing_session:
            return
        self.store.upsert_participant(
            room_id,
            {
                "participant_id": participant_id,
                "display_name": display_name,
                "participant_type": "agent",
                "role": "agent",
                "owner_id": clean_room_text(identity.get("owner_id"), 128),
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
                    "pending_event_modes": {},
                    "pending_event_observation_kinds": {},
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

    def ensure_provider_session(self, room_id: str, spec: NativeCliProviderSpec) -> None:
        agent_id = clean_room_text(spec.agent_id, 128)
        participant = self.store.participant(room_id, agent_id)
        session = self.store.session(room_id, agent_id)
        if session and not participant:
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
                    clean_room_text(session.get("last_provider_sync_event_id"), 128),
                )
            if "last_seen_seq" not in session:
                cursor_updates["last_seen_seq"] = self.store.event_sequence(
                    room_id,
                    clean_room_text(session.get("last_seen_event_id"), 128),
                )
            if "process_ownership" not in session:
                cursor_updates["process_ownership"] = (
                    "external" if session.get("external_owned") else "server"
                )
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
                model_verification_status=model_verification_status(
                    requested_model_id=spec.requested_model_id or spec.model,
                    observed_model_id=clean_room_text(session.get("observed_model_id"), 128),
                    selection_kind=spec.model_selection_kind,
                    observation_policy=spec.model_observation_policy,
                ),
                catalog_revision=spec.catalog_revision,
                reasoning_effort=spec.reasoning_effort,
                service_tier=spec.service_tier,
                variant=spec.variant,
                permission_mode=spec.permission_mode,
                max_output_tokens=spec.max_output_tokens,
                provider_endpoint=spec.provider_endpoint,
                persona_card_id=spec.persona_card_id,
                persona_card=dict(spec.persona_card_summary),
                runtime_profile_key=spec.runtime_profile_key(),
                pty=spec.transport in {"pty", "conpty"},
                transport=spec.transport,
                is_one_shot=False,
                **cursor_updates,
            )
            return
        self._create_provider_records(
            room_id,
            spec,
            require_absent=False,
            append_created_event=False,
        )

    def _create_provider_records(
        self,
        room_id: str,
        spec: NativeCliProviderSpec,
        *,
        require_absent: bool,
        append_created_event: bool,
    ) -> dict[str, object]:
        agent_id = clean_room_text(spec.agent_id, 128)
        latest_events = self.store.read_events(
            room_id,
            event_types=("message_final",),
            limit=1,
            newest=True,
        )
        latest_public_event = latest_events[-1] if latest_events else {}
        latest_public = clean_room_text(latest_public_event.get("id"), 128)
        latest_public_seq = int(latest_public_event.get("seq") or 0)
        with self.store.transaction(room_id) as transaction:
            existing_participant = transaction.participant(agent_id)
            existing_session = transaction.session(agent_id)
            if require_absent and (existing_participant or existing_session):
                raise RoomCommandRejected(
                    "An Agent Session with this identity already exists; re-add or configure the existing session instead.",
                    code="session_exists",
                )
            if existing_session:
                return existing_session
            if not existing_participant:
                transaction.upsert_participant(
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
                    "model_verification_status": model_verification_status(
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
                    "max_output_tokens": spec.max_output_tokens,
                    "provider_endpoint": spec.provider_endpoint,
                    "persona_card_id": spec.persona_card_id,
                    "persona_card": dict(spec.persona_card_summary),
                    "runtime_profile_key": spec.runtime_profile_key(),
                    "enabled": False,
                    "runtime_status": "stopped",
                    "pending_event_ids": [],
                    "pending_event_modes": {},
                    "pending_event_observation_kinds": {},
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
            if append_created_event:
                transaction.append_event(
                    "agent_session_created",
                    participant_id=agent_id,
                    session_id=agent_id,
                    provider_kind=spec.normalized_provider_kind(),
                )
            return created_session


def restorable_process_ownership(session: dict[str, object]) -> str:
    explicit = clean_room_text(session.get("process_ownership"), 32)
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


__all__ = [
    "EnsureRoom",
    "RoomProviderSessionService",
    "SessionCallback",
    "restorable_process_ownership",
]
