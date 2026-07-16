from __future__ import annotations

from collections import deque
import json
import hashlib
import os
import select
import statistics
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, cast
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.room.repository import RoomRepository
from agentsassemble.providers.sync_cursor import (
    ProviderSyncCursorParityError,
    assert_provider_sync_cursor_parity,
    provider_sync_session_fields,
)
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.codex_app_server import (
    CODEX_APP_SERVER_IDLE_COMPLETION_GRACE_SECONDS,
    CODEX_APP_SERVER_INFERRED_TURN_COMPLETED_METHOD,
    CODEX_APP_SERVER_METHOD_TAIL_LENGTH,
    CODEX_APP_SERVER_RUNTIME_SHARING_POLICIES,
    CODEX_APP_SERVER_STDERR_TAIL_CHARS,
    CODEX_APP_SERVER_STDERR_TAIL_LINES,
    DEFAULT_AGENT_TURN_TIMEOUT_SECONDS,
    DEFAULT_CODEX_APP_SERVER_RUNTIME_SHARING_POLICY,
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
CODEX_APP_SERVER_SMOKE_COMMANDS = {
    "codex-app-server-same-profile",
    "codex-app-server-profile-isolation",
    "codex-app-server-restart-recovery",
    "codex-app-server-stderr-backpressure",
    "codex-app-server-warm",
    "codex-app-server-two-agent",
}
AGENT_SESSION_AUTO_TURN_QUEUE_LIMIT = 20
ROOM_MEMORY_EMPTY = {
    "summary": "",
    "decisions": [],
    "open_questions": [],
    "up_to_event_id": "",
    "compacted_at": "",
}
_AUTO_TURN_QUEUE_LOCK = threading.Lock()
_AUTO_TURN_QUEUES: dict[str, deque[dict[str, object]]] = {}
_AUTO_TURN_WORKERS: set[str] = set()


def resume_agent_session_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
    process_service: "AgentSessionProcessService | None" = None,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    store = repository or RoomStore(output_root)
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
    service = process_service or AgentSessionProcessService(command_runner=command_runner)
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


def create_agent_session_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
    process_service: "AgentSessionProcessService | None" = None,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    store = repository or RoomStore(output_root)
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("agent"), limit=128)
    session_id = clean_lobby_text(payload.get("session_id") or payload.get("session"), limit=128) or agent_id
    if not room_id:
        raise ValueError("room_id is required.")
    if not agent_id:
        raise ValueError("agent_id is required.")
    if not session_id:
        raise ValueError("session_id is required.")

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
        service = process_service or AgentSessionProcessService(command_runner=command_runner)
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
        repository: RoomRepository | None = None,
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


