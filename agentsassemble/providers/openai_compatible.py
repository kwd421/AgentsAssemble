from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.providers.api_work_tools import (
    ApiWorkHarness,
    parse_work_tool_arguments,
    work_tool_schemas,
)
from agentsassemble.providers.openai_compatible_room_tools import (
    accumulate_streaming_tool_calls,
    complete_tool_calls,
    execute_room_tool,
    room_tool_schemas,
)
from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.providers.turn_progress import ProviderTurnProgress
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.providers.provider_errors import provider_http_error
from agentsassemble.room.text import clean_room_text


UrlOpen = Callable[..., IO[bytes]]


class OpenAICompatibleApiRuntime:
    """Persistent OpenAI-compatible conversation state behind a room bridge."""

    def __init__(
        self,
        agent_id: str,
        *,
        api_key: str,
        provider_name: str,
        model: str,
        allowed_models: frozenset[str],
        reasoning_effort: str,
        allowed_reasoning_efforts: frozenset[str],
        base_url: str,
        message_source: str,
        variant: str = "",
        include_reasoning_in_messages: bool = False,
        request_payload: dict[str, object] | None = None,
        request_headers: dict[str, str] | None = None,
        require_api_key: bool = True,
        transport: str = "https_sse",
        opener: UrlOpen = urlopen,
        room_portal: RoomPortal | None = None,
        workspace: str = "",
        permission_mode: str = "meeting_read_only",
    ) -> None:
        if require_api_key and not str(api_key or "").strip():
            raise RuntimeError("credential_missing")
        self.provider_name = clean_room_text(provider_name, limit=64)
        if not self.provider_name:
            raise ValueError("OpenAI-compatible provider name is required.")
        self.agent_id = clean_room_text(agent_id, limit=128)
        self._api_key = str(api_key).strip()
        self.model = clean_room_text(model, limit=128)
        if self.model not in allowed_models:
            raise ValueError(f"Unsupported {self.provider_name} model: {self.model}")
        self.reasoning_effort = clean_room_text(reasoning_effort, limit=32)
        if self.reasoning_effort not in allowed_reasoning_efforts:
            allowed = ", ".join(sorted(allowed_reasoning_efforts))
            raise ValueError(
                f"{self.provider_name} reasoning_effort must be one of: {allowed}."
            )
        self.variant = clean_room_text(variant, limit=64)
        self._include_reasoning_in_messages = bool(
            include_reasoning_in_messages
        )
        self.base_url = str(base_url or "").rstrip("/")
        self.message_source = clean_room_text(message_source, limit=64)
        self._request_payload = dict(request_payload or {})
        self._request_headers = dict(request_headers or {})
        self._transport = clean_room_text(transport, limit=64) or "https_sse"
        self._opener = opener
        self._room_portal = room_portal
        self.permission_mode = clean_room_text(permission_mode, limit=64) or "meeting_read_only"
        self._work_harness = ApiWorkHarness(
            workspace or ".",
            permission_mode=self.permission_mode,
        )
        self._messages: list[dict[str, object]] = []
        self._pending = ""
        self._pending_room_observation = False
        self._running = False
        self._started_at = ""
        self._last_error = ""
        self._interrupted = threading.Event()
        self._lock = threading.RLock()
        self._response: IO[bytes] | None = None

    def set_request_handler(self, handler: ProviderRequestHandler) -> None:
        self._work_harness.request_handler = handler

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
            raise RuntimeError(
                f"{self.provider_name} room observations require a private room portal."
            )
        self._queue(text, room_observation=True)

    def _queue(self, text: str, *, room_observation: bool) -> None:
        content = str(text or "").strip()
        if not content:
            raise ValueError(f"{self.provider_name} turn input is required.")
        self.start()
        with self._lock:
            if self._pending:
                raise RuntimeError(
                    f"{self.provider_name} runtime is already processing a turn."
                )
            self._pending = content
            self._pending_room_observation = room_observation
            self._interrupted.clear()

    def read_output(
        self,
        *,
        timeout_seconds: float,
        on_delta=None,
        on_activity=None,
    ) -> dict[str, object]:
        with self._lock:
            prompt = self._pending
            self._pending = ""
            room_observation = self._pending_room_observation
            self._pending_room_observation = False
            messages = [*self._messages, {"role": "user", "content": prompt}]
        if not prompt:
            raise RuntimeError(f"{self.provider_name} runtime has no pending turn.")
        progress = ProviderTurnProgress(timeout_seconds)
        tool_rounds = 0
        room_action_completed = False
        observed_model_id = ""
        api_calls: list[dict[str, object]] = []
        try:
            while True:
                round_result = self._stream_round(
                    messages,
                    room_observation=room_observation,
                    timeout_seconds=max(1.0, progress.remaining()),
                    progress=progress,
                    on_delta=on_delta,
                    on_reasoning=(
                        _reasoning_activity_reporter(
                            on_activity,
                            activity_id=f"api-reasoning-{len(api_calls) + 1}",
                        )
                        if on_activity is not None
                        else None
                    ),
                )
                observed_model_id = round_result.observed_model_id or observed_model_id
                api_calls.append(round_result.usage)
                if not round_result.tool_calls:
                    content = round_result.content.strip()
                    if not content and room_action_completed:
                        content = "RoomPortal action completed."
                    elif not content:
                        raise RuntimeError(
                            _empty_round_message(
                                self.provider_name,
                                round_result,
                                max_output_tokens=self._request_payload.get("max_tokens"),
                            )
                        )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": content,
                            **(
                                {"reasoning_content": round_result.reasoning_content}
                                if (
                                    self._include_reasoning_in_messages
                                    and round_result.reasoning_content
                                )
                                else {}
                            ),
                        }
                    )
                    break
                tool_rounds += 1
                if tool_rounds > 16:
                    raise RuntimeError(
                        f"{self.provider_name} exceeded the bounded tool-call rounds."
                    )
                assistant_message: dict[str, object] = {
                    "role": "assistant",
                    "content": round_result.content or None,
                    "tool_calls": round_result.tool_calls,
                }
                if (
                    self._include_reasoning_in_messages
                    and round_result.reasoning_content
                ):
                    assistant_message["reasoning_content"] = round_result.reasoning_content
                messages.append(assistant_message)
                for tool_call in round_result.tool_calls:
                    function = tool_call.get("function")
                    tool_name = (
                        str(function.get("name") or "")
                        if isinstance(function, dict)
                        else ""
                    )
                    activity_id = clean_room_text(tool_call.get("id"), limit=128)
                    activity_title, activity_detail = _tool_activity(
                        tool_call,
                        room_portal=self._room_portal if room_observation else None,
                    )
                    activity_fields = {
                        "category": "tool",
                        "activity_id": activity_id,
                        "activity_title": activity_title,
                        "activity_detail": activity_detail,
                        "content": activity_detail or activity_title,
                    }
                    if on_activity is not None:
                        on_activity({**activity_fields, "status": "running"})
                    try:
                        if tool_name in _work_tool_names():
                            executed_name, arguments = parse_work_tool_arguments(tool_call)
                            tool_result = json.dumps(
                                self._work_harness.execute(executed_name, arguments),
                                ensure_ascii=False,
                            )
                        elif room_observation and self._room_portal is not None:
                            executed_name, tool_result = execute_room_tool(
                                self._room_portal,
                                tool_call,
                            )
                        else:
                            raise RuntimeError(
                                f"{self.provider_name} requested a room tool outside a room observation."
                            )
                    except Exception:
                        if on_activity is not None:
                            on_activity({**activity_fields, "status": "failed"})
                        raise
                    if executed_name in {
                        "publish_message",
                        "decline_to_speak",
                        "create_vote",
                        "cast_vote",
                    }:
                        room_action_completed = True
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(tool_call.get("id") or ""),
                            "name": executed_name,
                            "content": tool_result,
                        }
                    )
                    if on_activity is not None:
                        on_activity({**activity_fields, "status": "completed"})
                    progress.record()
                if progress.expired():
                    raise TimeoutError(
                        f"{self.provider_name} runtime timed out after {timeout_seconds} seconds."
                    )
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
                    "message_source": self.message_source,
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
        progress: ProviderTurnProgress,
        on_delta=None,
        on_reasoning=None,
    ) -> _StreamRound:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._request_payload,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        tools = [*work_tool_schemas(self.permission_mode)]
        if room_observation and self._room_portal is not None:
            tools.extend(room_tool_schemas(self._room_portal.active_tool_names()))
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "AgentsAssemble/1.0",
            **self._request_headers,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls = {}
        observed_model_id = ""
        usage: dict[str, object] = {}
        finish_reason = ""
        started_at = _now()
        try:
            response = self._opener(request, timeout=max(1.0, float(timeout_seconds)))
        except HTTPError as error:
            raise provider_http_error(error, provider_name=self.provider_name) from error
        with self._lock:
            self._response = response
        progress.record()
        try:
            for raw_line in response:
                if self._interrupted.is_set():
                    raise RuntimeError(f"{self.provider_name} turn interrupted.")
                if progress.expired():
                    raise TimeoutError(
                        f"{self.provider_name} runtime timed out after "
                        f"{timeout_seconds:g} seconds."
                    )
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    progress.record()
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
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    # Why the round ended. Without it a response cut off at the
                    # token ceiling is indistinguishable from a genuinely empty
                    # one, and both surfaced as the same unhelpful
                    # "completed without a final message".
                    finish_reason = (
                        clean_room_text(choices[0].get("finish_reason"), limit=64)
                        or finish_reason
                    )
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
                reasoning = str(
                    delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("thinking")
                    or ""
                )
                if text:
                    content_parts.append(text)
                    if on_delta is not None:
                        on_delta(text)
                if reasoning:
                    reasoning_parts.append(reasoning)
                    if on_reasoning is not None:
                        on_reasoning(reasoning, False)
                raw_tool_calls = delta.get("tool_calls")
                accumulate_streaming_tool_calls(tool_calls, raw_tool_calls)
                if text or reasoning or raw_tool_calls or finish_reason or usage:
                    progress.record()
        finally:
            with self._lock:
                if self._response is response:
                    self._response = None
            try:
                response.close()
            except Exception:
                pass
        reasoning_content = "".join(reasoning_parts)
        if reasoning_content and on_reasoning is not None:
            on_reasoning("", True)
        return _StreamRound(
            content="".join(content_parts),
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
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
                "transport": self._transport,
                "pty": False,
                "is_one_shot": False,
                "provider_session_active": self._running,
                "provider_session_reused": bool(self._messages),
                "started_at": self._started_at,
                "last_error": self._last_error,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "variant": self.variant,
                "permission_mode": self.permission_mode,
                "work_harness": self._work_harness.enabled,
                "workspace": str(self._work_harness.workspace) if self._work_harness.enabled else "",
            }


