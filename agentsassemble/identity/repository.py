"""Storage-independent identity contracts and normalization helpers."""
from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from agentsassemble.room.text import clean_room_text
from agentsassemble.room.user_preferences import RoomUserPreferencesRecord

LOCAL_OPERATOR_USER_ID = "operator-local-user"
LOCAL_OPERATOR_PARTICIPANT_ID = "operator-local"
OPERATOR_PAIRING_REDEMPTION_STATUSES = {
    "claiming",
    "completed",
    "failed_retryable",
}
PARTICIPANT_TYPES = {
    "human",
    "subscription_ai",
    "api",
    "local",
    "remote",
    "unknown",
}


def normalize_participant_type(value: object, default: str = "human") -> str:
    cleaned = clean_room_text(value, limit=32).lower()
    aliases = {
        "agent": "remote",
        "ai": "remote",
        "bot": "remote",
        "person": "human",
        "user": "human",
    }
    cleaned = aliases.get(cleaned, cleaned)
    return cleaned if cleaned in PARTICIPANT_TYPES else default


def device_auth_key(device_token: str) -> str:
    """Fingerprint a client-held device credential without persisting it raw."""

    token = str(device_token or "").strip()
    if len(token) < 8:
        return ""
    return "device:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]


@runtime_checkable
class IdentityBackend(Protocol):
    """Storage contract for users, credentials, memberships, and usage.

    Implementations preserve the same record shapes and merge semantics:
    non-empty incoming identity and membership values may update saved values,
    while blank values must not erase existing profile data.
    """

    def count_users(self) -> int: ...
    def count_memberships(self) -> int: ...
    def server_id(self) -> str: ...
    def external_account_for_user(self, user_id: str) -> dict[str, object] | None: ...
    def user_for_external_account(
        self,
        provider: str,
        subject_fingerprint: str,
    ) -> dict[str, object] | None: ...
    def connect_external_account(
        self,
        user_id: str,
        *,
        account_id: str,
        provider: str,
        subject_fingerprint: str,
        display_name: str = "",
        email: str = "",
        avatar_image_url: str = "",
        connected_at: str,
    ) -> dict[str, object]: ...
    def bind_credential_to_user(
        self,
        user_id: str,
        *,
        auth_key: str,
        provider: str,
        used_at: str,
    ) -> dict[str, object]: ...
    def create_recovery_code(
        self,
        *,
        user_id: str,
        token_fingerprint: str,
        created_at: str,
    ) -> None: ...
    def recovery_code_user(self, token_fingerprint: str) -> dict[str, object] | None: ...
    def consume_recovery_code(
        self,
        *,
        token_fingerprint: str,
        auth_key: str,
        replacement_fingerprint: str,
        used_at: str,
    ) -> dict[str, object] | None: ...
    def user_for_credential(self, auth_key: str) -> dict[str, object] | None: ...
    def get_user(self, user_id: str) -> dict[str, object] | None: ...
    def user_for_participant(self, participant_id: str) -> dict[str, object] | None: ...
    def user_profile(self, user_id: str) -> dict[str, object] | None: ...
    def update_user_profile(
        self,
        user_id: str,
        profile: dict[str, object],
    ) -> dict[str, object]: ...
    def resolve_credential_user(
        self,
        auth_key: str,
        *,
        provider: str = "device",
        user_id: str = "",
        participant_id: str = "",
        display_name: str = "",
        avatar_image_url: str = "",
        participant_type: str = "",
    ) -> dict[str, object] | None: ...
    def set_user_operator(self, user_id: str, is_operator: bool) -> bool: ...
    def claim_local_operator_credential(
        self,
        auth_key: str,
        *,
        provider: str = "device",
        display_name: str = "",
    ) -> dict[str, object] | None: ...
    def create_operator_pairing(
        self,
        *,
        pairing_id: str,
        token_fingerprint: str,
        room_id: str,
        target_origin: str,
        created_at: str,
        expires_at: str,
    ) -> dict[str, object]: ...
    def operator_pairing_for_fingerprint(
        self,
        token_fingerprint: str,
    ) -> dict[str, object] | None: ...
    def consume_operator_pairing(
        self,
        *,
        token_fingerprint: str,
        target_origin: str,
        auth_key: str,
        used_at: str,
    ) -> dict[str, object]: ...
    def update_operator_pairing_redemption(
        self,
        *,
        pairing_id: str,
        auth_key: str,
        status: str,
        completed_at: str = "",
        session_fingerprint: str = "",
        failure_code: str = "",
    ) -> dict[str, object] | None: ...
    def revoke_operator_pairing(self, pairing_id: str, *, revoked_at: str) -> bool: ...
    def participant_is_operator(self, participant_id: str) -> bool: ...
    def operator_user_id(self) -> str: ...
    def list_memberships(self, meeting_id: str = "") -> list[dict[str, object]]: ...
    def get_membership(
        self,
        meeting_id: str,
        participant_id: str,
    ) -> dict[str, object] | None: ...
    def upsert_membership(self, record: dict[str, object]) -> dict[str, object]: ...
    def remove_membership(self, meeting_id: str, participant_id: str) -> bool: ...
    def set_membership_muted(
        self,
        meeting_id: str,
        participant_id: str,
        muted: bool,
    ) -> dict[str, object]: ...
    def membership_muted(self, meeting_id: str, participant_id: str) -> bool: ...
    def upsert_room(
        self,
        *,
        room_id: str,
        room_uid: str = "",
        owner_id: str = "",
        label: str = "",
        origin: str = "",
    ) -> dict[str, object]: ...
    def list_rooms(
        self,
        *,
        owner_id: str = "",
        include_archived: bool = False,
    ) -> list[dict[str, object]]: ...
    def get_room(self, room_id: str) -> dict[str, object] | None: ...
    def set_room_archived(self, room_id: str, archived: bool) -> bool: ...
    def touch_room(self, room_id: str) -> None: ...
    def delete_room(self, room_id: str) -> bool: ...
    def room_preferences(
        self,
        user_id: str,
        room_id: str,
    ) -> RoomUserPreferencesRecord: ...
    def update_room_preferences(
        self,
        user_id: str,
        room_id: str,
        updates: dict[str, object],
    ) -> RoomUserPreferencesRecord: ...
    def record_usage(self, event: dict[str, object]) -> None: ...
    def usage_summary(
        self,
        *,
        user_id: str = "",
        meeting_id: str = "",
        since: str = "",
    ) -> dict[str, object]: ...


__all__ = [
    "IdentityBackend",
    "LOCAL_OPERATOR_PARTICIPANT_ID",
    "LOCAL_OPERATOR_USER_ID",
    "OPERATOR_PAIRING_REDEMPTION_STATUSES",
    "PARTICIPANT_TYPES",
    "device_auth_key",
    "normalize_participant_type",
]
