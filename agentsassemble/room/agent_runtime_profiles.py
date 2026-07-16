from __future__ import annotations

from typing import Callable, Protocol

from agentsassemble.providers.capabilities import (
    ProviderCatalogSelectionError,
    ValidatedProviderSelection,
)
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
    ) -> None:
        self.store = store
        self.provider_catalog = provider_catalog
        self._configure_stopped_profile = configure_stopped_profile

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
        selected_values = {
            key: payload[key] if key in payload else current.get(key)
            for key in (
                "model",
                "reasoning_effort",
                "service_tier",
                "variant",
                "permission_mode",
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
            raise RoomCommandRejected(
                str(error),
                code="invalid_runtime_profile",
            ) from error
        session = self._configure_stopped_profile(room_id, spec)
        return {"status": "configured", "agent_session": session}


__all__ = [
    "ConfigureStoppedProfile",
    "ProviderSelectionCatalog",
    "RoomAgentRuntimeProfileService",
]
