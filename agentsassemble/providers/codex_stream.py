"""Parse Codex CLI JSONL output into room-visible event categories.

Codex emits one JSON object per line. We only care about a few shapes:

    {"type":"thread.started","thread_id":"..."}                     -> session id
    {"type":"item.started","item":{"type":"command_execution",      -> a tool run
                                    "command":"..."}}
    {"type":"item.completed","item":{"type":"agent_message",         -> a chunk of
                                     "text":"..."}}                     the reply
    {"type":"item.completed","item":{"type":"reasoning",...}}        -> reasoning

This is a pure function (no subprocess, no IO) so it is trivially testable; the
runner feeds it lines as they arrive and decides what to stream to the room.
"""

from __future__ import annotations

import json


def parse_codex_stream_line(line: str) -> dict | None:
    """Map one `--json` line to a thought event, or None to ignore it.

    Returns ``{"kind": ..., "text": ...}`` where kind is one of:
      - "thread"   : text is the codex session/thread id (for --resume)
      - "message"  : text is a chunk of the assistant's reply
      - "command"  : text is a shell command the agent is about to run
      - "reasoning": text is a reasoning/thinking blurb
    """
    text = (line or "").strip()
    if not text:
        return None
    try:
        event = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    event_type = str(event.get("type") or "")
    if event_type == "thread.started":
        thread_id = str(event.get("thread_id") or "").strip()
        return {"kind": "thread", "text": thread_id} if thread_id else None
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    item_type = str(item.get("type") or "")
    if event_type == "item.completed" and item_type == "agent_message":
        return {"kind": "message", "text": str(item.get("text") or "")}
    # A command run is most useful when it *starts* (so the room sees it live);
    # the completed copy would just duplicate it.
    if event_type == "item.started" and item_type == "command_execution":
        return {"kind": "command", "text": str(item.get("command") or "")}
    if event_type == "item.completed" and item_type == "reasoning":
        return {"kind": "reasoning", "text": str(item.get("text") or item.get("summary") or "")}
    return None


def parse_codex_stream(lines) -> list[dict]:
    """Parse a full sequence of `--json` lines into ordered thought events."""
    events: list[dict] = []
    for line in lines:
        event = parse_codex_stream_line(line)
        if event is not None:
            events.append(event)
    return events
