"""Bounded durable record validation for room admission workflows."""
from __future__ import annotations

from agentsassemble.room.text import clean_room_text


def validate_admission_workflow_record(
    value: object,
    *,
    workflow_id: str,
) -> dict[str, object]:
    """Return the durable allowlisted workflow record or reject it."""

    source = value if isinstance(value, dict) else {}
    record: dict[str, object] = {
        "workflow_id": clean_room_text(source.get("workflow_id") or workflow_id, limit=128),
        "request_id": clean_room_text(source.get("request_id"), limit=128),
        "token_fingerprint": clean_room_text(source.get("token_fingerprint"), limit=128),
        "device_auth_key": clean_room_text(source.get("device_auth_key"), limit=128),
        "payload_hash": clean_room_text(source.get("payload_hash"), limit=128),
        "status": clean_room_text(source.get("status"), limit=64),
        "resume_phase": clean_room_text(source.get("resume_phase"), limit=64),
        "invite_id": clean_room_text(source.get("invite_id"), limit=128),
        "room_id": clean_room_text(source.get("room_id"), limit=128),
        "base_agent_id": clean_room_text(source.get("base_agent_id"), limit=64),
        "invite_display_name": clean_room_text(
            source.get("invite_display_name"),
            limit=128,
        ),
        "invite_scope": clean_room_text(source.get("invite_scope"), limit=32),
        "participant_type": clean_room_text(source.get("participant_type"), limit=32),
        "client_type": clean_room_text(source.get("client_type"), limit=32),
        "provider_kind": clean_room_text(source.get("provider_kind"), limit=64),
        "owner_id": clean_room_text(source.get("owner_id"), limit=128),
        "reusable": bool(source.get("reusable")),
        "max_uses": max(0, int(source.get("max_uses", 1) or 0)),
        "nonce_fingerprint": clean_room_text(
            source.get("nonce_fingerprint"),
            limit=128,
        ),
        "invite_consumed": bool(source.get("invite_consumed")),
        "participant_id": clean_room_text(source.get("participant_id"), limit=128),
        "display_name": clean_room_text(source.get("display_name"), limit=128),
        "owner_display_name": clean_room_text(
            source.get("owner_display_name"),
            limit=64,
        ),
        "connection_kind": clean_room_text(source.get("connection_kind"), limit=64),
        "stable_identity": bool(source.get("stable_identity")),
        "operator": bool(source.get("operator")),
        "principal_user_id": clean_room_text(
            source.get("principal_user_id"),
            limit=128,
        ),
        "session_joined_at": clean_room_text(
            source.get("session_joined_at"),
            limit=64,
        ),
        "session_expires_at": clean_room_text(
            source.get("session_expires_at"),
            limit=64,
        ),
        "room_label": clean_room_text(source.get("room_label"), limit=128),
        "room_topic": clean_room_text(source.get("room_topic"), limit=160),
        "room_created_at": clean_room_text(source.get("room_created_at"), limit=64),
        "failure_code": clean_room_text(source.get("failure_code"), limit=128),
        "compensation_status": clean_room_text(
            source.get("compensation_status"),
            limit=64,
        ),
        "compensation_failure_code": clean_room_text(
            source.get("compensation_failure_code"),
            limit=128,
        ),
        "session_compensated": bool(source.get("session_compensated")),
        "membership_compensated": bool(source.get("membership_compensated")),
        "invite_consumption_retained": bool(source.get("invite_consumption_retained")),
        "compensated_at": clean_room_text(source.get("compensated_at"), limit=64),
        "created_at": clean_room_text(source.get("created_at"), limit=64),
        "updated_at": clean_room_text(source.get("updated_at"), limit=64),
    }
    if (
        not record["workflow_id"]
        or not record["request_id"]
        or not record["token_fingerprint"]
        or not record["payload_hash"]
        or not record["status"]
    ):
        raise ValueError("admission workflow is missing required fields")
    return record


__all__ = ["validate_admission_workflow_record"]
