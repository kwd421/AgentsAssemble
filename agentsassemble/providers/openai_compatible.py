from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import IO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.providers.api_context import (
    ApiContextLimitError,
    ApiContextPolicy,
    ApiContextProtocolError,
    ApiContextReferenceError,
    DEFAULT_API_CONTEXT_CONTRACT_BYTES,
)
from agentsassemble.providers.api_session import (
    ApiContextCheckpointMissing,
    ApiConversationStore,
)
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
from agentsassemble.providers.openai_compatible_transcript import (
    OpenAIStreamRound,
    aggregate_usage,
    empty_round_message,
    normalized_usage,
    reasoning_activity_reporter,
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
        context_contract_bytes: int = DEFAULT_API_CONTEXT_CONTRACT_BYTES,
        state_dir: str = "",
        resume_required: bool = False,
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
        self._conversation_store = (
            ApiConversationStore(
                state_dir,
                agent_id=self.agent_id,
                provider_name=self.provider_name,
                model=self.model,
            )
            if str(state_dir or "").strip()
            else None
        )
        if resume_required and (
            self._conversation_store is None or not self._conversation_store.exists
        ):
            raise ApiContextCheckpointMissing(
                "This API Agent Session has prior turns but no recoverable checkpoint; "
                "refusing to start a fresh conversation silently."
            )
        if self._conversation_store is None:
            self._messages: list[dict[str, object]] = []
            delivered_tool_call_ids: set[str] = set()
            tool_result_references: dict[str, str] = {}
        else:
            (
                self._messages,
                delivered_tool_call_ids,
                tool_result_references,
            ) = self._conversation_store.load()
        self._pending = ""
        self._pending_room_observation = False
        self._running = False
        self._started_at = ""
        self._last_error = ""
        self._interrupted = threading.Event()
        self._lock = threading.RLock()
        self._response: IO[bytes] | None = None
        self._context_policy = ApiContextPolicy(context_contract_bytes)
        self._delivered_tool_call_ids = delivered_tool_call_ids
        self._tool_result_references = tool_result_references
        self._context_compaction_count = 0

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
                    on_activity=on_activity,
                    on_reasoning=(
                        reasoning_activity_reporter(
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
                            empty_round_message(
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
                    tool_call_id = str(tool_call.get("id") or "")
                    function = tool_call.get("function")
                    tool_name = (
                        str(function.get("name") or "")
                        if isinstance(function, dict)
                        else ""
                    )
                    activity_id = clean_room_text(tool_call_id, limit=128)
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
                            "tool_call_id": tool_call_id,
                            "name": executed_name,
                            "content": tool_result,
                        }
                    )
                    if self._conversation_store is not None:
                        self._tool_result_references[tool_call_id] = (
                            self._conversation_store.record_tool_result(
                                tool_call_id,
                                tool_result,
                            )
                        )
                    if on_activity is not None:
                        on_activity({**activity_fields, "status": "completed"})
                    progress.record()
                if progress.expired():
                    raise TimeoutError(
                        f"{self.provider_name} runtime timed out after {timeout_seconds} seconds."
                    )
            with self._lock:
                retained_messages = _bounded_messages(messages)
                retained_tool_ids = {
                    str(message.get("tool_call_id") or "")
                    for message in retained_messages
                    if message.get("role") == "tool"
                }
                self._delivered_tool_call_ids.intersection_update(retained_tool_ids)
                self._tool_result_references = {
                    tool_call_id: marker
                    for tool_call_id, marker in self._tool_result_references.items()
                    if tool_call_id in retained_tool_ids
                }
                if self._conversation_store is not None:
                    self._messages = self._conversation_store.persist(
                        retained_messages,
                        self._delivered_tool_call_ids,
                        self._tool_result_references,
                    )
                else:
                    self._messages = retained_messages
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
                    "token_usage": aggregate_usage(api_calls),
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
        on_activity=None,
    ) -> OpenAIStreamRound:
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
        initial_size = self._context_policy.encoded_size(payload)
        compaction_started = initial_size > self._context_policy.hard_limit_bytes
        if compaction_started and on_activity is not None:
            on_activity({"category": "compaction", "status": "started"})
        try:
            request_view = self._context_policy.prepare(
                payload,
                delivered_tool_call_ids=self._delivered_tool_call_ids,
                tool_result_references=self._tool_result_references,
            )
        except (ApiContextLimitError, ApiContextProtocolError, ApiContextReferenceError):
            if compaction_started and on_activity is not None:
                on_activity({"category": "compaction", "status": "failed"})
            raise
        if request_view.compacted_tool_call_ids:
            self._context_compaction_count += 1
            if on_activity is not None:
                on_activity({"category": "compaction", "status": "completed"})
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
            data=json.dumps(request_view.payload, ensure_ascii=False).encode("utf-8"),
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
                        usage = normalized_usage(chunk["usage"])
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
        with self._lock:
            self._delivered_tool_call_ids.update(request_view.raw_tool_call_ids)
        if reasoning_content and on_reasoning is not None:
            on_reasoning("", True)
        return OpenAIStreamRound(
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
                "context_hard_limit_bytes": self._context_policy.hard_limit_bytes,
                "context_compaction_count": self._context_compaction_count,
            }


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


def _now() -> str:
    return datetime.now(UTC).isoformat()
