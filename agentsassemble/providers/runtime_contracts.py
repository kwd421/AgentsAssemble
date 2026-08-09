from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, Protocol


PublicationMode = Literal["automatic_final", "explicit_room_portal"]
AUTOMATIC_FINAL: PublicationMode = "automatic_final"
EXPLICIT_ROOM_PORTAL: PublicationMode = "explicit_room_portal"

RoomObservationKind = Literal["ordered_floor", "ambient_observation"]
ORDERED_FLOOR: RoomObservationKind = "ordered_floor"
AMBIENT_OBSERVATION: RoomObservationKind = "ambient_observation"
ROOM_OBSERVATION_KINDS = frozenset({ORDERED_FLOOR, AMBIENT_OBSERVATION})


SUPPORTED_DECLINE_REASONS = frozenset(
    {"nothing_useful_to_add", "not_addressed", "duplicate"}
)


class AdapterContractError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "adapter_contract_error",
    ) -> None:
        super().__init__(message)
        self.code = code


class BridgeRuntime(Protocol):
    def start(self) -> dict[str, object]: ...
    def send(self, text: str) -> None: ...
    def read_output(
        self,
        *,
        timeout_seconds: float,
        on_delta=None,
        on_activity=None,
    ) -> dict[str, object]: ...
    def interrupt(self) -> None: ...
    def stop(self, *, timeout_seconds: float = 2.0) -> None: ...
    def health(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class ProviderTurnResult:
    outcome: Literal["message", "decline"]
    content: str
    decline_reason: str
    metadata: dict[str, object]

    @classmethod
    def parse(cls, value: object) -> ProviderTurnResult:
        if not isinstance(value, dict):
            raise AdapterContractError("Provider turn result must be an object.")
        outcome = value.get("outcome")
        if outcome not in {"message", "decline"}:
            raise AdapterContractError(
                "Provider turn result outcome must be message or decline."
            )
        metadata_value = value.get("metadata", {})
        if not isinstance(metadata_value, dict):
            raise AdapterContractError(
                "Provider turn result metadata must be an object."
            )
        metadata = deepcopy(metadata_value)
        if outcome == "decline":
            reason = value.get("reason_code")
            if (
                not isinstance(reason, str)
                or reason not in SUPPORTED_DECLINE_REASONS
            ):
                raise AdapterContractError(
                    "Provider decline result requires a supported reason_code."
                )
            return cls(
                outcome="decline",
                content="",
                decline_reason=reason,
                metadata=metadata,
            )
        content = value.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AdapterContractError(
                "Provider message result requires non-empty text content."
            )
        return cls(
            outcome="message",
            content=content,
            decline_reason="",
            metadata=metadata,
        )


@dataclass(frozen=True)
class ProviderRuntimeHealth:
    running: bool
    transport: str
    started_at: str | None
    provider_session_active: bool
    details: dict[str, object]

    @property
    def pty(self) -> bool:
        return self.transport in {"pty", "conpty"}

    @classmethod
    def parse(cls, value: object) -> ProviderRuntimeHealth:
        if not isinstance(value, dict):
            raise AdapterContractError(
                "Provider runtime health must be an object."
            )
        running = value.get("running")
        if not isinstance(running, bool):
            raise AdapterContractError(
                "Provider runtime health requires boolean running."
            )
        transport = value.get("transport")
        if not isinstance(transport, str) or not transport.strip():
            raise AdapterContractError(
                "Provider runtime health requires transport."
            )
        active = value.get("provider_session_active")
        if not isinstance(active, bool):
            raise AdapterContractError(
                "Provider runtime health requires boolean provider_session_active."
            )
        started_at_value = value.get("started_at")
        if started_at_value is None or started_at_value == "":
            started_at = None
        elif isinstance(started_at_value, str):
            started_at = started_at_value
        else:
            raise AdapterContractError(
                "Provider runtime health started_at must be text or null."
            )
        return cls(
            running=running,
            transport=transport.strip(),
            started_at=started_at,
            provider_session_active=active,
            details=deepcopy(value),
        )


__all__ = [
    "AMBIENT_OBSERVATION",
    "AUTOMATIC_FINAL",
    "AdapterContractError",
    "BridgeRuntime",
    "EXPLICIT_ROOM_PORTAL",
    "ORDERED_FLOOR",
    "PublicationMode",
    "ProviderRuntimeHealth",
    "ProviderTurnResult",
    "ROOM_OBSERVATION_KINDS",
    "RoomObservationKind",
    "SUPPORTED_DECLINE_REASONS",
]
