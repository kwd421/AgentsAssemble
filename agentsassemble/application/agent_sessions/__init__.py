from __future__ import annotations

import json
import hashlib
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.room.repository import RoomRepository
from agentsassemble.application.agent_sessions.process import (
    agent_session_process_result as _agent_session_process_result,
    build_agent_session_launch_plan,
)
from agentsassemble.application.agent_sessions.commands import (
    active_room_members,
    clean_room_request_payload,
    merge_room_store_members,
    room_action_payload,
    room_lifecycle_payload,
    room_sse_frames_after_cursor,
    room_status_payload,
    stream_room_sse_frames,
)
from agentsassemble.application.agent_sessions.auto_queue import (
    AGENT_SESSION_AUTO_TURN_QUEUE_LIMIT,
    queue_agent_session_auto_turn_job,
    run_agent_session_auto_turn_job,
)
from agentsassemble.application.agent_sessions.turn_commands import (
    _agent_session_resume_mode,
    _default_agent_turn_jsonl_streamer,
    agent_session_codex_jsonl_turn_runner,
    agent_session_command_turn_runner,
    agent_session_streaming_command_turn_runner,
    build_agent_session_plain_turn_command,
    build_agent_session_turn_command,
)
from agentsassemble.application.agent_sessions.compatibility import (
    AgentSessionAdapter,
    AgyAgentSessionAdapter,
    ClaudeAgentSessionAdapter,
    GrokAgentSessionAdapter,
    UnsupportedAgentSessionAdapter,
)
from agentsassemble.application.agent_sessions.service import (
    create_agent_session as _create_agent_session,
    resume_agent_session as _resume_agent_session,
)
from agentsassemble.application.agent_sessions.turns import run_agent_session_turn_payload
from agentsassemble.diagnostics.codex_app_server_smoke import (
    CODEX_APP_SERVER_SMOKE_COMMANDS,
    _codex_app_server_smoke_turn_failure_kind,
    _diagnostics_indicate_timeout,
    _empty_codex_app_server_smoke_metrics,
    _finalize_codex_app_server_smoke_metrics,
    run_codex_app_server_smoke as _run_codex_app_server_smoke,
)
from agentsassemble.providers.sync_cursor import (
    ProviderSyncCursorParityError,
    assert_provider_sync_cursor_parity,
    provider_sync_session_fields,
)
from agentsassemble.providers.codex_app_server import (
    CODEX_APP_SERVER_IDLE_COMPLETION_GRACE_SECONDS,
    CODEX_APP_SERVER_INFERRED_TURN_COMPLETED_METHOD,
    CODEX_APP_SERVER_METHOD_TAIL_LENGTH,
    CODEX_APP_SERVER_RUNTIME_SHARING_POLICIES,
    CODEX_APP_SERVER_STDERR_TAIL_CHARS,
    CODEX_APP_SERVER_STDERR_TAIL_LINES,
    DEFAULT_AGENT_TURN_TIMEOUT_SECONDS,
    ProcessFactory,
    CodexAppServerRuntime,
    CodexAppServerRuntimeManager,
    _agent_turn_timeout_seconds,
    _app_server_agent_message_completed,
    _app_server_message_thread_id,
    _app_server_message_thread_status,
    _app_server_message_turn_id,
    _app_server_message_turn_status,
    _app_server_progress_text,
    _app_server_thread_idle,
    _codex_app_server_sandbox_policy,
    _codex_app_server_thread_start_settings,
    _codex_app_server_turn_start_settings,
    _codex_approval_policy,
    _codex_toml_string_config,
    _context_error_detected,
    _diagnostic_items,
    _earlier_deadline,
    _elapsed_ms,
    _nested_get,
    _now_iso,
    clean_agent_session_provider_kind,
    clean_codex_app_server_runtime_sharing_policy,
    clean_provider_session_id,
    codex_app_server_runtime_command,
    runtime_profile_key,
    runtime_profile_settings,
)
from agentsassemble.room.context import (
    DEFAULT_ROOM_CONTEXT_CHARS,
    DEFAULT_ROOM_CONTEXT_MESSAGES,
    project_room_context,
)

