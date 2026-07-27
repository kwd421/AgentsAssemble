"""Canonical create and resume flows for Agent Sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from agentsassemble.application.agent_sessions.compatibility import (
    ensure_legacy_agent_session,
)
from agentsassemble.providers.codex_app_server import (
    clean_agent_session_provider_kind,
    clean_codex_app_server_runtime_sharing_policy,
    clean_provider_session_id,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text as clean_lobby_text


CommandRunner = Callable[[list[str]], object]


class AgentSessionProcessServiceProtocol(Protocol):
    def resume(
        self,
        store: RoomRepository,
        room_id: str,
        agent_id: str,
        session: dict[str, object],
        payload: dict[str, object],
    ) -> dict[str, object]: ...


ProcessServiceFactory = Callable[[CommandRunner | None], AgentSessionProcessServiceProtocol]


def _ensure_no_canonical_session(
    store: RoomRepository,
    room_id: str,
    *,
    agent_id: str,
    session_id: str,
) -> None:
    for session in store.sessions(room_id):
        if (
            str(session.get("session_id") or "") == session_id
            or str(session.get("participant_id") or "") == agent_id
        ):
            ensure_legacy_agent_session(session)


def resume_agent_session(
    output_root: Path,
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
    process_service: AgentSessionProcessServiceProtocol | None = None,
    repository: RoomRepository,
    process_service_factory: ProcessServiceFactory,
) -> dict[str, object]:
    store = repository
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("agent"), limit=128)
    session_id = clean_lobby_text(payload.get("session_id") or payload.get("session"), limit=128) or agent_id
    if not room_id:
        raise ValueError("room_id is required.")
    if not agent_id:
        raise ValueError("agent_id is required.")
    if not session_id:
        raise ValueError("session_id is required.")

    _ensure_no_canonical_session(
        store,
        room_id,
        agent_id=agent_id,
        session_id=session_id,
    )
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
            "workspace": clean_lobby_text(payload.get("workspace") or payload.get("cwd"), limit=300),
            "codex_home": clean_lobby_text(payload.get("codex_home") or payload.get("config_profile"), limit=200),
            "runtime_sharing_policy": clean_codex_app_server_runtime_sharing_policy(payload.get("runtime_sharing_policy")),
        },
    )
    session, session_created = store.upsert_session(
        room_id,
        {
            "session_id": session_id,
            "participant_id": agent_id,
            "provider_session_id": clean_provider_session_id(
                payload.get("provider_session_id") or payload.get("codex_session_id") or previous_session.get("provider_session_id")
            ),
            "display_name": participant["display_name"],
            "status": "attached",
            "provider_kind": provider_kind,
            "model": clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128),
            "effort": clean_lobby_text(payload.get("effort"), limit=64),
            "sandbox": clean_lobby_text(payload.get("sandbox") or payload.get("codex_sandbox"), limit=64),
            "permissions": clean_lobby_text(payload.get("permissions") or payload.get("permission_option"), limit=64),
            "workspace": clean_lobby_text(payload.get("workspace") or payload.get("cwd"), limit=300),
            "codex_home": clean_lobby_text(payload.get("codex_home") or payload.get("config_profile"), limit=200),
            "runtime_sharing_policy": clean_codex_app_server_runtime_sharing_policy(payload.get("runtime_sharing_policy") or previous_session.get("runtime_sharing_policy")),
            "diagnostics": payload.get("diagnostics") if isinstance(payload.get("diagnostics"), list) else [],
        },
    )
    if participant_created or previous_participant.get("status") != "joined":
        store.append_event(room_id, "participant_joined", participant_id=agent_id, session_id=session_id)
    if session_created or previous_session.get("status") not in {"attached", ""}:
        store.append_event(room_id, "session_attached", participant_id=agent_id, session_id=session_id)
    store.append_event(room_id, "session_resumed", participant_id=agent_id, session_id=session_id)
    service = process_service or process_service_factory(command_runner)
    launch = service.resume(store, room_id, agent_id, session, payload)
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


def create_agent_session(
    output_root: Path,
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
    process_service: AgentSessionProcessServiceProtocol | None = None,
    repository: RoomRepository,
    process_service_factory: ProcessServiceFactory,
) -> dict[str, object]:
    store = repository
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("agent"), limit=128)
    session_id = clean_lobby_text(payload.get("session_id") or payload.get("session"), limit=128) or agent_id
    if not room_id:
        raise ValueError("room_id is required.")
    if not agent_id:
        raise ValueError("agent_id is required.")
    if not session_id:
        raise ValueError("session_id is required.")

    _ensure_no_canonical_session(
        store,
        room_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    owner_id = clean_lobby_text(payload.get("owner_id") or payload.get("created_by"), limit=128) or "operator-local"
    created_by = clean_lobby_text(payload.get("created_by") or owner_id, limit=128) or owner_id
    previous_participant = store.participant(room_id, agent_id)
    previous_session = store.session(room_id, session_id)
    provider_kind = clean_agent_session_provider_kind(
        payload.get("provider_kind") or payload.get("provider") or previous_session.get("provider_kind")
    )
    runtime_sharing_policy = clean_codex_app_server_runtime_sharing_policy(
        payload.get("runtime_sharing_policy") or previous_session.get("runtime_sharing_policy")
    )
    room = store.create_room(room_id, label=clean_lobby_text(payload.get("label"), limit=128))
    participant, participant_created = store.upsert_participant(
        room_id,
        {
            "participant_id": agent_id,
            "display_name": clean_lobby_text(payload.get("display_name"), limit=64) or agent_id,
            "role": "agent",
            "participant_type": "local",
            "status": "joined",
            "session_id": session_id,
            "owner_id": owner_id,
            "created_by": created_by,
            "provider_kind": provider_kind,
            "model": clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128),
            "effort": clean_lobby_text(payload.get("effort"), limit=64),
            "sandbox": clean_lobby_text(payload.get("sandbox") or payload.get("codex_sandbox"), limit=64),
            "permissions": clean_lobby_text(payload.get("permissions") or payload.get("permission_option"), limit=64),
            "workspace": clean_lobby_text(payload.get("workspace") or payload.get("cwd"), limit=300),
            "codex_home": clean_lobby_text(payload.get("codex_home") or payload.get("config_profile"), limit=200),
            "runtime_sharing_policy": runtime_sharing_policy,
        },
    )
    session, session_created = store.upsert_session(
        room_id,
        {
            "session_id": session_id,
            "participant_id": agent_id,
            "provider_session_id": clean_provider_session_id(
                payload.get("provider_session_id") or payload.get("codex_session_id") or previous_session.get("provider_session_id")
            ),
            "display_name": participant["display_name"],
            "status": "attached",
            "owner_id": owner_id,
            "created_by": created_by,
            "provider_kind": provider_kind,
            "model": clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128),
            "effort": clean_lobby_text(payload.get("effort"), limit=64),
            "sandbox": clean_lobby_text(payload.get("sandbox") or payload.get("codex_sandbox"), limit=64),
            "permissions": clean_lobby_text(payload.get("permissions") or payload.get("permission_option"), limit=64),
            "workspace": clean_lobby_text(payload.get("workspace") or payload.get("cwd"), limit=300),
            "codex_home": clean_lobby_text(payload.get("codex_home") or payload.get("config_profile"), limit=200),
            "runtime_sharing_policy": runtime_sharing_policy,
            "diagnostics": payload.get("diagnostics") if isinstance(payload.get("diagnostics"), list) else [],
        },
    )
    if participant_created or previous_participant.get("status") != "joined":
        store.append_event(room_id, "participant_joined", participant_id=agent_id, session_id=session_id)
    if session_created or previous_session.get("status") not in {"attached", ""}:
        store.append_event(room_id, "session_attached", participant_id=agent_id, session_id=session_id)
    store.append_event(
        room_id,
        "agent_session_created",
        participant_id=agent_id,
        session_id=session_id,
        owner_id=owner_id,
        created_by=created_by,
    )
    launch = {"process_status": "not_started", "diagnostics": []}
    if bool(payload.get("start")):
        service = process_service or process_service_factory(command_runner)
        launch = service.resume(store, room_id, agent_id, session, payload)
    return {
        "status": "created" if participant_created or session_created else "updated",
        "state_status": "created" if participant_created or session_created else "updated",
        **launch,
        "room": room,
        "participant": participant,
        "session": session,
        "participants": store.participants(room_id),
        "sessions": store.sessions(room_id),
    }
