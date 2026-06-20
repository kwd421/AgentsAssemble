from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_store import RoomStore

CommandRunner = Callable[[list[str]], dict[str, object] | subprocess.CompletedProcess[str] | None]


def resume_agent_session_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
) -> dict[str, object]:
    store = RoomStore(output_root)
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("agent"), limit=128)
    session_id = clean_lobby_text(payload.get("session_id") or payload.get("session"), limit=128) or agent_id
    if not room_id:
        raise ValueError("room_id is required.")
    if not agent_id:
        raise ValueError("agent_id is required.")
    if not session_id:
        raise ValueError("session_id is required.")

    room = store.create_room(room_id, label=clean_lobby_text(payload.get("label"), limit=128))
    previous_participant = store.participant(room_id, agent_id)
    previous_session = store.session(room_id, session_id)
    provider_kind = clean_agent_session_provider_kind(
        payload.get("provider_kind") or payload.get("provider") or previous_session.get("provider_kind")
    )
    participant, participant_created = store.upsert_participant(
        room_id,
        {
            "participant_id": agent_id,
            "display_name": clean_lobby_text(payload.get("display_name"), limit=64) or agent_id,
            "role": "agent",
            "participant_type": "local",
            "status": "joined",
            "session_id": session_id,
            "provider_kind": provider_kind,
            "model": clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128),
            "effort": clean_lobby_text(payload.get("effort"), limit=64),
            "sandbox": clean_lobby_text(payload.get("sandbox") or payload.get("codex_sandbox"), limit=64),
            "permissions": clean_lobby_text(payload.get("permissions") or payload.get("permission_option"), limit=64),
        },
    )
    session, session_created = store.upsert_session(
        room_id,
        {
            "session_id": session_id,
            "participant_id": agent_id,
            "display_name": participant["display_name"],
            "status": "attached",
            "provider_kind": provider_kind,
            "model": clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128),
            "effort": clean_lobby_text(payload.get("effort"), limit=64),
            "sandbox": clean_lobby_text(payload.get("sandbox") or payload.get("codex_sandbox"), limit=64),
            "permissions": clean_lobby_text(payload.get("permissions") or payload.get("permission_option"), limit=64),
            "diagnostics": payload.get("diagnostics") if isinstance(payload.get("diagnostics"), list) else [],
        },
    )
    if participant_created or previous_participant.get("status") != "joined":
        store.append_event(room_id, "participant_joined", participant_id=agent_id, session_id=session_id)
    if session_created or previous_session.get("status") not in {"attached", ""}:
        store.append_event(room_id, "session_attached", participant_id=agent_id, session_id=session_id)
    store.append_event(room_id, "session_resumed", participant_id=agent_id, session_id=session_id)
    launch = _agent_session_process_result(store, room_id, agent_id, session, payload, command_runner=command_runner)
    return {
        "status": "resumed",
        "state_status": "resumed",
        **launch,
        "room": room,
        "participant": participant,
        "session": session,
        "participants": store.participants(room_id),
        "sessions": store.sessions(room_id),
    }


def build_room_turn_packet(
    output_root: Path,
    *,
    room_id: str,
    participant_id: str,
    session_id: str,
    instruction: str,
) -> dict[str, object]:
    store = RoomStore(output_root)
    events = store.read_events(room_id)
    session = store.session(room_id, session_id)
    media_manifest = []
    for event in events:
        media = event.get("media")
        if isinstance(media, dict):
            media_manifest.append(dict(media))
    return {
        "room_id": room_id,
        "participant_id": participant_id,
        "session_id": session_id,
        "events": events,
        "media_manifest": media_manifest,
        "current_turn_instruction": clean_lobby_text(instruction, limit=2000),
        "settings": {
            "model": session.get("model", ""),
            "effort": session.get("effort", ""),
            "sandbox": session.get("sandbox", ""),
            "permissions": session.get("permissions", ""),
        },
        "explicit_non_goals": [
            "Do not inspect or edit the project unless the room conversation explicitly asks for it.",
            "Do not access credentials, secret environment variables, or unrelated local files.",
        ],
        "expected_reply_style": "Append one room-visible reply for this turn.",
    }