from agentsassemble.room.turn_context import (
    DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS,
    DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS,
    UNSUPPORTED_MEDIA_AUDIT_NOTE,
    _agent_turn_prompt,
    _bound_room_turn_packet,
    _nonnegative_int,
    build_provider_bootstrap_input,
    build_provider_recovery_input,
    build_provider_turn_input,
    build_room_turn_packet,
    room_memory_from_session,
)

CommandRunner = Callable[[list[str]], dict[str, object] | subprocess.CompletedProcess[str] | None]
AgentTurnChunk = dict[str, object]
AgentTurnRunner = Callable[[dict[str, object]], Iterable[AgentTurnChunk]]
AgentTurnCommandRunner = Callable[[list[str], str, float], subprocess.CompletedProcess[str]]
AgentTurnCommandStreamer = Callable[[list[str], str, float], Iterable[AgentTurnChunk]]
AgentTurnAdapter = Callable[[dict[str, object], dict[str, object]], Iterable[AgentTurnChunk]]
ROOM_MEMORY_EMPTY = {
    "summary": "",
    "decisions": [],
    "open_questions": [],
    "up_to_event_id": "",
    "compacted_at": "",
}


def resume_agent_session_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
    process_service: "AgentSessionProcessService | None" = None,
    repository: RoomRepository,
) -> dict[str, object]:
    return _resume_agent_session(
        output_root,
        payload,
        command_runner=command_runner,
        process_service=process_service,
        repository=repository,
        process_service_factory=lambda runner: AgentSessionProcessService(command_runner=runner),
    )


def create_agent_session_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
    process_service: "AgentSessionProcessService | None" = None,
    repository: RoomRepository,
) -> dict[str, object]:
    return _create_agent_session(
        output_root,
        payload,
        command_runner=command_runner,
        process_service=process_service,
        repository=repository,
        process_service_factory=lambda runner: AgentSessionProcessService(command_runner=runner),
    )


