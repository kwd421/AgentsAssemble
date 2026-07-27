"""Bounded canonical turn execution for Agent Sessions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Iterable
from uuid import uuid4

from agentsassemble.application.agent_sessions.compatibility import (
    ensure_legacy_agent_session,
)
from agentsassemble.application.agent_sessions.turn_commands import (
    AgentTurnChunk,
    AgentTurnCommandRunner,
    AgentTurnCommandStreamer,
    AgentTurnRunner,
    _agent_session_resume_mode,
    agent_session_codex_jsonl_turn_runner,
    agent_session_command_turn_runner,
    build_agent_session_turn_command,
)
from agentsassemble.providers.codex_app_server import (
    DEFAULT_AGENT_TURN_TIMEOUT_SECONDS,
    _agent_turn_timeout_seconds,
    _context_error_detected,
    _diagnostic_items,
    _elapsed_ms,
    _now_iso,
    clean_agent_session_provider_kind,
    clean_provider_session_id,
)
from agentsassemble.providers.sync_cursor import (
    ProviderSyncCursorParityError,
    assert_provider_sync_cursor_parity,
    provider_sync_session_fields,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.room.turn_context import (
    _agent_turn_prompt,
    _nonnegative_int,
    build_room_turn_packet,
)


from typing import Callable

AgentTurnAdapter = Callable[[dict[str, object], dict[str, object]], Iterable[AgentTurnChunk]]


def run_agent_session_turn_payload(
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
    ensure_legacy_agent_session(session)
    if clean_agent_session_provider_kind(session.get("provider_kind")) not in {
        "",
        "codex_live_session",
    }:
        raise ValueError(
            "The legacy Agent Session turn path only supports Codex sessions."
        )

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