def room_sse_frames_after_cursor(output_root: Path, room_id: str, *, cursor: str = "") -> list[str]:
    events = RoomStore(output_root).read_events(room_id, after=cursor)
    if not events:
        return ["event: heartbeat\ndata: {}\n\n"]
    frames = []
    for event in events:
        event_type = str(event.get("type") or "message")
        event_id = str(event.get("id") or "")
        lines = []
        if event_id:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event_type}")
        lines.append(f"data: {json.dumps(event, ensure_ascii=False, sort_keys=True)}")
        frames.append("\n".join(lines) + "\n\n")
    return frames


def stream_room_sse_frames(
    output_root: Path,
    room_id: str,
    *,
    cursor: str = "",
    max_iterations: int | None = None,
    wait: Callable[[], None] | None = None,
):
    current_cursor = cursor
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        frames = room_sse_frames_after_cursor(output_root, room_id, cursor=current_cursor)
        for frame in frames:
            event_id = _sse_frame_id(frame)
            if event_id:
                current_cursor = event_id
            yield frame
        iterations += 1
        if wait is not None:
            wait()


def build_agent_session_launch_plan(session: dict[str, object]) -> dict[str, object]:
    provider_kind = clean_lobby_text(session.get("provider_kind"), limit=64)
    model = clean_lobby_text(session.get("model") or session.get("model_id"), limit=128)
    effort = clean_lobby_text(session.get("effort"), limit=64)
    sandbox = clean_lobby_text(session.get("sandbox") or session.get("permissions"), limit=64) or "read-only"
    session_id = clean_lobby_text(session.get("session_id"), limit=128)
    if provider_kind == "codex_live_session":
        command = ["codex", "exec", "resume"]
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["-c", f'model_reasoning_effort="{effort}"'])
        command.extend(["--sandbox", sandbox, "--skip-git-repo-check"])
        if session_id:
            command.append(session_id)
        return {
            "provider_kind": provider_kind,
            "command": command,
            "permission_enforcement": "enforced" if sandbox == "read-only" else "advisory",
            "diagnostics": [],
        }
    return {
        "provider_kind": provider_kind,
        "command": [],
        "permission_enforcement": "unsupported",
        "diagnostics": [
            {
                "setting": "launch",
                "status": "unsupported",
                "message": "This Agent Session provider has no verified launch/resume setting mapping yet.",
            }
        ],
    }


def clean_agent_session_provider_kind(value: object) -> str:
    provider = clean_lobby_text(value, limit=64)
    aliases = {
        "codex": "codex_live_session",
        "codex-cli": "codex_live_session",
        "codex_cli": "codex_live_session",
    }
    return aliases.get(provider, provider)


def _agent_session_process_result(
    store: RoomStore,
    room_id: str,
    agent_id: str,
    session: dict[str, object],
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None,
) -> dict[str, object]:
    launch_plan = build_agent_session_launch_plan(session)
    diagnostics = list(launch_plan.get("diagnostics") if isinstance(launch_plan.get("diagnostics"), list) else [])
    if not session.get("provider_kind"):
        diagnostics.append(
            {
                "setting": "provider_kind",
                "status": "missing",
                "message": "No provider was supplied or persisted; Agent Session state was attached only.",
            }
        )
        return {"process_status": "not_started", "launch_plan": launch_plan, "diagnostics": diagnostics}
    if launch_plan.get("permission_enforcement") == "unsupported":
        return {"process_status": "unsupported", "launch_plan": launch_plan, "diagnostics": diagnostics}
    if not bool(payload.get("start")):
        diagnostics.append(
            {
                "setting": "start",
                "status": "not_started",
                "message": "Agent Session state was attached; no provider process was requested.",
            }
        )
        return {"process_status": "not_started", "launch_plan": launch_plan, "diagnostics": diagnostics}
    if bool(payload.get("dry_run")):
        diagnostics.append(
            {
                "setting": "dry_run",
                "status": "not_started",
                "message": "Dry run returned the launch plan without starting the provider.",
            }
        )
        return {"process_status": "not_started", "launch_plan": launch_plan, "diagnostics": diagnostics}
    command = launch_plan.get("command") if isinstance(launch_plan.get("command"), list) else []
    if not command_runner:
        diagnostics.append(
            {
                "setting": "command_runner",
                "status": "not_started",
                "message": "No command runner was provided; real provider execution is opt-in.",
            }
        )
        return {"process_status": "not_started", "launch_plan": launch_plan, "diagnostics": diagnostics}
    try:
        result = command_runner([str(part) for part in command])
    except Exception as error:  # pragma: no cover - for injected launchers
        diagnostics.append({"setting": "launch", "status": "failed", "message": str(error)})
        return {"process_status": "failed", "launch_plan": launch_plan, "diagnostics": diagnostics}
    returncode = getattr(result, "returncode", None)
    if isinstance(result, dict):
        returncode = result.get("returncode", returncode)
    if returncode not in (0, None):
        diagnostics.append({"setting": "launch", "status": "failed", "message": f"provider command exited {returncode}"})
        return {"process_status": "failed", "launch_plan": launch_plan, "diagnostics": diagnostics}
    store.append_event(room_id, "process_resumed", participant_id=agent_id, session_id=session.get("session_id"))
    return {"process_status": "resumed", "launch_plan": launch_plan, "diagnostics": diagnostics}


