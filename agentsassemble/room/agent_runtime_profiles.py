from __future__ import annotations

from typing import Callable, Protocol

from agentsassemble.providers.capabilities import (
    ProviderCatalogSelectionError,
    ValidatedProviderSelection,
)
from agentsassemble.persona_cards.library import PersonaSelectionError
from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    UnsupportedNativeCliProvider,
    native_cli_provider_definition,
    native_cli_provider_spec_from_payload,
)
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


ConfigureStoppedProfile = Callable[
    [str, NativeCliProviderSpec],
    dict[str, object],
]
ResolvePersona = Callable[[str, str], dict[str, object]]


class ProviderSelectionCatalog(Protocol):
    def validate_selection(
        self,
        *,
        catalog_revision: str,
        provider_id: str,
        values: dict[str, str],
    ) -> ValidatedProviderSelection: ...


class RoomAgentRuntimeProfileService:
    """Validate and replace a stopped Agent Session runtime profile."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        provider_catalog: ProviderSelectionCatalog,
        configure_stopped_profile: ConfigureStoppedProfile,
        resolve_persona: ResolvePersona | None = None,
    ) -> None:
        self.store = store
        self.provider_catalog = provider_catalog
        self._configure_stopped_profile = configure_stopped_profile
        self._resolve_persona = resolve_persona

    def configure(
        self,
        room_id: str,
        agent_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        current = self.store.session(room_id, agent_id)
        if not current:
            raise RoomCommandRejected(
                f"Agent session {agent_id} was not found.",
                code="not_found",
            )
        if current.get("runtime_status") in {
            "starting",
            "idle",
            "busy",
            "paused",
            "recovering",
            "stopping",
        }:
            raise RoomCommandRejected(
                "Stop this Agent Session before changing its runtime settings.",
                code="runtime_profile_conflict",
            )
        requested_provider = clean_room_text(
            payload.get("provider_id")
            or payload.get("provider_kind")
            or current.get("provider_kind"),
            64,
        )
        existing_provider = clean_room_text(current.get("provider_kind"), 64)
        definition = native_cli_provider_definition(requested_provider)
        if definition is None or definition.provider_kind != existing_provider:
            raise RoomCommandRejected(
                "An existing Agent Session cannot change provider kind; "
                "remove it and create a new session.",
                code="provider_mismatch",
            )
        merged = {
            **payload,
            "agent_id": agent_id,
            "provider_id": definition.provider_id,
            "display_name": (
                payload.get("display_name")
                or current.get("display_name")
                or agent_id
            ),
            "workspace": (
                payload["workspace"]
                if "workspace" in payload
                else current.get("workspace")
            ),
        }
        requested_persona_id = clean_room_text(
            payload.get("persona_card_id")
            if "persona_card_id" in payload
            else current.get("persona_card_id"),
            80,
        )
        try:
            persona_card = self._resolved_persona(
                definition.provider_id,
                requested_persona_id,
            )
        except PersonaSelectionError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        selected_values = {
            key: payload[key] if key in payload else current.get(key)
            for key in (
                "model",
                "provider_endpoint",
                "reasoning_effort",
                "service_tier",
                "variant",
                "permission_mode",
                "max_output_tokens",
            )
        }
        try:
            selection = self.provider_catalog.validate_selection(
                catalog_revision=clean_room_text(
                    payload.get("catalog_revision"),
                    128,
                ),
                provider_id=definition.provider_id,
                values={
                    "model": clean_room_text(selected_values["model"], 128),
                    "provider_endpoint": clean_room_text(
                        selected_values["provider_endpoint"],
                        1000,
                    ),
                    "reasoning_effort": clean_room_text(
                        selected_values["reasoning_effort"],
                        32,
                    ),
                    "service_tier": clean_room_text(
                        selected_values["service_tier"],
                        32,
                    ),
                    "variant": clean_room_text(selected_values["variant"], 64),
                    "permission_mode": clean_room_text(
                        selected_values["permission_mode"],
                        64,
                    ),
                    "max_output_tokens": str(
                        selected_values["max_output_tokens"] or ""
                    ),
                },
            )
        except ProviderCatalogSelectionError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        if (
            definition.runtime_kind == "api"
            and selection.permission_mode == "workspace_write"
            and clean_room_text(current.get("permission_mode"), 64) != "workspace_write"
            and not clean_room_text(payload.get("workspace"), 500)
        ):
            raise RoomCommandRejected(
                "Select a workspace before enabling the API work harness.",
                code="workspace_required",
            )
        try:
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
                    "max_output_tokens": selection.max_output_tokens,
                    "provider_endpoint": selection.provider_endpoint,
                    "persona_card_id": persona_card.get("id", ""),
                    "persona_card": persona_card,
                }
            )
        except (UnsupportedNativeCliProvider, ValueError) as error:
            raise RoomCommandRejected(
                str(error),
                code="invalid_runtime_profile",
            ) from error
        session = self._configure_stopped_profile(room_id, spec)
        return {"status": "configured", "agent_session": session}

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


__all__ = [
    "ConfigureStoppedProfile",
    "ProviderSelectionCatalog",
    "ResolvePersona",
    "RoomAgentRuntimeProfileService",
]