@dataclass(frozen=True)
class _StreamRound:
    content: str
    reasoning_content: str
    tool_calls: list[dict[str, object]]
    observed_model_id: str
    usage: dict[str, object]
    finish_reason: str = ""


def _reasoning_activity_reporter(on_activity, *, activity_id: str):
    """Coalesce provider reasoning deltas into bounded live activity updates."""

    parts: list[str] = []
    last_reported_chars = 0
    last_reported_at = 0.0

    def report(delta: str, completed: bool) -> None:
        nonlocal last_reported_chars, last_reported_at
        if delta:
            parts.append(str(delta))
        content = "".join(parts)
        now = time.monotonic()
        should_report = completed or (
            len(content) - last_reported_chars >= 40
            or now - last_reported_at >= 0.15
        )
        if not content or not should_report:
            return
        visible = content[-2000:]
        on_activity(
            {
                "category": "reasoning",
                "status": "completed" if completed else "running",
                "activity_id": activity_id,
                "activity_title": "생각",
                "activity_detail": visible,
                "content": visible,
            }
        )
        last_reported_chars = len(content)
        last_reported_at = now

    return report


def _empty_round_message(
    provider_name: str,
    round_result: "_StreamRound",
    *,
    max_output_tokens: object = None,
) -> str:
    """Say why the round produced no text, not merely that it did not.

    A reasoning model can spend the whole output budget thinking and stop at the
    ceiling before writing an answer; that arrives as finish_reason "length"
    with reasoning present and content empty. Reporting it identically to a
    genuinely empty response left the operator with nothing to act on.
    """
    if round_result.finish_reason == "length":
        budget = ""
        try:
            limit = int(max_output_tokens or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit:
            budget = f" (최대 응답 길이 {limit:,} 토큰)"
        thought = (
            "추론에만 쓰고 답변을 시작하지 못했습니다"
            if round_result.reasoning_content
            else "답변을 끝내지 못했습니다"
        )
        return (
            f"{provider_name}이(가) 최대 응답 길이에 걸려 {thought}{budget}. "
            "최대 응답 길이를 늘리거나 추론 강도를 낮추세요."
        )
    if round_result.reasoning_content:
        return (
            f"{provider_name}이(가) 추론만 남기고 답변 본문을 반환하지 않았습니다"
            f" (종료 사유: {round_result.finish_reason or '없음'})."
        )
    return (
        f"{provider_name} completed without a final message"
        f" (finish_reason: {round_result.finish_reason or 'none'})."
    )


_WORK_TOOL_NAMES = frozenset(
    {
        "list_workspace_files",
        "read_workspace_file",
        "search_workspace_text",
        "write_workspace_file",
        "replace_workspace_text",
        "run_workspace_command",
    }
)


def _work_tool_names() -> frozenset[str]:
    return _WORK_TOOL_NAMES


def _tool_title(tool_name: object) -> str:
    value = clean_room_text(tool_name, limit=120)
    return {
        "read_discussion": "방 대화 읽기",
        "list_participants": "참가자 확인",
        "publish_message": "메시지 공개",
        "decline_to_speak": "발언 건너뛰기",
        "roll_dice": "주사위 굴리기",
        "choose_random": "무작위 선택",
        "create_vote": "투표 만들기",
        "cast_vote": "투표하기",
        "vote_summary": "투표 결과 확인",
        "list_workspace_files": "작업 폴더 살펴보기",
        "read_workspace_file": "파일 읽기",
        "search_workspace_text": "파일 내용 검색",
        "write_workspace_file": "파일 쓰기",
        "replace_workspace_text": "파일 수정",
        "run_workspace_command": "명령 실행",
    }.get(value, value or "도구")


def _tool_activity(
    tool_call: dict[str, object],
    *,
    room_portal: RoomPortal | None = None,
) -> tuple[str, str]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return "도구", ""
    name = clean_room_text(function.get("name"), limit=120)
    title = _tool_title(name)
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except (TypeError, json.JSONDecodeError):
        return title, ""
    if not isinstance(arguments, dict):
        return title, ""
    if name == "roll_dice":
        return title, clean_room_text(arguments.get("notation"), limit=64)
    if name == "choose_random":
        options = arguments.get("options")
        if isinstance(options, list):
            return title, f"{len(options)}개 선택지"
    if name == "publish_message":
        target = (
            room_portal.resolve_handoff_target(arguments.get("next_agent_id"))
            if room_portal is not None
            else ""
        )
        if target:
            return title, f"<@{target}>에게 전달"
    if name in {"read_workspace_file", "write_workspace_file", "replace_workspace_text"}:
        return title, clean_room_text(arguments.get("path"), limit=240)
    if name == "search_workspace_text":
        return title, clean_room_text(arguments.get("query"), limit=240)
    if name == "run_workspace_command":
        command = arguments.get("command")
        if isinstance(command, list):
            return title, " ".join(str(part) for part in command)[:240]
    return title, ""


def _bounded_messages(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    turns: list[list[dict[str, object]]] = []
    for message in messages:
        if message.get("role") == "user" or not turns:
            turns.append([])
        turns[-1].append(message)

    def message_count() -> int:
        return sum(len(turn) for turn in turns)

    def encoded_size() -> int:
        return sum(
            len(json.dumps(message, ensure_ascii=False, default=str))
            for turn in turns
            for message in turn
        )

    # Tool results are protocol children of the preceding assistant tool-call
    # message. Evict complete user turns so retention can never leave an orphan
    # `tool` message that OpenAI-compatible providers reject.
    while len(turns) > 1 and (message_count() > 50 or encoded_size() > 64_000):
        turns.pop(0)
    return [message for turn in turns for message in turn]


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