def _sse_frame_id(frame: str) -> str:
    for line in frame.splitlines():
        if line.startswith("id:"):
            return line.removeprefix("id:").strip()
    return ""


def room_status_payload(output_root: Path, room_id: str) -> dict[str, object]:
    store = RoomStore(output_root)
    payload = store.room_payload(room_id)
    payload["active_participants"] = store.active_participants(room_id)
    return payload


def room_action_payload(output_root: Path, payload: dict[str, object], action: str) -> dict[str, object]:
    store = RoomStore(output_root)
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    participant_id = clean_lobby_text(payload.get("participant_id") or payload.get("agent_id"), limit=128)
    reason = clean_lobby_text(payload.get("reason"), limit=500)
    if action == "leave":
        participant = store.set_participant_status(room_id, participant_id, "left", reason=reason)
        return {"status": "left", "participant": participant, **room_status_payload(output_root, room_id)}
    if action == "kick":
        participant = store.set_participant_status(room_id, participant_id, "kicked", reason=reason)
        return {"status": "kicked", "participant": participant, **room_status_payload(output_root, room_id)}
    if action == "export":
        result = store.export_participant(room_id, participant_id, reason=reason)
        return {"status": "exported", **result, **room_status_payload(output_root, room_id)}
    raise ValueError(f"Unsupported room action: {action}")


def room_lifecycle_payload(output_root: Path, payload: dict[str, object], action: str) -> dict[str, object]:
    store = RoomStore(output_root)
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    status = "archived" if action == "archive" else "closed"
    room = store.set_room_status(room_id, status)
    return {"status": status, "room": room, **room_status_payload(output_root, room_id)}


def active_room_members(output_root: Path, room_id: str) -> list[dict[str, object]]:
    return RoomStore(output_root).active_participants(room_id)


def merge_room_store_members(output_root: Path, meeting_id: str, existing_members: list[dict[str, object]]) -> list[dict[str, object]]:
    if not meeting_id:
        return existing_members
    store = RoomStore(output_root)
    participants = store.participants(meeting_id)
    room_participant_ids = {str(participant.get("participant_id") or "") for participant in participants}
    active = [
        participant
        for participant in participants
        if str(participant.get("status") or "") == "joined"
    ]
    by_id: dict[str, dict[str, object]] = {
        str(member.get("participant_id") or ""): dict(member)
        for member in existing_members
        if str(member.get("participant_id") or "") not in room_participant_ids
    }
    for participant in active:
        participant_id = str(participant.get("participant_id") or "")
        existing = next(
            (
                member
                for member in existing_members
                if str(member.get("participant_id") or "") == participant_id
            ),
            {},
        )
        by_id[str(participant.get("participant_id") or "")] = {
            "meeting_id": meeting_id,
            "participant_id": participant.get("participant_id", ""),
            "display_name": participant.get("display_name", ""),
            "role": participant.get("role", "agent"),
            "participant_type": participant.get("participant_type", "local"),
            "provider_kind": participant.get("provider_kind", ""),
            "connection_kind": "agent_session",
            "status": participant.get("status", ""),
            "source": "agent_session",
            "muted": bool(existing.get("muted", False)),
            "created_at": participant.get("created_at", ""),
            "updated_at": participant.get("updated_at", ""),
            "last_seen_at": participant.get("updated_at", ""),
        }
    return list(by_id.values())


def clean_room_request_payload(value: Any) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