def run_agent_session_turn_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    turn_runner: AgentTurnRunner | None = None,
    turn_command_runner: AgentTurnCommandRunner | None = None,
    turn_command_streamer: AgentTurnCommandStreamer | None = None,
    turn_adapter: AgentTurnAdapter | None = None,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    store = repository or RoomStore(output_root)
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("agent") or payload.get("participant_id"), limit=128)
    session_id = clean_lobby_text(payload.get("session_id") or payload.get("session"), limit=128) or agent_id
    instruction = clean_lobby_text(payload.get("instruction"), limit=2000)
    if not room_id:
        raise ValueError("room_id is required.")
    if not agent_id:
        raise ValueError("agent_id is required.")
    if not session_id:
        raise ValueError("session_id is required.")
    if not instruction:
        raise ValueError("instruction is required.")
    session = store.session(room_id, session_id)
    if not session:
        raise ValueError(f"Session {session_id} was not found.")
    if clean_lobby_text(session.get("participant_id"), limit=128) != agent_id:
        raise ValueError("session does not belong to participant.")

    packet = build_room_turn_packet(
        output_root,
        room_id=room_id,
        participant_id=agent_id,
        session_id=session_id,
        instruction=instruction,
        max_recent_events=payload.get("max_recent_events"),
        max_prompt_chars=payload.get("max_prompt_chars"),
        media_ids=payload.get("media_ids") or payload.get("current_turn_media_ids"),
        repository=store,
    )
    turn_id = clean_lobby_text(payload.get("turn_id"), limit=128) or f"turn-{uuid4().hex[:12]}"
    provider_kind = clean_lobby_text(session.get("provider_kind"), limit=64)
    timeout_seconds = _agent_turn_timeout_seconds(payload.get("timeout_seconds"))
    packet["timeout_seconds"] = timeout_seconds
    if bool(payload.get("dry_run")):
        return {
            "status": "dry_run",
            "turn_status": "not_started",
            "turn_id": turn_id,
            "packet": packet,
            "events": [],
            "diagnostics": [
                {
                    "setting": "dry_run",
                    "status": "not_started",
                    "message": "Dry run built the Agent Session turn packet without running the provider.",
                }
            ],
        }
    runner_kind = "fake" if turn_runner is not None else ""
    runtime_mode = "fake" if turn_runner is not None else ""
    streaming = False
    if turn_runner is None and turn_adapter is not None:
        runner_kind = "agent_session_adapter"
        runtime_mode = "app_server"
        streaming = True

        def run_adapter(packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
            yield from turn_adapter(session, _provider_adapter_input(packet))

        turn_runner = run_adapter
    if turn_runner is None and turn_command_streamer is not None:
        runner_kind = "codex_jsonl_command"
        runtime_mode = "exec_jsonl_fallback"
        streaming = True
        turn_runner = agent_session_codex_jsonl_turn_runner(
            session,
            command_streamer=turn_command_streamer,
            timeout_seconds=timeout_seconds,
        )
    if turn_runner is None and turn_command_runner is not None:
        runner_kind = "final_command"
        runtime_mode = "exec_plain_fallback"
        turn_runner = agent_session_command_turn_runner(
            session,
            command_runner=turn_command_runner,
            timeout_seconds=timeout_seconds,
        )
    if turn_runner is None:
        return {
            "status": "not_started",
            "turn_status": "not_started",
            "turn_id": turn_id,
            "packet": packet,
            "events": [],
            "diagnostics": [
                {
                    "setting": "turn_runner",
                    "status": "not_started",
                    "message": "No Agent Session turn runner was provided; provider execution is opt-in.",
                }
            ],
        }
    command = build_agent_session_turn_command(session)
    started_monotonic = time.monotonic()
    started_at = _now_iso()
    runtime_state: dict[str, object] = {
        "turn_id": turn_id,
        "room_id": room_id,
        "participant_id": agent_id,
        "session_id": session_id,
        "provider_session_id": clean_provider_session_id(session.get("provider_session_id")),
        "provider_kind": provider_kind,
        "runner_kind": runner_kind or "custom",
        "runtime_mode": runtime_mode or "custom",
        "command_shape_hash": _command_shape_hash(command),
        "prompt_chars": len(_agent_turn_prompt(packet)),
        "prompt_bytes": len(_agent_turn_prompt(packet).encode("utf-8")),
        "input_mode": packet.get("input_mode", ""),
        "provider_visible_chars": packet.get("provider_visible_chars", 0),
        "provider_visible_event_count": packet.get("provider_visible_event_count", 0),
        "filtered_internal_event_count": packet.get("filtered_internal_event_count", 0),
        "filtered_message_delta_count": packet.get("filtered_message_delta_count", 0),
        "last_provider_sync_event_id_before": packet.get("last_provider_sync_event_id_before", ""),
        "last_provider_sync_event_id_after": packet.get("last_provider_sync_event_id_after", ""),
        "last_provider_sync_seq_before": packet.get("last_provider_sync_seq_before", 0),
        "last_provider_sync_seq_after": packet.get("last_provider_sync_seq_after", 0),
        "bootstrap_included": bool(packet.get("bootstrap_included")),
        "room_delta_included": bool(packet.get("room_delta_included")),
        "recovery_summary_included": bool(packet.get("recovery_summary_included")),
        "event_count_in_packet": len(packet.get("events") if isinstance(packet.get("events"), list) else []),
        "recent_event_count": packet.get("recent_event_count", 0),
        "summary_checkpoint_id": packet.get("summary_checkpoint_id", ""),
        "media_supported_count": packet.get("media_supported_count", 0),
        "media_unsupported_count": packet.get("media_unsupported_count", 0),
        "started_at": started_at,
        "resume_mode": _agent_session_resume_mode(session),
        "context_error_detected": False,
        "timeout_seconds": timeout_seconds,
        "streaming": bool(streaming),
        "stderr_tail": [],
        "stdout_bytes": 0,
        "message_final_chars": 0,
    }
    runtime_diagnostics = _diagnostic_items(runtime_state)

    appended: list[dict[str, object]] = [
        store.append_event(
            room_id,
            "turn_started",
            participant_id=agent_id,
            session_id=session_id,
            provider_kind=provider_kind,
            turn_id=turn_id,
            diagnostics=runtime_diagnostics,
        )
    ]
    try:
        for chunk in turn_runner(packet):
            if not isinstance(chunk, dict):
                continue
            event_type = clean_lobby_text(chunk.get("type") or chunk.get("kind"), limit=64)
            if event_type == "provider_session":
                provider_session_id = clean_provider_session_id(chunk.get("provider_session_id") or chunk.get("thread_id"))
                if provider_session_id:
                    if runtime_state.get("resume_mode") == "explicit_session_id" or runtime_state.get("runtime_mode") == "app_server":
                        runtime_state["provider_session_id"] = provider_session_id
                        store.upsert_session(room_id, {**store.session(room_id, session_id), "provider_session_id": provider_session_id})
                    else:
                        runtime_state["ephemeral_thread_id"] = provider_session_id
                        runtime_state["resume_capability"] = "none"
                provider_thread_id = clean_lobby_text(chunk.get("provider_thread_id"), limit=200)
                if provider_thread_id:
                    runtime_state["provider_thread_id"] = provider_thread_id
                    if runtime_state.get("runtime_mode") == "app_server":
                        store.upsert_session(room_id, {**store.session(room_id, session_id), "provider_thread_id": provider_thread_id})
                continue
            if event_type == "diagnostics":
                _merge_runtime_diagnostics(runtime_state, chunk)
                continue
            if event_type not in {
                "thinking_delta",
                "message_delta",
                "message_final",
                "error",
                "approval_requested",
                "approval_resolved",
                "context_compaction_started",
                "context_compaction_finished",
            }:
                continue
            content = clean_lobby_text(chunk.get("content") or chunk.get("text") or chunk.get("message"), limit=8000)
            if event_type in {"message_delta", "message_final"} and content:
                runtime_state["time_to_first_message_delta_ms"] = runtime_state.get("time_to_first_message_delta_ms") or _elapsed_ms(started_monotonic)
                runtime_state["stdout_bytes"] = int(runtime_state.get("stdout_bytes") or 0) + len(content.encode("utf-8"))
            if event_type == "thinking_delta" and content:
                runtime_state["time_to_first_thinking_delta_ms"] = runtime_state.get("time_to_first_thinking_delta_ms") or _elapsed_ms(started_monotonic)
            if event_type == "message_final":
                runtime_state["message_final_chars"] = len(content)
            _merge_runtime_diagnostics(runtime_state, chunk)
            diagnostics = chunk.get("diagnostics") if isinstance(chunk.get("diagnostics"), list) else []
            appended.append(
                store.append_event(
                    room_id,
                    event_type,
                    participant_id=agent_id,
                    session_id=session_id,
                    provider_kind=provider_kind,
                    turn_id=turn_id,
                    content=content,
                    diagnostics=diagnostics,
                )
            )
            if event_type == "error":
                runtime_state["context_error_detected"] = _context_error_detected([*diagnostics, content])
                if runtime_state["context_error_detected"] or _agent_session_error_requires_recovery(runtime_state, diagnostics):
                    runtime_state["recovery_required"] = True
                _mark_agent_session_turn_error(
                    store,
                    room_id,
                    session_id,
                    diagnostics=[*_diagnostic_items(runtime_state), *diagnostics],
                    recovery_required=bool(runtime_state.get("recovery_required")),
                )
                return {
                    "status": "error",
                    "turn_status": "error",
                    "turn_id": turn_id,
                    "packet": packet,
                    "events": appended,
                    "diagnostics": [*_diagnostic_items(runtime_state), *diagnostics],
                }
    except Exception as error:  # pragma: no cover - defensive for injected runners
        diagnostics = [{"setting": "turn_runner", "status": "failed", "message": str(error)}]
        runtime_state["context_error_detected"] = _context_error_detected(diagnostics)
        if runtime_state.get("runtime_mode") == "app_server" or runtime_state["context_error_detected"]:
            runtime_state["recovery_required"] = True
        appended.append(
            store.append_event(
                room_id,
                "error",
                participant_id=agent_id,
                session_id=session_id,
                provider_kind=provider_kind,
                turn_id=turn_id,
                diagnostics=diagnostics,
            )
        )
        _mark_agent_session_turn_error(
            store,
            room_id,
            session_id,
            diagnostics=[*_diagnostic_items(runtime_state), *diagnostics],
            recovery_required=bool(runtime_state.get("recovery_required")),
        )
        return {
            "status": "error",
            "turn_status": "error",
            "turn_id": turn_id,
            "packet": packet,
            "events": appended,
            "diagnostics": [*_diagnostic_items(runtime_state), *diagnostics],
        }
    turn_finished_diagnostics = _diagnostic_items(
        {**runtime_state, "turn_finished_ms": _elapsed_ms(started_monotonic)}
    )
    latest_public_event_id = clean_lobby_text(packet.get("last_provider_sync_event_id_after"), limit=128)
    latest_public_seq = _nonnegative_int(packet.get("last_provider_sync_seq_after"))
    last_spoke_event_id = next(
        (
            clean_lobby_text(event.get("id"), limit=128)
            for event in reversed(appended)
            if event.get("type") == "message_final"
        ),
        "",
    )
    with store.transaction(room_id) as transaction:
        current_session = transaction.session(session_id)
        current_state = transaction.attention_state(agent_id)
        assert_provider_sync_cursor_parity(current_session, current_state)
        if latest_public_seq < current_state.last_provider_sync_seq:
            raise ProviderSyncCursorParityError(
                "Provider turn completion cannot move the canonical sync cursor backward."
            )
        updated_state = transaction.advance_attention_state(
            agent_id,
            provider_sync_seq=latest_public_seq,
        )
        turn_finished = transaction.append_event(
            "turn_finished",
            participant_id=agent_id,
            session_id=session_id,
            provider_kind=provider_kind,
            turn_id=turn_id,
            diagnostics=turn_finished_diagnostics,
        )
        session_updates = {
            "status": "attached",
            "bootstrap_done": True,
            "recovery_required": False,
            "recovery_attempt_count": 0,
            **provider_sync_session_fields(
                updated_state,
                event_id=latest_public_event_id,
            ),
            "last_seen_event_id": latest_public_event_id,
            "last_seen_seq": latest_public_seq,
        }
        if last_spoke_event_id:
            session_updates["last_spoke_event_id"] = last_spoke_event_id
        updated_session = transaction.update_session_fields(session_id, **session_updates)
        assert_provider_sync_cursor_parity(updated_session, updated_state)
    appended.append(turn_finished)
    return {
        "status": "finished",
        "turn_status": "finished",
        "turn_id": turn_id,
        "packet": packet,
        "events": appended,
        "diagnostics": _diagnostic_items(runtime_state),
    }


def run_next_agent_session_turn_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    turn_runner: AgentTurnRunner | None = None,
    turn_command_runner: AgentTurnCommandRunner | None = None,
    turn_command_streamer: AgentTurnCommandStreamer | None = None,
    turn_adapter: AgentTurnAdapter | None = None,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    store = repository or RoomStore(output_root)
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
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    room_message = _room_store_message_from_lobby_event(lobby_event)
    if not room_message:
        return {"status": "ignored", "reason": "not_human_room_message"}
    store = repository or RoomStore(output_root)
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
    job = {
        "output_root": output_root,
        "room_id": room_id,
        "trigger_event_id": room_event["id"],
        "turn_runner": turn_runner,
        "turn_command_runner": turn_command_runner,
        "turn_command_streamer": turn_command_streamer,
        "turn_adapter": turn_adapter,
        "repository": store,
    }
    if not run_background:
        result = _run_agent_session_auto_turn_job(job)
        return {**result, "room_event": room_event}
    queued = _queue_agent_session_auto_turn_job(job)
    return {**queued, "room_event": room_event}


def _queue_agent_session_auto_turn_job(job: dict[str, object]) -> dict[str, object]:
    room_id = str(job["room_id"])
    with _AUTO_TURN_QUEUE_LOCK:
        queue = _AUTO_TURN_QUEUES.setdefault(room_id, deque())
        if len(queue) >= AGENT_SESSION_AUTO_TURN_QUEUE_LIMIT:
            repository = _auto_turn_job_repository(job)
            repository.append_event(
                room_id,
                "error",
                actor_id="agent_session_auto_turn",
                content="Agent Session auto-turn queue is full.",
                trigger_event_id=job.get("trigger_event_id", ""),
            )
            return {"status": "queue_full", "trigger_event_id": job.get("trigger_event_id", "")}
        queue.append(job)
        should_start = room_id not in _AUTO_TURN_WORKERS
        if should_start:
            _AUTO_TURN_WORKERS.add(room_id)
    if should_start:
        thread = threading.Thread(
            target=_drain_agent_session_auto_turn_queue,
            args=(room_id,),
            daemon=True,
            name=f"agent-session-auto-turn-{room_id}",
        )
        thread.start()
    return {"status": "queued", "trigger_event_id": job.get("trigger_event_id", "")}


def _drain_agent_session_auto_turn_queue(room_id: str) -> None:
    while True:
        with _AUTO_TURN_QUEUE_LOCK:
            queue = _AUTO_TURN_QUEUES.get(room_id)
            if not queue:
                _AUTO_TURN_WORKERS.discard(room_id)
                _AUTO_TURN_QUEUES.pop(room_id, None)
                return
            job = queue.popleft()
        _run_agent_session_auto_turn_job(job)


def _run_agent_session_auto_turn_job(job: dict[str, object]) -> dict[str, object]:
    output_root = Path(job["output_root"])
    room_id = str(job["room_id"])
    repository = _auto_turn_job_repository(job)
    try:
        return run_next_agent_session_turn_payload(
            output_root,
            {
                "room_id": room_id,
                "trigger_event_id": job.get("trigger_event_id", ""),
            },
            turn_runner=job.get("turn_runner"),  # type: ignore[arg-type]
            turn_command_runner=job.get("turn_command_runner"),  # type: ignore[arg-type]
            turn_command_streamer=job.get("turn_command_streamer"),  # type: ignore[arg-type]
            turn_adapter=job.get("turn_adapter"),  # type: ignore[arg-type]
            repository=repository,
        )
    except Exception as error:  # pragma: no cover - defensive for background worker
        repository.append_event(
            room_id,
            "error",
            actor_id="agent_session_auto_turn",
            content=clean_lobby_text(str(error), limit=1000),
            trigger_event_id=job.get("trigger_event_id", ""),
        )
        return {"status": "error", "turn_status": "error", "message": str(error)}


def _auto_turn_job_repository(job: dict[str, object]) -> RoomRepository:
    repository = job.get("repository")
    if repository is None:
        raise RuntimeError("Agent Session auto-turn job has no room repository.")
    return cast(RoomRepository, repository)


def agent_session_codex_jsonl_turn_runner(
    session: dict[str, object],
    *,
    command_streamer: AgentTurnCommandStreamer | None = None,
    timeout_seconds: float = DEFAULT_AGENT_TURN_TIMEOUT_SECONDS,
) -> AgentTurnRunner:
    command = build_agent_session_turn_command(session)
    streamer = command_streamer or _default_agent_turn_jsonl_streamer

    def run(packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        if not command:
            yield {
                "type": "error",
                "diagnostics": [
                    {
                        "setting": "turn_command",
                        "status": "unsupported",
                        "message": "This Agent Session provider has no verified turn command mapping yet.",
                    }
                ],
            }
            return
        prompt = _agent_turn_prompt(packet)
        final_parts: list[str] = []
        for chunk in streamer(command, prompt, float(timeout_seconds)):
            if isinstance(chunk, dict) and str(chunk.get("type") or "") in {
                "thinking_delta",
                "message_delta",
                "message_final",
                "error",
                "diagnostics",
                "provider_session",
            }:
                yield chunk
                continue
            line = str(chunk.get("content") if isinstance(chunk, dict) else chunk or "").strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                yield {
                    "type": "diagnostics",
                    "diagnostics": [{"setting": "jsonl", "status": "malformed", "message": clean_lobby_text(line, limit=500)}],
                }
                continue
            event_type = clean_lobby_text(item.get("type") or item.get("event") or item.get("kind"), limit=128)
            if event_type == "thread.started":
                thread_id = clean_provider_session_id(item.get("thread_id") or item.get("id") or item.get("session_id"))
                if thread_id:
                    yield {"type": "provider_session", "provider_session_id": thread_id}
                continue
            if event_type in {"turn.failed", "error"}:
                yield {
                    "type": "error",
                    "diagnostics": [
                        {
                            "setting": "codex_jsonl",
                            "status": "failed",
                            "message": clean_lobby_text(item.get("message") or item.get("error") or str(item), limit=1000),
                        }
                    ],
                }
                return
            if event_type == "turn.completed":
                usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
                if usage:
                    yield {"type": "diagnostics", "usage": usage}
                if final_parts:
                    yield {"type": "message_final", "content": clean_lobby_text("".join(final_parts), limit=8000)}
                continue
            text = _codex_jsonl_visible_message_text(item)
            if text and _codex_jsonl_is_agent_message(item, event_type):
                final_parts.append(text)
                yield {"type": "message_delta", "content": text}
                continue
            progress = _codex_jsonl_progress_text(item, event_type)
            if progress:
                yield {"type": "thinking_delta", "content": progress}

    return run


class AgentSessionAdapter(Protocol):
    def start(self, config: dict[str, object]) -> dict[str, object]: ...

    def attach(self, ids: dict[str, object]) -> dict[str, object]: ...

    def send_turn(self, handle: dict[str, object], packet: dict[str, object]) -> Iterable[AgentTurnChunk]: ...

    def compact(self, handle: dict[str, object], policy: dict[str, object]) -> Iterable[AgentTurnChunk]: ...

    def detach(self, handle: dict[str, object]) -> None: ...

    def diagnose(self, handle: dict[str, object]) -> dict[str, object]: ...


class UnsupportedAgentSessionAdapter:
    provider_name = "unsupported"
    reason = "Provider Agent Session adapter is not configured yet."

    def start(self, config: dict[str, object]) -> dict[str, object]:
        return {
            "provider_kind": self.provider_name,
            "status": "unsupported",
            "resumable": False,
            "reason": self.reason,
        }

    def attach(self, ids: dict[str, object]) -> dict[str, object]:
        return {
            "provider_kind": self.provider_name,
            "status": "unsupported",
            "resumable": False,
            "reason": self.reason,
        }

    def send_turn(self, handle: dict[str, object], packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        yield {
            "type": "error",
            "diagnostics": [
                {
                    "setting": f"{self.provider_name}_adapter",
                    "status": "unsupported",
                    "message": self.reason,
                }
            ],
        }

    def compact(self, handle: dict[str, object], policy: dict[str, object]) -> Iterable[AgentTurnChunk]:
        yield from self.send_turn(handle, {})

    def detach(self, handle: dict[str, object]) -> None:
        return None

    def diagnose(self, handle: dict[str, object]) -> dict[str, object]:
        return {
            "provider_kind": self.provider_name,
            "status": "unsupported",
            "resumable": False,
            "reason": self.reason,
        }


class GrokAgentSessionAdapter(UnsupportedAgentSessionAdapter):
    provider_name = "grok"
    reason = "Grok is not wired into Agent Session runtime yet."


class ClaudeAgentSessionAdapter(UnsupportedAgentSessionAdapter):
    provider_name = "claude"
    reason = "Claude Agent Session runtime is Agent SDK only; claude -p is intentionally forbidden."


class AgyAgentSessionAdapter(UnsupportedAgentSessionAdapter):
    provider_name = "agy"
    reason = "AGY is unavailable until protocol verified."




def run_codex_app_server_smoke(
    smoke: str,
    *,
    approve_real_provider: bool = False,
) -> dict[str, object]:
    clean_smoke = clean_lobby_text(smoke, limit=128)
    if clean_smoke not in CODEX_APP_SERVER_SMOKE_COMMANDS:
        raise ValueError(f"unsupported Codex app-server smoke: {clean_smoke}")
    if not approve_real_provider:
        return _codex_app_server_smoke_skipped(clean_smoke)

    with tempfile.TemporaryDirectory(prefix="agentsassemble-codex-app-server-smoke-") as tmp:
        output_root = Path(tmp)
        workspace = output_root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        room_id = f"{clean_smoke}-{uuid4().hex[:8]}"
        manager = CodexAppServerRuntimeManager()
        store = RoomStore(output_root)
        store.create_room(room_id, label=clean_smoke)
        metrics: dict[str, object] = _empty_codex_app_server_smoke_metrics()
        errors: list[str] = []
        blocking_errors: list[str] = []
        timeout_count = 0
        context_error_detected = False
        sessions = _codex_app_server_smoke_sessions(clean_smoke, workspace=str(workspace))
        for session in sessions:
            resume_agent_session_payload(
                output_root,
                {
                    "room_id": room_id,
                    "agent_id": session["participant_id"],
                    "session_id": session["session_id"],
                    "display_name": session["display_name"],
                    "provider_kind": session["provider_kind"],
                    "model": session["model"],
                    "effort": session["effort"],
                    "sandbox": session["sandbox"],
                    "permissions": session["permissions"],
                    "workspace": session["workspace"],
                    "runtime_sharing_policy": session.get("runtime_sharing_policy", DEFAULT_CODEX_APP_SERVER_RUNTIME_SHARING_POLICY),
                },
            )
        started_at = time.monotonic()
        rss_start = 0
        turn_plan = _codex_app_server_smoke_turn_plan(clean_smoke, sessions)
        try:
            for index, session in enumerate(turn_plan):
                instruction = f"Turn {index + 1}. Reply with one short sentence and do not inspect files."
                result = run_agent_session_turn_payload(
                    output_root,
                    {
                        "room_id": room_id,
                        "agent_id": session["participant_id"],
                        "session_id": session["session_id"],
                        "instruction": instruction,
                        "timeout_seconds": _codex_app_server_smoke_timeout_seconds(clean_smoke),
                    },
                    turn_adapter=lambda runtime_session, packet: manager.send_turn(runtime_session, packet),
                )
                if clean_smoke == "codex-app-server-restart-recovery" and index == 0:
                    persisted = RoomStore(output_root).session(room_id, session["session_id"])
                    manager.detach_session(persisted, shutdown_unused=True)
                turn_diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else []
                packet = result.get("packet") if isinstance(result.get("packet"), dict) else {}
                _record_codex_app_server_smoke_turn(metrics, result, packet, turn_diagnostics)
                context_error_detected = context_error_detected or _context_error_detected(turn_diagnostics)
                if result.get("turn_status") != "finished":
                    failure_kind = _codex_app_server_smoke_turn_failure_kind(turn_diagnostics)
                    metrics["failure_kind"].append(failure_kind)
                    if failure_kind == "provider_unsupported":
                        metrics["turn_status"][-1] = "provider_unsupported"
                    error_label = f"{session['session_id']} turn {index + 1}: {failure_kind}"
                    errors.append(error_label)
                    if failure_kind != "provider_unsupported":
                        blocking_errors.append(error_label)
                    metrics["error_diagnostics"].append(_diagnostics_sample(turn_diagnostics))
                    if failure_kind == "timeout":
                        timeout_count += 1
                    if clean_smoke != "codex-app-server-profile-isolation":
                        break
                pids = [int(pid) for pid in metrics["app_server_pid"] if str(pid).isdigit()]
                if pids and not rss_start:
                    rss_start = sum(_process_rss_kb(pid) for pid in sorted(set(pids)))
        finally:
            runtime_snapshots = [runtime.diagnose({}) for runtime in manager._runtimes.values()]
            for snapshot in runtime_snapshots:
                _record_codex_app_server_smoke_diagnostics(metrics, snapshot)
            pids_before_detach = sorted({int(pid) for pid in metrics["app_server_pid"] if str(pid).isdigit()})
            rss_end = sum(_process_rss_kb(pid) for pid in pids_before_detach)
            manager.shutdown_all()
        alive_after_detach = any(_process_alive(pid) for pid in pids_before_detach)
        metrics["rss_kb_start"] = rss_start
        metrics["rss_kb_end"] = rss_end
        metrics["rss_kb_delta"] = rss_end - rss_start if rss_start or rss_end else 0
        metrics["p50_time_to_first_agent_delta_ms"] = _p50(metrics["time_to_first_agent_delta_ms"])
        metrics["p95_time_to_first_agent_delta_ms"] = _p95(metrics["time_to_first_agent_delta_ms"])
        metrics["p50_turn_completed_ms"] = _p50(metrics["turn_completed_ms"])
        metrics["p95_turn_completed_ms"] = _p95(metrics["turn_completed_ms"])
        metrics["stderr_byte_count"] = max([0, *[int(value) for value in metrics["stderr_byte_count"]]])
        metrics["stderr_warning_count"] = max([0, *[int(value) for value in metrics["stderr_warning_count"]]])
        metrics["stderr_line_count"] = max([0, *[int(value) for value in metrics["stderr_line_count"]]])
        metrics["stderr_tail_sample"] = _last_text(metrics["stderr_tail"])
        metrics["stderr_tail"] = [metrics["stderr_tail_sample"]] if metrics["stderr_tail_sample"] else []
        metrics["context_error_detected"] = context_error_detected
        metrics["timeout_count"] = timeout_count
        metrics["alive_after_detach"] = alive_after_detach
        _finalize_codex_app_server_smoke_metrics(metrics, total_turns=len(turn_plan))
        metrics["distinct_runtime_profile_key_count"] = len(set(str(value) for value in metrics["runtime_profile_key"] if value))
        metrics["elapsed_ms"] = _elapsed_ms(started_at)
        if blocking_errors or alive_after_detach:
            status = "failed"
        elif metrics.get("provider_unsupported_count") and clean_smoke != "codex-app-server-profile-isolation":
            status = "provider_unsupported"
        else:
            status = "ok"
        return {
            "status": status,
            "smoke": clean_smoke,
            "requires_approval": True,
            "approved": True,
            "metrics": metrics,
            "errors": errors,
        }


def _codex_app_server_smoke_skipped(smoke: str) -> dict[str, object]:
    return {
        "status": "skipped",
        "smoke": smoke,
        "requires_approval": True,
        "approved": False,
        "metrics": _empty_codex_app_server_smoke_metrics(),
    }


def _empty_codex_app_server_smoke_metrics() -> dict[str, object]:
    return {
        "runtime_profile_key": [],
        "runtime_sharing_policy": [],
        "runtime_reused": [],
        "thread_reused": [],
        "app_server_pid": [],
        "provider_thread_id": [],
        "provider_session_id": [],
        "provider_visible_chars": [],
        "time_to_first_agent_delta_ms": [],
        "turn_completed_ms": [],
        "stderr_byte_count": [],
        "stderr_line_count": [],
        "stderr_warning_count": [],
        "stderr_tail": [],
        "error_diagnostics": [],
        "failure_kind": [],
        "turn_status": [],
        "rss_kb_start": 0,
        "rss_kb_end": 0,
        "rss_kb_delta": 0,
        "context_error_detected": False,
        "timeout_count": 0,
        "provider_unsupported_count": 0,
        "context_error_count": 0,
        "alive_after_detach": None,
    }


def _codex_app_server_smoke_sessions(smoke: str, *, workspace: str) -> list[dict[str, str]]:
    base = {
        "provider_kind": "codex_live_session",
        "model": "gpt-5.3-codex-spark",
        "effort": "medium",
        "sandbox": "read-only",
        "permissions": "never",
        "workspace": workspace,
    }
    if smoke == "codex-app-server-profile-isolation":
        return [
            {**base, "participant_id": "spark-a", "session_id": "spark-a", "display_name": "Spark A"},
            {**base, "participant_id": "spark-model-b", "session_id": "spark-model-b", "display_name": "Spark Model B", "model": "gpt-5.3-codex"},
            {
                **base,
                "participant_id": "spark-sandbox-c",
                "session_id": "spark-sandbox-c",
                "display_name": "Spark Sandbox C",
                "sandbox": "workspace-write",
                "permissions": "on-request",
            },
        ]
    if smoke == "codex-app-server-same-profile":
        return [
            {**base, "participant_id": "spark-a", "session_id": "spark-a", "display_name": "Spark A", "runtime_sharing_policy": "shared_profile"},
            {**base, "participant_id": "spark-b", "session_id": "spark-b", "display_name": "Spark B", "runtime_sharing_policy": "shared_profile"},
        ]
    return [
        {**base, "participant_id": "spark-a", "session_id": "spark-a", "display_name": "Spark A"},
        {**base, "participant_id": "spark-b", "session_id": "spark-b", "display_name": "Spark B"},
    ]


def _codex_app_server_smoke_turn_plan(smoke: str, sessions: list[dict[str, str]]) -> list[dict[str, str]]:
    if smoke == "codex-app-server-stderr-backpressure":
        return [sessions[index % 2] for index in range(30)]
    if smoke == "codex-app-server-two-agent":
        return [sessions[index % 2] for index in range(30)]
    if smoke in {"codex-app-server-same-profile", "codex-app-server-warm"}:
        return [sessions[0], sessions[1]]
    if smoke == "codex-app-server-restart-recovery":
        return [sessions[0], sessions[0]]
    return list(sessions)


def _codex_app_server_smoke_timeout_seconds(smoke: str) -> int:
    return 180


def _record_codex_app_server_smoke_turn(
    metrics: dict[str, object],
    result: dict[str, object],
    packet: dict[str, object],
    diagnostics: list[dict[str, object]],
) -> None:
    metrics["turn_status"].append(str(result.get("turn_status") or result.get("status") or "unknown"))
    metrics["provider_visible_chars"].append(int(packet.get("provider_visible_chars") or 0))
    for key in (
        "runtime_profile_key",
        "runtime_sharing_policy",
        "runtime_reused",
        "thread_reused",
        "app_server_pid",
        "provider_thread_id",
        "provider_session_id",
        "time_to_first_agent_delta_ms",
        "turn_completed_ms",
        "stderr_byte_count",
        "stderr_line_count",
        "stderr_warning_count",
        "stderr_tail",
    ):
        value = _diagnostic_value(diagnostics, key)
        if value not in ("", None):
            metrics[key].append(value)


def _record_codex_app_server_smoke_diagnostics(metrics: dict[str, object], diagnostics: dict[str, object]) -> None:
    for key in (
        "runtime_profile_key",
        "runtime_sharing_policy",
        "runtime_reused",
        "thread_reused",
        "app_server_pid",
        "stderr_byte_count",
        "stderr_line_count",
        "stderr_warning_count",
        "stderr_tail",
    ):
        value = diagnostics.get(key)
        if value not in ("", None):
            metrics[key].append(value)


def _diagnostic_value(diagnostics: list[dict[str, object]], key: str) -> object:
    for item in reversed(diagnostics):
        if isinstance(item, dict) and item.get("setting") == key:
            return item.get("status")
    return ""


def _diagnostics_indicate_timeout(diagnostics: list[dict[str, object]]) -> bool:
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        status = clean_lobby_text(item.get("status"), limit=128).lower()
        message = clean_lobby_text(item.get("message"), limit=1000).lower()
        if "timeout" in status or "timed out" in message or "before timeout" in message:
            return True
    return False


def _codex_app_server_smoke_turn_failure_kind(diagnostics: list[dict[str, object]]) -> str:
    text = str(diagnostics).lower()
    if "not supported" in text and "model" in text:
        return "provider_unsupported"
    if _context_error_detected(diagnostics):
        return "context_error"
    if _diagnostics_indicate_timeout(diagnostics):
        return "timeout"
    return "error"


def _finalize_codex_app_server_smoke_metrics(metrics: dict[str, object], *, total_turns: int) -> dict[str, object]:
    failure_kinds = [str(value) for value in metrics.get("failure_kind", []) if value]
    metrics["finished_turns"] = len([status for status in metrics.get("turn_status", []) if status == "finished"])
    metrics["total_turns"] = total_turns
    metrics["provider_unsupported_count"] = failure_kinds.count("provider_unsupported")
    metrics["context_error_count"] = failure_kinds.count("context_error")
    metrics["timeout_count"] = failure_kinds.count("timeout") if failure_kinds else int(metrics.get("timeout_count") or 0)
    return metrics


def _diagnostics_sample(diagnostics: list[dict[str, object]]) -> str:
    interesting: list[dict[str, object]] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        setting = clean_lobby_text(item.get("setting"), limit=128).lower()
        status = clean_lobby_text(item.get("status"), limit=128).lower()
        message = clean_lobby_text(item.get("message"), limit=1000).lower()
        if (
            setting
            in {
                "app_server",
                "app_server_error",
                "app_server_last_event_at",
                "app_server_last_method",
                "app_server_turn_event_count",
                "context_error_detected",
                "recovery_required",
                "stderr_warning_count",
                "turn_runner",
            }
            or "error" in setting
            or status in {"error", "failed", "stopped", "timeout"}
            or "error" in message
            or "failed" in message
            or "stopped" in message
            or "timeout" in message
        ):
            interesting.append(_diagnostics_sample_item(item))
    selected = interesting[-12:] or [_diagnostics_sample_item(item) for item in diagnostics[-12:] if isinstance(item, dict)]
    return clean_lobby_text(json.dumps(selected, ensure_ascii=True), limit=4000)


def _diagnostics_sample_item(item: dict[str, object]) -> dict[str, str]:
    setting = clean_lobby_text(item.get("setting"), limit=128)
    status_limit = 800 if setting == "stderr_tail" else 1200
    message_limit = 800 if setting == "stderr_tail" else 1200
    return {
        "setting": setting,
        "status": _sample_text(item.get("status"), limit=status_limit),
        "message": _sample_text(item.get("message"), limit=message_limit),
    }


def _sample_text(value: object, *, limit: int) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[-limit:].strip()


def _numeric_values(values: object) -> list[int]:
    if not isinstance(values, list):
        return []
    numbers = []
    for value in values:
        try:
            numbers.append(int(float(str(value))))
        except (TypeError, ValueError):
            continue
    return numbers


def _p50(values: object) -> int | None:
    numbers = _numeric_values(values)
    if not numbers:
        return None
    return int(statistics.median(numbers))


def _p95(values: object) -> int | None:
    numbers = sorted(_numeric_values(values))
    if not numbers:
        return None
    index = min(len(numbers) - 1, int((len(numbers) * 0.95) + 0.999999) - 1)
    return numbers[index]


def _last_text(values: object) -> str:
    if not isinstance(values, list):
        return ""
    for value in reversed(values):
        text = str(value or "")
        if text:
            return clean_lobby_text(text, limit=2000)
    return ""


def _process_rss_kb(pid: int) -> int:
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return 0
    try:
        return int((completed.stdout or "").strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True




def agent_session_streaming_command_turn_runner(
    session: dict[str, object],
    *,
    command_streamer: AgentTurnCommandStreamer | None = None,
    timeout_seconds: float = DEFAULT_AGENT_TURN_TIMEOUT_SECONDS,
) -> AgentTurnRunner:
    command = build_agent_session_plain_turn_command(session)
    streamer = command_streamer or _default_agent_turn_command_streamer

    def run(packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        if not command:
            yield {
                "type": "error",
                "diagnostics": [
                    {
                        "setting": "turn_command",
                        "status": "unsupported",
                        "message": "This Agent Session provider has no verified turn command mapping yet.",
                    }
                ],
            }
            return
        prompt = _agent_turn_prompt(packet)
        yield from streamer(command, prompt, float(timeout_seconds))

    return run


def agent_session_command_turn_runner(
    session: dict[str, object],
    *,
    command_runner: AgentTurnCommandRunner | None = None,
    timeout_seconds: float = DEFAULT_AGENT_TURN_TIMEOUT_SECONDS,
) -> AgentTurnRunner:
    command = build_agent_session_plain_turn_command(session)
    runner = command_runner or _default_agent_turn_command_runner

    def run(packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        if not command:
            yield {
                "type": "error",
                "diagnostics": [
                    {
                        "setting": "turn_command",
                        "status": "unsupported",
                        "message": "This Agent Session provider has no verified turn command mapping yet.",
                    }
                ],
            }
            return
        prompt = _agent_turn_prompt(packet)
        try:
            completed = runner(command, prompt, float(timeout_seconds))
        except subprocess.TimeoutExpired:
            yield {
                "type": "error",
                "diagnostics": [
                    {
                        "setting": "turn_command",
                        "status": "timeout",
                        "message": f"provider command timed out after {float(timeout_seconds):g}s",
                    }
                ],
            }
            return
        except Exception as error:  # pragma: no cover - defensive for injected runners
            yield {
                "type": "error",
                "diagnostics": [{"setting": "turn_command", "status": "failed", "message": str(error)}],
            }
            return
        if completed.returncode != 0:
            yield {
                "type": "error",
                "diagnostics": [
                    {
                        "setting": "turn_command",
                        "status": "failed",
                        "message": f"provider command exited {completed.returncode}",
                    },
                    *_stderr_diagnostics(completed.stderr),
                ],
            }
            return
        message = clean_lobby_text(completed.stdout, limit=8000)
        if message:
            yield {"type": "message_final", "content": message}
            return
        yield {
            "type": "error",
            "diagnostics": [
                {
                    "setting": "turn_command",
                    "status": "empty",
                    "message": "provider command completed without a room-visible reply",
                }
            ],
        }

    return run


def build_agent_session_turn_command(session: dict[str, object]) -> list[str]:
    launch = build_agent_session_launch_plan(session)
    if launch.get("permission_enforcement") == "unsupported":
        return []
    command = [str(part) for part in launch.get("command", []) if str(part)]
    if not command:
        return []
    return [*command, "-"]


def build_agent_session_plain_turn_command(session: dict[str, object]) -> list[str]:
    command = build_agent_session_turn_command(session)
    return [part for part in command if part != "--json"]


def _default_agent_turn_command_runner(
    command: list[str],
    prompt: str,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _default_agent_turn_command_streamer(
    command: list[str],
    prompt: str,
    timeout_seconds: float,
) -> Iterable[AgentTurnChunk]:
    process = subprocess.Popen(
        [str(part) for part in command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    process.stdin.write(prompt)
    process.stdin.close()
    started_at = time.monotonic()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    streams = [process.stdout, process.stderr]
    while streams:
        if time.monotonic() - started_at > timeout_seconds:
            process.kill()
            yield {
                "type": "error",
                "diagnostics": [
                    {
                        "setting": "turn_command",
                        "status": "timeout",
                        "message": f"provider command timed out after {timeout_seconds:g}s",
                    }
                ],
            }
            return
        readable, _, _ = select.select(streams, [], [], 0.1)
        if not readable and process.poll() is not None:
            readable = list(streams)
        for stream in readable:
            line = stream.readline()
            if line == "":
                streams.remove(stream)
                continue
            if stream is process.stderr:
                stderr_parts.append(line)
                chunk = _stderr_progress_chunk(line)
                if chunk is not None:
                    yield chunk
                continue
            stdout_parts.append(line)
            yield {"type": "message_delta", "content": line}
    returncode = process.wait(timeout=1)
    if returncode != 0:
        yield {
            "type": "error",
            "diagnostics": [
                {
                    "setting": "turn_command",
                    "status": "failed",
                    "message": f"provider command exited {returncode}",
                },
                *_stderr_diagnostics("".join(stderr_parts)),
            ],
        }
        return
    message = clean_lobby_text("".join(stdout_parts), limit=8000)
    if message:
        yield {"type": "message_final", "content": message}
        return
    yield {
        "type": "error",
        "diagnostics": [
            {
                "setting": "turn_command",
                "status": "empty",
                "message": "provider command completed without a room-visible reply",
            }
        ],
    }


def _default_agent_turn_jsonl_streamer(
    command: list[str],
    prompt: str,
    timeout_seconds: float,
) -> Iterable[AgentTurnChunk]:
    process = subprocess.Popen(
        [str(part) for part in command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    process.stdin.write(prompt)
    process.stdin.close()
    started_at = time.monotonic()
    stderr_tail: list[str] = []
    streams = [process.stdout, process.stderr]
    while streams:
        if time.monotonic() - started_at > timeout_seconds:
            process.kill()
            yield {
                "type": "error",
                "diagnostics": [
                    {
                        "setting": "turn_command",
                        "status": "timeout",
                        "message": f"provider command timed out after {timeout_seconds:g}s",
                    }
                ],
            }
            return
        readable, _, _ = select.select(streams, [], [], 0.1)
        if not readable and process.poll() is not None:
            readable = list(streams)
        for stream in readable:
            line = stream.readline()
            if line == "":
                streams.remove(stream)
                continue
            if stream is process.stderr:
                stderr_tail = [*stderr_tail, clean_lobby_text(line, limit=500)][-8:]
                chunk = _stderr_progress_chunk(line)
                if chunk is not None:
                    yield chunk
                continue
            yield {"type": "jsonl_line", "content": line}
    returncode = process.wait(timeout=1)
    if returncode != 0:
        diagnostics = [
            {
                "setting": "turn_command",
                "status": "failed",
                "message": f"provider command exited {returncode}",
            },
            {"setting": "stderr_tail", "status": "captured", "message": "\n".join(stderr_tail)},
        ]
        if _context_error_detected(diagnostics):
            diagnostics.append({"setting": "context_error_detected", "status": "true", "message": "true"})
        yield {"type": "error", "diagnostics": diagnostics}
        return
    yield {"type": "diagnostics", "stderr_tail": stderr_tail, "exit_code": returncode}


def _stderr_progress_chunk(line: str) -> AgentTurnChunk | None:
    safe = clean_lobby_text(line, limit=1000)
    lower = safe.lower()
    if not safe:
        return None
    if lower.startswith(("progress:", "thinking:", "status:")):
        return {"type": "thinking_delta", "content": safe.split(":", 1)[1].strip() or safe}
    return None


def _stderr_diagnostics(stderr: str | None) -> list[dict[str, str]]:
    safe = clean_lobby_text(stderr, limit=1000)
    if not safe:
        return []
    return [{"setting": "stderr", "status": "captured", "message": safe}]


def _command_shape_hash(command: list[str]) -> str:
    redacted = ["<id>" if index and command[index - 1] == "resume" else part for index, part in enumerate(command)]
    return hashlib.sha256(json.dumps(redacted, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _merge_runtime_diagnostics(state: dict[str, object], chunk: dict[str, object]) -> None:
    for key in (
        "runtime_mode",
        "transport",
        "app_server_pid",
        "provider_thread_id",
        "ephemeral_thread_id",
        "resume_capability",
        "input_mode",
        "provider_visible_chars",
        "provider_visible_event_count",
        "filtered_internal_event_count",
        "filtered_message_delta_count",
        "last_provider_sync_event_id_before",
        "last_provider_sync_event_id_after",
        "last_provider_sync_seq_before",
        "last_provider_sync_seq_after",
        "bootstrap_included",
        "room_delta_included",
        "recovery_summary_included",
        "recovery_required",
        "runtime_reused",
        "app_server_reused",
        "runtime_profile_key",
        "thread_reused",
        "thread_resume_skipped",
        "app_server_initialize_ms",
        "app_server_completion_signal",
        "app_server_completion_inferred",
        "thread_start_ms",
        "thread_resume_ms",
        "turn_start_request_ms",
        "time_to_turn_start_ack_ms",
        "time_to_first_notification_ms",
        "time_to_first_agent_item_ms",
        "time_to_first_item_event_ms",
        "time_to_first_agent_text_delta_ms",
        "time_to_first_agent_delta_ms",
        "time_to_message_final_ms",
        "turn_completed_ms",
        "compaction_started_ms",
        "compaction_completed_ms",
        "time_to_process_spawn_ms",
        "time_to_first_stdout_ms",
        "time_to_first_json_event_ms",
        "process_exit_ms",
        "exit_code",
        "stdout_bytes",
        "stderr_drained",
        "stderr_line_count",
        "stderr_byte_count",
        "stderr_tail",
        "stderr_tail_truncated",
        "stderr_last_line_at",
        "stderr_warning_count",
    ):
        if key in chunk:
            state[key] = chunk[key]
    usage = chunk.get("usage")
    if isinstance(usage, dict):
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
            if key in usage:
                state[f"usage.{key}"] = usage[key]
    diagnostics = chunk.get("diagnostics") if isinstance(chunk.get("diagnostics"), list) else []
    if _context_error_detected(diagnostics):
        state["context_error_detected"] = True


def _agent_session_error_requires_recovery(
    runtime_state: dict[str, object],
    diagnostics: list[dict[str, object]],
) -> bool:
    if runtime_state.get("runtime_mode") == "app_server":
        return True
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        if str(item.get("setting") or "") == "recovery_required":
            return True
        if str(item.get("status") or "") in {"resume_failed", "stopped", "turn_start_failed"}:
            return True
    return False


def _mark_agent_session_turn_error(
    store: RoomRepository,
    room_id: str,
    session_id: str,
    *,
    diagnostics: list[dict[str, object]],
    recovery_required: bool,
) -> None:
    session = store.session(room_id, session_id)
    if not session:
        return
    update = {
        **session,
        "status": "error",
        "diagnostics": diagnostics,
    }
    if recovery_required:
        update["recovery_required"] = True
    store.upsert_session(room_id, update)


def _provider_adapter_input(packet: dict[str, object]) -> dict[str, object]:
    return {
        "provider_input": str(packet.get("provider_input") or ""),
        "input_mode": packet.get("input_mode", ""),
        "provider_visible_chars": packet.get("provider_visible_chars", 0),
        "provider_visible_event_count": packet.get("provider_visible_event_count", 0),
        "media_manifest": packet.get("media_manifest") if isinstance(packet.get("media_manifest"), list) else [],
        "media_supported_count": packet.get("media_supported_count", 0),
        "media_unsupported_count": packet.get("media_unsupported_count", 0),
        "media_notes": packet.get("media_notes") if isinstance(packet.get("media_notes"), list) else [],
        "timeout_seconds": packet.get("timeout_seconds", DEFAULT_AGENT_TURN_TIMEOUT_SECONDS),
    }




def _agent_session_resume_mode(session: dict[str, object]) -> str:
    raw = clean_lobby_text(session.get("provider_session_id") or session.get("codex_session_id"), limit=200)
    if raw == "--last":
        return "last_forbidden"
    return "explicit_session_id" if clean_provider_session_id(raw) else "none"




def _codex_jsonl_visible_message_text(item: dict[str, object]) -> str:
    candidates: list[object] = [item.get("text"), item.get("content"), item.get("message")]
    payload = item.get("item") if isinstance(item.get("item"), dict) else {}
    candidates.extend([payload.get("text"), payload.get("content"), payload.get("message")])
    for candidate in candidates:
        if isinstance(candidate, str):
            text = clean_lobby_text(candidate, limit=8000)
            if text:
                return text
        if isinstance(candidate, list):
            parts = []
            for entry in candidate:
                if isinstance(entry, dict):
                    parts.append(str(entry.get("text") or entry.get("content") or ""))
                elif isinstance(entry, str):
                    parts.append(entry)
            text = clean_lobby_text("".join(parts), limit=8000)
            if text:
                return text
    return ""


def _codex_jsonl_is_agent_message(item: dict[str, object], event_type: str) -> bool:
    role = clean_lobby_text(item.get("role") or (item.get("item") or {}).get("role") if isinstance(item.get("item"), dict) else "", limit=64)
    item_type = clean_lobby_text(item.get("item_type") or (item.get("item") or {}).get("type") if isinstance(item.get("item"), dict) else "", limit=64)
    if not event_type.startswith("item."):
        return False
    if item_type:
        return item_type in {"agent_message", "assistant_message", "message"}
    return role in {"assistant", "agent"}


def _codex_jsonl_progress_text(item: dict[str, object], event_type: str) -> str:
    if "reasoning" not in event_type:
        return ""
    text = clean_lobby_text(item.get("summary") or item.get("progress"), limit=1000)
    return text


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


def room_sse_frames_after_cursor(
    output_root: Path,
    room_id: str,
    *,
    cursor: str = "",
    repository: RoomRepository | None = None,
) -> list[str]:
    store = repository or RoomStore(output_root)
    events = store.read_events(room_id, after=cursor)
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
    repository: RoomRepository | None = None,
):
    current_cursor = cursor
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        frames = room_sse_frames_after_cursor(
            output_root,
            room_id,
            cursor=current_cursor,
            repository=repository,
        )
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
    provider_session_id_raw = clean_lobby_text(session.get("provider_session_id") or session.get("codex_session_id"), limit=200)
    provider_session_id = clean_provider_session_id(provider_session_id_raw)
    if provider_kind == "codex_live_session":
        # Deterministic Codex CLI shape, verified in tests with a fake runner:
        # fresh:  codex exec --json --ephemeral --model <model> ... -
        # resume: codex exec --json --model <model> ... resume <provider_session_id> -
        # `--ignore-rules` prevents repo rules from mutating this read-only
        # launch path. Codex owns actual sandbox enforcement.
        diagnostics = []
        if provider_session_id_raw == "--last":
            diagnostics.append(
                {
                    "setting": "resume_mode",
                    "status": "last_forbidden",
                    "message": "Agent Session runtime forbids Codex resume --last; attach an explicit provider_session_id or use fresh mode.",
                }
            )
        command = ["codex", "exec", "--json"]
        if not provider_session_id:
            command.append("--ephemeral")
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["-c", f'model_reasoning_effort="{effort}"'])
        command.extend(["--sandbox", sandbox, "--ignore-rules", "--skip-git-repo-check"])
        if provider_session_id:
            command.extend(["resume", provider_session_id])
        return {
            "provider_kind": provider_kind,
            "command": command,
            "permission_enforcement": "codex_readonly" if sandbox == "read-only" else "advisory",
            "resume_mode": "explicit_session_id" if provider_session_id else ("last_forbidden" if provider_session_id_raw == "--last" else "none"),
            "provider_session_id": provider_session_id,
            "diagnostics": diagnostics,
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


def _agent_session_process_result(
    store: RoomRepository,
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


def room_status_payload(
    output_root: Path,
    room_id: str,
    *,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    store = repository or RoomStore(output_root)
    payload = store.room_payload(room_id)
    payload["active_participants"] = store.active_participants(room_id)
    return payload


def room_action_payload(
    output_root: Path,
    payload: dict[str, object],
    action: str,
    *,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    store = repository or RoomStore(output_root)
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    participant_id = clean_lobby_text(payload.get("participant_id") or payload.get("agent_id"), limit=128)
    reason = clean_lobby_text(payload.get("reason"), limit=500)
    if action == "leave":
        participant = store.set_participant_status(room_id, participant_id, "left", reason=reason)
        return {"status": "left", "participant": participant, **room_status_payload(output_root, room_id, repository=store)}
    if action == "kick":
        participant = store.set_participant_status(room_id, participant_id, "kicked", reason=reason)
        return {"status": "kicked", "participant": participant, **room_status_payload(output_root, room_id, repository=store)}
    if action == "export":
        result = store.export_participant(room_id, participant_id, reason=reason)
        return {"status": "exported", **result, **room_status_payload(output_root, room_id, repository=store)}
    raise ValueError(f"Unsupported room action: {action}")


def room_lifecycle_payload(
    output_root: Path,
    payload: dict[str, object],
    action: str,
    *,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    store = repository or RoomStore(output_root)
    room_id = clean_lobby_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    status = "archived" if action == "archive" else "closed"
    room = store.set_room_status(room_id, status)
    return {"status": status, "room": room, **room_status_payload(output_root, room_id, repository=store)}


def active_room_members(
    output_root: Path,
    room_id: str,
    *,
    repository: RoomRepository | None = None,
) -> list[dict[str, object]]:
    return (repository or RoomStore(output_root)).active_participants(room_id)


def merge_room_store_members(
    output_root: Path,
    meeting_id: str,
    existing_members: list[dict[str, object]],
    *,
    repository: RoomRepository | None = None,
) -> list[dict[str, object]]:
    if not meeting_id:
        return existing_members
    store = repository or RoomStore(output_root)
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
        session = store.session(meeting_id, str(participant.get("session_id") or participant_id))
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
            "session_id": participant.get("session_id", ""),
            "owner_id": participant.get("owner_id", ""),
            "created_by": participant.get("created_by", ""),
            "model_id": participant.get("model", ""),
            "effort": participant.get("effort", ""),
            "sandbox_enforcement": participant.get("sandbox", ""),
            "permission_option": participant.get("permissions", ""),
            "runtime_sharing_policy": participant.get("runtime_sharing_policy", ""),
            "execution_mode": "agent_session_app_server",
            "engagement_mode": "agent_session",
            "join_semantics": "agent_session",
            "session_status": session.get("status", ""),
            "source": "agent_session",
            "muted": bool(existing.get("muted", False)),
            "created_at": participant.get("created_at", ""),
            "updated_at": participant.get("updated_at", ""),
            "last_seen_at": participant.get("updated_at", ""),
        }
    return list(by_id.values())


def clean_room_request_payload(value: Any) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
