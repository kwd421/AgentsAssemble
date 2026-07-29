from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO
from urllib.request import Request, urlopen

from agentsassemble.providers.openai_compatible_room_tools import (
    ROOM_TOOL_SCHEMAS,
    accumulate_streaming_tool_calls,
    complete_tool_calls,
    execute_room_tool,
)
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.text import clean_room_text


UrlOpen = Callable[..., IO[bytes]]


class DeepSeekApiRuntime:
    """Persistent conversation state behind the room bridge streaming contract."""

    def __init__(
        self,
        agent_id: str,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        reasoning_effort: str = "high",
        thinking: bool = True,
        base_url: str = "https://api.deepseek.com",
        opener: UrlOpen = urlopen,
        room_portal: RoomPortal | None = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise RuntimeError("credential_missing")
        self.agent_id = clean_room_text(agent_id, limit=128)
        self._api_key = str(api_key).strip()
        self.model = clean_room_text(model, limit=128) or "deepseek-v4-flash"
        if self.model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
            raise ValueError(f"Unsupported DeepSeek model: {self.model}")
        self.reasoning_effort = clean_room_text(reasoning_effort, limit=32) or "high"
        if self.reasoning_effort not in {"high", "max"}:
            raise ValueError("DeepSeek reasoning_effort must be high or max.")
        self.thinking = bool(thinking)
        self.base_url = str(base_url or "").rstrip("/")
        self._opener = opener
        self._room_portal = room_portal
        self._messages: list[dict[str, object]] = []
        self._pending = ""
        self._pending_room_observation = False
        self._running = False
        self._started_at = ""
        self._last_error = ""
        self._interrupted = threading.Event()
        self._lock = threading.RLock()
        self._response: IO[bytes] | None = None

    def start(self) -> dict[str, object]:
        with self._lock:
            self._running = True
            self._started_at = self._started_at or _now()
            self._last_error = ""
        return self.health()

    def send(self, text: str) -> None:
        self._queue(text, room_observation=False)

    def send_room_observation(
        self,
        text: str,
        *,
        media_blocks: list[dict[str, str]] | None = None,
    ) -> None:
        del media_blocks
        if self._room_portal is None:
            raise RuntimeError("DeepSeek room observations require a private room portal.")
        self._queue(text, room_observation=True)

    def _queue(self, text: str, *, room_observation: bool) -> None:
        content = str(text or "").strip()
        if not content:
            raise ValueError("DeepSeek turn input is required.")
        self.start()
        with self._lock:
            if self._pending:
                raise RuntimeError("DeepSeek runtime is already processing a turn.")
            self._pending = content
            self._pending_room_observation = room_observation
            self._interrupted.clear()

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None) -> dict[str, object]:
        with self._lock:
            prompt = self._pending
            self._pending = ""
            room_observation = self._pending_room_observation
            self._pending_room_observation = False
            messages = [*self._messages, {"role": "user", "content": prompt}]
        if not prompt:
            raise RuntimeError("DeepSeek runtime has no pending turn.")
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        tool_rounds = 0
        observed_model_id = ""
        api_calls: list[dict[str, object]] = []
        try:
            while True:
                round_result = self._stream_round(
                    messages,
                    room_observation=room_observation,
                    timeout_seconds=max(1.0, deadline - time.monotonic()),
                    on_delta=on_delta,
                )
                observed_model_id = round_result.observed_model_id or observed_model_id
                api_calls.append(round_result.usage)
                if not round_result.tool_calls:
                    content = round_result.content.strip()
                    if not content:
                        raise RuntimeError("DeepSeek completed without a final message.")
                    messages.append(
                        {
                            "role": "assistant",
                            "content": content,
                            **(
                                {"reasoning_content": round_result.reasoning_content}
                                if round_result.reasoning_content
                                else {}
                            ),
                        }
                    )
                    break
                if not room_observation or self._room_portal is None:
                    raise RuntimeError("DeepSeek requested room tools outside a room observation.")
                tool_rounds += 1
                if tool_rounds > 8:
                    raise RuntimeError("DeepSeek exceeded the bounded room tool-call rounds.")
                assistant_message: dict[str, object] = {
                    "role": "assistant",
                    "content": round_result.content or None,
                    "tool_calls": round_result.tool_calls,
                }
                if round_result.reasoning_content:
                    assistant_message["reasoning_content"] = round_result.reasoning_content
                messages.append(assistant_message)
                for tool_call in round_result.tool_calls:
                    function = tool_call.get("function")
                    tool_name = (
                        str(function.get("name") or "")
                        if isinstance(function, dict)
                        else ""
                    )
                    if on_activity is not None:
                        on_activity(
                            {
                                "category": "tool",
                                "status": "running",
                                "content": f"Using room tool: {tool_name}",
                            }
                        )
                    executed_name, tool_result = execute_room_tool(
                        self._room_portal,
                        tool_call,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(tool_call.get("id") or ""),
                            "name": executed_name,
                            "content": tool_result,
                        }
                    )
                    if on_activity is not None:
                        on_activity(
                            {
                                "category": "tool",
                                "status": "completed",
                                "content": f"Used room tool: {executed_name}",
                            }
                        )
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"DeepSeek runtime timed out after {timeout_seconds} seconds.")
            with self._lock:
                self._messages = _bounded_messages(messages)
                self._last_error = ""
            return {
                "outcome": "message",
                "actor_id": self.agent_id,
                "actor_type": "agent",
                "kind": "agent_message",
                "content": content,
                "metadata": {
                    "message_source": "deepseek_sse",
                    "model": self.model,
                    "observed_model_id": observed_model_id,
                    "room_tool_rounds": tool_rounds,
                    "api_calls": api_calls,
                    "token_usage": _aggregate_usage(api_calls),
                },
            }
        except Exception as error:
            with self._lock:
                self._last_error = type(error).__name__
            raise
        finally:
            with self._lock:
                response = self._response
                self._response = None
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def _stream_round(
        self,
        messages: list[dict[str, object]],
        *,
        room_observation: bool,
        timeout_seconds: float,
        on_delta=None,
    ) -> _StreamRound:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "thinking": {"type": "enabled" if self.thinking else "disabled"},
            "reasoning_effort": self.reasoning_effort,
        }
        if room_observation:
            payload["tools"] = list(ROOM_TOOL_SCHEMAS)
            payload["tool_choice"] = "auto"
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls = {}
        observed_model_id = ""
        usage: dict[str, object] = {}
        started_at = _now()
        response = self._opener(request, timeout=max(1.0, float(timeout_seconds)))
        with self._lock:
            self._response = response
        try:
            for raw_line in response:
                if self._interrupted.is_set():
                    raise RuntimeError("DeepSeek turn interrupted.")
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if isinstance(chunk, dict):
                    observed_model_id = (
                        clean_room_text(chunk.get("model"), limit=128)
                        or observed_model_id
                    )
                    if isinstance(chunk.get("usage"), dict):
                        usage = _normalized_usage(chunk["usage"])
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                delta = (
                    choices[0].get("delta")
                    if isinstance(choices, list)
                    and choices
                    and isinstance(choices[0], dict)
                    else {}
                )
                if not isinstance(delta, dict):
                    continue
                text = str(delta.get("content") or "")
                reasoning = str(delta.get("reasoning_content") or "")
                if text:
                    content_parts.append(text)
                    if on_delta is not None:
                        on_delta(text)
                if reasoning:
                    reasoning_parts.append(reasoning)
                accumulate_streaming_tool_calls(tool_calls, delta.get("tool_calls"))
        finally:
            with self._lock:
                if self._response is response:
                    self._response = None
            try:
                response.close()
            except Exception:
                pass
        return _StreamRound(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            tool_calls=complete_tool_calls(tool_calls),
            observed_model_id=observed_model_id,
            usage={
                "started_at": started_at,
                "finished_at": _now(),
                **usage,
            },
        )

    def interrupt(self) -> None:
        self._interrupted.set()
        with self._lock:
            response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.interrupt()
        with self._lock:
            self._running = False
            self._pending = ""
            self._pending_room_observation = False
            self._api_key = ""

    def health(self) -> dict[str, object]:
        with self._lock:
            return {
                "agent_id": self.agent_id,
                "runtime_kind": "api",
                "running": self._running,
                "transport": "https_sse",
                "pty": False,
                "is_one_shot": False,
                "provider_session_active": self._running,
                "provider_session_reused": bool(self._messages),
                "started_at": self._started_at,
                "last_error": self._last_error,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "variant": "thinking" if self.thinking else "non_thinking",
                "permission_mode": "meeting_read_only",
            }


