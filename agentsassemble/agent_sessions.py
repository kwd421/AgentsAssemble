from __future__ import annotations

import json
import hashlib
import select
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_store import RoomStore

CommandRunner = Callable[[list[str]], dict[str, object] | subprocess.CompletedProcess[str] | None]
AgentTurnChunk = dict[str, object]
AgentTurnRunner = Callable[[dict[str, object]], Iterable[AgentTurnChunk]]
AgentTurnCommandRunner = Callable[[list[str], str, float], subprocess.CompletedProcess[str]]
AgentTurnCommandStreamer = Callable[[list[str], str, float], Iterable[AgentTurnChunk]]
DEFAULT_AGENT_TURN_TIMEOUT_SECONDS = 600.0
DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS = 40
DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS = 20000
UNSUPPORTED_MEDIA_AUDIT_NOTE = "Unsupported media is listed for audit only; do not claim you viewed unsupported files."


def resume_agent_session_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
    process_service: "AgentSessionProcessService | None" = None,
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


class AgentSessionProcessService:
    """Owns Agent Session state/process separation for CLI and HTTP callers."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        turn_runner: AgentTurnRunner | None = None,
        turn_command_runner: AgentTurnCommandRunner | None = None,
        turn_command_streamer: AgentTurnCommandStreamer | None = None,
    ) -> None:
        self.command_runner = command_runner
        self.turn_runner = turn_runner
        self.turn_command_runner = turn_command_runner
        self.turn_command_streamer = turn_command_streamer

    def resume(
        self,
        store: RoomStore,
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
    ) -> dict[str, object]:
        return run_agent_session_turn_payload(
            output_root,
            payload,
            turn_runner=self.turn_runner,
            turn_command_runner=self.turn_command_runner,
            turn_command_streamer=self.turn_command_streamer,
        )


def run_agent_session_turn_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    turn_runner: AgentTurnRunner | None = None,
    turn_command_runner: AgentTurnCommandRunner | None = None,
    turn_command_streamer: AgentTurnCommandStreamer | None = None,
) -> dict[str, object]:
    store = RoomStore(output_root)
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
    )
    turn_id = clean_lobby_text(payload.get("turn_id"), limit=128) or f"turn-{uuid4().hex[:12]}"
    provider_kind = clean_lobby_text(session.get("provider_kind"), limit=64)
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
    streaming = False
    timeout_seconds = _agent_turn_timeout_seconds(payload.get("timeout_seconds"))
    if turn_runner is None and turn_command_streamer is not None:
        runner_kind = "codex_jsonl_command"
        streaming = True
        turn_runner = agent_session_codex_jsonl_turn_runner(
            session,
            command_streamer=turn_command_streamer,
            timeout_seconds=timeout_seconds,
        )
    if turn_runner is None and turn_command_runner is not None:
        runner_kind = "final_command"
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
        "command_shape_hash": _command_shape_hash(command),
        "prompt_chars": len(_agent_turn_prompt(packet)),
        "prompt_bytes": len(_agent_turn_prompt(packet).encode("utf-8")),
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
                    runtime_state["provider_session_id"] = provider_session_id
                    store.upsert_session(room_id, {**session, "provider_session_id": provider_session_id})
                continue
            if event_type == "diagnostics":
                _merge_runtime_diagnostics(runtime_state, chunk)
                continue
            if event_type not in {"thinking_delta", "message_delta", "message_final", "error"}:
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
                return {
                    "status": "error",
                    "turn_status": "error",
                    "turn_id": turn_id,
                    "packet": packet,
                    "events": appended,
                    "diagnostics": [*_diagnostic_items(runtime_state), *diagnostics],
                }
    except Exception as error:  # pragma: no cover - defensive for injected runners
        appended.append(
            store.append_event(
                room_id,
                "error",
                participant_id=agent_id,
                session_id=session_id,
                provider_kind=provider_kind,
                turn_id=turn_id,
                diagnostics=[{"setting": "turn_runner", "status": "failed", "message": str(error)}],
            )
        )
        return {
            "status": "error",
            "turn_status": "error",
            "turn_id": turn_id,
            "packet": packet,
            "events": appended,
            "diagnostics": runtime_diagnostics,
        }
    appended.append(
        store.append_event(
            room_id,
            "turn_finished",
            participant_id=agent_id,
            session_id=session_id,
            provider_kind=provider_kind,
            turn_id=turn_id,
            diagnostics=_diagnostic_items({**runtime_state, "turn_finished_ms": _elapsed_ms(started_monotonic)}),
        )
    )
    packet_events = packet.get("events") if isinstance(packet.get("events"), list) else []
    if packet_events:
        store.upsert_session(room_id, {**store.session(room_id, session_id), "last_seen_event_id": packet_events[-1].get("id")})
    return {
        "status": "finished",
        "turn_status": "finished",
        "turn_id": turn_id,
        "packet": packet,
        "events": appended,
        "diagnostics": _diagnostic_items(runtime_state),
    }


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


def _agent_turn_prompt(packet: dict[str, object]) -> str:
    return (
        "You are answering one AgentsAssemble room turn. Read the JSON packet, "
        "use only the room-visible context and supported media manifest, follow "
        "the explicit non-goals, and return one room-visible answer.\n\n"
        + json.dumps(packet, ensure_ascii=False, sort_keys=True)
        + "\n"
    )


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


def _agent_turn_timeout_seconds(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_AGENT_TURN_TIMEOUT_SECONDS
    if parsed <= 0:
        return DEFAULT_AGENT_TURN_TIMEOUT_SECONDS
    return min(parsed, DEFAULT_AGENT_TURN_TIMEOUT_SECONDS)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _elapsed_ms(started_monotonic: float) -> int:
    return int((time.monotonic() - started_monotonic) * 1000)


def _command_shape_hash(command: list[str]) -> str:
    redacted = ["<id>" if index and command[index - 1] == "resume" else part for index, part in enumerate(command)]
    return hashlib.sha256(json.dumps(redacted, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _diagnostic_items(state: dict[str, object]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for key, value in state.items():
        if value in (None, "", [], {}):
            continue
        items.append({"setting": str(key), "status": str(value), "message": str(value)})
    return items


def _merge_runtime_diagnostics(state: dict[str, object], chunk: dict[str, object]) -> None:
    for key in (
        "time_to_process_spawn_ms",
        "time_to_first_stdout_ms",
        "time_to_first_json_event_ms",
        "process_exit_ms",
        "exit_code",
        "stdout_bytes",
        "stderr_tail",
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


def _context_error_detected(values: object) -> bool:
    text = str(values).lower()
    return "context window" in text or "ran out of room" in text or "context_length" in text


def clean_provider_session_id(value: object) -> str:
    provider_session_id = clean_lobby_text(value, limit=200)
    if provider_session_id == "--last":
        return ""
    return provider_session_id


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


def build_room_turn_packet(
    output_root: Path,
    *,
    room_id: str,
    participant_id: str,
    session_id: str,
    instruction: str,
    max_recent_events: object = None,
    max_prompt_chars: object = None,
) -> dict[str, object]:
    store = RoomStore(output_root)
    session = store.session(room_id, session_id)
    last_seen_event_id = clean_lobby_text(session.get("last_seen_event_id"), limit=128)
    events_after_seen = store.read_events(room_id, after=last_seen_event_id) if last_seen_event_id else store.read_events(room_id)
    all_events = store.read_events(room_id)
    recent_limit = _positive_int(max_recent_events, DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS)
    prompt_limit = _positive_int(max_prompt_chars, DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS)
    recent_events = all_events[-recent_limit:]
    events = _dedupe_events([*events_after_seen, *recent_events])
    session = store.session(room_id, session_id)
    media_manifest = []
    for event in events:
        media = event.get("media")
        if isinstance(media, dict):
            media_manifest.append(dict(media))
    unsupported_media = [media for media in media_manifest if not bool(media.get("supported"))]
    summary = session.get("summary") if isinstance(session.get("summary"), dict) else {}
    packet = {
        "room_id": room_id,
        "participant_id": participant_id,
        "session_id": session_id,
        "provider_session_id": clean_provider_session_id(session.get("provider_session_id")),
        "summary": summary,
        "include_summary": bool(summary),
        "summary_checkpoint_id": clean_lobby_text(summary.get("up_to_event_id") if isinstance(summary, dict) else "", limit=128),
        "after_event_id": last_seen_event_id,
        "events": events,
        "recent_event_count": len(recent_events),
        "max_recent_events": recent_limit,
        "max_prompt_chars": prompt_limit,
        "media_manifest": media_manifest,
        "media_supported_count": len([media for media in media_manifest if bool(media.get("supported"))]),
        "media_unsupported_count": len(unsupported_media),
        "media_notes": [UNSUPPORTED_MEDIA_AUDIT_NOTE] if unsupported_media else [],
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
    return _bound_room_turn_packet(packet, prompt_limit)


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _dedupe_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    deduped = []
    for event in events:
        event_id = str(event.get("id") or "")
        key = event_id or json.dumps(event, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _bound_room_turn_packet(packet: dict[str, object], prompt_limit: int) -> dict[str, object]:
    events = list(packet.get("events") if isinstance(packet.get("events"), list) else [])
    while events and len(_agent_turn_prompt({**packet, "events": events})) > prompt_limit:
        events.pop(0)
    return {**packet, "events": events, "event_count_in_packet": len(events)}


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
