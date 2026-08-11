"""Room tool schemas and execution for OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agentsassemble.plugin.agent_tools import plugin_agent_tool_schemas
from agentsassemble.providers.room_portal import RoomPortal


_OPENAI_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def openai_room_tool_name(name: object) -> str:
    """Encode a canonical room tool name for strict OpenAI-compatible APIs.

    Activity plugins intentionally namespace tools with a dot (for example
    ``rimworld.observe``), while OpenAI-compatible function names only accept
    letters, numbers, underscores, and hyphens. The canonical name remains
    authoritative inside AgentsAssemble; this alias exists only at the
    provider protocol boundary.
    """

    canonical = str(name or "")
    if _OPENAI_TOOL_NAME_PATTERN.fullmatch(canonical):
        return canonical
    return re.sub(r"[^a-zA-Z0-9_-]", "_", canonical)


def canonical_room_tool_name(name: object, active_names: object) -> str:
    """Resolve a provider-safe alias back to one active canonical room tool."""

    provider_name = str(name or "")
    active = (
        tuple(str(item) for item in active_names if isinstance(item, str) and item)
        if isinstance(active_names, (set, frozenset, list, tuple))
        else ()
    )
    if provider_name in active:
        return provider_name
    matches = [
        canonical
        for canonical in active
        if openai_room_tool_name(canonical) == provider_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"OpenAI room tool alias is ambiguous: {provider_name}.")
    return provider_name


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
    room_schemas = tuple(
        schema
        for schema in ROOM_TOOL_SCHEMAS
        if isinstance(schema.get("function"), dict)
        and schema["function"].get("name") in allowed
    )
    plugin_ids = sorted(
        {name.partition(".")[0] for name in allowed if "." in str(name)}
    )
    plugin_schemas = tuple(
        {
            "type": "function",
            "function": {
                "name": openai_room_tool_name(schema["name"]),
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
        }
        for plugin_id in plugin_ids
        for schema in plugin_agent_tool_schemas(plugin_id)
        if schema["name"] in allowed
    )
    return (*room_schemas, *plugin_schemas)


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
    provider_name = str(function.get("name") or "")
    name = canonical_room_tool_name(provider_name, portal.active_tool_names())
    raw_arguments = function.get("arguments")
    try:
        arguments = json.loads(raw_arguments or "{}")
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{name or 'Room tool'} arguments were not valid JSON.") from error
    if not isinstance(arguments, dict):
        raise RuntimeError(f"{name or 'Room tool'} arguments must be an object.")
    if name not in portal.active_tool_names():
        raise RuntimeError(f"Provider requested unavailable room tool: {name or '(missing)'}.")
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
    elif name.endswith(".observe"):
        result = portal.activity_plugin_observe()
    elif name.endswith(".inspect"):
        result = portal.activity_plugin_inspect(arguments)
    elif name.endswith(".act"):
        result = portal.activity_plugin_act(
            arguments.get("action"),
            arguments.get("action_args"),
        )
    elif name.endswith(".speak"):
        result = portal.activity_plugin_speak(arguments.get("text"))
    else:
        raise RuntimeError(f"Provider requested unsupported room tool: {name or '(missing)'}.")
    return name, json.dumps(result, ensure_ascii=False)


__all__ = [
    "ROOM_TOOL_SCHEMAS",
    "StreamingToolCall",
    "accumulate_streaming_tool_calls",
    "canonical_room_tool_name",
    "complete_tool_calls",
    "execute_room_tool",
    "openai_room_tool_name",
    "room_tool_schemas",
]
