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

from agentsassemble.room_engagement import chain_depth as _chain_depth
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


def run_provider_ws_resident(
    server_url: str,
    session_token: str,
    config,
    command_runner: Callable[..., str],
    *,
    streams: tuple[str, ...] = ("lobby",),
    poll_timeout: float = 0.5,
    buffer_size: int = 40,
    max_replies: int = 0,
    max_idle_rounds: int = 0,
) -> int:
    """Run a CLI/provider agent over WS at the HTTP runner's prompt fidelity.

    Reuses the runner's own logic — `event_reply_candidate` (engagement: always /
    mentioned / human_only / chain-depth / self-skip) and `delegate_prompt` (the
    identity + anti-yes-man + recent-conversation envelope) — but drives it over
    the governed WS transport instead of the HTTP poll loop. `command_runner` is
    the provider's resident runner (CodexResidentCommandRunner, etc.). The control-
    meta filter drops replies that leak runner/control instructions.

    This is the additive WS agent path (task #39): it does NOT touch LiveAgentRunner.
    """
    from agentsassemble.live_agent_runner import (
        delegate_prompt,
        event_reply_candidate,
        visible_reply_contains_control_meta,
    )

    agent_id = str(config.agent_id)
    display_name = str(config.display_name or config.agent_id)
    client = connect_room_ws(server_url, session_token, list(streams))
    try:
        client.sock.settimeout(poll_timeout)
    except OSError:
        pass

    buffer: list[dict] = []
    last_observed = ""
    seeded = False
    replies = 0
    idle = 0
    try:
        while not client.closed:
            messages = client.receive()
            if not messages:
                idle += 1
                if max_idle_rounds and idle >= max_idle_rounds:
                    break
                continue
            idle = 0
            got_lobby_event = False
            got_live_event = False
            got_snapshot_event = False
            for message in messages:
                if message.get("op") == "event" and message.get("stream") == "lobby":
                    events = message.get("events") or []
                    if events:
                        got_lobby_event = True
                        buffer.extend(events)
                        if message.get("snapshot") is True:
                            got_snapshot_event = True
                            last_observed = str(events[-1].get("id") or last_observed)
                        else:
                            got_live_event = True
            if len(buffer) > buffer_size:
                del buffer[:-buffer_size]
            if not seeded:
                # Don't answer history that existed before we joined. Newer WS
                # servers mark that subscribe-time history as a snapshot, so a
                # live event batched after it can still be answered. Unmarked
                # first lobby batches are treated as legacy snapshots.
                seeded = True
                if got_lobby_event and (not got_live_event or not got_snapshot_event):
                    last_observed = str(buffer[-1].get("id") or "")
                    continue
                if not got_lobby_event:
                    continue
            if not got_live_event:
                continue
            candidate = event_reply_candidate(
                buffer,
                agent_id,
                display_name,
                last_observed,
                max_chain_depth=config.max_chain_depth,
                engagement_mode=config.engagement_mode,
                meeting_id=config.meeting_id,
            )
            if candidate is None:
                continue
            source_event_id = str(candidate.get("id") or last_observed)
            last_observed = source_event_id
            room = {
                "lobby_events": list(buffer),
                "agent": {"agent_id": agent_id, "engagement_mode": config.engagement_mode},
                "meeting_id": config.meeting_id,
                "shared_memory": {},
            }
            prompt = delegate_prompt(config, room, candidate)
            client.thinking(True)
            try:
                reply = command_runner([], prompt, timeout_seconds=config.timeout_seconds)
            finally:
                client.thinking(False)
            reply = str(reply or "").strip()
            if not reply or visible_reply_contains_control_meta(reply):
                continue
            client.say(
                reply,
                kind="message",
                source_event_id=source_event_id,
                auto_chain_depth=_chain_depth(candidate) + 1,
                flow_meeting_id=config.meeting_id,
            )
            replies += 1
            if max_replies and replies >= max_replies:
                return replies
        return replies
    finally:
        client.close()


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
