from __future__ import annotations

import hashlib
import json
from typing import Callable

from agentsassemble.room.bridge_diagnostics import (
    DiagnosticRedactor,
    default_diagnostic_redactor,
    session_diagnostic_redactor,
)
from agentsassemble.providers.launch_specs import (
    EXTERNAL_AGENT_PROVIDER_KIND,
    native_cli_provider_definition,
)
from agentsassemble.providers.model_verification import model_verification_status
from agentsassemble.providers.runtime_config import (
    ProviderRuntimeConfigError,
    ProviderRuntimeProfile,
)
from agentsassemble.providers.runtime_contracts import (
    AdapterContractError,
    ProviderRuntimeHealth,
)
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.projection import (
    public_session,
    runtime_diagnostic_fields,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


BridgeSessionLookup = Callable[..., tuple[str, dict[str, object]]]
PendingAssignment = Callable[[str, str], bool]
SessionCallback = Callable[[str, dict[str, object]], object]


class RoomBridgeReportService:
    """Validate Agent Bridge runtime reports and persist canonical state."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        bridge_session: BridgeSessionLookup,
        assign_pending: PendingAssignment,
        publish_session_state: SessionCallback,
        redact_diagnostic: DiagnosticRedactor | None = None,
    ) -> None:
        self.store = store
        self.broker = broker
        self._bridge_session = bridge_session
        self._assign_pending = assign_pending
        self._publish_session_state = publish_session_state
        self._redact_diagnostic = redact_diagnostic or default_diagnostic_redactor

    def ready(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        agent_id, session = self._bridge_session(
            identity,
            room_id,
            allow_unleased=True,
        )
        redact_text = session_diagnostic_redactor(
            self._redact_diagnostic,
            room_id,
            session["session_id"],
        )
        try:
            health = ProviderRuntimeHealth.parse(payload)
        except AdapterContractError as error:
            raise RoomCommandRejected(str(error), code="adapter_health_invalid") from error
        if not health.running:
            raise RoomCommandRejected(
                "A stopped provider cannot become ready.",
                code="adapter_health_invalid",
            )
        connection_id = clean_room_text(identity.get("connection_id"), 128)
        channel = self.broker.channel(connection_id)
        if channel is None:
            raise RoomCommandRejected(
                "Agent bridge connection is no longer active.",
                code="bridge_disconnected",
            )
        external_profile: dict[str, object] = {}
        external_kind = clean_room_text(session.get("provider_kind"), 64)
        if (
            session.get("process_ownership") == "external"
            and external_kind != EXTERNAL_AGENT_PROVIDER_KIND
        ):
            # A provider this server knows, being run by the joiner instead of
            # by us: its report must match the definition we hold. An
            # external_agent has no such definition -- it is whatever the
            # participant is running -- so there is nothing to match it against
            # and the room simply records what it reports.
            definition = native_cli_provider_definition(session.get("provider_kind"))
            if definition is None:
                raise RoomCommandRejected(
                    "The external provider kind is not supported.",
                    code="provider_profile_invalid",
                )
            try:
                profile = ProviderRuntimeProfile.parse_strict(payload)
            except ProviderRuntimeConfigError as error:
                raise RoomCommandRejected(
                    str(error),
                    code="provider_profile_invalid",
                ) from error
            if profile.provider_kind != clean_room_text(session.get("provider_kind"), 64):
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
                ("max_output_tokens", definition.default_max_output_tokens),
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
                "model_verification_status": model_verification_status(
                    requested_model_id=profile.model,
                    observed_model_id="",
                    selection_kind="exact",
                    observation_policy=observation_policy,
                ),
                "reasoning_effort": profile.reasoning_effort,
                "service_tier": profile.service_tier,
                "variant": profile.variant,
                "execution_harness": profile.execution_harness,
                "permission_mode": profile.permission_mode,
                "max_output_tokens": profile.max_output_tokens,
                "context_contract_bytes": profile.context_contract_bytes,
                "runtime_kind": profile.runtime_kind,
                "transport": profile.transport,
                "runtime_profile_key": external_runtime_profile_key(profile),
            }
        generation = self.broker.activate_bridge(channel)
        identity["bridge_generation"] = generation
        previous_participant = self.store.participant(room_id, agent_id)
        self.store.update_participant_fields(room_id, agent_id, status="joined")
        self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            status="attached",
            enabled=True,
            runtime_status="idle",
            reported_provider_pid=safe_int_or_none(payload.get("pid")),
            bridge_generation=generation,
            pty=health.pty,
            reported_transport=health.transport,
            is_one_shot=bool(payload.get("is_one_shot", False)),
            started_at=health.started_at,
            last_error="",
            **external_profile,
            **runtime_diagnostic_fields(payload, redact_text=redact_text),
        )
        if previous_participant.get("status") != "joined":
            self.store.append_event(
                room_id,
                "participant_joined",
                participant_id=agent_id,
                session_id=session["session_id"],
            )
        self.store.append_event(
            room_id,
            "session_attached",
            participant_id=agent_id,
            session_id=session["session_id"],
        )
        self._assign_pending(room_id, agent_id)
        current = self.store.session(room_id, str(session["session_id"]))
        self._publish_session_state(room_id, current)
        return {"agent_session": public_session(current)}

    def health(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        _agent_id, session = self._bridge_session(identity, room_id)
        redact_text = session_diagnostic_redactor(
            self._redact_diagnostic,
            room_id,
            session["session_id"],
        )
        try:
            health = ProviderRuntimeHealth.parse(payload)
        except AdapterContractError as error:
            raise RoomCommandRejected(str(error), code="adapter_health_invalid") from error
        fields: dict[str, object] = {}
        if "resolved_executable" in payload:
            fields["resolved_executable"] = clean_room_text(
                payload.get("resolved_executable"),
                1000,
            )
        if "last_error" in payload:
            fields["last_error"] = redact_text(payload.get("last_error"), 4000)
        if "returncode" in payload:
            fields["returncode"] = safe_int_or_none(payload.get("returncode"))
        fields.update(
            running=health.running,
            pty=health.pty,
            reported_transport=health.transport,
            started_at=health.started_at,
        )
        if "pid" in payload:
            fields["reported_provider_pid"] = safe_int_or_none(payload.get("pid"))
        fields.update(runtime_diagnostic_fields(payload, redact_text=redact_text))
        updated = self.store.update_session_fields(
            room_id,
            str(session["session_id"]),
            **fields,
        )
        self._publish_session_state(room_id, updated)
        return {"agent_session": public_session(updated)}


def external_runtime_profile_key(profile: ProviderRuntimeProfile) -> str:
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


def safe_int_or_none(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "BridgeSessionLookup",
    "PendingAssignment",
    "RoomBridgeReportService",
    "SessionCallback",
    "external_runtime_profile_key",
    "safe_int_or_none",
]
