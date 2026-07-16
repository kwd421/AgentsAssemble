"""Stable user identity for room participants (SQLite-backed).

Guests used to get a fresh participant id on every join (reusable invites mint
unique ids), so one person re-entering after session expiry appeared as three
different members. This module maps a client-held device token to one stable
user record, so the same browser/AI always resolves to the same participant id
and remembered profile.

Identity model (Google login lands later in the same tables):
- auth provider "device": key is a SHA-256 fingerprint of the client's device
  token (raw tokens are never persisted), generated once by the client and
  reused forever.
- future "google": key will be the Google account subject id.

Storage moved from users.json to identity.db (see identity_store.py); a legacy
users.json next to the configured path is imported once into an empty DB.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from agentsassemble.identity.repository import (
    IdentityBackend,
    LOCAL_OPERATOR_PARTICIPANT_ID,
    PARTICIPANT_TYPES,
    device_auth_key,
    normalize_participant_type,
)
from agentsassemble.identity_store import (
    IDENTITY_DB_FILENAME,
    identity_store_at,
    migrate_legacy_users_json,
)

_state_lock = threading.Lock()
_store: IdentityBackend | None = None
_ephemeral_dir: tempfile.TemporaryDirectory | None = None


def _clear_store_locked() -> None:
    global _store, _ephemeral_dir
    ephemeral_dir = _ephemeral_dir
    _store = None
    _ephemeral_dir = None
    if ephemeral_dir is not None:
        ephemeral_dir.cleanup()


def configure_room_users_store(path: str | os.PathLike[str] | None) -> None:
    """Point identity storage at a DB path (or a legacy users.json location).

    Given a *.json path (the old store convention), the DB lives alongside it
    as identity.db and the JSON is imported once while the DB is still empty.
    """
    global _store, _ephemeral_dir
    with _state_lock:
        _clear_store_locked()
        if not path:
            return
        target = Path(path)
        if target.suffix == ".json":
            db_path = target.with_name(IDENTITY_DB_FILENAME)
            legacy_candidates = [target]
        else:
            db_path = target
            # Cover both historical users.json locations relative to the DB.
            legacy_candidates = [
                target.with_name("users.json"),
                target.parent / ".agentsassemble" / "users.json",
            ]
        _store = identity_store_at(db_path)
        if _store.count_users() == 0:
            for legacy_json in legacy_candidates:
                if migrate_legacy_users_json(_store, legacy_json):
                    break


def configure_room_users_backend(store: IdentityBackend | None) -> None:
    """Use the server-selected identity authority for module-level helpers."""

    global _store
    with _state_lock:
        _clear_store_locked()
        _store = store


def release_room_users_backend(store: IdentityBackend) -> bool:
    """Release only the backend owned by the stopping server instance."""

    global _store
    with _state_lock:
        if _store is not store:
            return False
        _store = None
        return True


def default_room_users_store_path(output_root: Path) -> Path:
    return output_root / IDENTITY_DB_FILENAME


def _active_store() -> IdentityBackend:
    """The configured store, or an ephemeral one (unconfigured = no persistence)."""
    global _store, _ephemeral_dir
    if _store is None:
        _ephemeral_dir = tempfile.TemporaryDirectory(prefix="agentsassemble-users-")
        _store = identity_store_at(Path(_ephemeral_dir.name) / IDENTITY_DB_FILENAME)
    return _store


def resolve_device_user(
    device_token: str,
    *,
    display_name: str = "",
    avatar_image_url: str = "",
    participant_type: str = "",
) -> dict[str, object] | None:
    """Return the stable user for this device token, creating/refreshing it.

    Returns None when the token is missing/too short (caller falls back to
    legacy per-join identity).
    """
    auth_key = device_auth_key(device_token)
    if not auth_key:
        return None
    with _state_lock:
        return _active_store().resolve_credential_user(
            auth_key,
            provider="device",
            display_name=display_name,
            avatar_image_url=avatar_image_url,
            participant_type=normalize_participant_type(participant_type, default=""),
        )


def user_for_device_token(device_token: str) -> dict[str, object] | None:
    """Look up (without creating) the user behind a device token."""
    auth_key = device_auth_key(device_token)
    if not auth_key:
        return None
    with _state_lock:
        return _active_store().user_for_credential(auth_key)


def participant_is_operator(participant_id: str) -> bool:
    """True when this participant id belongs to the server operator's account."""
    with _state_lock:
        if _store is None:
            return False
        return _store.participant_is_operator(participant_id)


def user_for_participant(participant_id: str) -> dict[str, object] | None:
    with _state_lock:
        if _store is None:
            return None
        return _store.user_for_participant(participant_id)


def operator_user_id() -> str:
    with _state_lock:
        if _store is None:
            return ""
        return _store.operator_user_id()


def upsert_room(*, room_id: str, owner_id: str = "", label: str = "", origin: str = "") -> dict[str, object]:
    with _state_lock:
        return _active_store().upsert_room(
            room_id=room_id,
            owner_id=owner_id,
            label=label,
            origin=origin,
        )


def list_rooms(*, owner_id: str = "", include_archived: bool = False) -> list[dict[str, object]]:
    with _state_lock:
        return _active_store().list_rooms(owner_id=owner_id, include_archived=include_archived)


def get_room(room_id: str) -> dict[str, object] | None:
    with _state_lock:
        return _active_store().get_room(room_id)


def set_room_archived(room_id: str, archived: bool) -> bool:
    with _state_lock:
        return _active_store().set_room_archived(room_id, archived)


def touch_room(room_id: str) -> None:
    with _state_lock:
        if _store is None:
            return
        _store.touch_room(room_id)


def grant_operator_to_device(device_token: str, *, display_name: str = "") -> dict[str, object] | None:
    """Attach a host-authorized device to the one local operator identity."""
    auth_key = device_auth_key(device_token)
    if not auth_key:
        return None
    with _state_lock:
        store = _active_store()
        return store.claim_local_operator_credential(
            auth_key,
            provider="device",
            display_name=display_name,
        )


def reset_state() -> None:
    """Reset module state. For testing only."""
    global _store, _ephemeral_dir
    with _state_lock:
        _clear_store_locked()
