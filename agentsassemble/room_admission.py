"""Side-effect-free browser identity decisions for room invite startup."""
from __future__ import annotations

from collections.abc import Callable

from agentsassemble.identity_store import IdentityBackend, LOCAL_OPERATOR_PARTICIPANT_ID
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_repository import RoomRepository
from agentsassemble.room_users import device_auth_key

_INACTIVE_MEMBERSHIP_STATUSES = {"kicked", "left", "exported", "removed"}


class RoomAdmissionService:
    """Resolve invite startup state without consuming or creating anything."""

    def __init__(
        self,
        *,
        identities: IdentityBackend,
        rooms: RoomRepository,
        invite_inspector: Callable[..., dict[str, object]],
    ) -> None:
        self._identities = identities
        self._rooms = rooms
        self._inspect_invite = invite_inspector

    def resolve(
        self,
        *,
        invite_token: str,
        device_token: str = "",
        session: dict[str, object] | None = None,
    ) -> dict[str, object]:
        invite = self._inspect_invite(invite_token)
        if invite.get("status") != "valid":
            reason = clean_lobby_text(invite.get("reason"), limit=64) or "invite_invalid"
            return {
                "status": "invite_expired" if reason == "token_expired" else "invite_invalid",
                "reason": reason,
                "can_auto_join": False,
            }

        room_id = clean_lobby_text(invite.get("meeting_id"), limit=128)
        room = self._rooms.room(room_id) if room_id else {}
        if not room:
            return {
                "status": "invite_invalid",
                "reason": "room_unavailable",
                "can_auto_join": False,
            }

        base = {
            "room_id": room_id,
            "room_label": clean_lobby_text(room.get("label"), limit=128) or room_id,
            "invite_scope": clean_lobby_text(invite.get("invite_scope"), limit=32) or "room",
        }
        if session and clean_lobby_text(session.get("meeting_id"), limit=128) == room_id:
            participant_id = clean_lobby_text(session.get("agent_id"), limit=128)
            return {
                **base,
                "status": "existing_session",
                "can_auto_join": True,
                "participant": self._participant_profile(
                    room_id,
                    participant_id,
                    session=session,
                ),
                "operator": bool(
                    participant_id == LOCAL_OPERATOR_PARTICIPANT_ID
                    and self._identities.participant_is_operator(participant_id)
                ),
            }

        auth_key = device_auth_key(device_token)
        user = self._identities.user_for_credential(auth_key) if auth_key else None
        if not user:
            return {
                **base,
                "status": "profile_required",
                "can_auto_join": False,
            }

        participant_id = clean_lobby_text(user.get("participant_id"), limit=128)
        participant = self._rooms.participant(room_id, participant_id) or {}
        membership = self._identities.get_membership(room_id, participant_id) or {}
        participant_status = clean_lobby_text(participant.get("status"), limit=32).lower()
        membership_status = clean_lobby_text(membership.get("status"), limit=32).lower()
        existing_member = bool(participant or membership) and not (
            participant_status in _INACTIVE_MEMBERSHIP_STATUSES
            or membership_status in _INACTIVE_MEMBERSHIP_STATUSES
        )
        return {
            **base,
            "status": "existing_member" if existing_member else "known_user",
            "can_auto_join": True,
            "participant": self._participant_profile(
                room_id,
                participant_id,
                user=user,
            ),
            "operator": bool(
                participant_id == LOCAL_OPERATOR_PARTICIPANT_ID and user.get("is_operator")
            ),
        }

    def _participant_profile(
        self,
        room_id: str,
        participant_id: str,
        *,
        user: dict[str, object] | None = None,
        session: dict[str, object] | None = None,
    ) -> dict[str, object]:
        participant = self._rooms.participant(room_id, participant_id) or {}
        resolved_user = user or self._identities.user_for_participant(participant_id) or {}
        return {
            "participant_id": participant_id,
            "display_name": clean_lobby_text(
                participant.get("display_name")
                or resolved_user.get("display_name")
                or (session or {}).get("display_name"),
                limit=128,
            )
            or participant_id,
            "avatar_image_url": clean_lobby_text(
                participant.get("avatar_image_url") or resolved_user.get("avatar_image_url"),
                limit=2048,
            ),
        }
