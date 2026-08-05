"""Private durable conversation state for direct API runtimes."""

from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path


class ApiConversationStateError(RuntimeError):
    code = "api_conversation_state_invalid"


class ApiConversationStore:
    def __init__(self, state_dir: str | Path, *, provider_name: str, model: str) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.provider_name = provider_name
        self.model = model

    @property
    def path(self) -> Path:
        identity = hashlib.sha256(
            f"{self.provider_name}\0{self.model}".encode("utf-8")
        ).hexdigest()[:16]
        return self.state_dir / f"api-conversation-{identity}.json"

    def load(self) -> tuple[list[dict[str, object]], set[str]]:
        if not self.path.exists():
            return [], set()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise ApiConversationStateError(
                "Saved API conversation state is unreadable; refusing to resume without context."
            ) from error
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("provider_name") != self.provider_name
            or payload.get("model") != self.model
        ):
            raise ApiConversationStateError(
                "Saved API conversation state does not match this provider and model."
            )
        messages = payload.get("messages")
        delivered = payload.get("delivered_tool_call_ids")
        if not isinstance(messages, list) or not isinstance(delivered, list):
            raise ApiConversationStateError("Saved API conversation state has an invalid shape.")
        validated = _validated_messages(messages)
        tool_ids = {
            str(message.get("tool_call_id") or "")
            for message in validated
            if message.get("role") == "tool"
        }
        delivered_ids = {str(value) for value in delivered if str(value)}
        if not delivered_ids.issubset(tool_ids):
            raise ApiConversationStateError(
                "Saved API conversation state refers to an unknown tool result."
            )
        return validated, delivered_ids

    def persist(
        self,
        messages: list[dict[str, object]],
        delivered_tool_call_ids: set[str],
    ) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "provider_name": self.provider_name,
            "model": self.model,
            "messages": _validated_messages(messages),
            "delivered_tool_call_ids": sorted(delivered_tool_call_ids),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)


def _validated_messages(values: list[object]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    declared_tool_ids: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or value.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise ApiConversationStateError("Saved API conversation contains an invalid message.")
        message = dict(value)
        if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list):
            for call in message["tool_calls"]:
                if isinstance(call, dict) and str(call.get("id") or ""):
                    declared_tool_ids.add(str(call["id"]))
        if message.get("role") == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            if not tool_call_id or tool_call_id not in declared_tool_ids:
                raise ApiConversationStateError(
                    "Saved API conversation contains an orphaned tool result."
                )
        messages.append(message)
    return messages


__all__ = ["ApiConversationStateError", "ApiConversationStore"]
