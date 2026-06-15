"""WS resident loop (WS-resident, task #39).

Run an agent over the governed WebSocket transport instead of the HTTP poll loop:
connect → subscribe → receive room events → decide → reply → say. This is the
NEW resident path on top of WsRoomClient. It is additive — it does not touch the
existing LiveAgentRunner; swapping the runner's default onto this is the follow-up.

The "brain" is a pluggable callable (lobby_event_dict -> reply_text or ""). That
keeps the loop model-agnostic: a stub for tests, `codex exec`/an API call in
production. Free-flow vs turn-based is policy in `should_reply`, NOT transport.
"""
from __future__ import annotations

import socket as socket_module
from typing import Callable

from agentsassemble.ws_room_client import WsRoomClient, connect_room_ws

Brain = Callable[[dict], str]
ShouldReply = Callable[[dict], bool]


def reply_to_humans(event: dict) -> bool:
    """Default engagement: reply to human messages only (skips agents — and so
    never replies to itself, and never agent↔agent loops). Free-flow allows the
    same agent to speak again when a new human message arrives."""
    return str(event.get("actor_type") or "") == "human" and str(event.get("kind") or "message") == "message"


def run_resident_loop(
    client: WsRoomClient,
    brain: Brain,
    *,
    should_reply: ShouldReply = reply_to_humans,
    poll_timeout: float = 0.5,
    max_replies: int = 0,
    max_idle_rounds: int = 0,
) -> int:
    """Drive an already-opened+subscribed WsRoomClient. Returns reply count.
    `max_replies`/`max_idle_rounds`=0 mean unbounded (a real resident); tests set
    them so the loop terminates."""
    try:
        client.sock.settimeout(poll_timeout)
    except OSError:
        pass
    seen: set[str] = set()
    replies = 0
    idle = 0
    while not client.closed:
        messages = client.receive()
        if not messages:
            idle += 1
            if max_idle_rounds and idle >= max_idle_rounds:
                break
            continue
        idle = 0
        for message in messages:
            if message.get("op") != "event" or message.get("stream") != "lobby":
                continue
            for event in message.get("events", []):
                event_id = str(event.get("id") or "")
                if event_id in seen:
                    continue
                seen.add(event_id)
                if not should_reply(event):
                    continue
                client.thinking(True)  # light up the typing indicator while generating
                try:
                    reply = brain(event)
                finally:
                    client.thinking(False)
                if not reply:
                    continue
                client.say(reply)
                replies += 1
                if max_replies and replies >= max_replies:
                    return replies
    return replies


def run_ws_resident(
    server_url: str,
    session_token: str,
    brain: Brain,
    *,
    streams: tuple[str, ...] = ("lobby",),
    should_reply: ShouldReply = reply_to_humans,
    poll_timeout: float = 0.5,
    max_replies: int = 0,
    max_idle_rounds: int = 0,
) -> int:
    """Connect a resident to the room over WS and run its loop. Convenience that
    wraps connect_room_ws + run_resident_loop; closes the socket on exit."""
    client = connect_room_ws(server_url, session_token, list(streams))
    try:
        return run_resident_loop(
            client,
            brain,
            should_reply=should_reply,
            poll_timeout=poll_timeout,
            max_replies=max_replies,
            max_idle_rounds=max_idle_rounds,
        )
    finally:
        client.close()
