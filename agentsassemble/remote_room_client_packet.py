from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

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
) -> dict[str, object]:
    normalized_room_url = _normalized_room_url(room_url)
    normalized_invite_token = str(invite_token or "").strip()
    normalized_meeting_id = clean_lobby_text(meeting_id, limit=128)
    normalized_agent_id = clean_lobby_text(agent_id, limit=64)
    normalized_display_name = clean_lobby_text(display_name or agent_id, limit=128)
    normalized_expires_at = clean_lobby_text(expires_at, limit=64)
    normalized_join_url = clean_lobby_text(join_url, limit=512)
    endpoints = _room_endpoints(normalized_room_url)
    return {
        "status": "generated",
        "packet_kind": "native_remote_room_client_entry_packet",
        "agent": {
            "agent_id": normalized_agent_id,
            "display_name": normalized_display_name,
            "meeting_id": normalized_meeting_id,
            "connection_kind": "native_remote_room_client",
        },
        "entry_contract": {
            "mode": "remote_client_owned",
            "room_role": "remote_lobby_participant",
            "provider_context": "remote_provider_owned",
            "host_prompt_injection": "not_required",
            "session_token_source": "api_room_invite_join",
        },
        "admission_contract": {
            "identity_proof": "hmac_sha256_invite_token",
            "invite_use": "single_use",
            "session_token": "issued_after_join",
            "session_scope": "one_meeting_room_api",
            "provider_execution": "not_started_by_invite",
        },
        "execution_contract": {
            "join_semantics": "native_remote_room_client",
            "context_durability": "remote_owner_managed",
            "sandbox_enforcement": "advisory",
            "provider_execution": "not_started_by_invite",
            "evidence_basis": "host_generated_room_invite",
        },
        "env": {
            "AGENTSASSEMBLE_ROOM_URL": normalized_room_url,
            "AGENTSASSEMBLE_INVITE_TOKEN": normalized_invite_token,
            "AGENTSASSEMBLE_ROOM_SESSION_TOKEN": "<set from join response>",
            "AGENTSASSEMBLE_MEETING_ID": normalized_meeting_id,
            "AGENTSASSEMBLE_AGENT_ID": normalized_agent_id,
            "AGENTSASSEMBLE_DISPLAY_NAME": normalized_display_name,
        },
        "http": {
            "join": {
                "method": "POST",
                "url": endpoints["join"],
                "json": {"invite_token": "$AGENTSASSEMBLE_INVITE_TOKEN"},
                "stores": "session_token -> AGENTSASSEMBLE_ROOM_SESSION_TOKEN",
            },
            "read_lobby": {
                "method": "GET",
                "url": endpoints["lobby"],
                "headers": {"Authorization": "Bearer $AGENTSASSEMBLE_ROOM_SESSION_TOKEN"},
            },
            "events": {
                "method": "GET",
                "url": endpoints["events"],
                "headers": {"Authorization": "Bearer $AGENTSASSEMBLE_ROOM_SESSION_TOKEN"},
            },
            "say": {
                "method": "POST",
                "url": endpoints["say"],
                "headers": {
                    "Authorization": "Bearer $AGENTSASSEMBLE_ROOM_SESSION_TOKEN",
                    "Content-Type": "application/json",
                },
                "json": {"message": "<message>"},
            },
            "leave": {
                "method": "POST",
                "url": endpoints["leave"],
                "headers": {"Authorization": "Bearer $AGENTSASSEMBLE_ROOM_SESSION_TOKEN"},
                "json": {},
            },
        },
        "shell": {
            "join": (
                'curl -fsS -X POST "$AGENTSASSEMBLE_ROOM_URL/api/room-invite/join" '
                "-H 'Content-Type: application/json' "
                '-d "{\\"invite_token\\":\\"$AGENTSASSEMBLE_INVITE_TOKEN\\"}"'
            ),
            "read_lobby": (
                'curl -fsS "$AGENTSASSEMBLE_ROOM_URL/api/room/lobby" '
                '-H "Authorization: Bearer $AGENTSASSEMBLE_ROOM_SESSION_TOKEN"'
            ),
            "say": (
                'curl -fsS -X POST "$AGENTSASSEMBLE_ROOM_URL/api/room/say" '
                '-H "Authorization: Bearer $AGENTSASSEMBLE_ROOM_SESSION_TOKEN" '
                "-H 'Content-Type: application/json' "
                '-d "{\\"message\\":\\"<message>\\"}"'
            ),
            "leave": (
                'curl -fsS -X POST "$AGENTSASSEMBLE_ROOM_URL/api/room-invite/leave" '
                '-H "Authorization: Bearer $AGENTSASSEMBLE_ROOM_SESSION_TOKEN" '
                "-H 'Content-Type: application/json' -d '{}'"
            ),
        },
        "instructions": [
            "This packet is for an already-running AI or remote client controlled by the invite recipient.",
            "Set the env values, call http.join once, and store the returned session_token locally.",
            "Use http.read_lobby or http.events to observe the room before deciding whether to speak.",
            "Use http.say for one visible lobby message at a time; the server enforces the admitted identity.",
            "Use http.leave before intentionally exiting the room.",
            "Do not treat this packet as permission to start provider CLIs, access files, or promote lobby chat to official records.",
        ],
        "browser_join_url": normalized_join_url,
        "expires_at": normalized_expires_at,
        "safety": {
            "contains_invite_token": bool(normalized_invite_token),
            "contains_session_token": False,
            "provider_executed": False,
            "host_filesystem_granted": False,
            "official_record_write": False,
        },
    }


def _normalized_room_url(room_url: object) -> str:
    value = str(room_url or "").strip().rstrip("/") or "http://127.0.0.1:8765"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "http://127.0.0.1:8765"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "http://127.0.0.1:8765"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _room_endpoints(room_url: str) -> dict[str, str]:
    return {
        "join": f"{room_url}/api/room-invite/join",
        "lobby": f"{room_url}/api/room/lobby",
        "events": f"{room_url}/api/room/events",
        "say": f"{room_url}/api/room/say",
        "leave": f"{room_url}/api/room-invite/leave",
    }
