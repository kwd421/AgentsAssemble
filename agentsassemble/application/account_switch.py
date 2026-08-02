"""Confirmed replacement of a temporary guest with an existing public account."""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.identity.accounts import AccountLinkConflict
from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.room.errors import RoomCommandRejected


RoomCommandHandler = Callable[
    [dict[str, object], dict[str, object]],
    dict[str, object],
]


class ConfirmedGuestAccountSwitchService:
    """Retire guest access before moving its current device to an account.

    Public room history remains in the room repository. Mutable guest identity,
    credentials, recovery data, and active room access are removed instead of
    being merged into the destination account.
    """

    def __init__(
        self,
        *,
        identities: IdentityBackend,
        sessions: RoomSessionService,
        handle_room_command: RoomCommandHandler,
    ) -> None:
        self._identities = identities
        self._sessions = sessions
        self._handle_room_command = handle_room_command

    def switch(
        self,
        current_user: dict[str, object],
        target_user: dict[str, object],
        device_auth_key: str,
        switched_at: str,
    ) -> dict[str, object]:
        guest_user_id = str(current_user.get("user_id") or "").strip()
        target_user_id = str(target_user.get("user_id") or "").strip()
        participant_id = str(current_user.get("participant_id") or "").strip()
        if not guest_user_id or not target_user_id or not participant_id or not device_auth_key:
            raise AccountLinkConflict(
                "The current guest identity is incomplete.",
                code="account_switch_unavailable",
            )
        if bool(current_user.get("is_operator")):
            raise AccountLinkConflict(
                "The server operator identity cannot be discarded.",
                code="account_switch_operator_forbidden",
            )
        self._preflight_identity_state(
            guest_user_id=guest_user_id,
            target_user_id=target_user_id,
            device_auth_key=device_auth_key,
        )
        self._reject_guest_room_owners(guest_user_id, participant_id)

        memberships = [
            membership
            for membership in self._identities.list_memberships()
            if str(membership.get("participant_id") or "") == participant_id
        ]
        membership_room_ids = {
            str(membership.get("meeting_id") or "")
            for membership in memberships
            if str(membership.get("meeting_id") or "")
        }
        session_room_ids = {
            str(session.get("meeting_id") or "")
            for session in self._sessions.active_summary()
            if str(session.get("agent_id") or "") == participant_id
            and str(session.get("meeting_id") or "")
        }
        active_room_ids = {
            str(membership.get("meeting_id") or "")
            for membership in memberships
            if str(membership.get("status") or "") not in {"left", "kicked"}
        }
        active_room_ids.update(session_room_ids)

        for room_id in sorted(active_room_ids):
            self._leave_room(
                room_id,
                guest_user_id=guest_user_id,
                participant_id=participant_id,
            )
        for room_id in sorted(membership_room_ids | session_room_ids):
            self._sessions.revoke_participant(room_id, participant_id)

        return self._identities.retire_guest_for_existing_account(
            guest_user_id,
            target_user_id,
            auth_key=device_auth_key,
            switched_at=switched_at,
        )

    def _preflight_identity_state(
        self,
        *,
        guest_user_id: str,
        target_user_id: str,
        device_auth_key: str,
    ) -> None:
        if guest_user_id == target_user_id:
            raise AccountLinkConflict(
                "The current identity is already connected to this account.",
                code="account_switch_unavailable",
            )
        credential_user = self._identities.user_for_credential(device_auth_key)
        if (
            credential_user is None
            or str(credential_user.get("user_id") or "") != guest_user_id
        ):
            raise AccountLinkConflict(
                "The current device no longer belongs to this guest.",
                code="account_switch_unavailable",
            )
        if self._identities.external_account_for_user(guest_user_id) is not None:
            raise AccountLinkConflict(
                "A linked public account cannot be discarded as a guest.",
                code="account_switch_unavailable",
            )
        if self._identities.external_account_for_user(target_user_id) is None:
            raise AccountLinkConflict(
                "The destination public account is no longer linked.",
                code="account_switch_unavailable",
            )

    def _reject_guest_room_owners(self, guest_user_id: str, participant_id: str) -> None:
        owned_rooms = {
            str(room.get("room_id") or "")
            for owner_id in (guest_user_id, participant_id)
            for room in self._identities.list_rooms(
                owner_id=owner_id,
                include_archived=True,
            )
            if str(room.get("room_id") or "")
        }
        if owned_rooms:
            raise AccountLinkConflict(
                "Transfer or delete guest-owned servers before switching accounts.",
                code="account_switch_guest_owns_room",
            )

    def _leave_room(
        self,
        room_id: str,
        *,
        guest_user_id: str,
        participant_id: str,
    ) -> None:
        try:
            self._handle_room_command(
                {
                    "meeting_id": room_id,
                    "agent_id": participant_id,
                    "user_id": guest_user_id,
                    "client_type": "browser",
                    "invite_scope": "room",
                    "operator": False,
                },
                {
                    "request_id": f"account-switch-{uuid4().hex}",
                    "action": "participant.leave",
                    "payload": {},
                },
            )
        except RoomCommandRejected as error:
            if error.code == "not_found":
                return
            raise AccountLinkConflict(
                "Could not leave every active room before switching accounts.",
                code="account_switch_cleanup_failed",
            ) from error
        except RuntimeError as error:
            raise AccountLinkConflict(
                "Could not leave every active room before switching accounts.",
                code="account_switch_cleanup_failed",
            ) from error


__all__ = ["ConfirmedGuestAccountSwitchService"]
