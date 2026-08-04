"""Translate native coding-harness model wires to OpenAI Chat Completions.

Codex and Claude Code continue to own their agent loops and tools.  These
helpers only translate model request and response envelopes for providers that
offer the OpenAI-compatible Chat Completions wire.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable


def responses_request_to_chat(
    request: dict[str, object],
    *,
    model: str,
    max_output_tokens: int,
    reasoning_effort: str,
    extra_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    messages: list[dict[str, object]] = []
    instructions = _text(request.get("instructions"))
    if instructions:
        messages.append({"role": "system", "content": instructions})
    for item in _list(request.get("input")):
        if not isinstance(item, dict):
            continue
        item_type = _text(item.get("type"))
        if item_type == "message":
            role = _text(item.get("role")) or "user"
            if role == "developer":
                role = "system"
            messages.append(
                {
                    "role": role,
                    "content": _response_content_to_chat(item.get("content")),
                }
            )
        elif item_type in {"function_call", "custom_tool_call"}:
            name = _text(item.get("name")) or item_type
            arguments = _text(item.get("arguments") or item.get("input")) or "{}"
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": _text(item.get("call_id") or item.get("id"))
                            or f"call_{uuid.uuid4().hex}",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            )
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": _text(item.get("call_id")),
                    "content": _tool_output_text(item.get("output")),
                }
            )
        elif item_type == "local_shell_call":
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": _text(item.get("call_id") or item.get("id"))
                            or f"call_{uuid.uuid4().hex}",
                            "type": "function",
                            "function": {
                                "name": "local_shell",
                                "arguments": json.dumps(
                                    item.get("action") or {}, ensure_ascii=False
                                ),
                            },
                        }
                    ],
                }
            )
        elif item_type == "local_shell_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": _text(item.get("call_id") or item.get("id")),
                    "content": _tool_output_text(item.get("output")),
                }
            )
    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": False,
        **dict(extra_payload or {}),
    }
    tools = [_responses_tool_to_chat(tool) for tool in _list(request.get("tools"))]
    tools = [tool for tool in tools if tool is not None]
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = bool(
            request.get("parallel_tool_calls", True)
        )
    if max_output_tokens > 0:
        payload["max_tokens"] = max_output_tokens
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    return payload


def anthropic_request_to_chat(
    request: dict[str, object],
    *,
    model: str,
    max_output_tokens: int,
    reasoning_effort: str,
    extra_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    messages: list[dict[str, object]] = []
    system = _anthropic_text(request.get("system"))
    if system:
        messages.append({"role": "system", "content": system})
    for message in _list(request.get("messages")):
        if not isinstance(message, dict):
            continue
        role = _text(message.get("role")) or "user"
        content = message.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        text_parts: list[dict[str, object]] = []
        tool_calls: list[dict[str, object]] = []
        tool_results: list[dict[str, object]] = []
        for block in _list(content):
            if not isinstance(block, dict):
                continue
            block_type = _text(block.get("type"))
            if block_type == "text":
                text_parts.append({"type": "text", "text": _text(block.get("text"))})
            elif block_type == "image":
                converted = _anthropic_image_to_chat(block)
                if converted:
                    text_parts.append(converted)
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": _text(block.get("id")) or f"call_{uuid.uuid4().hex}",
                        "type": "function",
                        "function": {
                            "name": _text(block.get("name")) or "tool",
                            "arguments": json.dumps(
                                block.get("input") or {}, ensure_ascii=False
                            ),
                        },
                    }
                )
            elif block_type == "tool_result":
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": _text(block.get("tool_use_id")),
                        "content": _anthropic_text(block.get("content")),
                    }
                )
        if text_parts or tool_calls:
            outgoing: dict[str, object] = {
                "role": role,
                "content": text_parts or None,
            }
            if tool_calls:
                outgoing["tool_calls"] = tool_calls
            messages.append(outgoing)
        messages.extend(tool_results)
    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": False,
        **dict(extra_payload or {}),
    }
    tools = [_anthropic_tool_to_chat(tool) for tool in _list(request.get("tools"))]
    tools = [tool for tool in tools if tool is not None]
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    output_limit = max_output_tokens or _integer(request.get("max_tokens"))
    if output_limit > 0:
        payload["max_tokens"] = output_limit
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    return payload


def chat_response_to_responses_events(
    response: dict[str, object], *, model: str
) -> list[dict[str, object]]:
    response_id = _text(response.get("id")) or f"resp_{uuid.uuid4().hex}"
    message = _first_choice_message(response)
    output: list[dict[str, object]] = []
    events: list[dict[str, object]] = [
        {
            "type": "response.created",
            "response": {
                "id": response_id,
                "object": "response",
                "status": "in_progress",
                "model": model,
                "output": [],
            },
        }
    ]
    reasoning = _reasoning_text(message)
    if reasoning:
        reasoning_id = f"rs_{uuid.uuid4().hex}"
        reasoning_item = {
            "type": "reasoning",
            "id": reasoning_id,
            "summary": [{"type": "summary_text", "text": reasoning}],
            "content": None,
            "encrypted_content": None,
        }
        events.extend(
            [
                {
                    "type": "response.reasoning_summary_part.added",
                    "item_id": reasoning_id,
                    "output_index": len(output),
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                },
                {
                    "type": "response.reasoning_summary_text.delta",
                    "item_id": reasoning_id,
                    "output_index": len(output),
                    "summary_index": 0,
                    "delta": reasoning,
                },
                {
                    "type": "response.reasoning_summary_text.done",
                    "item_id": reasoning_id,
                    "output_index": len(output),
                    "summary_index": 0,
                    "text": reasoning,
                },
                {
                    "type": "response.output_item.done",
                    "output_index": len(output),
                    "item": reasoning_item,
                },
            ]
        )
        output.append(reasoning_item)
    content = _chat_content_text(message.get("content"))
    if content:
        item_id = f"msg_{uuid.uuid4().hex}"
        item = {
            "type": "message",
            "id": item_id,
            "role": "assistant",
            "content": [{"type": "output_text", "text": content}],
            "phase": "final_answer",
        }
        events.extend(
            [
                {
                    "type": "response.output_text.delta",
                    "item_id": item_id,
                    "output_index": len(output),
                    "content_index": 0,
                    "delta": content,
                },
                {
                    "type": "response.output_item.done",
                    "output_index": len(output),
                    "item": item,
                },
            ]
        )
        output.append(item)
    for tool_call in _list(message.get("tool_calls")):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        item = {
            "type": "function_call",
            "id": f"fc_{uuid.uuid4().hex}",
            "call_id": _text(tool_call.get("id")) or f"call_{uuid.uuid4().hex}",
            "name": _text(function.get("name")) or "tool",
            "arguments": _text(function.get("arguments")) or "{}",
        }
        events.append(
            {
                "type": "response.output_item.done",
                "output_index": len(output),
                "item": item,
            }
        )
        output.append(item)
    usage = _responses_usage(response.get("usage"))
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "object": "response",
                "status": "completed",
                "model": model,
                "output": output,
                "usage": usage,
                "end_turn": not any(item["type"] == "function_call" for item in output),
            },
        }
    )
    return events


def chat_response_to_anthropic_events(
    response: dict[str, object], *, model: str
) -> list[dict[str, object]]:
    message = _first_choice_message(response)
    usage = _chat_usage(response.get("usage"))
    message_id = _text(response.get("id")) or f"msg_{uuid.uuid4().hex}"
    events: list[dict[str, object]] = [
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 0},
            },
        }
    ]
    block_index = 0
    content = _chat_content_text(message.get("content"))
    if content:
        events.extend(
            [
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": content},
                },
                {"type": "content_block_stop", "index": block_index},
            ]
        )
        block_index += 1
    tool_calls = []
    for tool_call in _list(message.get("tool_calls")):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        call_id = _text(tool_call.get("id")) or f"toolu_{uuid.uuid4().hex}"
        name = _text(function.get("name")) or "tool"
        arguments = _text(function.get("arguments")) or "{}"
        events.extend(
            [
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "input_json_delta", "partial_json": arguments},
                },
                {"type": "content_block_stop", "index": block_index},
            ]
        )
        tool_calls.append(tool_call)
        block_index += 1
    events.extend(
        [
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "tool_use" if tool_calls else "end_turn",
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": usage["output_tokens"]},
            },
            {"type": "message_stop"},
        ]
    )
    return events


def chat_response_to_anthropic_message(
    response: dict[str, object], *, model: str
) -> dict[str, object]:
    """Return the non-streaming Anthropic Messages envelope Claude expects.

    Claude Code uses non-streaming side requests for native permission
    classification even while its main agent turn is streamed.  Those callers
    inspect the top-level usage object, so an SSE response is not interchangeable
    with this envelope.
    """

    message = _first_choice_message(response)
    usage = _chat_usage(response.get("usage"))
    content: list[dict[str, object]] = []
    text = _chat_content_text(message.get("content"))
    if text:
        content.append({"type": "text", "text": text})
    for tool_call in _list(message.get("tool_calls")):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        arguments = _text(function.get("arguments")) or "{}"
        try:
            tool_input = json.loads(arguments)
        except json.JSONDecodeError:
            tool_input = {"input": arguments}
        if not isinstance(tool_input, dict):
            tool_input = {"input": tool_input}
        content.append(
            {
                "type": "tool_use",
                "id": _text(tool_call.get("id")) or f"toolu_{uuid.uuid4().hex}",
                "name": _text(function.get("name")) or "tool",
                "input": tool_input,
            }
        )
    return {
        "id": _text(response.get("id")) or f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": (
            "tool_use"
            if any(block.get("type") == "tool_use" for block in content)
            else "end_turn"
        ),
        "stop_sequence": None,
        "usage": usage,
    }


def approximate_anthropic_input_tokens(request: dict[str, object]) -> int:
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    return max(1, (len(encoded) + 3) // 4)


def _responses_tool_to_chat(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    tool_type = _text(value.get("type"))
    if tool_type == "function":
        function = value.get("function")
        source = function if isinstance(function, dict) else value
        name = _text(source.get("name"))
        if not name:
            return None
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": _text(source.get("description")),
                "parameters": source.get("parameters") or _object_schema(),
            },
        }
    name = _text(value.get("name")) or tool_type
    if not name:
        return None
    description = _text(value.get("description"))
    if tool_type == "custom":
        parameters = {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        }
    else:
        parameters = value.get("parameters") or value.get("input_schema") or _object_schema()
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def _anthropic_tool_to_chat(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    name = _text(value.get("name"))
    if not name:
        return None
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _text(value.get("description")),
            "parameters": value.get("input_schema") or _object_schema(),
        },
    }


def _response_content_to_chat(value: object) -> object:
    if isinstance(value, str):
        return value
    parts: list[dict[str, object]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        item_type = _text(item.get("type"))
        if item_type in {"input_text", "output_text", "text"}:
            parts.append({"type": "text", "text": _text(item.get("text"))})
        elif item_type == "input_image":
            url = _text(item.get("image_url"))
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts or ""


def _anthropic_image_to_chat(block: dict[str, object]) -> dict[str, object] | None:
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") == "base64":
        media_type = _text(source.get("media_type")) or "image/png"
        data = _text(source.get("data"))
        if data:
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            }
    return None


def _anthropic_text(value: object) -> str:
    if isinstance(value, str):
        return value
    parts: list[str] = []
    for block in _list(value):
        if isinstance(block, dict) and block.get("type") in {"text", "tool_result"}:
            content = block.get("text") if block.get("type") == "text" else block.get("content")
            parts.append(_anthropic_text(content))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(part for part in parts if part)


def _tool_output_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _first_choice_message(response: dict[str, object]) -> dict[str, object]:
    choices = _list(response.get("choices"))
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            return message
    return {}


def _reasoning_text(message: dict[str, object]) -> str:
    return _text(
        message.get("reasoning_content")
        or message.get("reasoning")
        or message.get("thinking")
    )


def _chat_content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return "".join(
        _text(part.get("text"))
        for part in _list(value)
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _responses_usage(value: object) -> dict[str, object]:
    usage = value if isinstance(value, dict) else {}
    input_tokens = _integer(usage.get("prompt_tokens") or usage.get("input_tokens"))
    output_tokens = _integer(usage.get("completion_tokens") or usage.get("output_tokens"))
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": _integer(usage.get("total_tokens")) or input_tokens + output_tokens,
    }


def _chat_usage(value: object) -> dict[str, int]:
    usage = value if isinstance(value, dict) else {}
    return {
        "input_tokens": _integer(usage.get("prompt_tokens") or usage.get("input_tokens")),
        "output_tokens": _integer(usage.get("completion_tokens") or usage.get("output_tokens")),
    }


def _object_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)) else []


def _text(value: object) -> str:
    return str(value or "")


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "anthropic_request_to_chat",
    "approximate_anthropic_input_tokens",
    "chat_response_to_anthropic_events",
    "chat_response_to_anthropic_message",
    "chat_response_to_responses_events",
    "responses_request_to_chat",
]
