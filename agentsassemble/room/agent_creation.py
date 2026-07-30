from __future__ import annotations

from typing import Callable

from agentsassemble.providers.capabilities import ProviderCatalogSelectionError
from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    UnsupportedNativeCliProvider,
    native_cli_provider_spec_from_payload,
)
from agentsassemble.room.agent_runtime_profiles import ProviderSelectionCatalog
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


CreateProviderSession = Callable[
    [str, NativeCliProviderSpec],
    dict[str, object],
]
StartAgent = Callable[..., dict[str, object]]


class RoomAgentCreationService:
    """Create one catalog-validated server-owned Agent Session."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        provider_catalog: ProviderSelectionCatalog,
        create_provider_session: CreateProviderSession,
        start_agent: StartAgent,
    ) -> None:
        self.store = store
        self.provider_catalog = provider_catalog
        self._create_provider_session = create_provider_session
        self._start_agent = start_agent

    def create(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], str] | None,
    ) -> dict[str, object]:
        provider_id = clean_room_text(
            payload.get("provider_id")
            or payload.get("provider_kind")
            or payload.get("provider"),
            64,
        )
        catalog_revision = clean_room_text(payload.get("catalog_revision"), 128)
        try:
            selection = self.provider_catalog.validate_selection(
                catalog_revision=catalog_revision,
                provider_id=provider_id,
                values={
                    "model": clean_room_text(
                        payload.get("model") or payload.get("model_id"),
                        128,
                    ),
                    "reasoning_effort": clean_room_text(
                        payload.get("reasoning_effort") or payload.get("effort"),
                        32,
                    ),
                    "service_tier": clean_room_text(
                        payload.get("service_tier"),
                        32,
                    ),
                    "variant": clean_room_text(payload.get("variant"), 64),
                    "permission_mode": clean_room_text(
                        payload.get("permission_mode")
                        or payload.get("permission_option"),
                        64,
                    ),
                    "max_output_tokens": str(
                        payload.get("max_output_tokens") or ""
                    ),
                },
            )
        except ProviderCatalogSelectionError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        try:
            spec = native_cli_provider_spec_from_payload(
                {
                    "provider_id": selection.provider_id,
                    "agent_id": (
                        payload.get("agent_id")
                        or payload.get("participant_id")
                    ),
                    "display_name": payload.get("display_name"),
                    "workspace": (
                        payload.get("workspace")
                        or payload.get("workspace_path")
                        or payload.get("cwd")
                    ),
                    "model": selection.model,
                    "model_selection_kind": selection.model_selection_kind,
                    "catalog_revision": selection.catalog_revision,
                    "reasoning_effort": selection.reasoning_effort,
                    "service_tier": selection.service_tier,
                    "variant": selection.variant,
                    "permission_mode": selection.permission_mode,
                    "max_output_tokens": selection.max_output_tokens,
                }
            )
        except UnsupportedNativeCliProvider as error:
            raise RoomCommandRejected(
                str(error),
                code="unsupported_provider",
            ) from error
        except ValueError as error:
            raise RoomCommandRejected(
                str(error),
                code="invalid_runtime_profile",
            ) from error
        session = self._create_provider_session(room_id, spec)
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


__all__ = [
    "CreateProviderSession",
    "RoomAgentCreationService",
    "StartAgent",
]