class AgentSessionProcessService:
    """Owns Agent Session state/process separation for CLI and HTTP callers."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        turn_runner: AgentTurnRunner | None = None,
        turn_command_runner: AgentTurnCommandRunner | None = None,
        turn_command_streamer: AgentTurnCommandStreamer | None = None,
        turn_adapter: AgentTurnAdapter | None = None,
    ) -> None:
        self.command_runner = command_runner
        self.turn_runner = turn_runner
        self.turn_command_runner = turn_command_runner
        self.turn_command_streamer = turn_command_streamer
        self.turn_adapter = turn_adapter

    def resume(
        self,
        store: RoomRepository,
        room_id: str,
        agent_id: str,
        session: dict[str, object],
        payload: dict[str, object],
    ) -> dict[str, object]:
        return _agent_session_process_result(
            store,
            room_id,
            agent_id,
            session,
            payload,
            command_runner=self.command_runner,
        )

    def run_turn(
        self,
        output_root: Path,
        payload: dict[str, object],
        *,
        repository: RoomRepository,
    ) -> dict[str, object]:
        return run_agent_session_turn_payload(
            output_root,
            payload,
            turn_runner=self.turn_runner,
            turn_command_runner=self.turn_command_runner,
            turn_command_streamer=self.turn_command_streamer,
            turn_adapter=self.turn_adapter,
            repository=repository,
        )




def run_next_agent_session_turn_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    turn_runner: AgentTurnRunner | None = None,
    turn_command_runner: AgentTurnCommandRunner | None = None,
    turn_command_streamer: AgentTurnCommandStreamer | None = None,
    turn_adapter: AgentTurnAdapter | None = None,
    repository: RoomRepository,
) -> dict[str, object]:
    store = repository
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    if not room_id:
        raise ValueError("room_id is required.")
    trigger_event_id = clean_lobby_text(payload.get("trigger_event_id"), limit=128)
    trigger_event = _agent_session_trigger_event(store.read_events(room_id), trigger_event_id)
    if not trigger_event:
        raise ValueError("No public room message is available for the next Agent Session turn.")
    candidates = _ordered_agent_session_candidates(store, room_id)
    if not candidates:
        raise ValueError("No active Agent Session participant is available.")
    participant, session = _next_ordered_agent_session(store.read_events(room_id), candidates)
    turn_id = clean_lobby_text(payload.get("turn_id"), limit=128) or f"turn-{uuid4().hex[:12]}"
    queued = store.append_event(
        room_id,
        "turn_queued",
        participant_id=participant["participant_id"],
        session_id=session["session_id"],
        trigger_event_id=trigger_event["id"],
        turn_id=turn_id,
    )
    assigned = store.append_event(
        room_id,
        "turn_assigned",
        participant_id=participant["participant_id"],
        session_id=session["session_id"],
        trigger_event_id=trigger_event["id"],
        turn_id=turn_id,
    )
    result = run_agent_session_turn_payload(
        output_root,
        {
            **payload,
            "room_id": room_id,
            "agent_id": participant["participant_id"],
            "session_id": session["session_id"],
            "instruction": "Respond to the latest room message.",
            "turn_id": turn_id,
        },
        turn_runner=turn_runner,
        turn_command_runner=turn_command_runner,
        turn_command_streamer=turn_command_streamer,
        turn_adapter=turn_adapter,
        repository=store,
    )
    return {
        **result,
        "participant_id": participant["participant_id"],
        "session_id": session["session_id"],
        "trigger_event_id": trigger_event["id"],
        "events": [queued, assigned, *(result.get("events") if isinstance(result.get("events"), list) else [])],
    }


def enqueue_agent_session_auto_turn_for_lobby_event(
    output_root: Path,
    lobby_event: dict[str, object],
    *,
    turn_runner: AgentTurnRunner | None = None,
    turn_command_runner: AgentTurnCommandRunner | None = None,
    turn_command_streamer: AgentTurnCommandStreamer | None = None,
    turn_adapter: AgentTurnAdapter | None = None,
    run_background: bool = True,
    repository: RoomRepository,
) -> dict[str, object]:
    room_message = _room_store_message_from_lobby_event(lobby_event)
    if not room_message:
        return {"status": "ignored", "reason": "not_human_room_message"}
    store = repository
    room_id = str(room_message["room_id"])
    store.create_room(room_id)
    room_event = store.append_event(
        room_id,
        "message_final",
        actor_id=room_message.get("actor_id", ""),
        actor_type=room_message.get("actor_type", "human"),
        content=room_message["content"],
        lobby_event_id=room_message.get("lobby_event_id", ""),
    )
    if not _ordered_agent_session_candidates(store, room_id):
        return {"status": "no_agent_session", "room_event": room_event}
    job: dict[str, object] = {
        "output_root": output_root,
        "room_id": room_id,
        "trigger_event_id": room_event["id"],
        "repository": store,
    }
    job["execute"] = lambda: run_next_agent_session_turn_payload(
        output_root,
        {
            "room_id": room_id,
            "trigger_event_id": room_event["id"],
        },
        turn_runner=turn_runner,
        turn_command_runner=turn_command_runner,
        turn_command_streamer=turn_command_streamer,
        turn_adapter=turn_adapter,
        repository=store,
    )
    if not run_background:
        result = run_agent_session_auto_turn_job(job)
        return {**result, "room_event": room_event}
    queued = queue_agent_session_auto_turn_job(job)
    return {**queued, "room_event": room_event}






def run_codex_app_server_smoke(
    smoke: str,
    *,
    approve_real_provider: bool = False,
) -> dict[str, object]:
    return _run_codex_app_server_smoke(
        smoke,
        approve_real_provider=approve_real_provider,
        resume_session=resume_agent_session_payload,
        run_turn=run_agent_session_turn_payload,
    )












def _latest_public_event_id(events: list[dict[str, object]]) -> str:
    for event in reversed(events):
        if clean_lobby_text(event.get("type"), limit=64) == "message_final":
            return clean_lobby_text(event.get("id"), limit=128)
    return ""


def _agent_session_trigger_event(events: list[dict[str, object]], trigger_event_id: str) -> dict[str, object]:
    public_messages = [
        event
        for event in events
        if clean_lobby_text(event.get("type"), limit=64) == "message_final"
        and not clean_lobby_text(event.get("participant_id"), limit=128)
    ]
    if trigger_event_id:
        for event in public_messages:
            if clean_lobby_text(event.get("id"), limit=128) == trigger_event_id:
                return event
        return {}
    return public_messages[-1] if public_messages else {}


def _room_store_message_from_lobby_event(event: dict[str, object]) -> dict[str, object]:
    kind = clean_lobby_text(event.get("kind"), limit=64) or "message"
    room_id = clean_lobby_text(event.get("flow_meeting_id") or event.get("room_id") or event.get("meeting_id"), limit=128)
    content = clean_lobby_text(event.get("message") or event.get("content"), limit=8000)
    actor_type = clean_lobby_text(event.get("actor_type"), limit=64)
    if kind != "message" or not room_id or not content:
        return {}
    if actor_type == "agent" or bool(event.get("live_agent_endpoint")):
        return {}
    return {
        "room_id": room_id,
        "content": content,
        "actor_id": clean_lobby_text(event.get("actor_id") or event.get("name"), limit=128),
        "actor_type": actor_type or "human",
        "lobby_event_id": clean_lobby_text(event.get("id"), limit=128),
    }


def _ordered_agent_session_candidates(
    store: RoomRepository,
    room_id: str,
) -> list[tuple[dict[str, object], dict[str, object]]]:
    sessions_by_participant = {
        clean_lobby_text(session.get("participant_id"), limit=128): session
        for session in store.sessions(room_id)
        if clean_lobby_text(session.get("status"), limit=64) in {"attached", "available"}
    }
    candidates: list[tuple[dict[str, object], dict[str, object]]] = []
    for participant in store.active_participants(room_id):
        if clean_lobby_text(participant.get("role"), limit=64) != "agent":
            continue
        participant_id = clean_lobby_text(participant.get("participant_id"), limit=128)
        session = sessions_by_participant.get(participant_id)
        if session:
            candidates.append((participant, session))
    return candidates


def _next_ordered_agent_session(
    events: list[dict[str, object]],
    candidates: list[tuple[dict[str, object], dict[str, object]]],
) -> tuple[dict[str, object], dict[str, object]]:
    candidate_ids = [clean_lobby_text(participant.get("participant_id"), limit=128) for participant, _ in candidates]
    if not candidate_ids:
        raise ValueError("No active Agent Session participant is available.")
    for event in reversed(events):
        if clean_lobby_text(event.get("type"), limit=64) != "turn_assigned":
            continue
        participant_id = clean_lobby_text(event.get("participant_id"), limit=128)
        if participant_id not in candidate_ids:
            continue
        return candidates[(candidate_ids.index(participant_id) + 1) % len(candidates)]
    return candidates[0]


def _latest_own_message_event_id(events: list[dict[str, object]], participant_id: str) -> str:
    clean_participant = clean_lobby_text(participant_id, limit=128)
    for event in reversed(events):
        if clean_lobby_text(event.get("type"), limit=64) != "message_final":
            continue
        if clean_lobby_text(event.get("participant_id"), limit=128) == clean_participant:
            return clean_lobby_text(event.get("id"), limit=128)
    return ""
