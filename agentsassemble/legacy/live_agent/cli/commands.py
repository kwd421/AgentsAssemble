"""Dispatch and inline execution for retained live-agent CLI commands."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from agentsassemble.character_mode import clean_persona_card_id


CommandHandler = Callable[[argparse.Namespace], int]
OptionalCommandHandler = Callable[[argparse.Namespace], int | None]


@dataclass(frozen=True)
class LegacyLiveAgentCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    heartbeat_payload: Callable[[argparse.Namespace], dict[str, object]]
    session_command: OptionalCommandHandler
    process_command: OptionalCommandHandler
    smoke_command: OptionalCommandHandler
    diagnostic_command: OptionalCommandHandler
    presence_command: OptionalCommandHandler
    operations_command: OptionalCommandHandler
    meeting_command: OptionalCommandHandler
    handlers: Mapping[str, CommandHandler]
    runnable_commands: Collection[str]


def run_live_agent_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyLiveAgentCliRuntime,
) -> int:
    command = str(getattr(args, "live_agent_command", ""))
    if (
        command in runtime.runnable_commands
        and not bool(getattr(args, "legacy_internal", False))
        and os.environ.get("AGENTSASSEMBLE_LEGACY_INTERNAL") != "1"
    ):
        print(
            "live-agent commands are legacy/internal; use Agent Session room commands instead.",
            file=sys.stderr,
        )
        return 2

    try:
        for nested_handler in (
            runtime.session_command,
            runtime.process_command,
            runtime.smoke_command,
            runtime.diagnostic_command,
            runtime.presence_command,
            runtime.operations_command,
            runtime.meeting_command,
        ):
            result = nested_handler(args)
            if result is not None:
                return result

        if command == "register":
            return _register(args, runtime)
        if command == "heartbeat":
            return _heartbeat(args, runtime)
        if command == "say":
            return _say(args, runtime)
        if command == "room":
            return _room(args, runtime)

        handler = runtime.handlers.get(command)
        if handler is not None:
            return handler(args)
    except (
        OSError,
        subprocess.SubprocessError,
        urllib.error.URLError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


def _register(args: argparse.Namespace, runtime: LegacyLiveAgentCliRuntime) -> int:
    payload: dict[str, object] = {
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
    if args.join_semantics:
        payload["join_semantics"] = args.join_semantics
    persona_card_id = clean_persona_card_id(args.persona_card_id)
    if persona_card_id:
        payload["persona_card_id"] = persona_card_id
    if args.character_mode:
        payload["character_mode"] = args.character_mode
    response = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agents"),
        method="POST",
        payload=payload,
    )
    agent = response.get("agent", {}) if isinstance(response.get("agent"), dict) else {}
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(f"Registered {agent.get('agent_id') or args.agent_id}")
    return 0


def _heartbeat(args: argparse.Namespace, runtime: LegacyLiveAgentCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/heartbeat"),
        method="POST",
        payload=runtime.heartbeat_payload(args),
    )
    agent = response.get("agent", {}) if isinstance(response.get("agent"), dict) else {}
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(f"{agent.get('agent_id') or args.agent_id}: {agent.get('status') or args.status}")
    return 0


def _say(args: argparse.Namespace, runtime: LegacyLiveAgentCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    payload: dict[str, object] = {"message": " ".join(args.message), "kind": "message"}
    if args.source_event_id:
        payload["source_event_id"] = args.source_event_id
    if args.auto_chain_depth is not None:
        payload["auto_chain_depth"] = args.auto_chain_depth
    if args.flow_id:
        payload["flow_id"] = args.flow_id
        payload["flow_action"] = "speak"
        payload["flow_runtime_mode"] = "provider_tool_loop"
    if args.flow_meeting_id:
        payload["flow_meeting_id"] = args.flow_meeting_id
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/lobby"),
        method="POST",
        payload=payload,
    )
    event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(f"Posted {event.get('id') or 'lobby message'}")
    return 0


def _room(args: argparse.Namespace, runtime: LegacyLiveAgentCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/room")
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0
