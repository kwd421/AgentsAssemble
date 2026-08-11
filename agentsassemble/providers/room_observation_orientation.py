"""Provider-specific instructions for one content-free room observation."""

from __future__ import annotations

from collections.abc import Iterable

from agentsassemble.providers.runtime_contracts import (
    AMBIENT_OBSERVATION,
    ORDERED_FLOOR,
    RoomObservationKind,
)
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.tool_modes import CORE_ROOM_TOOLS


def _room_interfaces(provider_kind: object = "") -> tuple[str, str, str]:
    kind = clean_room_text(provider_kind, limit=64)
    provider_note = ""
    if kind in {
        "codex_live_session",
        "cursor_live_session",
        "grok_live_session",
        "opencode_server",
        "cerebras_api",
        "deepseek_api",
        "openrouter_api",
        "vercel_ai_gateway",
        "llm_gateway_api",
        "tokenrouter_api",
        "custom_openai_api",
        "ollama_api",
        "lmstudio_api",
    }:
        read_interface = "the `read_discussion` MCP tool"
        speak_interface = (
            "the `publish_message` MCP tool with `content` and, when deliberately "
            "handing the floor to one participant, `next_agent_id`"
        )
    elif kind in {"claude_code", "antigravity_live_session"}:
        read_interface = "terminal command `agentsassemble-room read`"
        speak_interface = (
            'terminal command `agentsassemble-room speak "<message>"`, or '
            '`agentsassemble-room speak-to <agent-id> "<message>"` when deliberately '
            "handing the floor to one participant"
        )
        provider_note = """
- `agentsassemble-room` is already on `PATH`. Run the documented `read`,
  collaboration, and `speak` commands directly. `agentsassemble-room help`
  lists their syntax; do not try to locate or inspect the helper with `which`,
  `find`, or other discovery commands.
- The terminal command is shell-parsed. Wrap the whole public message in one
  pair of ASCII double quotes. Inside the message, use Unicode quotation marks
  such as `「」` and Unicode arrows such as `→`; do not use ASCII `"`, `$`, or
  backticks. This keeps ordinary room prose inside the one approved command."""
    else:
        read_interface = (
            "the provider's private room read interface: Codex `read_discussion` MCP, "
            "terminal `agentsassemble-room read`, or ACP `/agentsassemble-room/current.md`"
        )
        speak_interface = (
            "the matching private room speak interface: Codex `publish_message` MCP, "
            'terminal `agentsassemble-room speak "<message>"` / `speak-to`, or ACP '
            "`/agentsassemble-room/outbox.txt` / `outbox-to/<agent-id>.txt`"
        )
    return read_interface, speak_interface, provider_note


def room_wake_orientation(
    provider_kind: object = "",
    *,
    observation_kind: RoomObservationKind,
    tool_names: Iterable[str] = CORE_ROOM_TOOLS,
) -> str:
    read_interface, speak_interface, provider_note = _room_interfaces(provider_kind)
    kind = clean_room_text(provider_kind, limit=64)
    if observation_kind == ORDERED_FLOOR:
        floor_note = """
- Queue provenance: ordered selection. This session was the single provider
  selected for this event when it was queued."""
    elif observation_kind == AMBIENT_OBSERVATION:
        floor_note = """
- Queue provenance: shared ambient observation. Other eligible sessions may
  receive the same triggering event."""
    else:
        raise ValueError("Unsupported room observation kind.")
    random_note = ""
    available_tools = frozenset(tool_names)
    if "roll_dice" in available_tools and kind in {
        "codex_live_session",
        "cursor_live_session",
        "grok_live_session",
        "opencode_server",
        "cerebras_api",
        "deepseek_api",
        "openrouter_api",
        "vercel_ai_gateway",
        "llm_gateway_api",
        "tokenrouter_api",
        "custom_openai_api",
        "ollama_api",
        "lmstudio_api",
    }:
        random_note = """
- For official game randomness, use `roll_dice` with NdS±M notation or
  `choose_random`; do not invent a result yourself."""
    elif "roll_dice" in available_tools and kind in {"claude_code", "antigravity_live_session"}:
        random_note = """
- For official game dice, run exactly one terminal command per roll:
  `agentsassemble-room roll '<NdS±M>'`. If another roll is needed, wait for the
  first result and use a separate tool call. Shell chaining such as `&&`, `;`,
  or `|` is rejected."""
    activity_note = ""
    if "rimworld.observe" in available_tools and kind in {
        "claude_code",
        "antigravity_live_session",
    }:
        activity_note = """
- RimWorld activity tools use `agentsassemble-room rim-observe`,
  `rim-inspect <colonist|structure|cell>`, `rim-act <action> '<json-args>'`,
  and `rim-speak '<text>'`. Stage at most one action and one speech line in a
  wake; the bridge sends them together against the observed game revision."""
    return f"""Current turn contract: room wake
- `room.wake <turn-id>` is only a content-free signal that assigned, finalized
  room activity is available.
- Read the private room mirror through {read_interface}.
- If you should speak, only {speak_interface} creates a public room message.
- Ordinary assistant output is private on this turn and is never published.
  Do not merely draft the intended public message as your final answer.
- Never invent or simulate a room tool. Only operations listed under Available
  room tools in the private room mirror are real for this turn.{floor_note}{random_note}{activity_note}{provider_note}"""


__all__ = ["room_wake_orientation"]
