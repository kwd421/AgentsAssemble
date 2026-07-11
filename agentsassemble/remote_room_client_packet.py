from __future__ import annotations

from agentsassemble.meeting_events import clean_lobby_text


def build_remote_room_client_packet(
    *,
    room_url: object,
    invite_token: object,
    meeting_id: object,
    agent_id: object,
    display_name: object,
    expires_at: object = "",
    join_url: object = "",
    invite_use: str = "single_use",
) -> dict[str, object]:
    del room_url
    del invite_token
    normalized_meeting_id = clean_lobby_text(meeting_id, limit=128)
    normalized_agent_id = clean_lobby_text(agent_id, limit=64)
    normalized_display_name = clean_lobby_text(display_name or agent_id, limit=128)
    normalized_expires_at = clean_lobby_text(expires_at, limit=64)
    normalized_join_url = clean_lobby_text(join_url, limit=4096)
    return {
        "status": "generated",
        "packet_kind": "agent_attendee_entry_packet",
        "agent": {
            "agent_id": normalized_agent_id,
            "display_name": normalized_display_name,
            "meeting_id": normalized_meeting_id,
            "connection_kind": "canonical_room_websocket",
        },
        "entry_contract": {
            "mode": "agent_owned_attendee",
            "room_role": "room_participant",
            "provider_context": "remote_provider_owned",
            "host_prompt_injection": "not_required",
            "transport": "canonical_websocket",
        },
        "admission_contract": {
            "identity_proof": "hmac_sha256_invite_token",
            "invite_use": clean_lobby_text(invite_use, limit=32) or "single_use",
            "session_token": "held_only_by_attendee_process",
            "session_scope": "one_room_websocket",
            "provider_execution": "not_started_by_invite",
        },
        "attend": {
            "command": "assemble room attend --provider <provider>",
            "invite_input": "hidden_stdin",
            "live_transport": "websocket_push",
            "full_transcript_replay": False,
        },
        "instructions": [
            "Run the attendee command and paste the invite URL into its hidden prompt.",
            "The attendee process keeps the canonical WebSocket and provider session alive.",
            "The model receives only room guidance and bounded recent conversation, never credentials or backend details.",
        ],
        "browser_join_url": normalized_join_url,
        "join_url": normalized_join_url,
        "expires_at": normalized_expires_at,
        "safety": {
            "contains_invite_token": False,
            "contains_session_token": False,
            "provider_executed": False,
            "host_filesystem_granted": False,
            "room_url_exposed_to_model": False,
            "official_record_write": False,
        },
    }
