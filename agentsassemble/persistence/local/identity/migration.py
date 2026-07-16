"""One-time identity imports from the pre-SQLite JSON stores."""
from __future__ import annotations

import json
from pathlib import Path

from agentsassemble.identity.repository import IdentityBackend


def migrate_legacy_members_json(
    store: IdentityBackend,
    members_json_path: Path,
) -> int:
    """Import room_members.json roster rows; returns how many were imported."""

    try:
        payload = json.loads(members_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, list):
        return 0
    imported = 0
    for member in members:
        if not isinstance(member, dict) or not member.get("participant_id"):
            continue
        try:
            store.upsert_membership(member)
            imported += 1
        except ValueError:
            continue
    return imported


def migrate_legacy_users_json(
    store: IdentityBackend,
    users_json_path: Path,
) -> int:
    """Import users.json identities and device credentials."""

    try:
        payload = json.loads(users_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    users = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users, dict):
        return 0
    imported = 0
    for auth_key, record in users.items():
        if not isinstance(record, dict) or not record.get("user_id"):
            continue
        resolved = store.resolve_credential_user(
            str(auth_key),
            provider=str(record.get("auth_provider") or "device"),
            user_id=str(record.get("user_id") or ""),
            participant_id=str(record.get("participant_id") or ""),
            display_name=str(record.get("display_name") or ""),
            avatar_image_url=str(record.get("avatar_image_url") or ""),
            participant_type=str(record.get("participant_type") or ""),
        )
        if resolved:
            imported += 1
    return imported


__all__ = ["migrate_legacy_members_json", "migrate_legacy_users_json"]
