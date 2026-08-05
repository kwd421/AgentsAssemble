"""Bounded request views for persistent OpenAI-compatible API sessions."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass


DEFAULT_API_CONTEXT_CONTRACT_BYTES = 256_000
DEFAULT_API_OUTPUT_RESERVE_BYTES = 16_384
DEFAULT_API_CONTEXT_SAFETY_BYTES = 32_768
DEFAULT_API_CONTEXT_HYSTERESIS_BYTES = 32_768


class ApiContextLimitError(RuntimeError):
    code = "api_context_budget_exceeded"

    def __init__(self, *, encoded_bytes: int, hard_limit_bytes: int) -> None:
        super().__init__(
            "API request context remains too large after safe compaction "
            f"({encoded_bytes:,} bytes; limit {hard_limit_bytes:,}). "
            "The newest tool result was kept intact because the model has not seen it yet."
        )
        self.encoded_bytes = encoded_bytes
        self.hard_limit_bytes = hard_limit_bytes


class ApiContextProtocolError(RuntimeError):
    code = "api_context_protocol_invalid"


class ApiContextReferenceError(RuntimeError):
    code = "api_context_reference_missing"


@dataclass(frozen=True)
class ApiRequestView:
    payload: dict[str, object]
    encoded_bytes: int
    compacted_tool_call_ids: tuple[str, ...]
    raw_tool_call_ids: tuple[str, ...]


class ApiContextPolicy:
    """Compact only tool results already delivered in an earlier request."""

    def __init__(self, context_contract_bytes: int = DEFAULT_API_CONTEXT_CONTRACT_BYTES) -> None:
        contract = max(65_536, int(context_contract_bytes))
        self.hard_limit_bytes = max(
            16_384,
            contract - DEFAULT_API_OUTPUT_RESERVE_BYTES - DEFAULT_API_CONTEXT_SAFETY_BYTES,
        )
        self.target_bytes = max(
            8_192,
            self.hard_limit_bytes - DEFAULT_API_CONTEXT_HYSTERESIS_BYTES,
        )

    @staticmethod
    def encoded_size(payload: dict[str, object]) -> int:
        return len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        )

    def prepare(
        self,
        payload: dict[str, object],
        *,
        delivered_tool_call_ids: set[str],
        tool_result_references: dict[str, str] | None = None,
    ) -> ApiRequestView:
        request_payload = copy.deepcopy(payload)
        messages = request_payload.get("messages")
        if not isinstance(messages, list):
            messages = []
        _validate_message_transactions(messages)
        references = tool_result_references or {}
        size = self.encoded_size(request_payload)
        compacted: list[str] = []
        if size > self.hard_limit_bytes:
            for message in messages:
                if not isinstance(message, dict) or message.get("role") != "tool":
                    continue
                tool_call_id = str(message.get("tool_call_id") or "")
                if not tool_call_id or tool_call_id not in delivered_tool_call_ids:
                    continue
                marker = references.get(tool_call_id)
                if not marker:
                    raise ApiContextReferenceError(
                        "A delivered tool result cannot be compacted because its private "
                        "backing reference is missing."
                    )
                message["content"] = marker
                compacted.append(tool_call_id)
                size = self.encoded_size(request_payload)
                if size <= self.target_bytes:
                    break
        if size > self.hard_limit_bytes:
            raise ApiContextLimitError(
                encoded_bytes=size,
                hard_limit_bytes=self.hard_limit_bytes,
            )
        raw_ids = tuple(
            str(message.get("tool_call_id") or "")
            for message in messages
            if isinstance(message, dict)
            and message.get("role") == "tool"
            and str(message.get("tool_call_id") or "") not in delivered_tool_call_ids
        )
        return ApiRequestView(
            payload=request_payload,
            encoded_bytes=size,
            compacted_tool_call_ids=tuple(compacted),
            raw_tool_call_ids=raw_ids,
        )


def _validate_message_transactions(messages: list[object]) -> None:
    declared: set[str] = set()
    unresolved: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            raise ApiContextProtocolError("API request contains an invalid message.")
        role = message.get("role")
        if role != "tool" and unresolved:
            raise ApiContextProtocolError(
                "API request contains an incomplete assistant/tool transaction."
            )
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            for call in message["tool_calls"]:
                tool_call_id = str(call.get("id") or "") if isinstance(call, dict) else ""
                if not tool_call_id or tool_call_id in declared:
                    raise ApiContextProtocolError(
                        "API request contains an invalid assistant tool call."
                    )
                declared.add(tool_call_id)
                unresolved.add(tool_call_id)
        elif role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            if not tool_call_id or tool_call_id not in unresolved:
                raise ApiContextProtocolError("API request contains an orphaned tool result.")
            unresolved.remove(tool_call_id)
    if unresolved:
        raise ApiContextProtocolError(
            "API request contains an incomplete assistant/tool transaction."
        )


__all__ = [
    "ApiContextLimitError",
    "ApiContextPolicy",
    "ApiContextProtocolError",
    "ApiContextReferenceError",
    "ApiRequestView",
    "DEFAULT_API_CONTEXT_CONTRACT_BYTES",
]
