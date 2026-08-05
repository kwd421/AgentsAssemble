from __future__ import annotations

from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from agentsassemble.providers.capabilities import ProviderCatalogSelectionError
from agentsassemble.persona_cards.library import PersonaSelectionError
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
ResolvePersona = Callable[[str, str], dict[str, object]]


class RoomAgentCreationService:
    """Create one catalog-validated server-owned Agent Session."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        provider_catalog: ProviderSelectionCatalog,
        create_provider_session: CreateProviderSession,
        start_agent: StartAgent,
        resolve_persona: ResolvePersona | None = None,
    ) -> None:
        self.store = store
        self.provider_catalog = provider_catalog
        self._create_provider_session = create_provider_session
        self._start_agent = start_agent
        self._resolve_persona = resolve_persona

    def create(
        self,
        room_id: str,
        payload: dict[str, object],
        *,
        operation_id: str,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], object] | None,
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
                    "provider_endpoint": clean_room_text(
                        payload.get("provider_endpoint"),
                        1000,
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
                    "execution_harness": clean_room_text(
                        payload.get("execution_harness"),
                        32,
                    ),
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
        persona_card_id = clean_room_text(payload.get("persona_card_id"), 80)
        try:
            persona_card = self._resolved_persona(
                selection.provider_id,
                persona_card_id,
            )
        except PersonaSelectionError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        agent_id = _agent_id_for_creation(
            provider_id,
            operation_id,
        )
        try:
            spec = native_cli_provider_spec_from_payload(
                {
                    "provider_id": selection.provider_id,
                    "agent_id": agent_id,
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
                    "execution_harness": selection.execution_harness,
                    "permission_mode": selection.permission_mode,
                    "max_output_tokens": selection.max_output_tokens,
                    "context_contract_bytes": selection.context_contract_bytes,
                    "provider_endpoint": selection.provider_endpoint,
                    "persona_card_id": persona_card.get("id", ""),
                    "persona_card": persona_card,
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

    def _resolved_persona(
        self,
        provider_id: str,
        persona_card_id: str,
    ) -> dict[str, object]:
        if not persona_card_id:
            return {}
        if self._resolve_persona is None:
            raise PersonaSelectionError(
                "The bot-card library is unavailable.",
                code="persona_not_found",
            )
        return dict(self._resolve_persona(provider_id, persona_card_id))


def _agent_id_for_creation(provider_id: str, operation_id: str) -> str:
    clean_operation_id = clean_room_text(operation_id, 128)
    if not clean_operation_id:
        raise RoomCommandRejected(
            "Agent Session creation identity is unavailable.",
            code="agent_identity_unavailable",
        )
    provider_prefix = clean_room_text(provider_id, 48).casefold() or "agent"
    return f"{provider_prefix}-{uuid5(NAMESPACE_URL, f'agentsassemble:{clean_operation_id}')}"


__all__ = [
    "CreateProviderSession",
    "RoomAgentCreationService",
    "ResolvePersona",
    "StartAgent",
]