@dataclass(frozen=True)
class _StreamRound:
    content: str
    reasoning_content: str
    tool_calls: list[dict[str, object]]
    observed_model_id: str
    usage: dict[str, object]


def _bounded_messages(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    bounded = messages[-50:]
    while bounded and sum(
        len(json.dumps(item, ensure_ascii=False, default=str))
        for item in bounded
    ) > 64_000:
        bounded.pop(0)
    return bounded


def _normalized_usage(value: dict[str, object]) -> dict[str, int]:
    details = (
        value.get("completion_tokens_details")
        if isinstance(value.get("completion_tokens_details"), dict)
        else {}
    )
    return {
        "input_tokens": _usage_int(value.get("prompt_tokens")),
        "output_tokens": _usage_int(value.get("completion_tokens")),
        "total_tokens": _usage_int(value.get("total_tokens")),
        "cache_hit_input_tokens": _usage_int(value.get("prompt_cache_hit_tokens")),
        "cache_miss_input_tokens": _usage_int(value.get("prompt_cache_miss_tokens")),
        "reasoning_tokens": _usage_int(details.get("reasoning_tokens")),
    }


def _aggregate_usage(api_calls: list[dict[str, object]]) -> dict[str, int]:
    fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_hit_input_tokens",
        "cache_miss_input_tokens",
        "reasoning_tokens",
    )
    return {
        field: sum(_usage_int(call.get(field)) for call in api_calls)
        for field in fields
    }


def _usage_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _now() -> str:
    return datetime.now(UTC).isoformat()
