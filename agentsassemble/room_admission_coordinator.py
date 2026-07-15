"""Mutating room-admission workflow across invite, identity, and room stores."""
from __future__ import annotations

import secrets
from collections.abc import Callable

from agentsassemble.identity_store import IdentityBackend
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.multi_host_invites import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room_invite import InviteApplicationService, PreparedInviteAdmission
from agentsassemble.room_repository import RoomRepository
from agentsassemble.room_session_service import RoomSessionService
from agentsassemble.room_users import device_auth_key, normalize_participant_type


class RoomAdmissionCoordinator:
    """Own the ordered mutations that turn valid invite evidence into membership.

    The coordinator is intentionally distinct from ``RoomAdmissionService``:
    preflight answers what the browser should show without side effects, while
    this object consumes admission authority and writes canonical state.
    Durable phase resumption is added at this boundary in the next slice.
    """

    def __init__(
        self,
        *,
        invites: InviteApplicationService,
        sessions: RoomSessionService,
        identities: IdentityBackend,
        rooms: RoomRepository,
        participant_suffix: Callable[[], str] | None = None,
    ) -> None:
        self._invites = invites
        self._sessions = sessions
        self._identities = identities
        self._rooms = rooms
        self._participant_suffix = participant_suffix or (lambda: secrets.token_hex(3))

    def admit(
        self,
        *,
        invite_token: str,
        meeting_id: str = "",
        display_name: str = "",
        device_token: str = "",
        participant_type: str = "",
        owner_display_name: str = "",
    ) -> dict[str, object]:
        prepared = self._invites.prepare_admission(
            invite_token,
            meeting_id=meeting_id,
        )
        if isinstance(prepared, dict):
            return prepared

        room = self._rooms.room(prepared.meeting_id)
        if not room:
            return {"status": "rejected", "reason": "room_unavailable"}
        settings = self._rooms.room_settings(prepared.meeting_id)

        resolved_type = (
            normalize_participant_type(participant_type, default="")
            or prepared.participant_type
        )
        if prepared.client_type == "agent_bridge":
            resolved_type = "remote"
        stable_user = self._resolve_stable_user(
            prepared,
            device_token=device_token,
            display_name=display_name,
            participant_type=resolved_type,
        )
        participant_id = self._participant_id(prepared, stable_user)
        resolved_name = (
            clean_lobby_text(display_name, limit=128)
            or clean_lobby_text((stable_user or {}).get("display_name"), limit=128)
            or prepared.display_name
            or prepared.base_agent_id
        )
        clean_owner_name = clean_lobby_text(owner_display_name, limit=64)

        consume_error = self._invites.consume(prepared)
        if consume_error:
            return {"status": "rejected", "reason": consume_error}

        connection_kind = (
            "native_cli_bridge"
            if prepared.client_type == "agent_bridge"
            else NATIVE_REMOTE_ROOM_CLIENT_KIND
        )
        session_token, session = self._sessions.issue(
            {
                "agent_id": participant_id,
                "display_name": resolved_name,
                "meeting_id": prepared.meeting_id,
                "invite_scope": prepared.invite_scope,
                "participant_type": resolved_type,
                "client_type": prepared.client_type,
                "provider_kind": prepared.provider_kind,
                "owner_id": prepared.created_by_user_id,
                "connection_kind": connection_kind,
            }
        )
        self._commit_membership(
            prepared,
            participant_id=participant_id,
            display_name=resolved_name,
            participant_type=resolved_type,
            connection_kind=connection_kind,
        )

        return {
            "status": "admitted",
            "session_token": session_token,
            "agent_id": participant_id,
            "display_name": resolved_name,
            "meeting_id": prepared.meeting_id,
            "invite_scope": prepared.invite_scope,
            "participant_type": resolved_type,
            "client_type": prepared.client_type,
            "provider_kind": prepared.provider_kind,
            "owner_display_name": clean_owner_name,
            "owner_id": prepared.created_by_user_id,
            "stable_identity": stable_user is not None,
            "operator": bool(stable_user and stable_user.get("is_operator")),
            "connection_kind": connection_kind,
            "expires_at": str(session.get("expires_at") or ""),
            "room_label": clean_lobby_text(
                settings.get("label") or room.get("label"),
                limit=128,
            )
            or prepared.meeting_id,
            "room_topic": clean_lobby_text(
                settings.get("topic") or room.get("topic"),
                limit=160,
            ),
            "room_created_at": clean_lobby_text(room.get("created_at"), limit=64),
            "guide": self._invites.usage_guide(
                prepared,
                participant_id=participant_id,
                display_name=resolved_name,
                owner_display_name=clean_owner_name,
            ),
        }

    def _resolve_stable_user(
        self,
        prepared: PreparedInviteAdmission,
        *,
        device_token: str,
        display_name: str,
        participant_type: str,
    ) -> dict[str, object] | None:
        if not prepared.reusable:
            return None
        auth_key = device_auth_key(device_token)
        if not auth_key:
            return None
        return self._identities.resolve_credential_user(
            auth_key,
            provider="device",
            display_name=display_name,
            participant_type=participant_type,
        )

    def _participant_id(
        self,
        prepared: PreparedInviteAdmission,
        stable_user: dict[str, object] | None,
    ) -> str:
        if stable_user is not None:
            return clean_lobby_text(stable_user.get("participant_id"), limit=128)
        if prepared.reusable:
            return f"{prepared.base_agent_id or 'guest'}-{self._participant_suffix()}"
        return prepared.base_agent_id

    def _commit_membership(
        self,
        prepared: PreparedInviteAdmission,
        *,
        participant_id: str,
        display_name: str,
        participant_type: str,
        connection_kind: str,
    ) -> None:
        role = "human" if participant_type == "human" else "agent"
        self._rooms.upsert_participant(
            prepared.meeting_id,
            {
                "participant_id": participant_id,
                "display_name": display_name,
                "participant_type": participant_type,
                "role": role,
                "provider_kind": prepared.provider_kind,
                "connection_kind": connection_kind,
                "status": "joined",
                "owner_id": prepared.created_by_user_id,
            },
        )
        self._identities.upsert_membership(
            {
                "meeting_id": prepared.meeting_id,
                "participant_id": participant_id,
                "display_name": display_name,
                "role": role,
                "participant_type": participant_type,
                "provider_kind": prepared.provider_kind,
                "connection_kind": connection_kind,
                "status": "online",
                "is_host": False,
                "source": "room_invite",
            }
        )
