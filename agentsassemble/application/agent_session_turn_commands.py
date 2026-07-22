"""One-shot command and JSONL adapters for Agent Session turns."""

from __future__ import annotations

import hashlib
import json
import select
import subprocess
import time
from typing import Callable, Iterable

from agentsassemble.application.agent_session_process import build_agent_session_launch_plan
from agentsassemble.providers.codex_app_server import (
    DEFAULT_AGENT_TURN_TIMEOUT_SECONDS,
    _context_error_detected,
    clean_provider_session_id,
)
from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.room.turn_context import _agent_turn_prompt


AgentTurnChunk = dict[str, object]
AgentTurnRunner = Callable[[dict[str, object]], Iterable[AgentTurnChunk]]
AgentTurnCommandRunner = Callable[[list[str], str, float], subprocess.CompletedProcess[str]]
AgentTurnCommandStreamer = Callable[[list[str], str, float], Iterable[AgentTurnChunk]]


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

