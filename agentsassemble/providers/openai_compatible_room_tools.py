"""Room tool schemas and execution for OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from agentsassemble.providers.room_portal import RoomPortal


ROOM_TOOL_SCHEMAS: tuple[dict[str, object], ...] = (
    {
        "type": "function",
        "function": {
            "name": "read_discussion",
            "description": "Read the finalized messages currently visible in the shared room.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_participants",
            "description": "List the people and agents currently visible in the shared room.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_message",
            "description": "Publish one substantive message to the shared room.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The public message to post in the shared room.",
                    },
                    "next_agent_id": {
                        "type": "string",
                        "description": (
                            "Optional floor handoff to one agent. Use only an exact agent id "
                            "listed under Agent handles; omit it when replying to a human or "
                            "when no specific agent should speak next."
                        ),
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decline_to_speak",
            "description": (
                "End this room wake without posting a message when speaking would add no value."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason_code": {
                        "type": "string",
                        "enum": ["nothing_useful_to_add", "not_addressed", "duplicate"],
                    }
                },
                "required": ["reason_code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_vote",
            "description": "Create one structured room vote as this turn's public action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 10,
                    },
                    "duration_seconds": {
                        "type": "integer",
                        "description": "0 for no deadline, otherwise 30 to 86400 seconds.",
                    },
                },
                "required": ["question", "options"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cast_vote",
            "description": "Cast or replace this agent's ballot in an existing structured vote.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vote_id": {"type": "string"},
                    "choice": {
                        "type": "string",
                        "description": "Exact option text or its 1-based number.",
                    },
                },
                "required": ["vote_id", "choice"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vote_summary",
            "description": (
                "Summarize a vote from this session's current bounded room view. "
                "The result identifies that scope and is not an authoritative full-history query."
            ),
            "parameters": {
                "type": "object",
                "properties": {"vote_id": {"type": "string"}},
                "required": ["vote_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Roll official server-side dice using bounded NdS±M notation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "notation": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["notation"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "choose_random",
            "description": "Choose one official server-random item from 2 to 50 options.",
            "parameters": {
                "type": "object",
                "properties": {
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 50,
                    },
                    "reason": {"type": "string"},
                },
                "required": ["options"],
                "additionalProperties": False,
            },
        },
    },
)


def room_tool_schemas(tool_names: object) -> tuple[dict[str, object], ...]:
    allowed = (
        frozenset(tool_names)
        if isinstance(tool_names, (set, frozenset, list, tuple))
        else frozenset()
    )
    return tuple(
        schema
        for schema in ROOM_TOOL_SCHEMAS
        if isinstance(schema.get("function"), dict)
        and schema["function"].get("name") in allowed
    )


@dataclass
class StreamingToolCall:
    index: int
    call_id: str = ""
    name: str = ""
    argument_parts: list[str] = field(default_factory=list)

    def as_message_value(self) -> dict[str, object]:
        return {
            "id": self.call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": "".join(self.argument_parts),
            },
        }


def accumulate_streaming_tool_calls(
    calls: dict[int, StreamingToolCall],
    delta: object,
) -> None:
    for value in delta if isinstance(delta, list) else []:
        if not isinstance(value, dict):
            continue
        index = value.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            continue
        call = calls.setdefault(index, StreamingToolCall(index=index))
        call_id = value.get("id")
        if isinstance(call_id, str) and call_id:
            call.call_id = call_id
        function = value.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            call.name += name
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            call.argument_parts.append(arguments)


def complete_tool_calls(
    calls: dict[int, StreamingToolCall],
) -> list[dict[str, object]]:
    result = [calls[index].as_message_value() for index in sorted(calls)]
    for item in result:
        function = item["function"]
        assert isinstance(function, dict)
        if not item["id"] or not function.get("name"):
            raise RuntimeError("Provider returned an incomplete room tool call.")
    return result


def execute_room_tool(
    portal: RoomPortal,
    tool_call: dict[str, object],
) -> tuple[str, str]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise RuntimeError("Provider returned a malformed room tool call.")
    name = str(function.get("name") or "")
    raw_arguments = function.get("arguments")
    try:
        arguments = json.loads(raw_arguments or "{}")
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{name or 'Room tool'} arguments were not valid JSON.") from error
    if not isinstance(arguments, dict):
        raise RuntimeError(f"{name or 'Room tool'} arguments must be an object.")
    if name == "read_discussion":
        result: object = portal.read_discussion()
    elif name == "list_participants":
        result = portal.list_participants()
    elif name == "publish_message":
        portal.publish_message(
            arguments.get("content"),
            next_agent_id=arguments.get("next_agent_id"),
        )
        result = {"published": True}
    elif name == "decline_to_speak":
        result = portal.decline_to_speak(arguments.get("reason_code"))
    elif name == "create_vote":
        options = arguments.get("options")
        if not isinstance(options, list):
            raise RuntimeError("create_vote options must be a list.")
        result = portal.create_vote(
            arguments.get("question"),
            options,
            duration_seconds=arguments.get("duration_seconds", 0),
        )
    elif name == "cast_vote":
        result = portal.cast_vote(
            arguments.get("vote_id"),
            arguments.get("choice"),
        )
    elif name == "vote_summary":
        result = portal.vote_summary(arguments.get("vote_id"))
    elif name == "roll_dice":
        result = portal.roll_dice(
            arguments.get("notation"),
            reason=arguments.get("reason"),
        )
    elif name == "choose_random":
        options = arguments.get("options")
        if not isinstance(options, list):
            raise RuntimeError("choose_random options must be a list.")
        result = portal.choose_random(options, reason=arguments.get("reason"))
    else:
        raise RuntimeError(f"Provider requested unsupported room tool: {name or '(missing)'}.")
    return name, json.dumps(result, ensure_ascii=False)


__all__ = [
    "ROOM_TOOL_SCHEMAS",
    "StreamingToolCall",
    "accumulate_streaming_tool_calls",
    "complete_tool_calls",
    "execute_room_tool",
    "room_tool_schemas",
]
