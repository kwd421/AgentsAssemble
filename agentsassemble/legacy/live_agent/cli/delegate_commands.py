"""Legacy one-shot delegate command execution."""
from __future__ import annotations

import argparse
import subprocess
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from agentsassemble.legacy.live_agent.cli.room_interaction_commands import _delegate_chain_depth
from agentsassemble.live_agent_runner import reply_length_directive


@dataclass(frozen=True)
class LegacyDelegateCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    run_delegate_command: Callable[..., str]


def run_legacy_delegate_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyDelegateCliRuntime,
) -> int:
    payload = {
        "agent_id": args.agent_id,
        "display_name": args.display_name,
        "provider_kind": args.provider_kind,
        "connection_kind": args.connection_kind,
        "session_id": args.session_id,
        "endpoint": args.endpoint,
        "meeting_id": args.meeting_id,
        "engagement_mode": args.engagement_mode,
        "capabilities": ["room_chat", "mentions"],
    }
    runtime.request_json(runtime.server_url(args.server, "/api/live-agents"), method="POST", payload=payload)
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/heartbeat"),
        method="POST",
        payload={"status": "working"},
    )
    room = runtime.request_json(runtime.server_url(args.server, f"/api/live-agents/{agent_id}/room"))
    try:
        reply = runtime.run_delegate_command(
            args.delegate_command,
            _delegate_prompt(args, room),
            timeout_seconds=args.timeout,
        ).strip()
        if not reply:
            raise ValueError("Delegate command returned an empty reply.")
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        _heartbeat_delegate_error(args, agent_id, error, runtime=runtime)
        raise
    lobby_payload = {"message": reply, "kind": "message"}
    source_event = _delegate_source_event(args, room)
    if source_event is not None:
        lobby_payload["source_event_id"] = str(source_event.get("id") or "")
        lobby_payload["auto_chain_depth"] = _delegate_chain_depth(source_event) + 1
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/lobby"),
        method="POST",
        payload=lobby_payload,
    )
    runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/heartbeat"),
        method="POST",
        payload={"status": "online"},
    )
    event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
    print(f"Posted {event.get('id') or 'lobby message'}")
    return 0


def run_delegate_subprocess(command: list[str], prompt: str, *, timeout_seconds: int) -> str:
    if not command:
        raise ValueError("Delegate command is required.")
    completed = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
    )
    return completed.stdout


def _delegate_prompt(args: argparse.Namespace, room: dict[str, object]) -> str:
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    lines = [
        "You are a live AgentsAssemble participant in the room, working through a local CLI bridge with your normal tools available.",
        f"Agent id: {args.agent_id}",
        f"Display name: {args.display_name or args.agent_id}",
        "Judge what the latest message needs, the way you normally would:",
        "- Just conversation -> reply conversationally.",
        "- A task (edit files, run or check something, investigate) -> actually do it with your tools, then report what you did or found.",
        "Do the real work with your tools (not by pasting it into chat). If you lack the access to do something here, say so plainly instead of pretending.",
        reply_length_directive(getattr(args, "reply_char_limit", 0)),
        "Write like a chat: break your message into short lines with a newline after each sentence or distinct thought, not one dense paragraph.",
        "Do not describe this runner, polling, heartbeats, control prompts, or delivery envelopes. No markdown fences.",
        "",
        "Recent lobby events:",
    ]
    for event in events[-12:]:
        if not isinstance(event, dict):
            continue
        name = str(event.get("name") or "participant")
        message = str(event.get("message") or "").strip()
        if message:
            lines.append(f"- {name}: {message}")
    return "\n".join(lines).strip() + "\n"


def _delegate_source_event(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    for event in reversed(_delegate_unobserved_events(args, room, events)):
        if not isinstance(event, dict):
            continue
        if not str(event.get("id") or "").strip():
            continue
        if not str(event.get("message") or "").strip():
            continue
        if _delegate_self_event(args, event):
            continue
        return event
    return None


def _delegate_unobserved_events(
    args: argparse.Namespace,
    room: dict[str, object],
    events: list[object],
) -> list[object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    if str(agent.get("agent_id") or "") != args.agent_id:
        return events
    cursor = str(agent.get("last_observed_event_id") or "").strip()
    if not cursor:
        return events
    for index, event in enumerate(events):
        if isinstance(event, dict) and str(event.get("id") or "") == cursor:
            return events[index + 1 :]
    return events


def _delegate_self_event(args: argparse.Namespace, event: dict[str, object]) -> bool:
    actor_id = str(event.get("actor_id") or "")
    if actor_id and actor_id == args.agent_id:
        return True
    display_name = str(args.display_name or args.agent_id or "")
    return bool(display_name) and str(event.get("name") or "") == display_name


def _heartbeat_delegate_error(
    args: argparse.Namespace,
    quoted_agent_id: str,
    error: Exception,
    *,
    runtime: LegacyDelegateCliRuntime,
) -> None:
    try:
        runtime.request_json(
            runtime.server_url(args.server, f"/api/live-agents/{quoted_agent_id}/heartbeat"),
            method="POST",
            payload={"status": "error", "last_error": _delegate_error_message(error)},
        )
    except Exception:
        return


def _delegate_error_message(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return f"Delegate command exited with return code {error.returncode}."
    if isinstance(error, subprocess.TimeoutExpired):
        return f"Delegate command timed out after {error.timeout} seconds."
    if isinstance(error, OSError):
        detail = str(getattr(error, "strerror", "") or "").strip() or error.__class__.__name__
        return f"Delegate command failed: {detail}."
    message = str(error).strip()
    return message or "Delegate command failed."
